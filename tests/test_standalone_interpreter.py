import datetime
import json
import logging
import shutil
import sys
import urllib.error

import pytest

from helpers import (
    run_pipx_cli,
)
from package_info import PKG
from pipx import paths, standalone_python
from pipx.util import PipxError

MAJOR_PYTHON_VERSION = sys.version_info.major
MINOR_PYTHON_VERSION = sys.version_info.minor
TARGET_PYTHON_VERSION = f"{MAJOR_PYTHON_VERSION}.{MINOR_PYTHON_VERSION}"

original_which = shutil.which


def mock_which(name):
    if name == TARGET_PYTHON_VERSION:
        return None
    return original_which(name)


def test_legacy_standalone_python_index_is_refreshed(pipx_temp_env, monkeypatch):
    legacy_link = (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        "20250818/cpython-3.13.7%2B20250818-x86_64-unknown-linux-gnu-install_only.tar.gz"
    )
    digest = "sha256:" + "0" * 64
    current_releases = [(legacy_link, digest)]
    cache_dir = standalone_python.paths.ctx.standalone_python_cachedir
    index_file = cache_dir / "index.json"
    cache_dir.mkdir(parents=True)
    index_file.write_text(
        json.dumps(
            {
                "fetched": datetime.datetime.now().timestamp(),
                "releases": [legacy_link],
            }
        )
    )

    monkeypatch.setattr(standalone_python, "get_latest_python_releases", lambda: current_releases)

    assert standalone_python.get_or_update_index()["releases"] == current_releases
    assert json.loads(index_file.read_text())["releases"] == [[legacy_link, digest]]


def test_list_no_standalone_interpreters(pipx_temp_env, monkeypatch, capsys):
    assert not run_pipx_cli(["interpreter", "list"])

    captured = capsys.readouterr()
    assert "Standalone interpreters" in captured.out
    assert len(captured.out.splitlines()) == 1


def test_list_used_standalone_interpreters(pipx_temp_env, monkeypatch, mocked_github_api, capsys):
    monkeypatch.setattr(shutil, "which", mock_which)

    assert not run_pipx_cli(
        [
            "install",
            "--fetch-python=missing",
            "--python",
            TARGET_PYTHON_VERSION,
            PKG["pycowsay"]["spec"],
        ]
    )

    capsys.readouterr()
    assert not run_pipx_cli(["interpreter", "list"])

    captured = capsys.readouterr()
    assert TARGET_PYTHON_VERSION in captured.out
    assert "pycowsay" in captured.out


def test_list_unused_standalone_interpreters(pipx_temp_env, monkeypatch, mocked_github_api, capsys):
    monkeypatch.setattr(shutil, "which", mock_which)

    assert not run_pipx_cli(
        [
            "install",
            "--fetch-python=missing",
            "--python",
            TARGET_PYTHON_VERSION,
            PKG["pycowsay"]["spec"],
        ]
    )

    assert not run_pipx_cli(["uninstall", "pycowsay"])
    capsys.readouterr()
    assert not run_pipx_cli(["interpreter", "list"])

    captured = capsys.readouterr()
    assert TARGET_PYTHON_VERSION in captured.out
    assert "pycowsay" not in captured.out
    assert "Unused" in captured.out


def test_prune_unused_standalone_interpreters(pipx_temp_env, monkeypatch, mocked_github_api, capsys):
    monkeypatch.setattr(shutil, "which", mock_which)

    assert not run_pipx_cli(
        [
            "install",
            "--fetch-python=missing",
            "--python",
            TARGET_PYTHON_VERSION,
            PKG["pycowsay"]["spec"],
        ]
    )

    capsys.readouterr()
    assert not run_pipx_cli(["interpreter", "prune"])
    captured = capsys.readouterr()
    assert "Nothing to remove" in captured.out

    assert not run_pipx_cli(["uninstall", "pycowsay"])
    capsys.readouterr()

    assert not run_pipx_cli(["interpreter", "prune"])
    captured = capsys.readouterr()
    assert "Successfully removed:" in captured.out
    assert f"- Python {TARGET_PYTHON_VERSION}" in captured.out

    assert not run_pipx_cli(["interpreter", "list"])
    captured = capsys.readouterr()
    assert "Standalone interpreters" in captured.out
    assert len(captured.out.splitlines()) == 1

    assert not run_pipx_cli(["interpreter", "prune"])
    captured = capsys.readouterr()
    assert "Nothing to remove" in captured.out


def test_upgrade_standalone_interpreter(pipx_temp_env, root, monkeypatch, capsys):
    monkeypatch.setattr(shutil, "which", mock_which)

    with open(root / "testdata" / "standalone_python_index_20250818.json") as f:
        new_index = json.load(f)
    monkeypatch.setattr(standalone_python, "get_or_update_index", lambda _: new_index)

    assert not run_pipx_cli(
        [
            "install",
            "--fetch-python=missing",
            "--python",
            TARGET_PYTHON_VERSION,
            PKG["pycowsay"]["spec"],
        ]
    )

    with open(root / "testdata" / "standalone_python_index_20250828.json") as f:
        new_index = json.load(f)
    monkeypatch.setattr(standalone_python, "get_or_update_index", lambda _: new_index)

    assert not run_pipx_cli(["interpreter", "upgrade"])


def test_upgrade_standalone_interpreter_nothing_to_upgrade(pipx_temp_env, capsys, mocked_github_api):
    assert not run_pipx_cli(["interpreter", "upgrade"])
    captured = capsys.readouterr()
    assert "Nothing to upgrade" in captured.out


# ---------------------------------------------------------------------------
# Helpers for index cache tests
# ---------------------------------------------------------------------------

