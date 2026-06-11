import logging
import os
import re
import shlex
import sys
from collections.abc import Generator, Iterable
from pathlib import Path
from typing import Final

from packaging.utils import canonicalize_name

from pipx import paths
from pipx.backends import assert_not_pip_under_uv
from pipx.colors import bold
from pipx.commands.common import package_name_from_spec, run_post_install_actions
from pipx.constants import EXIT_CODE_INJECT_ERROR, EXIT_CODE_OK, ExitCode
from pipx.emojis import hazard, stars
from pipx.util import PipxError, pipx_wrap
from pipx.venv import Venv

_LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

_COMMENT_RE: Final[re.Pattern[str]] = re.compile(r"(^|\s+)#.*$")

# pip options that take a value argument; when they appear in a requirements
# file they are not package specifications and must be skipped.
_OPTION_WITH_ARG_RE: Final[re.Pattern[str]] = re.compile(
    r"""^"""
    r"""(?:-i|--index-url"""
    r"""|--extra-index-url"""
    r"""|-f|--find-links"""
    r"""|-e|--editable"""
    r"""|--hash"""
    r"""|--global-option"""
    r"""|--config-settings"""
    r"""|--install-option"""     # deprecated but still recognised
    r"""|--root"""
    r"""|--prefix"""
    r"""|--target"""
    r"""|--build"""
    r"""|--src"""
    r"""|--upgrade-strategy"""
    r"""|--constraint"""         # handled separately by _extract_include
    r"""|--requirement"""        # handled separately by _extract_include
    r"""|[cfier])"""             # short aliases (most already in long list)
    r"""(?:\s|=)""",
    re.VERBOSE,
)

