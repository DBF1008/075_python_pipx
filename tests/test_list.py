import json
import os
import re
import shutil
import sys
import time

import pytest

from helpers import (
    PIPX_METADATA_LEGACY_VERSIONS,
    app_name,
    assert_package_metadata,
    create_package_info_ref,
    mock_legacy_venv,
    remove_venv_interpreter,
    run_pipx_cli,
    skip_if_windows,
)
from package_info import PKG
from pipx import constants, paths, shared_libs, venv
from pipx.pipx_metadata_file import PackageInfo, _json_decoder_object_hook
from pipx.util import PipxError


def test_cli(pipx_temp_env, monkeypatch, capsys):
    assert not run_pipx_cli(["list"])
    captured = capsys.readouterr()
    assert "nothing has been installed with pipx" in captured.err


@skip_if_windows
def test_cli_global(pipx_temp_env, monkeypatch, capsys):
    assert not run_pipx_cli(["install", "pycowsay"])
    captured = capsys.readouterr()
    assert "installed package" in captured.out

    assert not run_pipx_cli(["list", "--global"])
    captured = capsys.readouterr()
    assert "nothing has been installed with pipx" in captured.err


def test_missing_interpreter(pipx_temp_env, monkeypatch, capsys):
    assert not run_pipx_cli(["install", "pycowsay"])

    assert not run_pipx_cli(["list"])
    captured = capsys.readouterr()
    assert "package pycowsay has invalid interpreter" not in captured.err

    remove_venv_interpreter("pycowsay")

    assert run_pipx_cli(["list"])
    captured = capsys.readouterr()
    assert "package pycowsay has invalid interpreter" in captured.err


def test_list_suffix(pipx_temp_env, monkeypatch, capsys):
    suffix = "_x"
    assert not run_pipx_cli(["install", "pycowsay", f"--suffix={suffix}"])

    assert not run_pipx_cli(["list"])
    captured = capsys.readouterr()
    assert f"package pycowsay 0.0.0.2 (pycowsay{suffix})," in captured.out


@pytest.mark.parametrize("metadata_version", PIPX_METADATA_LEGACY_VERSIONS)
def test_list_legacy_venv(pipx_temp_env, monkeypatch, capsys, metadata_version):
    assert not run_pipx_cli(["install", "pycowsay"])
    mock_legacy_venv("pycowsay", metadata_version=metadata_version)

    if metadata_version is None:
        assert run_pipx_cli(["list"])
        captured = capsys.readouterr()
        assert "package pycowsay has missing internal pipx metadata" in captured.err
    else:
        assert not run_pipx_cli(["list"])
        captured = capsys.readouterr()
        assert "package pycowsay 0.0.0.2," in captured.out


@pytest.mark.parametrize("metadata_version", ["0.1"])
def test_list_suffix_legacy_venv(pipx_temp_env, monkeypatch, capsys, metadata_version):
    suffix = "_x"
    assert not run_pipx_cli(["install", "pycowsay", f"--suffix={suffix}"])
    mock_legacy_venv(f"pycowsay{suffix}", metadata_version=metadata_version)

    assert not run_pipx_cli(["list"])
    captured = capsys.readouterr()
    assert f"package pycowsay 0.0.0.2 (pycowsay{suffix})," in captured.out


def test_list_json(pipx_temp_env, capsys):
    pipx_venvs_dir = paths.ctx.home / "venvs"
    venv_bin_dir = "Scripts" if constants.WINDOWS else "bin"

    assert not run_pipx_cli(["install", PKG["pycowsay"]["spec"]])
    assert not run_pipx_cli(["install", PKG["pylint"]["spec"]])
    assert not run_pipx_cli(["inject", "pylint", PKG["black"]["spec"]])
    captured = capsys.readouterr()

    assert not run_pipx_cli(["list", "--json"])
    captured = capsys.readouterr()

    assert not re.search(r"\S", captured.err)
    json_parsed = json.loads(captured.out, object_hook=_json_decoder_object_hook)

    # raises error if not valid json
    assert sorted(json_parsed["venvs"].keys()) == ["pycowsay", "pylint"]

    # pycowsay venv
    pycowsay_package_ref = create_package_info_ref("pycowsay", "pycowsay", pipx_venvs_dir)
    assert_package_metadata(
        PackageInfo(**json_parsed["venvs"]["pycowsay"]["metadata"]["main_package"]),
        pycowsay_package_ref,
    )
    assert json_parsed["venvs"]["pycowsay"]["metadata"]["injected_packages"] == {}

    # pylint venv
    pylint_package_ref = create_package_info_ref(
        "pylint",
        "pylint",
        pipx_venvs_dir,
        app_paths_of_dependencies={
            "dill": [
                pipx_venvs_dir / "pylint" / venv_bin_dir / "get_gprof",
                pipx_venvs_dir / "pylint" / venv_bin_dir / "get_objgraph",
                pipx_venvs_dir / "pylint" / venv_bin_dir / "undill",
            ],
            "isort": [
                pipx_venvs_dir / "pylint" / venv_bin_dir / app_name("isort"),
                pipx_venvs_dir / "pylint" / venv_bin_dir / app_name("isort-identify-imports"),
            ],
        },
    )
    assert_package_metadata(
        PackageInfo(**json_parsed["venvs"]["pylint"]["metadata"]["main_package"]),
        pylint_package_ref,
    )
    assert sorted(json_parsed["venvs"]["pylint"]["metadata"]["injected_packages"].keys()) == ["black"]
    black_package_ref = create_package_info_ref("pylint", "black", pipx_venvs_dir, include_apps=False)
    assert_package_metadata(
        PackageInfo(**json_parsed["venvs"]["pylint"]["metadata"]["injected_packages"]["black"]),
        black_package_ref,
    )


