from pathlib import Path

import pytest

from pipx.package_specifier import (
    fix_package_name,
    parse_specifier_for_install,
    parse_specifier_for_metadata,
    parse_specifier_for_upgrade,
    resolve_pip_args_constraints,
    valid_pypi_name,
)
from pipx.util import PipxError

TEST_DATA_PATH = "./testdata/test_package_specifier"


@pytest.mark.parametrize(
    "package_spec_in,package_name_out",
    [
        ("Black", "black"),
        ("https://github.com/ambv/black/archive/18.9b0.zip", None),
        ("black @ https://github.com/ambv/black/archive/18.9b0.zip", None),
        ("black-18.9b0-py36-none-any.whl", None),
        ("black-18.9b0.tar.gz", None),
    ],
)
def test_valid_pypi_name(package_spec_in, package_name_out):
    assert valid_pypi_name(package_spec_in) == package_name_out


@pytest.mark.parametrize(
    "package_spec_in,package_name,package_spec_out",
    [
        (
            "https://github.com/ambv/black/archive/18.9b0.zip",
            "black",
            "https://github.com/ambv/black/archive/18.9b0.zip",
        ),
        (
            "nox@https://github.com/ambv/black/archive/18.9b0.zip",
            "black",
            "black @ https://github.com/ambv/black/archive/18.9b0.zip",
        ),
        (
            "nox[extra]@https://github.com/ambv/black/archive/18.9b0.zip",
            "black",
            "black[extra] @ https://github.com/ambv/black/archive/18.9b0.zip",
        ),
    ],
)
def test_fix_package_name(package_spec_in, package_name, package_spec_out):
    assert fix_package_name(package_spec_in, package_name) == package_spec_out


_ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    "package_spec_in,package_or_url_correct,valid_spec",
    [
        ("pipx", "pipx", True),
        ("PiPx_stylized.name", "pipx-stylized-name", True),
        ("pipx==0.15.0", "pipx==0.15.0", True),
        ("pipx>=0.15.0", "pipx>=0.15.0", True),
        ("pipx<=0.15.0", "pipx<=0.15.0", True),
        ('pipx;python_version>="3.6"', "pipx", True),
        ('pipx==0.15.0;python_version>="3.6"', "pipx==0.15.0", True),
        ("pipx[extra1]", "pipx[extra1]", True),
        ("pipx[extra1, extra2]", "pipx[extra1,extra2]", True),
        ("src/pipx", str((_ROOT / "src" / "pipx").resolve()), True),
        (
            "git+https://github.com/cs01/nox.git@5ea70723e9e6",
            "git+https://github.com/cs01/nox.git@5ea70723e9e6",
            True,
        ),
        (
            "nox@git+https://github.com/cs01/nox.git@5ea70723e9e6",
            "nox @ git+https://github.com/cs01/nox.git@5ea70723e9e6",
            True,
        ),
        (
            "https://github.com/ambv/black/archive/18.9b0.zip",
            "https://github.com/ambv/black/archive/18.9b0.zip",
            True,
        ),
        (
            "black@https://github.com/ambv/black/archive/18.9b0.zip",
            "black @ https://github.com/ambv/black/archive/18.9b0.zip",
            True,
        ),
        (
            "black @ https://github.com/ambv/black/archive/18.9b0.zip",
            "black @ https://github.com/ambv/black/archive/18.9b0.zip",
            True,
        ),
        (
            "black[extra] @ https://github.com/ambv/black/archive/18.9b0.zip",
            "black[extra] @ https://github.com/ambv/black/archive/18.9b0.zip",
            True,
        ),
        (
            'my-project[cli] @ git+ssh://git@bitbucket.org/my-company/myproject.git ; python_version<"3.8"',
            "my-project[cli] @ git+ssh://git@bitbucket.org/my-company/myproject.git",
            True,
        ),
        ("path/doesnt/exist", "non-existent-path", False),
        (
            "https:/github.com/ambv/black/archive/18.9b0.zip",
            "URL-syntax-error-slash",
            False,
        ),
    ],
)
def test_parse_specifier_for_metadata(package_spec_in, package_or_url_correct, valid_spec, monkeypatch, root):
    monkeypatch.chdir(root)
    if valid_spec:
        package_or_url = parse_specifier_for_metadata(package_spec_in)
        assert package_or_url == package_or_url_correct
    else:
        # print package_spec_in for info in case no error is raised
        print(f"package_spec_in = {package_spec_in}")
        with pytest.raises(PipxError, match=r"^Unable to parse package spec"):
            package_or_url = parse_specifier_for_metadata(package_spec_in)


