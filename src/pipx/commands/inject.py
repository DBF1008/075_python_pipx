import dataclasses
import logging
import os
import re
import sys
from collections.abc import Iterable
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
_INCLUDE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:-r|--requirement)\s*=?\s*(.+)$"
)
_CONSTRAINT_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:-c|--constraint)\s*=?\s*(.+)$"
)


@dataclasses.dataclass
class ParsedRequirements:
    """Structured result from parsing requirements files."""

    packages: list[str]
    constraints: list[str]


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
    constraint_args: list[str] = []
    for filename in requirement_files:
        parsed = parse_requirements(filename)
        packages.extend(parsed.packages)
        for constraint_path in parsed.constraints:
            constraint_args.extend(["-c", constraint_path])

    # Remove duplicates and order deterministically
    packages = sorted(set(packages))
    all_pip_args = pip_args + constraint_args

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
            pip_args=all_pip_args,
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


def _is_local_path(spec: str) -> bool:
    """Check whether *spec* looks like a local filesystem path."""
    # Strip extras/markers (e.g. "./lib[extra]" or "./lib ; python_version")
    base = re.split(r"[\[;]", spec, maxsplit=1)[0].strip()
    if base.startswith(("./", "../", "/")):
        return True
    if os.sep != "/" and base.startswith((".\\", "..\\",)):
        return True
    return False


def _parse_requirements_impl(
    filepath: Path,
    packages: dict[str, None],
    constraints: list[str],
    visited: set[str],
) -> None:
    """Recursively parse a requirements file.

    * ``-r`` / ``--requirement`` lines are followed recursively.
    * ``-c`` / ``--constraint`` lines have their resolved path collected.
    * Other option lines (``-e``, ``--index-url``, …) are silently skipped.
    * Relative paths (both in directives and in package specs such as
      ``./mylib``) are resolved against the directory of the file that
      contains them, matching pip's own behaviour.
    """
    resolved = filepath.resolve()
    canon = str(resolved)
    if canon in visited:
        return
    visited.add(canon)

    base_dir = resolved.parent

    with open(resolved) as fh:
        for raw_line in fh:
            # Strip inline comments and surrounding whitespace
            line = _COMMENT_RE.sub("", raw_line).strip()
            if not line:
                continue

            # -r / --requirement -----------------------------------------------
            m = _INCLUDE_RE.match(line)
            if m:
                ref = m.group(1).strip()
                ref_path = (base_dir / ref).resolve()
                _parse_requirements_impl(ref_path, packages, constraints, visited)
                continue

            # -c / --constraint ------------------------------------------------
            m = _CONSTRAINT_RE.match(line)
            if m:
                ref = m.group(1).strip()
                ref_path = (base_dir / ref).resolve()
                constraints.append(str(ref_path))
                continue

            # Skip any other pip option lines (--index-url, -e, etc.)
            if line.startswith("-"):
                continue

            # Resolve local-path specs relative to the requirements file
            if _is_local_path(line):
                # Preserve extras / markers after the path
                parts = re.split(r"(\[.*)", line, maxsplit=1)
                path_part = parts[0].strip()
                rest = parts[1] if len(parts) > 1 else ""
                line = str((base_dir / path_part).resolve()) + rest

            packages[line] = None


def parse_requirements(filename: str | os.PathLike) -> ParsedRequirements:
    """
    Extract package specifications from a requirements file.

    Supports ``-r``/``--requirement`` (recursive includes) and
    ``-c``/``--constraint`` directives.  Relative paths inside the file are
    resolved against the directory containing *filename*, matching pip's
    behaviour.  Circular includes are detected and silently skipped.
    """
    packages: dict[str, None] = {}  # insertion-ordered set
    constraints: list[str] = []
    visited: set[str] = set()

    _parse_requirements_impl(
        Path(filename).resolve(), packages, constraints, visited,
    )

    return ParsedRequirements(
        packages=list(packages),
        constraints=constraints,
    )


__all__ = [
    "ParsedRequirements",
    "inject",
    "inject_dep",
    "parse_requirements",
]