def test_list_short(pipx_temp_env, monkeypatch, capsys):
    assert not run_pipx_cli(["install", PKG["pycowsay"]["spec"]])
    assert not run_pipx_cli(["install", PKG["pylint"]["spec"]])
    captured = capsys.readouterr()

    assert not run_pipx_cli(["list", "--short"])
    captured = capsys.readouterr()

    assert "pycowsay 0.0.0.2" in captured.out
    assert "pylint 3.0.4" in captured.out


def test_list_standalone_interpreter(pipx_temp_env, monkeypatch, mocked_github_api, capsys):
    def which(name):
        return None

    monkeypatch.setattr(shutil, "which", which)

    major = sys.version_info.major
    minor = sys.version_info.minor
    target_python = f"{major}.{minor}"

    assert not run_pipx_cli(
        [
            "install",
            "--fetch-python=missing",
            "--python",
            target_python,
            PKG["pycowsay"]["spec"],
        ]
    )
    captured = capsys.readouterr()

    assert not run_pipx_cli(["list"])
    captured = capsys.readouterr()

    assert "standalone" in captured.out


def test_list_does_not_trigger_maintenance(pipx_temp_env, caplog):
    assert not run_pipx_cli(["install", PKG["pycowsay"]["spec"]])
    assert not run_pipx_cli(["install", PKG["pylint"]["spec"]])

    now = time.time()
    shared_libs.shared_libs.create(verbose=True, pip_args=[])
    shared_libs.shared_libs.has_been_updated_this_run = False

    access_time = now  # this can be anything
    os.utime(
        shared_libs.shared_libs.pip_path,
        (access_time, -shared_libs.SHARED_LIBS_MAX_AGE_SEC - 5 * 60 + now),
    )
    assert shared_libs.shared_libs.needs_upgrade
    run_pipx_cli(["list"])
    assert not shared_libs.shared_libs.has_been_updated_this_run
    assert shared_libs.shared_libs.needs_upgrade

    # same test with --skip-maintenance, which is a no-op
    # we expect the same result, along with a warning
    os.utime(
        shared_libs.shared_libs.pip_path,
        (access_time, -shared_libs.SHARED_LIBS_MAX_AGE_SEC - 5 * 60 + now),
    )
    shared_libs.shared_libs.has_been_updated_this_run = False
    assert shared_libs.shared_libs.needs_upgrade
    run_pipx_cli(["list", "--skip-maintenance"])
    assert not shared_libs.shared_libs.has_been_updated_this_run
    assert shared_libs.shared_libs.needs_upgrade


def test_list_pinned_packages(pipx_temp_env, monkeypatch, capsys):
    assert not run_pipx_cli(["install", PKG["pycowsay"]["spec"]])
    assert not run_pipx_cli(["install", PKG["black"]["spec"]])
    captured = capsys.readouterr()

    assert not run_pipx_cli(["pin", "black"])
    assert not run_pipx_cli(["list", "--pinned"])

    captured = capsys.readouterr()
    assert "black 22.8.0" in captured.out
    assert "pycowsay 0.0.0.2" not in captured.out


def test_list_pinned_packages_include_injected(pipx_temp_env, monkeypatch, capsys):
    assert not run_pipx_cli(["install", PKG["pylint"]["spec"], PKG["nox"]["spec"]])
    assert not run_pipx_cli(["inject", "pylint", PKG["black"]["spec"]])

    assert not run_pipx_cli(["pin", "pylint"])
    assert not run_pipx_cli(["pin", "nox"])

    captured = capsys.readouterr()

    assert not run_pipx_cli(["list", "--pinned", "--include-injected"])

    captured = capsys.readouterr()

    assert "nox 2023.4.22" in captured.out
    assert "pylint 3.0.4" in captured.out
    assert "black 22.8.0 (injected in venv pylint)" in captured.out


def test_list_installed_packages_error(monkeypatch, tmp_path, fake_process):
    fake_venv = venv.Venv(tmp_path / "fake_venv")
    pip_list_args = [str(fake_venv.python_path), "-m", "pip", "list", "--format=json"]

    fake_process.register(pip_list_args, returncode=1, stderr="unit test stderr")

    with pytest.raises(PipxError) as excinfo:
        fake_venv.list_installed_packages()

    # Collapse the wrapping pipx_wrap applies on render so we can assert on substrings.
    rendered = " ".join(str(excinfo.value).split())
    assert "Failed to execute" in rendered
    assert "Process exited with return code 1" in rendered
    assert "unit test stderr" in rendered