@pytest.mark.parametrize(
    "package_spec_in,package_or_url_correct,valid_spec",
    [
        ("pipx", "pipx", True),
        ("PiPx_stylized.name", "pipx-stylized-name", True),
        ("pipx==0.15.0", "pipx", True),
        ("pipx>=0.15.0", "pipx", True),
        ("pipx<=0.15.0", "pipx", True),
        ('pipx;python_version>="3.6"', "pipx", True),
        ('pipx==0.15.0;python_version>="3.6"', "pipx", True),
        ("pipx[extra1]", "pipx[extra1]", True),
        ("pipx[extra1, extra2]", "pipx[extra1,extra2]", True),
        ("src/pipx", str((_ROOT / "src" / "pipx").resolve()), True),
        (
            "git+https://github.com/cs01/nox.git@5ea70723e9e6",
            "git+https://github.com/cs01/nox.git@5ea70723e9e6",
            True,
        ),
        (
            "nox@git+https://github.com/cs01/nox.git@5ea70723e9e6",
            "nox @ git+https://github.com/cs01/nox.git@5ea70723e9e6",
            True,
        ),
        (
            "https://github.com/ambv/black/archive/18.9b0.zip",
            "https://github.com/ambv/black/archive/18.9b0.zip",
            True,
        ),
        (
            "black@https://github.com/ambv/black/archive/18.9b0.zip",
            "black @ https://github.com/ambv/black/archive/18.9b0.zip",
            True,
        ),
        (
            "black @ https://github.com/ambv/black/archive/18.9b0.zip",
            "black @ https://github.com/ambv/black/archive/18.9b0.zip",
            True,
        ),
        (
            "black[extra] @ https://github.com/ambv/black/archive/18.9b0.zip",
            "black[extra] @ https://github.com/ambv/black/archive/18.9b0.zip",
            True,
        ),
        (
            'my-project[cli] @ git+ssh://git@bitbucket.org/my-company/myproject.git ; python_version<"3.8"',
            "my-project[cli] @ git+ssh://git@bitbucket.org/my-company/myproject.git",
            True,
        ),
        ("path/doesnt/exist", "non-existent-path", False),
        (
            "https:/github.com/ambv/black/archive/18.9b0.zip",
            "URL-syntax-error-slash",
            False,
        ),
    ],
)
def test_parse_specifier_for_upgrade(package_spec_in, package_or_url_correct, valid_spec, monkeypatch, root):
    monkeypatch.chdir(root)
    if valid_spec:
        package_or_url = parse_specifier_for_upgrade(package_spec_in)
        assert package_or_url == package_or_url_correct
    else:
        # print package_spec_in for info in case no error is raised
        print(f"package_spec_in = {package_spec_in}")
        with pytest.raises(PipxError, match=r"^Unable to parse package spec"):
            package_or_url = parse_specifier_for_upgrade(package_spec_in)


@pytest.mark.parametrize(
    "package_spec_in,pip_args_in,package_spec_expected,pip_args_expected,warning_str",
    [
        ('pipx==0.15.0;python_version>="3.6"', [], "pipx==0.15.0", [], None),
        ("pipx==0.15.0", ["--editable"], "pipx==0.15.0", [], "Ignoring --editable"),
        (
            'pipx==0.15.0;python_version>="3.6"',
            [],
            "pipx==0.15.0",
            [],
            'Ignoring environment markers (python_version >= "3.6") in package',
        ),
        (
            "pipx==0.15.0",
            ["--no-cache-dir", "--editable"],
            "pipx==0.15.0",
            ["--no-cache-dir"],
            "Ignoring --editable",
        ),
        (
            "git+https://github.com/cs01/nox.git@5ea70723e9e6",
            ["--editable"],
            "git+https://github.com/cs01/nox.git@5ea70723e9e6",
            [],
            "Ignoring --editable",
        ),
        (
            "https://github.com/ambv/black/archive/18.9b0.zip",
            ["--editable"],
            "https://github.com/ambv/black/archive/18.9b0.zip",
            [],
            "Ignoring --editable",
        ),
        (
            "src/pipx",
            ["--editable"],
            str(Path("src/pipx").resolve()),
            ["--editable"],
            None,
        ),
        (
            TEST_DATA_PATH + "/local_extras",
            [],
            str(Path(TEST_DATA_PATH + "/local_extras").resolve),
            [],
            None,
        ),
        (
            TEST_DATA_PATH + "/local_extras[cow]",
            [],
            str(Path(TEST_DATA_PATH + "/local_extras").resolve) + "[cow]",
            [],
            None,
        ),
        (
            TEST_DATA_PATH + "/local_extras",
            ["--editable"],
            str(Path(TEST_DATA_PATH + "/local_extras").resolve),
            ["--editable"],
            None,
        ),
        (
            TEST_DATA_PATH + "/local_extras[cow]",
            ["--editable"],
            str(Path(TEST_DATA_PATH + "/local_extras").resolve) + "[cow]",
            ["--editable"],
            None,
        ),
    ],
)
def test_parse_specifier_for_install(
    caplog,
    package_spec_in,
    pip_args_in,
    package_spec_expected,
    pip_args_expected,
    warning_str,
    monkeypatch,
    root,
):
    monkeypatch.chdir(root)
    parse_specifier_for_install(package_spec_in, pip_args_in)
    if warning_str is not None:
        assert warning_str in caplog.text


