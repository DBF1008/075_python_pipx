"""Unit tests for _get_temporary_venv_path cache-key generation.

These tests verify that different combinations of requirements, pip args,
venv args, backend, and python produce distinct cache bucket names, and
that identical inputs always map to the same bucket.
"""

import pytest

from pipx.commands.run import _get_temporary_venv_path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULTS = dict(
    requirements=["pkg"],
    python="/usr/bin/python3",
    pip_args=[],
    venv_args=[],
    backend="pip",
)


def _cache_name(**overrides) -> str:
    """Return only the hash folder name (last path component)."""
    kw = {**_DEFAULTS, **overrides}
    return _get_temporary_venv_path(**kw).name


# ---------------------------------------------------------------------------
# Determinism – same inputs ⟹ same hash
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_identical_inputs_same_hash(self):
        a = _cache_name()
        b = _cache_name()
        assert a == b

    def test_identical_complex_inputs_same_hash(self):
        kw = dict(
            requirements=["requests>=2.0", "flask"],
            python="/usr/bin/python3.12",
            pip_args=["--index-url", "https://pypi.example.com/simple"],
            venv_args=["--system-site-packages"],
            backend="uv",
        )
        assert _cache_name(**kw) == _cache_name(**kw)


# ---------------------------------------------------------------------------
# Each field must affect the hash
# ---------------------------------------------------------------------------


class TestFieldIsolation:
    def test_different_requirements(self):
        assert _cache_name(requirements=["pkg-a"]) != _cache_name(requirements=["pkg-b"])

    def test_different_python(self):
        assert _cache_name(python="/usr/bin/python3.11") != _cache_name(python="/usr/bin/python3.12")

    def test_different_pip_args(self):
        assert _cache_name(pip_args=["--index-url", "https://a.com"]) != _cache_name(
            pip_args=["--index-url", "https://b.com"]
        )

    def test_different_venv_args(self):
        assert _cache_name(venv_args=["--system-site-packages"]) != _cache_name(venv_args=[])

    def test_different_backend(self):
        assert _cache_name(backend="pip") != _cache_name(backend="uv")


# ---------------------------------------------------------------------------
# Boundary-aware hashing – the old "".join() collisions
# ---------------------------------------------------------------------------


class TestNoElementBoundaryCollisions:
    """Verify that distinct list *splits* produce distinct hashes.

    The old implementation used ``"".join(list)`` which merged element
    boundaries:  ["ab","cd"] and ["abc","d"] both became "abcd".
    """

    def test_requirements_boundary(self):
        assert _cache_name(requirements=["ab", "cd"]) != _cache_name(requirements=["abc", "d"])

    def test_requirements_boundary_single_vs_multi(self):
        assert _cache_name(requirements=["abcd"]) != _cache_name(requirements=["ab", "cd"])

    def test_pip_args_boundary(self):
        assert _cache_name(pip_args=["--index-url", "X"]) != _cache_name(pip_args=["--index-urlX"])

    def test_pip_args_boundary_single_vs_multi(self):
        assert _cache_name(pip_args=["a", "b", "c"]) != _cache_name(pip_args=["ab", "c"])

    def test_venv_args_boundary(self):
        assert _cache_name(venv_args=["--a", "--b"]) != _cache_name(venv_args=["--a--b"])


# ---------------------------------------------------------------------------
# Cross-field isolation – content in one field must not collide with another
# ---------------------------------------------------------------------------


class TestNoCrossFieldCollisions:
    def test_requirements_vs_python(self):
        """Appending to requirements vs prepending to python must differ."""
        assert _cache_name(requirements=["pkgX"], python="Y") != _cache_name(
            requirements=["pkg"], python="XY"
        )

    def test_pip_args_vs_venv_args(self):
        assert _cache_name(pip_args=["--extra"], venv_args=[]) != _cache_name(
            pip_args=[], venv_args=["--extra"]
        )

    def test_venv_args_vs_backend(self):
        assert _cache_name(venv_args=["pip"], backend="") != _cache_name(venv_args=[], backend="pip")


# ---------------------------------------------------------------------------
# Empty-list handling
# ---------------------------------------------------------------------------


class TestEmptyLists:
    def test_empty_requirements(self):
        # Should not crash and should differ from non-empty
        assert _cache_name(requirements=[]) != _cache_name(requirements=["pkg"])

    def test_empty_pip_args(self):
        assert _cache_name(pip_args=[]) != _cache_name(pip_args=["--verbose"])

    def test_empty_venv_args(self):
        assert _cache_name(venv_args=[]) != _cache_name(venv_args=["--system-site-packages"])

    def test_all_empty_lists(self):
        """All-empty is a valid combination and must not crash."""
        name = _cache_name(requirements=[], pip_args=[], venv_args=[])
        assert len(name) == 15
        assert all(c in "0123456789abcdef" for c in name)


# ---------------------------------------------------------------------------
# Hash format
# ---------------------------------------------------------------------------


class TestHashFormat:
    def test_length_is_15_hex_chars(self):
        name = _cache_name()
        assert len(name) == 15
        assert all(c in "0123456789abcdef" for c in name)

    def test_path_is_under_venv_cache(self):
        from pipx import paths

        path = _get_temporary_venv_path(**_DEFAULTS)
        assert path.parent == paths.ctx.venv_cache
