import logging
import re
import textwrap

import pytest

from helpers import PIPX_METADATA_LEGACY_VERSIONS, mock_legacy_venv, run_pipx_cli, skip_if_windows
from package_info import PKG
from pipx.commands.inject import (
    _deduplicate_packages,
    _extract_include,
    _is_pip_option,
    _join_continued_lines,
    _strip_comment,
    parse_requirements,
)
from pipx.util import PipxError


# Note that this also checks that packages used in other tests can be injected individually
@pytest.mark.parametrize(
    "pkg_spec,",
    [
        PKG["black"]["spec"],
        PKG["nox"]["spec"],
        PKG["pylint"]["spec"],
        PKG["ipython"]["spec"],
        "jaraco.clipboard==2.0.1",  # tricky character
    ],
)
def test_inject_single_package(pipx_temp_env, capsys, caplog, pkg_spec):
    assert not run_pipx_cli(["install", "pycowsay"])
    assert not run_pipx_cli(["inject", "pycowsay", pkg_spec])

    # Check arguments have been parsed correctly
    assert f"Injecting packages: {[pkg_spec]!r}" in caplog.text

    # Check it's actually being installed and into correct venv
    captured = capsys.readouterr()
    injected = re.findall(r"injected package (.+?) into venv pycowsay", captured.out)
    pkg_name = pkg_spec.split("=", 1)[0].replace(".", "-")  # assuming spec is always of the form <name>==<version>
    assert set(injected) == {pkg_name}


@skip_if_windows
def test_inject_simple_global(pipx_temp_env, capsys):
    assert not run_pipx_cli(["install", "--global", "pycowsay"])
    assert not run_pipx_cli(["inject", "--global", "pycowsay", PKG["black"]["spec"]])


@pytest.mark.parametrize("metadata_version", PIPX_METADATA_LEGACY_VERSIONS)
def test_inject_simple_legacy_venv(pipx_temp_env, capsys, metadata_version):
    assert not run_pipx_cli(["install", "pycowsay"])
    mock_legacy_venv("pycowsay", metadata_version=metadata_version)
    if metadata_version is not None:
        assert not run_pipx_cli(["inject", "pycowsay", PKG["black"]["spec"]])
    else:
        # no metadata in venv should result in PipxError with message
        assert run_pipx_cli(["inject", "pycowsay", PKG["black"]["spec"]])
        assert "Please uninstall and install" in capsys.readouterr().err


@pytest.mark.parametrize("with_suffix,", [(False,), (True,)])
def test_inject_include_apps(pipx_temp_env, capsys, with_suffix):
    install_args = []
    suffix = ""

    if with_suffix:
        suffix = "_x"
        install_args = [f"--suffix={suffix}"]

    assert not run_pipx_cli(["install", "pycowsay", *install_args])
    assert not run_pipx_cli(["inject", f"pycowsay{suffix}", PKG["black"]["spec"], "--include-deps"])

    if suffix:
        assert run_pipx_cli(["inject", "pycowsay", PKG["black"]["spec"], "--include-deps"])

    assert not run_pipx_cli(["inject", f"pycowsay{suffix}", PKG["black"]["spec"], "--include-deps"])


@pytest.mark.parametrize(
    "with_packages,",
    [
        (),  # no extra packages
        ("black",),  # duplicate from requirements file
        ("ipython",),  # additional package
    ],
)
def test_inject_with_req_file(pipx_temp_env, capsys, caplog, tmp_path, with_packages):
    caplog.set_level(logging.INFO)

    req_file = tmp_path / "inject-requirements.txt"
    req_file.write_text(
        textwrap.dedent(
            f"""
                {PKG["black"]["spec"]} # a comment inline
                {PKG["nox"]["spec"]}

                {PKG["pylint"]["spec"]}
                # comment on separate line
            """
        ).strip()
    )
    assert not run_pipx_cli(["install", "pycowsay"])

    assert not run_pipx_cli(
        ["inject", "pycowsay", *(PKG[pkg]["spec"] for pkg in with_packages), "--requirement", str(req_file)]
    )

    packages = [
        ("black", PKG["black"]["spec"]),
        ("nox", PKG["nox"]["spec"]),
        ("pylint", PKG["pylint"]["spec"]),
    ]
    packages.extend((pkg, PKG[pkg]["spec"]) for pkg in with_packages)
    packages = sorted(set(packages))

    # Check arguments and files have been parsed correctly
    assert f"Injecting packages: {[p for _, p in packages]!r}" in caplog.text

    # Check they're actually being installed and into correct venv
    captured = capsys.readouterr()
    injected = re.findall(r"injected package (.+?) into venv pycowsay", captured.out)
    assert set(injected) == {pkg for pkg, _ in packages}