@pytest.mark.parametrize(
    "pip_args_in,pip_args_expected",
    [
        (
            ["-c", "https://example.com/constraints.txt"],
            ["-c", "https://example.com/constraints.txt"],
        ),
        (
            ["--constraint", "https://example.com/constraints.txt"],
            ["--constraint", "https://example.com/constraints.txt"],
        ),
        (
            ["--constraint=https://example.com/constraints.txt"],
            ["--constraint=https://example.com/constraints.txt"],
        ),
        (
            ["-c", "constraints.txt"],
            ["-c", str(Path("constraints.txt").resolve())],
        ),
        (
            ["--constraint=constraints.txt"],
            [f"--constraint={Path('constraints.txt').resolve()}"],
        ),
    ],
)
def test_parse_specifier_for_install_constraint_args(
    pip_args_in: list[str],
    pip_args_expected: list[str],
) -> None:
    _, pip_args_out = parse_specifier_for_install("pipx", pip_args_in)
    assert pip_args_out == pip_args_expected


# ---------------------------------------------------------------------------
# resolve_pip_args_constraints – direct tests
# ---------------------------------------------------------------------------


class TestResolvePipArgsConstraints:
    """Unit tests for the standalone constraint-path resolver."""

    def test_empty_args(self) -> None:
        assert resolve_pip_args_constraints([]) == []

    def test_non_constraint_args_untouched(self) -> None:
        args = ["--no-cache-dir", "--index-url", "https://pypi.org/simple"]
        assert resolve_pip_args_constraints(args) == args

    def test_single_constraint_short_flag(self) -> None:
        out = resolve_pip_args_constraints(["-c", "constraints.txt"])
        assert out == ["-c", str(Path("constraints.txt").resolve())]

    def test_single_constraint_long_flag(self) -> None:
        out = resolve_pip_args_constraints(["--constraint", "constraints.txt"])
        assert out == ["--constraint", str(Path("constraints.txt").resolve())]

    def test_single_constraint_equals_form(self) -> None:
        out = resolve_pip_args_constraints(["--constraint=constraints.txt"])
        assert out == [f"--constraint={Path('constraints.txt').resolve()}"]

    def test_multiple_constraints_all_short(self) -> None:
        """Regression: the old code had a ``break`` so only the first ``-c``
        was resolved.  Multiple constraints must *all* be absolute."""
        out = resolve_pip_args_constraints(
            ["-c", "constraints1.txt", "-c", "constraints2.txt", "-c", "constraints3.txt"]
        )
        assert out == [
            "-c",
            str(Path("constraints1.txt").resolve()),
            "-c",
            str(Path("constraints2.txt").resolve()),
            "-c",
            str(Path("constraints3.txt").resolve()),
        ]

    def test_multiple_constraints_all_long(self) -> None:
        out = resolve_pip_args_constraints(
            ["--constraint", "a.txt", "--constraint", "b.txt"]
        )
        assert out == [
            "--constraint",
            str(Path("a.txt").resolve()),
            "--constraint",
            str(Path("b.txt").resolve()),
        ]

    def test_multiple_constraints_equals_form(self) -> None:
        out = resolve_pip_args_constraints(
            ["--constraint=a.txt", "--constraint=b.txt"]
        )
        assert out == [
            f"--constraint={Path('a.txt').resolve()}",
            f"--constraint={Path('b.txt').resolve()}",
        ]

    def test_mixed_constraint_forms(self) -> None:
        """A realistic mix of -c, --constraint, and --constraint=."""
        out = resolve_pip_args_constraints(
            [
                "-c",
                "base.txt",
                "--constraint",
                "platform.txt",
                "--constraint=overrides.txt",
            ]
        )
        assert out == [
            "-c",
            str(Path("base.txt").resolve()),
            "--constraint",
            str(Path("platform.txt").resolve()),
            f"--constraint={Path('overrides.txt').resolve()}",
        ]

    def test_url_constraints_not_modified(self) -> None:
        """URLs must never be touched, even when mixed with local paths."""
        args = [
            "-c",
            "https://example.com/constraints.txt",
            "--constraint",
            "http://other.dev/c.txt",
            "--constraint=ftp://mirror/constraints.txt",
        ]
        assert resolve_pip_args_constraints(args) == args

    def test_mixed_url_and_local_constraints(self) -> None:
        out = resolve_pip_args_constraints(
            [
                "-c",
                "local.txt",
                "-c",
                "https://example.com/constraints.txt",
                "--constraint=other_local.txt",
            ]
        )
        assert out == [
            "-c",
            str(Path("local.txt").resolve()),
            "-c",
            "https://example.com/constraints.txt",
            f"--constraint={Path('other_local.txt').resolve()}",
        ]

    def test_already_absolute_paths_preserved(self) -> None:
        abs_path = str(Path("/etc/constraints.txt"))
        out = resolve_pip_args_constraints(["-c", abs_path])
        assert out == ["-c", abs_path]

    def test_tilde_expansion(self) -> None:
        out = resolve_pip_args_constraints(["-c", "~/constraints.txt"])
        expected = str(Path("~/constraints.txt").expanduser().resolve())
        assert out == ["-c", expected]

    def test_constraints_among_other_pip_args(self) -> None:
        """Non-constraint args around constraints should be untouched."""
        out = resolve_pip_args_constraints(
            [
                "--no-cache-dir",
                "-c",
                "constraints.txt",
                "--index-url",
                "https://pypi.org/simple",
                "--constraint",
                "extra.txt",
            ]
        )
        assert out == [
            "--no-cache-dir",
            "-c",
            str(Path("constraints.txt").resolve()),
            "--index-url",
            "https://pypi.org/simple",
            "--constraint",
            str(Path("extra.txt").resolve()),
        ]

    def test_does_not_mutate_input(self) -> None:
        """The helper must return a new list, not modify the original."""
        original = ["-c", "constraints.txt"]
        original_copy = list(original)
        _ = resolve_pip_args_constraints(original)
        assert original == original_copy