# ---------------------------------------------------------------------------
# Environment observability: backend & python_source
# ---------------------------------------------------------------------------


def test_list_shows_backend_and_python_source(pipx_temp_env, capsys):
    """Human-readable list output includes backend and python source."""
    assert not run_pipx_cli(["install", PKG["pycowsay"]["spec"]])
    capsys.readouterr()  # discard install output

    assert not run_pipx_cli(["list"])
    captured = capsys.readouterr()

    assert "backend: pip" in captured.out
    # A normal install records the real interpreter → "(system)"
    assert "(system)" in captured.out


def test_list_json_includes_python_source_and_backend(pipx_temp_env, capsys):
    """JSON list output carries python_source and backend fields."""
    assert not run_pipx_cli(["install", PKG["pycowsay"]["spec"]])
    capsys.readouterr()

    assert not run_pipx_cli(["list", "--json"])
    captured = capsys.readouterr()

    data = json.loads(captured.out, object_hook=_json_decoder_object_hook)
    venv_entry = data["venvs"]["pycowsay"]

    # python_source sits at the venv-entry level (not inside metadata)
    assert venv_entry["python_source"] == "system"
    # backend lives inside the metadata dict
    assert venv_entry["metadata"]["backend"] == "pip"


@pytest.mark.parametrize("metadata_version", ["0.1", "0.2", "0.3"])
def test_list_legacy_venv_backend_degradation(pipx_temp_env, capsys, metadata_version):
    """Legacy metadata missing backend/source_interpreter degrades gracefully."""
    assert not run_pipx_cli(["install", PKG["pycowsay"]["spec"]])
    mock_legacy_venv("pycowsay", metadata_version=metadata_version)
    capsys.readouterr()

    assert not run_pipx_cli(["list"])
    captured = capsys.readouterr()

    # backend defaults to pip for pre-0.6 metadata
    assert "backend: pip" in captured.out
    # source_interpreter is None for pre-0.4 → no source tag in parentheses
    assert "(system)" not in captured.out
    assert "(standalone)" not in captured.out


@pytest.mark.parametrize("metadata_version", ["0.1", "0.2", "0.3"])
def test_list_json_legacy_venv_degradation(pipx_temp_env, capsys, metadata_version):
    """JSON output for legacy venvs shows unknown python_source and pip backend."""
    assert not run_pipx_cli(["install", PKG["pycowsay"]["spec"]])
    mock_legacy_venv("pycowsay", metadata_version=metadata_version)
    capsys.readouterr()

    assert not run_pipx_cli(["list", "--json"])
    captured = capsys.readouterr()

    data = json.loads(captured.out, object_hook=_json_decoder_object_hook)
    venv_entry = data["venvs"]["pycowsay"]

    assert venv_entry["python_source"] == "unknown"
    assert venv_entry["metadata"]["backend"] == "pip"


def test_list_standalone_shows_backend(pipx_temp_env, mocked_github_api, capsys):
    """Standalone interpreter install shows both (standalone) and backend."""
    def _which(name):
        return None

    import shutil as _shutil
    original_which = _shutil.which

    major = sys.version_info.major
    minor = sys.version_info.minor

    from unittest.mock import patch
    with patch.object(_shutil, "which", _which):
        assert not run_pipx_cli(
            ["install", "--fetch-python=missing", "--python", f"{major}.{minor}", PKG["pycowsay"]["spec"]]
        )
    capsys.readouterr()

    assert not run_pipx_cli(["list"])
    captured = capsys.readouterr()

    assert "(standalone)" in captured.out
    assert "backend: pip" in captured.out


def test_list_json_standalone_python_source(pipx_temp_env, mocked_github_api, capsys):
    """JSON output for standalone interpreter shows python_source=standalone."""
    def _which(name):
        return None

    major = sys.version_info.major
    minor = sys.version_info.minor

    import shutil as _shutil
    from unittest.mock import patch
    with patch.object(_shutil, "which", _which):
        assert not run_pipx_cli(
            ["install", "--fetch-python=missing", "--python", f"{major}.{minor}", PKG["pycowsay"]["spec"]]
        )
    capsys.readouterr()

    assert not run_pipx_cli(["list", "--json"])
    captured = capsys.readouterr()

    data = json.loads(captured.out, object_hook=_json_decoder_object_hook)
    assert data["venvs"]["pycowsay"]["python_source"] == "standalone"


def test_get_python_source_unit(tmp_path):
    """Unit test for get_python_source classification logic."""
    from pathlib import Path
    from pipx.commands.common import get_python_source

    # None → None (legacy metadata, cannot determine)
    assert get_python_source(None) is None

    # Path under standalone cache → "standalone"
    standalone_cache = paths.ctx.standalone_python_cachedir.resolve()
    assert get_python_source(standalone_cache / "cpython-3.11" / "bin" / "python3") == "standalone"

    # Any other path → "system"
    assert get_python_source(Path("/usr/bin/python3")) == "system"
    assert get_python_source(Path("/home/user/.pyenv/versions/3.11/bin/python3")) == "system"