# ---------------------------------------------------------------------------
# Unit tests for internal helpers
# ---------------------------------------------------------------------------


class TestJoinContinuedLines:
    def test_no_continuation(self):
        assert list(_join_continued_lines(["a\n", "b\n", "c\n"])) == ["a", "b", "c"]

    def test_simple_continuation(self):
        assert list(_join_continued_lines(["a\\\n", "b\n"])) == ["ab"]

    def test_multi_continuation(self):
        lines = ["a\\\n", "b\\\n", "c\n"]
        assert list(_join_continued_lines(lines)) == ["abc"]

    def test_trailing_backslash_on_last_line(self):
        assert list(_join_continued_lines(["a\\\n", "b\\"])) == ["ab"]

    def test_empty_input(self):
        assert list(_join_continued_lines([])) == []


class TestStripComment:
    def test_no_comment(self):
        assert _strip_comment("requests==2.28.0") == "requests==2.28.0"

    def test_inline_comment(self):
        assert _strip_comment("requests==2.28.0 # pinned") == "requests==2.28.0"

    def test_full_line_comment(self):
        assert _strip_comment("# this is a comment") == ""

    def test_leading_whitespace_comment(self):
        assert _strip_comment("   # comment") == ""

    def test_url_with_fragment(self):
        # ``#egg=...`` is not preceded by whitespace → kept intact
        line = "git+https://github.com/user/repo.git#egg=pkg"
        assert _strip_comment(line) == line

    def test_url_then_comment(self):
        line = "git+https://github.com/user/repo.git#egg=pkg  # a comment"
        assert _strip_comment(line) == "git+https://github.com/user/repo.git#egg=pkg"

    def test_empty_line(self):
        assert _strip_comment("") == ""


class TestExtractInclude:
    def test_short_requirement(self):
        assert _extract_include("-r base.txt") == ("r", "base.txt")

    def test_short_constraint(self):
        assert _extract_include("-c constraints.txt") == ("c", "constraints.txt")

    def test_long_requirement_space(self):
        assert _extract_include("--requirement base.txt") == ("r", "base.txt")

    def test_long_requirement_equals(self):
        assert _extract_include("--requirement=base.txt") == ("r", "base.txt")

    def test_long_constraint_space(self):
        assert _extract_include("--constraint constraints.txt") == ("c", "constraints.txt")

    def test_long_constraint_equals(self):
        assert _extract_include("--constraint=constraints.txt") == ("c", "constraints.txt")

    def test_short_no_space(self):
        assert _extract_include("-rbase.txt") == ("r", "base.txt")
        assert _extract_include("-cconstraints.txt") == ("c", "constraints.txt")

    def test_regular_package(self):
        assert _extract_include("requests==2.28.0") is None

    def test_empty(self):
        assert _extract_include("") is None

    def test_flag_without_path(self):
        assert _extract_include("-r") is None
        assert _extract_include("--requirement") is None


class TestIsPipOption:
    def test_index_url(self):
        assert _is_pip_option("--index-url https://pypi.org/simple")
        assert _is_pip_option("-i https://pypi.org/simple")

    def test_find_links(self):
        assert _is_pip_option("-f /some/path")
        assert _is_pip_option("--find-links /some/path")

    def test_extra_index_url(self):
        assert _is_pip_option("--extra-index-url https://pypi.org/simple")

    def test_editable(self):
        assert _is_pip_option("-e ./local-pkg")
        assert _is_pip_option("--editable ./local-pkg")

    def test_hash(self):
        assert _is_pip_option("--hash=sha256:abcdef")

    def test_boolean_flags(self):
        assert _is_pip_option("--no-binary :all:")
        assert _is_pip_option("--prefer-binary")
        assert _is_pip_option("--pre")
        assert _is_pip_option("--no-deps")
        assert _is_pip_option("--no-index")

    def test_package_not_option(self):
        assert not _is_pip_option("requests==2.28.0")
        assert not _is_pip_option("black")
        assert not _is_pip_option("./local-package")
        assert not _is_pip_option("git+https://github.com/user/repo.git")


