"""Tests for the deprecation helper — Batch P4-C."""
from __future__ import annotations

import warnings
from typing import Iterator

import pytest

from gitpilot import _deprecation
from gitpilot._deprecation import (
    deprecated,
    deprecated_alias,
    reset_deprecation_log_for_tests,
)


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_deprecation_log_for_tests()
    yield
    reset_deprecation_log_for_tests()


# ----------------------------------------------------------------------
# @deprecated decorator
# ----------------------------------------------------------------------

def test_deprecated_emits_warning_on_first_call() -> None:
    @deprecated(replacement="gitpilot.public_api.shiny", removed_in="2.0")
    def old() -> int:
        return 42

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        assert old() == 42

    assert len(captured) == 1
    msg = str(captured[0].message)
    assert "old" in msg
    assert "shiny" in msg
    assert "v2.0" in msg
    assert captured[0].category is DeprecationWarning


def test_deprecated_emits_only_once_per_process() -> None:
    @deprecated(replacement="gitpilot.public_api.shiny", removed_in="2.0")
    def old() -> str:
        return "ok"

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        old()
        old()
        old()

    # Only the first call emits.
    assert sum(1 for w in captured if w.category is DeprecationWarning) == 1


def test_deprecated_preserves_signature_and_metadata() -> None:
    @deprecated(replacement="b", removed_in="9.9")
    def my_fn(a: int, b: int = 0) -> int:
        """docstring kept."""
        return a + b

    assert my_fn.__doc__ == "docstring kept."
    assert my_fn.__name__ == "my_fn"
    assert hasattr(my_fn, "__gitpilot_deprecated__")
    meta = my_fn.__gitpilot_deprecated__
    assert meta["replacement"] == "b"
    assert meta["removed_in"] == "9.9"


def test_legacy_name_override() -> None:
    @deprecated(replacement="x", removed_in="9.9", legacy_name="my_old_name")
    def some_fn() -> None:
        return None

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        some_fn()
    assert "my_old_name" in str(captured[0].message)


# ----------------------------------------------------------------------
# deprecated_alias
# ----------------------------------------------------------------------

def test_alias_delegates_to_target() -> None:
    def shiny(x: int) -> int:
        return x * 2

    legacy = deprecated_alias(
        "legacy", shiny,
        replacement="gitpilot.public_api.shiny", removed_in="2.0",
    )
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        assert legacy(3) == 6
    assert any(w.category is DeprecationWarning for w in captured)
    assert "legacy" in str(captured[0].message)


def test_alias_emit_once_independent_of_target() -> None:
    def target() -> None:
        return None

    legacy = deprecated_alias(
        "legacy", target,
        replacement="new", removed_in="2.0",
    )
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        legacy()
        legacy()
    dep_warns = [w for w in captured if w.category is DeprecationWarning]
    assert len(dep_warns) == 1


# ----------------------------------------------------------------------
# Reset helper
# ----------------------------------------------------------------------

def test_reset_clears_emit_once_state() -> None:
    @deprecated(replacement="b", removed_in="1.0")
    def old() -> None:
        return None

    with warnings.catch_warnings(record=True) as first:
        warnings.simplefilter("always")
        old()
    assert any(w.category is DeprecationWarning for w in first)

    reset_deprecation_log_for_tests()

    with warnings.catch_warnings(record=True) as second:
        warnings.simplefilter("always")
        old()
    assert any(w.category is DeprecationWarning for w in second)


# ----------------------------------------------------------------------
# Internal store remains private
# ----------------------------------------------------------------------

def test_module_does_not_export_internal_state() -> None:
    # The internal warned-set is an implementation detail; callers must
    # use the public reset helper, not poke at ``_WARNED``.
    assert hasattr(_deprecation, "_WARNED")
    # But the name is leading-underscored — no public re-export.
    public = [name for name in dir(_deprecation) if not name.startswith("_")]
    assert "WARNED" not in public
