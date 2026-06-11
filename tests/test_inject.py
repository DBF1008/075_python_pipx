import logging
import os
import re
import textwrap

import pytest

from helpers import PIPX_METADATA_LEGACY_VERSIONS, mock_legacy_venv, run_pipx_cli, skip_if_windows
from package_info import PKG
from pipx.commands.inject import parse_requirements


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
# Unit tests for parse_requirements
# ---------------------------------------------------------------------------


class TestParseRequirementsSimple:
    """Verify that plain requirements files still work as before."""

    def test_simple_packages(self, tmp_path):
        req = tmp_path / "req.txt"
        req.write_text("requests==2.28.0\nflask==2.2.0\n")
        result = parse_requirements(req)
        assert result.packages == ["requests==2.28.0", "flask==2.2.0"]
        assert result.constraints == []

    def test_comments_and_blank_lines(self, tmp_path):
        req = tmp_path / "req.txt"
        req.write_text(
            textwrap.dedent("""\
                # header comment
                requests==2.28.0  # inline comment

                flask==2.2.0
                # another comment
            """)
        )
        result = parse_requirements(req)
        assert result.packages == ["requests==2.28.0", "flask==2.2.0"]
        assert result.constraints == []

    def test_empty_file(self, tmp_path):
        req = tmp_path / "req.txt"
        req.write_text("")
        result = parse_requirements(req)
        assert result.packages == []
        assert result.constraints == []