# ---------------------------------------------------------------------------
# parse_specifier_for_install – multiple constraint integration
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pip_args_in,pip_args_expected",
    [
        (
            ["-c", "a.txt", "-c", "b.txt"],
            ["-c", str(Path("a.txt").resolve()), "-c", str(Path("b.txt").resolve())],
        ),
        (
            ["--constraint", "a.txt", "--constraint", "b.txt"],
            [
                "--constraint",
                str(Path("a.txt").resolve()),
                "--constraint",
                str(Path("b.txt").resolve()),
            ],
        ),
        (
            ["--constraint=a.txt", "--constraint=b.txt"],
            [
                f"--constraint={Path('a.txt').resolve()}",
                f"--constraint={Path('b.txt').resolve()}",
            ],
        ),
        (
            [
                "-c",
                "a.txt",
                "--constraint",
                "b.txt",
                "--constraint=c.txt",
            ],
            [
                "-c",
                str(Path("a.txt").resolve()),
                "--constraint",
                str(Path("b.txt").resolve()),
                f"--constraint={Path('c.txt').resolve()}",
            ],
        ),
    ],
)
def test_parse_specifier_for_install_multiple_constraints(
    pip_args_in: list[str],
    pip_args_expected: list[str],
) -> None:
    """Multiple constraint files must all be resolved (regression for the
    old ``break`` that only handled the first)."""
    _, pip_args_out = parse_specifier_for_install("pipx", pip_args_in)
    assert pip_args_out == pip_args_expected


def test_parse_specifier_for_install_constraint_url_preserved_with_multiple() -> None:
    """When mixing local and URL constraints, URLs must be untouched."""
    pip_args = [
        "-c",
        "local.txt",
        "-c",
        "https://example.com/constraints.txt",
    ]
    _, out = parse_specifier_for_install("pipx", pip_args)
    assert out == [
        "-c",
        str(Path("local.txt").resolve()),
        "-c",
        "https://example.com/constraints.txt",
    ]