class TestDeduplicatePackages:
    def test_no_duplicates(self):
        result = _deduplicate_packages(["black==22.8.0", "nox==2023.4.22"])
        assert result == ["black==22.8.0", "nox==2023.4.22"]

    def test_exact_duplicates(self):
        result = _deduplicate_packages(["black==22.8.0", "black==22.8.0"])
        assert result == ["black==22.8.0"]

    def test_same_name_different_versions(self):
        # First occurrence wins
        result = _deduplicate_packages(["black==22.8.0", "black==23.1.0"])
        assert result == ["black==22.8.0"]

    def test_canonical_name_normalization(self):
        # Underscore vs hyphen: same canonical name
        result = _deduplicate_packages(["my-package==1.0", "my_package==2.0"])
        assert result == ["my-package==1.0"]

    def test_first_seen_order_preserved(self):
        result = _deduplicate_packages(["pylint==3.0.4", "black==22.8.0", "nox==2023.4.22"])
        assert result == ["pylint==3.0.4", "black==22.8.0", "nox==2023.4.22"]

    def test_empty_input(self):
        assert _deduplicate_packages([]) == []

    def test_mixed_duplicates(self):
        result = _deduplicate_packages(["A==1.0", "B==2.0", "a==3.0", "b==4.0", "C==5.0"])
        assert result == ["A==1.0", "B==2.0", "C==5.0"]


# ---------------------------------------------------------------------------
# Unit tests for parse_requirements (file-based, uses tmp_path)
# ---------------------------------------------------------------------------


class TestParseRequirements:
    def test_simple_file(self, tmp_path):
        f = tmp_path / "reqs.txt"
        f.write_text("black==22.8.0\nnox==2023.4.22\n")
        assert list(parse_requirements(f)) == ["black==22.8.0", "nox==2023.4.22"]

    def test_comments_and_blanks(self, tmp_path):
        f = tmp_path / "reqs.txt"
        f.write_text("# header\nblack==22.8.0  # pinned\n\nnox==2023.4.22\n")
        assert list(parse_requirements(f)) == ["black==22.8.0", "nox==2023.4.22"]

    def test_line_continuation(self, tmp_path):
        f = tmp_path / "reqs.txt"
        f.write_text("black\\\n==22.8.0\nnox==2023.4.22\n")
        assert list(parse_requirements(f)) == ["black==22.8.0", "nox==2023.4.22"]

    def test_recursive_requirement(self, tmp_path):
        (tmp_path / "base.txt").write_text("black==22.8.0\n")
        (tmp_path / "main.txt").write_text("-r base.txt\nnox==2023.4.22\n")
        assert list(parse_requirements(tmp_path / "main.txt")) == [
            "black==22.8.0",
            "nox==2023.4.22",
        ]

    def test_constraint_file(self, tmp_path):
        (tmp_path / "constraints.txt").write_text("setuptools>=65.0\n")
        (tmp_path / "main.txt").write_text("-c constraints.txt\nrequests==2.28.0\n")
        assert list(parse_requirements(tmp_path / "main.txt")) == [
            "setuptools>=65.0",
            "requests==2.28.0",
        ]

    def test_relative_path_resolution(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "base.txt").write_text("black==22.8.0\n")
        (tmp_path / "main.txt").write_text("-r sub/base.txt\nnox==2023.4.22\n")
        assert list(parse_requirements(tmp_path / "main.txt")) == [
            "black==22.8.0",
            "nox==2023.4.22",
        ]

    def test_circular_include_detection(self, tmp_path):
        (tmp_path / "a.txt").write_text("-r b.txt\nfoo==1.0\n")
        (tmp_path / "b.txt").write_text("-r a.txt\nbar==2.0\n")
        # Should not raise / recurse infinitely; each file parsed once
        assert list(parse_requirements(tmp_path / "a.txt")) == [
            "bar==2.0",
            "foo==1.0",
        ]

    def test_self_referencing_file(self, tmp_path):
        f = tmp_path / "self.txt"
        f.write_text("-r self.txt\nrequests==2.28.0\n")
        assert list(parse_requirements(f)) == ["requests==2.28.0"]

    def test_skips_pip_options(self, tmp_path):
        f = tmp_path / "reqs.txt"
        f.write_text(
            "--index-url https://pypi.org/simple\n"
            "-f /local/wheels\n"
            "--extra-index-url https://extra.pypi.org/simple\n"
            "--no-binary :all:\n"
            "--prefer-binary\n"
            "requests==2.28.0\n"
        )
        assert list(parse_requirements(f)) == ["requests==2.28.0"]

    def test_skips_editable(self, tmp_path):
        f = tmp_path / "reqs.txt"
        f.write_text("-e ./local-package\nrequests==2.28.0\n")
        assert list(parse_requirements(f)) == ["requests==2.28.0"]

    def test_deep_nesting(self, tmp_path):
        """Three levels of recursive includes."""
        (tmp_path / "level3.txt").write_text("deep-pkg==3.0\n")
        (tmp_path / "level2.txt").write_text("-r level3.txt\nmid-pkg==2.0\n")
        (tmp_path / "level1.txt").write_text("-r level2.txt\ntop-pkg==1.0\n")
        assert list(parse_requirements(tmp_path / "level1.txt")) == [
            "deep-pkg==3.0",
            "mid-pkg==2.0",
            "top-pkg==1.0",
        ]

    def test_nonexistent_file_raises(self, tmp_path):
        with pytest.raises(PipxError, match="Requirements file not found"):
            list(parse_requirements(tmp_path / "nonexistent.txt"))

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        assert list(parse_requirements(f)) == []

    def test_only_comments(self, tmp_path):
        f = tmp_path / "reqs.txt"
        f.write_text("# just a comment\n# another\n")
        assert list(parse_requirements(f)) == []

    def test_long_form_requirement(self, tmp_path):
        (tmp_path / "base.txt").write_text("black==22.8.0\n")
        (tmp_path / "main.txt").write_text("--requirement base.txt\nnox==2023.4.22\n")
        assert list(parse_requirements(tmp_path / "main.txt")) == [
            "black==22.8.0",
            "nox==2023.4.22",
        ]

    def test_long_form_equals_syntax(self, tmp_path):
        (tmp_path / "base.txt").write_text("black==22.8.0\n")
        (tmp_path / "main.txt").write_text("--requirement=base.txt\nnox==2023.4.22\n")
        assert list(parse_requirements(tmp_path / "main.txt")) == [
            "black==22.8.0",
            "nox==2023.4.22",
        ]

    def test_constraint_long_form(self, tmp_path):
        (tmp_path / "constraints.txt").write_text("setuptools>=65.0\n")
        (tmp_path / "main.txt").write_text("--constraint constraints.txt\nrequests==2.28.0\n")
        assert list(parse_requirements(tmp_path / "main.txt")) == [
            "setuptools>=65.0",
            "requests==2.28.0",
        ]

    def test_mixed_r_and_c(self, tmp_path):
        """Both -r and -c in the same file, with nested includes."""
        (tmp_path / "base.txt").write_text("black==22.8.0\n")
        (tmp_path / "constraints.txt").write_text("setuptools>=65.0\n")
        (tmp_path / "main.txt").write_text("-r base.txt\n-c constraints.txt\nnox==2023.4.22\n")
        assert list(parse_requirements(tmp_path / "main.txt")) == [
            "black==22.8.0",
            "setuptools>=65.0",
            "nox==2023.4.22",
        ]

    def test_deduplication_across_files(self, tmp_path):
        """Same package in base and main: first-seen wins."""
        (tmp_path / "base.txt").write_text("black==22.8.0\n")
        (tmp_path / "main.txt").write_text("-r base.txt\nblack==23.1.0\n")
        result = list(parse_requirements(tmp_path / "main.txt"))
        assert result == ["black==22.8.0", "black==23.1.0"]
        # After deduplication at the inject level only the first survives
        assert _deduplicate_packages(result) == ["black==22.8.0"]

    def test_local_relative_path_preserved(self, tmp_path):
        """A relative-path package spec is NOT confused with pip options."""
        f = tmp_path / "reqs.txt"
        f.write_text("./local-package\n../other-package\n")
        assert list(parse_requirements(f)) == ["./local-package", "../other-package"]