class TestParseRequirementsInclude:
    """Test -r / --requirement recursive includes."""

    def test_simple_include(self, tmp_path):
        base = tmp_path / "base.txt"
        base.write_text("flask==2.2.0\n")
        top = tmp_path / "top.txt"
        top.write_text(f"requests==2.28.0\n-r base.txt\n")
        result = parse_requirements(top)
        assert result.packages == ["requests==2.28.0", "flask==2.2.0"]

    def test_include_long_form(self, tmp_path):
        base = tmp_path / "base.txt"
        base.write_text("flask==2.2.0\n")
        top = tmp_path / "top.txt"
        top.write_text("requests==2.28.0\n--requirement base.txt\n")
        result = parse_requirements(top)
        assert result.packages == ["requests==2.28.0", "flask==2.2.0"]

    def test_include_equals_syntax(self, tmp_path):
        base = tmp_path / "base.txt"
        base.write_text("flask==2.2.0\n")
        top = tmp_path / "top.txt"
        top.write_text("--requirement=base.txt\n")
        result = parse_requirements(top)
        assert result.packages == ["flask==2.2.0"]

    def test_nested_includes(self, tmp_path):
        level2 = tmp_path / "level2.txt"
        level2.write_text("celery==5.2.0\n")
        level1 = tmp_path / "level1.txt"
        level1.write_text("flask==2.2.0\n-r level2.txt\n")
        top = tmp_path / "top.txt"
        top.write_text("requests==2.28.0\n-r level1.txt\n")
        result = parse_requirements(top)
        assert result.packages == ["requests==2.28.0", "flask==2.2.0", "celery==5.2.0"]

    def test_include_relative_path_subdirectory(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        child = subdir / "child.txt"
        child.write_text("flask==2.2.0\n")
        top = tmp_path / "top.txt"
        top.write_text("-r sub/child.txt\n")
        result = parse_requirements(top)
        assert result.packages == ["flask==2.2.0"]

    def test_include_relative_path_from_child(self, tmp_path):
        """A child file's -r should resolve relative to the child's dir."""
        subdir = tmp_path / "sub"
        subdir.mkdir()
        sibling = subdir / "sibling.txt"
        sibling.write_text("celery==5.2.0\n")
        child = subdir / "child.txt"
        child.write_text("-r sibling.txt\n")
        top = tmp_path / "top.txt"
        top.write_text("requests==2.28.0\n-r sub/child.txt\n")
        result = parse_requirements(top)
        assert result.packages == ["requests==2.28.0", "celery==5.2.0"]


class TestParseRequirementsConstraint:
    """Test -c / --constraint directive collection."""

    def test_simple_constraint(self, tmp_path):
        cons = tmp_path / "constraints.txt"
        cons.write_text("requests<3.0\n")
        top = tmp_path / "top.txt"
        top.write_text("requests\n-c constraints.txt\n")
        result = parse_requirements(top)
        assert result.packages == ["requests"]
        assert result.constraints == [str(cons.resolve())]

    def test_constraint_long_form(self, tmp_path):
        cons = tmp_path / "constraints.txt"
        cons.write_text("requests<3.0\n")
        top = tmp_path / "top.txt"
        top.write_text("--constraint constraints.txt\n")
        result = parse_requirements(top)
        assert result.constraints == [str(cons.resolve())]

    def test_constraint_equals_syntax(self, tmp_path):
        cons = tmp_path / "constraints.txt"
        cons.write_text("requests<3.0\n")
        top = tmp_path / "top.txt"
        top.write_text("--constraint=constraints.txt\n")
        result = parse_requirements(top)
        assert result.constraints == [str(cons.resolve())]

    def test_constraint_relative_path(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        cons = subdir / "cons.txt"
        cons.write_text("flask<3\n")
        top = tmp_path / "top.txt"
        top.write_text("flask\n-c sub/cons.txt\n")
        result = parse_requirements(top)
        assert result.packages == ["flask"]
        assert result.constraints == [str(cons.resolve())]

    def test_constraint_from_included_file(self, tmp_path):
        """A constraint referenced inside an included file should be collected."""
        cons = tmp_path / "constraints.txt"
        cons.write_text("flask<3\n")
        child = tmp_path / "child.txt"
        child.write_text("flask\n-c constraints.txt\n")
        top = tmp_path / "top.txt"
        top.write_text("requests\n-r child.txt\n")
        result = parse_requirements(top)
        assert result.packages == ["requests", "flask"]
        assert result.constraints == [str(cons.resolve())]

    def test_multiple_constraints(self, tmp_path):
        c1 = tmp_path / "c1.txt"
        c1.write_text("requests<3\n")
        c2 = tmp_path / "c2.txt"
        c2.write_text("flask<3\n")
        top = tmp_path / "top.txt"
        top.write_text("requests\nflask\n-c c1.txt\n-c c2.txt\n")
        result = parse_requirements(top)
        assert result.constraints == [str(c1.resolve()), str(c2.resolve())]


class TestParseRequirementsCycleDetection:
    """Ensure circular includes don't cause infinite recursion."""

    def test_self_referencing(self, tmp_path):
        req = tmp_path / "req.txt"
        req.write_text("requests\n-r req.txt\n")
        result = parse_requirements(req)
        assert result.packages == ["requests"]

    def test_mutual_cycle(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("requests\n-r b.txt\n")
        b.write_text("flask\n-r a.txt\n")
        result = parse_requirements(a)
        assert set(result.packages) == {"requests", "flask"}

    def test_triangle_cycle(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        c = tmp_path / "c.txt"
        a.write_text("pkg-a\n-r b.txt\n")
        b.write_text("pkg-b\n-r c.txt\n")
        c.write_text("pkg-c\n-r a.txt\n")
        result = parse_requirements(a)
        assert set(result.packages) == {"pkg-a", "pkg-b", "pkg-c"}


class TestParseRequirementsDedup:
    """Duplicate package specs should appear only once."""

    def test_dedup_within_file(self, tmp_path):
        req = tmp_path / "req.txt"
        req.write_text("requests==2.28.0\nflask==2.2.0\nrequests==2.28.0\n")
        result = parse_requirements(req)
        assert result.packages == ["requests==2.28.0", "flask==2.2.0"]

    def test_dedup_across_includes(self, tmp_path):
        child = tmp_path / "child.txt"
        child.write_text("requests==2.28.0\nflask==2.2.0\n")
        top = tmp_path / "top.txt"
        top.write_text("requests==2.28.0\n-r child.txt\n")
        result = parse_requirements(top)
        assert result.packages == ["requests==2.28.0", "flask==2.2.0"]


class TestParseRequirementsLocalPaths:
    """Local path specs should be resolved relative to the requirements file."""

    def test_relative_dot_path(self, tmp_path):
        req = tmp_path / "req.txt"
        req.write_text("./mylib\n")
        result = parse_requirements(req)
        expected = str((tmp_path / "mylib").resolve())
        assert result.packages == [expected]

    def test_relative_dotdot_path(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        req = subdir / "req.txt"
        req.write_text("../mylib\n")
        result = parse_requirements(req)
        expected = str((tmp_path / "mylib").resolve())
        assert result.packages == [expected]

    def test_relative_path_with_extras(self, tmp_path):
        req = tmp_path / "req.txt"
        req.write_text("./mylib[extra1,extra2]\n")
        result = parse_requirements(req)
        expected = str((tmp_path / "mylib").resolve()) + "[extra1,extra2]"
        assert result.packages == [expected]

    def test_local_path_from_included_file(self, tmp_path):
        """Local path in a child should resolve relative to child's dir."""
        subdir = tmp_path / "sub"
        subdir.mkdir()
        child = subdir / "child.txt"
        child.write_text("./locallib\n")
        top = tmp_path / "top.txt"
        top.write_text("-r sub/child.txt\n")
        result = parse_requirements(top)
        expected = str((subdir / "locallib").resolve())
        assert result.packages == [expected]


class TestParseRequirementsSkipOptions:
    """Unrecognised pip option lines should be silently skipped."""

    def test_skip_editable(self, tmp_path):
        req = tmp_path / "req.txt"
        req.write_text("-e git+https://example.com/repo.git#egg=pkg\nrequests\n")
        result = parse_requirements(req)
        assert result.packages == ["requests"]

    def test_skip_index_url(self, tmp_path):
        req = tmp_path / "req.txt"
        req.write_text("--index-url https://pypi.example.com/simple\nrequests\n")
        result = parse_requirements(req)
        assert result.packages == ["requests"]

    def test_skip_find_links(self, tmp_path):
        req = tmp_path / "req.txt"
        req.write_text("--find-links /path/to/wheels\nrequests\n")
        result = parse_requirements(req)
        assert result.packages == ["requests"]


class TestParseRequirementsMixed:
    """Complex files mixing -r, -c, packages, comments, and local paths."""

    def test_full_scenario(self, tmp_path):
        # Set up directory structure:
        # top.txt -> includes sub/deps.txt, constraints cons.txt
        # sub/deps.txt -> includes sub/more.txt, has ./locallib
        # sub/more.txt -> has celery
        subdir = tmp_path / "sub"
        subdir.mkdir()

        cons = tmp_path / "cons.txt"
        cons.write_text("requests<3\nflask<3\n")

        more = subdir / "more.txt"
        more.write_text("celery==5.2.0\n")

        deps = subdir / "deps.txt"
        deps.write_text(
            textwrap.dedent("""\
                flask==2.2.0
                -r more.txt
                ./locallib
            """)
        )

        top = tmp_path / "top.txt"
        top.write_text(
            textwrap.dedent("""\
                # Top-level requirements
                requests==2.28.0
                --index-url https://pypi.example.com/simple
                -r sub/deps.txt
                -c cons.txt
            """)
        )

        result = parse_requirements(top)

        assert result.packages == [
            "requests==2.28.0",
            "flask==2.2.0",
            "celery==5.2.0",
            str((subdir / "locallib").resolve()),
        ]
        assert result.constraints == [str(cons.resolve())]
