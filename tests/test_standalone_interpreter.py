import datetime
import json
import shutil
import sys

import pytest

from helpers import (
    run_pipx_cli,
)
from package_info import PKG
from pipx import standalone_python
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


# --- Regression tests for offline / stale-cache fallback (get_or_update_index) ---


def _write_stale_index(cache_dir, root, days_ago=31):
    """Helper: write a structurally valid but stale index using real testdata."""
    with open(root / "testdata" / "standalone_python_index_20250818.json") as f:
        real_index = json.load(f)
    stale_ts = (datetime.datetime.now() - datetime.timedelta(days=days_ago)).timestamp()
    stale_index = {"fetched": stale_ts, "releases": real_index["releases"]}
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "index.json").write_text(json.dumps(stale_index))
    return stale_index


def _fail_network(*args, **kwargs):
    """Simulate an offline environment by raising the same error urlopen would."""
    raise PipxError("Unable to fetch python-build-standalone release data (offline simulation).")


def test_stale_cache_offline_fallback(pipx_temp_env, monkeypatch, root):
    """Stale but valid cache + network failure → returns stale cache, no crash."""
    cache_dir = standalone_python.paths.ctx.standalone_python_cachedir
    stale = _write_stale_index(cache_dir, root, days_ago=45)

    monkeypatch.setattr(standalone_python, "get_latest_python_releases", _fail_network)

    result = standalone_python.get_or_update_index()
    assert result == stale
    assert result["releases"] == stale["releases"]


def test_corrupted_cache_offline_raises(pipx_temp_env, monkeypatch):
    """Corrupted cache + network failure → raises PipxError (no silent fallback)."""
    cache_dir = standalone_python.paths.ctx.standalone_python_cachedir
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Write a structurally invalid index (releases entries are not [url, digest] pairs)
    (cache_dir / "index.json").write_text(json.dumps({"fetched": 12345.0, "releases": ["not_a_pair"]}))

    monkeypatch.setattr(standalone_python, "get_latest_python_releases", _fail_network)

    with pytest.raises(PipxError, match="Unable to fetch"):
        standalone_python.get_or_update_index()


def test_unreadable_cache_offline_raises(pipx_temp_env, monkeypatch):
    """Unparseable JSON cache + network failure → raises PipxError."""
    cache_dir = standalone_python.paths.ctx.standalone_python_cachedir
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "index.json").write_text("this is not json {{{")

    monkeypatch.setattr(standalone_python, "get_latest_python_releases", _fail_network)

    with pytest.raises(PipxError, match="Unable to fetch"):
        standalone_python.get_or_update_index()


def test_stale_cache_online_refreshes(pipx_temp_env, monkeypatch, root):
    """Stale cache + successful network → refreshes cache and returns new index."""
    cache_dir = standalone_python.paths.ctx.standalone_python_cachedir
    _write_stale_index(cache_dir, root, days_ago=45)

    new_link = (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        "20250901/cpython-3.13.7%2B20250901-x86_64-unknown-linux-gnu-install_only.tar.gz"
    )
    new_digest = "sha256:" + "a" * 64
    monkeypatch.setattr(standalone_python, "get_latest_python_releases", lambda: [(new_link, new_digest)])

    result = standalone_python.get_or_update_index()
    assert result["releases"] == [(new_link, new_digest)]
    # Verify the on-disk cache was updated
    persisted = json.loads((cache_dir / "index.json").read_text())
    assert persisted["releases"] == [[new_link, new_digest]]


def test_no_cache_offline_raises(pipx_temp_env, monkeypatch):
    """No cache at all + network failure → raises PipxError."""
    monkeypatch.setattr(standalone_python, "get_latest_python_releases", _fail_network)

    with pytest.raises(PipxError, match="Unable to fetch"):
        standalone_python.get_or_update_index()


def test_fresh_cache_returned_directly(pipx_temp_env, monkeypatch, root):
    """Fresh cache (< 30 days) → returned without network call."""
    cache_dir = standalone_python.paths.ctx.standalone_python_cachedir
    with open(root / "testdata" / "standalone_python_index_20250818.json") as f:
        real_index = json.load(f)
    fresh_ts = datetime.datetime.now().timestamp()
    fresh_index = {"fetched": fresh_ts, "releases": real_index["releases"]}
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "index.json").write_text(json.dumps(fresh_index))

    # Network should NOT be called — if it is, fail loudly
    monkeypatch.setattr(
        standalone_python,
        "get_latest_python_releases",
        lambda: (_ for _ in ()).throw(AssertionError("Network should not be called for fresh cache")),
    )

    result = standalone_python.get_or_update_index()
    assert result["releases"] == fresh_index["releases"]


def test_resolve_python_version_offline_with_stale_cache(pipx_temp_env, monkeypatch, root):
    """resolve_python_version succeeds via stale cache when offline."""
    cache_dir = standalone_python.paths.ctx.standalone_python_cachedir
    _write_stale_index(cache_dir, root, days_ago=60)

    monkeypatch.setattr(standalone_python, "get_latest_python_releases", _fail_network)

    # Pick a version known to exist in the testdata index
    full_version, (link, digest) = standalone_python.resolve_python_version("3.10")
    assert full_version.startswith("3.10.")
    assert link.endswith(".tar.gz") or link.endswith(".tar.zst")
    assert digest.startswith("sha256:")


def test_list_pythons_offline_with_stale_cache(pipx_temp_env, monkeypatch, root):
    """list_pythons returns available versions from stale cache when offline."""
    import re

    semver_re = re.compile(r"^\d+\.\d+\.\d+$")

    cache_dir = standalone_python.paths.ctx.standalone_python_cachedir
    _write_stale_index(cache_dir, root, days_ago=60)

    monkeypatch.setattr(standalone_python, "get_latest_python_releases", _fail_network)

    available = standalone_python.list_pythons()
    assert isinstance(available, dict)
    assert len(available) > 0
    for version, (link, digest) in available.items():
        assert semver_re.match(version), f"Unexpected version format: {version}"
        assert digest.startswith("sha256:")


def test_list_interpreters_command_offline_with_stale_cache(pipx_temp_env, monkeypatch, root, capsys):
    """The 'interpreter list' CLI command works when backed by a stale cache."""
    cache_dir = standalone_python.paths.ctx.standalone_python_cachedir
    _write_stale_index(cache_dir, root, days_ago=60)

    monkeypatch.setattr(standalone_python, "get_latest_python_releases", _fail_network)

    # The list command itself should not crash (it reads installed dirs, not the index)
    assert not run_pipx_cli(["interpreter", "list"])
    captured = capsys.readouterr()
    assert "Standalone interpreters" in captured.out