STALE_TIMESTAMP = (datetime.datetime.now() - datetime.timedelta(days=31)).timestamp()
FRESH_TIMESTAMP = datetime.datetime.now().timestamp()

SAMPLE_RELEASES = [
    (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        "20250818/cpython-3.13.7%2B20250818-x86_64-unknown-linux-gnu-install_only.tar.gz",
        "sha256:" + "a" * 64,
    ),
]


@pytest.fixture
def standalone_cache(tmp_path, monkeypatch):
    """Lightweight fixture that only sets up standalone_python_cachedir."""
    monkeypatch.setattr(paths, "OVERRIDE_PIPX_HOME", tmp_path / "pipxhome")
    paths.ctx.make_local()
    cache_dir = paths.ctx.standalone_python_cachedir
    cache_dir.mkdir(parents=True, exist_ok=True)
    yield cache_dir
    paths.ctx.make_local()


def _make_valid_index(timestamp, releases=None):
    return {"fetched": timestamp, "releases": releases or SAMPLE_RELEASES}


def _write_index(cache_dir, data):
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "index.json").write_text(json.dumps(data))


def _raise_url_error():
    raise urllib.error.URLError("simulated network failure")


# ---------------------------------------------------------------------------
# get_or_update_index regression tests
# ---------------------------------------------------------------------------


def test_stale_cache_offline_fallback(standalone_cache, monkeypatch, caplog):
    """Stale but valid cache + network failure → fall back with warning."""
    stale_index = _make_valid_index(STALE_TIMESTAMP)
    _write_index(standalone_cache, stale_index)

    monkeypatch.setattr(standalone_python, "get_latest_python_releases", _raise_url_error)

    with caplog.at_level(logging.WARNING):
        result = standalone_python.get_or_update_index()

    # JSON round-trips tuples to lists, so compare as lists
    assert result["releases"] == [list(r) for r in stale_index["releases"]]
    assert "stale cached index" in caplog.text


def test_corrupt_cache_offline_raises(standalone_cache, monkeypatch):
    """Corrupt cache + network failure → PipxError (no usable fallback)."""
    _write_index(standalone_cache, {"bad": "data"})

    monkeypatch.setattr(standalone_python, "get_latest_python_releases", _raise_url_error)

    with pytest.raises(PipxError, match="(?s)no usable.*cached index"):
        standalone_python.get_or_update_index()


def test_no_cache_offline_raises(standalone_cache, monkeypatch):
    """No cache at all + network failure → PipxError."""
    monkeypatch.setattr(standalone_python, "get_latest_python_releases", _raise_url_error)

    with pytest.raises(PipxError, match="(?s)no usable.*cached index"):
        standalone_python.get_or_update_index()


def test_stale_cache_online_refreshes(standalone_cache, monkeypatch):
    """Stale cache + successful network → refreshes the index."""
    old_releases = [("https://old.example.com/old.tar.gz", "sha256:" + "b" * 64)]
    _write_index(standalone_cache, _make_valid_index(STALE_TIMESTAMP, old_releases))

    new_releases = [("https://new.example.com/new.tar.gz", "sha256:" + "c" * 64)]
    monkeypatch.setattr(standalone_python, "get_latest_python_releases", lambda: new_releases)

    result = standalone_python.get_or_update_index()
    assert result["releases"] == new_releases
    # Verify the file was updated on disk
    on_disk = json.loads((standalone_cache / "index.json").read_text())
    assert on_disk["releases"] == [list(r) for r in new_releases]


def test_fresh_cache_no_fetch(standalone_cache, monkeypatch):
    """Fresh valid cache → returned without any network call."""
    fresh_index = _make_valid_index(FRESH_TIMESTAMP)
    _write_index(standalone_cache, fresh_index)

    # If get_latest_python_releases is called, the test fails
    def _should_not_be_called():
        raise AssertionError("get_latest_python_releases should not have been called")

    monkeypatch.setattr(standalone_python, "get_latest_python_releases", _should_not_be_called)

    result = standalone_python.get_or_update_index()
    assert result["releases"] == [list(r) for r in fresh_index["releases"]]


# ---------------------------------------------------------------------------
# End-to-end: list_pythons / resolve_python_version with offline stale cache
# ---------------------------------------------------------------------------


def test_list_pythons_offline_stale_cache(standalone_cache, root, monkeypatch, caplog):
    """list_pythons() succeeds with a stale cache when offline."""
    with open(root / "testdata" / "standalone_python_index_20250818.json") as f:
        full_index = json.load(f)
    full_index["fetched"] = STALE_TIMESTAMP
    _write_index(standalone_cache, full_index)

    monkeypatch.setattr(standalone_python, "get_latest_python_releases", _raise_url_error)

    with caplog.at_level(logging.WARNING):
        pythons = standalone_python.list_pythons()

    assert len(pythons) > 0
    assert "stale cached index" in caplog.text


def test_resolve_python_version_offline_stale_cache(standalone_cache, root, monkeypatch, caplog):
    """resolve_python_version() succeeds with a stale cache when offline."""
    with open(root / "testdata" / "standalone_python_index_20250818.json") as f:
        full_index = json.load(f)
    full_index["fetched"] = STALE_TIMESTAMP
    _write_index(standalone_cache, full_index)

    monkeypatch.setattr(standalone_python, "get_latest_python_releases", _raise_url_error)

    major = sys.version_info.major
    minor = sys.version_info.minor

    with caplog.at_level(logging.WARNING):
        full_version, (url, digest) = standalone_python.resolve_python_version(f"{major}.{minor}")

    assert full_version.startswith(f"{major}.{minor}.")
    assert "stale cached index" in caplog.text