# Standalone pip boolean flags (no argument) that must be skipped.
_STANDALONE_FLAG_RE: Final[re.Pattern[str]] = re.compile(
    r"""^"""
    r"""(?:--no-binary"""
    r"""|--only-binary"""
    r"""|--prefer-binary"""
    r"""|--require-hashes"""
    r"""|--pre"""
    r"""|--trusted-host"""
    r"""|--no-deps"""
    r"""|--no-clean"""
    r"""|--no-index"""
    r"""|--ignore-installed"""
    r"""|--force-reinstall"""
    r"""|--user"""
    r"""|--compile"""
    r"""|--no-compile"""
    r"""|--no-build-isolation"""
    r"""|--use-pep517"""
    r"""|--no-use-pep517"""
    r"""|--break-system-packages"""
    r"""|--disable-pip-version-check"""
    r"""|--progress-bar"""
    r"""|--cert"""
    r"""|--client-cert"""
    r"""|--proxy"""
    r"""|--retries"""
    r"""|--timeout"""
    r"""|--exists-action"""
    r"""|--no-input"""
    r"""|--prefer-tool"""
    r"""|-[A-Za-z]"""
    r""")$"""
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _join_continued_lines(lines: Iterable[str]) -> Generator[str, None, None]:
    """Join lines that end with a backslash continuation character.

    Yields one logical line per group of physical lines.
    """
    continued = ""
    for line in lines:
        stripped = line.rstrip("\n").rstrip("\r")
        if stripped.endswith("\\"):
            continued += stripped[:-1]
        else:
            yield continued + stripped
            continued = ""
    # Handle a trailing backslash on the very last line
    if continued:
        yield continued


def _strip_comment(line: str) -> str:
    """Remove inline comments (``# ...`` preceded by whitespace or at BOL)."""
    return _COMMENT_RE.sub("", line).strip()


def _is_pip_option(line: str) -> bool:
    """Return *True* if *line* looks like a pip CLI option rather than a
    package specification."""
    if not line.startswith("-"):
        return False
    if _OPTION_WITH_ARG_RE.match(line):
        return True
    if _STANDALONE_FLAG_RE.match(line):
        return True
    # Fallback: any unrecognised ``--<word>`` flag
    if line.startswith("--"):
        return True
    return False


def _extract_include(line: str) -> tuple[str, str] | None:
    """If *line* is a ``-r``/``--requirement`` or ``-c``/``--constraint``
    directive, return ``(kind, path)`` where *kind* is ``'r'`` or ``'c'``.

    Returns ``None`` for any other line.
    """
    try:
        tokens = shlex.split(line)
    except ValueError:
        # Malformed quoting – treat as a plain package spec
        return None

    if not tokens:
        return None

    flag = tokens[0]

    # Short form: -r <path>  /  -c <path>  (also -rpath / -cpath)
    if flag in ("-r", "-c") and len(tokens) >= 2:
        return (flag, tokens[1])
    if flag.startswith("-r") and len(flag) > 2 and not flag[2:].startswith("-"):
        return ("r", flag[2:])
    if flag.startswith("-c") and len(flag) > 2 and not flag[2:].startswith("-"):
        return ("c", flag[2:])

    # Long form: --requirement=<path>  /  --requirement <path>
    for long_flag, kind in (("--requirement", "r"), ("--constraint", "c")):
        if flag == long_flag and len(tokens) >= 2:
            return (kind, tokens[1])
        if flag.startswith(long_flag + "="):
            return (kind, flag[len(long_flag) + 1 :])

    return None


def _deduplicate_packages(specs: Iterable[str]) -> list[str]:
    """Deduplicate package specifications by canonical package name.

    For each unique canonical name the **first** occurrence wins, preserving
    the original insertion order while preventing the same package from being
    installed more than once.
    """
    seen: set[str] = set()
    result: list[str] = []
    for spec in specs:
        name = canonicalize_name(re.split(r"[>=<!~\s@;]", spec, maxsplit=1)[0].strip())
        if name not in seen:
            seen.add(name)
            result.append(spec)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_requirements(
    filename: str | os.PathLike,
    *,
    _visited: set[str] | None = None,
) -> Generator[str, None, None]:
    """Parse a pip-style requirements file, yielding package specifications.

    Supported features:

    * **Recursive includes** – ``-r`` / ``--requirement`` directives are
      followed recursively.
    * **Constraint files** – ``-c`` / ``--constraint`` directives are
      followed; entries in constraint files are yielded alongside regular
      requirements.
    * **Relative paths** – paths in ``-r`` / ``-c`` directives are resolved
      relative to the *including* file's directory.
    * **Line continuations** – a trailing ``\\`` joins the current line with
      the next one.
    * **Comments** – full-line and inline ``#`` comments are stripped.
    * **pip options** – common pip options (``--index-url``, ``-f``, ``-e``,
      ``--hash``, …) are silently skipped.
    * **Circular includes** – a file that has already been parsed is not
      processed again.
    """
    filepath = Path(filename).resolve()

    if _visited is None:
        _visited = set()

    real_path = str(filepath)
    if real_path in _visited:
        _LOGGER.debug("Skipping already-visited requirements file: %s", filepath)
        return
    _visited.add(real_path)

    if not filepath.is_file():
        raise PipxError(f"Requirements file not found: {filename}")

    base_dir = filepath.parent

    with open(filepath) as f:
        for logical_line in _join_continued_lines(f):
            line = _strip_comment(logical_line)
            if not line:
                continue

            # -r / -c include
            include = _extract_include(line)
            if include is not None:
                _kind, rel_path = include
                yield from parse_requirements(
                    base_dir / rel_path,
                    _visited=_visited,
                )
                continue

            # Skip pip options that are not package specs
            if _is_pip_option(line):
                _LOGGER.debug("Skipping pip option in requirements file: %s", line)
                continue

            yield line


def inject_dep(
    venv_dir: Path,
    package_name: str | None,
    package_spec: str,
    pip_args: list[str],
    *,
    verbose: bool,
    include_apps: bool,
    include_dependencies: bool,
    force: bool,
    suffix: bool = False,
    backend: str | None = None,
    env_backend: str | None = None,
) -> bool:
    _LOGGER.debug("Injecting package %s", package_spec)

    if not venv_dir.exists() or next(venv_dir.iterdir(), None) is None:
        raise PipxError(
            f"""
            Can't inject {package_spec!r} into nonexistent Virtual Environment
            {venv_dir.name!r}. Be sure to install the package first with 'pipx
            install {venv_dir.name}' before injecting into it.
            """
        )

    venv = Venv(venv_dir, verbose=verbose, backend=backend, env_backend=env_backend)
    venv.check_upgrade_shared_libs(pip_args=pip_args, verbose=verbose)

    if not venv.package_metadata:
        raise PipxError(
            f"""
            Can't inject {package_spec!r} into Virtual Environment
            {venv.name!r}. {venv.name!r} has missing internal pipx metadata. It
            was likely installed using a pipx version before 0.15.0.0. Please
            uninstall and install {venv.name!r}, or reinstall-all to fix.
            """
        )

    # package_spec is anything pip-installable, including package_name, vcs spec,
    #   zip file, or tar.gz file.
    if package_name is None:
        package_name = package_name_from_spec(
            package_spec,
            os.fspath(venv.python_path),
            pip_args=pip_args,
            verbose=verbose,
            backend=venv.backend_name,
            env_backend=env_backend,
        )

    # Mirrors the install-side guard: dropping pip into a uv venv works for
    # ``pipx run`` but breaks anyone reaching for the venv's missing pip.
    assert_not_pip_under_uv(canonicalize_name(package_name), venv.backend_name)

    if not force and venv.has_package(package_name):
        _LOGGER.info("Package %s has already been injected", package_name)
        print(
            pipx_wrap(
                f"""
                {hazard} {package_name} already seems to be injected in {venv.name!r}.
                Not modifying existing installation in '{venv_dir}'.
                Pass '--force' to force installation.
                """
            )
        )
        return True

    if suffix:
        venv_suffix = venv.package_metadata[venv.main_package_name].suffix
    else:
        venv_suffix = ""
    venv.install_package(
        package_name=package_name,
        package_or_url=package_spec,
        pip_args=pip_args,
        include_dependencies=include_dependencies,
        include_apps=include_apps,
        is_main_package=False,
        suffix=venv_suffix,
    )
    if include_apps:
        run_post_install_actions(
            venv,
            package_name,
            paths.ctx.bin_dir,
            paths.ctx.man_dir,
            venv_dir,
            include_dependencies,
            force=force,
        )

    print(f"  injected package {bold(package_name)} into venv {bold(venv.name)}")
    print(f"done! {stars}", file=sys.stderr)

    # Any failure to install will raise PipxError, otherwise success
    return True


def inject(
    venv_dir: Path,
    package_specs: Iterable[str],
    requirement_files: Iterable[str],
    pip_args: list[str],
    *,
    verbose: bool,
    include_apps: bool,
    include_dependencies: bool,
    force: bool,
    suffix: bool = False,
    backend: str | None = None,
    env_backend: str | None = None,
) -> ExitCode:
    """Returns pipx exit code."""
    # Combined collection of package specifications
    packages = list(package_specs)
    for filename in requirement_files:
        packages.extend(parse_requirements(filename))

    # Deduplicate by canonical package name (first-seen wins) and produce a
    # stable, deterministic ordering.
    packages = _deduplicate_packages(packages)

    if not packages:
        raise PipxError("No packages have been specified.")
    _LOGGER.info("Injecting packages: %r", packages)

    # Inject packages
    if not include_apps and include_dependencies:
        include_apps = True
    all_success = True
    for dependency in packages:
        all_success &= inject_dep(
            venv_dir,
            package_name=None,
            package_spec=dependency,
            pip_args=pip_args,
            verbose=verbose,
            include_apps=include_apps,
            include_dependencies=include_dependencies,
            force=force,
            suffix=suffix,
            backend=backend,
            env_backend=env_backend,
        )

    # Any failure to install will raise PipxError, otherwise success
    return EXIT_CODE_OK if all_success else EXIT_CODE_INJECT_ERROR


__all__ = [
    "inject",
    "inject_dep",
    "parse_requirements",
]