# ---------------------------------------------------------------------------
# Integration test – inject with recursive requirements file
# ---------------------------------------------------------------------------


def test_inject_with_recursive_req_file(pipx_temp_env, capsys, caplog, tmp_path):
    """Verify that inject correctly follows -r includes and deduplicates."""
    caplog.set_level(logging.INFO)

    # base.txt: black
    base_file = tmp_path / "base-requirements.txt"
    base_file.write_text(f"{PKG['black']['spec']}\n")

    # main.txt: -r base.txt + nox + pylint
    req_file = tmp_path / "inject-requirements.txt"
    req_file.write_text(
        textwrap.dedent(
            f"""\
            -r base-requirements.txt
            {PKG["nox"]["spec"]}
            {PKG["pylint"]["spec"]}
            """
        )
    )

    assert not run_pipx_cli(["install", "pycowsay"])
    assert not run_pipx_cli(["inject", "pycowsay", "--requirement", str(req_file)])

    expected_specs = sorted([PKG["black"]["spec"], PKG["nox"]["spec"], PKG["pylint"]["spec"]])
    assert f"Injecting packages: {expected_specs!r}" in caplog.text

    captured = capsys.readouterr()
    injected = re.findall(r"injected package (.+?) into venv pycowsay", captured.out)
    assert set(injected) == {"black", "nox", "pylint"}
