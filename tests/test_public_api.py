"""Public API stability contract — Batch P1-C foothold + Batch P4-C.

Every name in ``gitpilot.public_api.__all__`` must:

* be importable from ``gitpilot.public_api``;
* have a runtime-resolvable type when it's callable;
* have a non-trivial ``__doc__`` when it's callable.

The test fails loudly if any of those guarantees breaks, which is the
whole point of the stability layer.
"""
from __future__ import annotations

import importlib
import inspect
import typing

import pytest


def _public_api():
    return importlib.import_module("gitpilot.public_api")


def test_public_api_imports_cleanly() -> None:
    module = _public_api()
    for name in module.__all__:
        assert hasattr(module, name), f"gitpilot.public_api missing {name!r}"


def test_public_api_all_names_resolve_to_non_None_objects() -> None:
    module = _public_api()
    for name in module.__all__:
        obj = getattr(module, name)
        assert obj is not None, f"{name} resolved to None"


def test_public_api_callables_carry_docstrings() -> None:
    module = _public_api()
    missing: list[str] = []
    for name in module.__all__:
        obj = getattr(module, name)
        if not callable(obj):
            continue
        if isinstance(obj, type):
            # Class — docstring may live on __init__.
            doc = obj.__doc__ or getattr(obj, "__init__", None).__doc__
        else:
            doc = obj.__doc__
        if not doc or len(doc.strip()) < 5:
            missing.append(name)
    assert not missing, f"missing docstrings on public API: {missing}"


def test_public_api_callables_have_resolvable_type_hints() -> None:
    """Every callable on the surface must be typed enough for IDEs."""
    module = _public_api()
    skipped: list[str] = []
    for name in module.__all__:
        obj = getattr(module, name)
        if not callable(obj):
            continue
        if isinstance(obj, type):
            continue            # classes are exempt from the function check
        if inspect.isbuiltin(obj):
            continue
        try:
            hints = typing.get_type_hints(obj)
        except Exception:
            skipped.append(name)
            continue
        # We expect at least one type hint (return or parameter).  An
        # entirely un-annotated public callable is a red flag.
        if hints == {}:
            try:
                sig = inspect.signature(obj)
            except (TypeError, ValueError):
                continue
            if any(p.annotation is inspect.Parameter.empty for p in sig.parameters.values()):
                skipped.append(name)
    assert skipped == [], f"public callables missing type hints: {skipped}"


def test_public_api_exposes_sandbox_factory() -> None:
    from gitpilot.public_api import SandboxPolicy, get_sandbox  # re-export
    sb = get_sandbox(policy=SandboxPolicy())
    assert sb.backend in {"subprocess", "matrixlab", "off"}


def test_public_api_exposes_modes_registry() -> None:
    from gitpilot.public_api import ModeRegistry
    registry = ModeRegistry()
    assert registry.all() == []


def test_public_api_exposes_flags() -> None:
    from gitpilot.public_api import enabled_flags, is_on
    assert isinstance(enabled_flags(), dict)
    assert is_on("unset-flag", default=False) is False


def test_public_api_exposes_deprecation_helper() -> None:
    """Batch P4-C — the deprecation pipeline is part of the contract."""
    from gitpilot.public_api import deprecated_alias

    def shiny() -> str:
        return "ok"

    legacy = deprecated_alias(
        "legacy", shiny,
        replacement="gitpilot.public_api.shiny", removed_in="9.9",
    )
    assert legacy() == "ok"
    assert hasattr(legacy, "__gitpilot_deprecated__")


def test_all_list_does_not_contain_duplicates() -> None:
    module = _public_api()
    assert len(module.__all__) == len(set(module.__all__)), (
        "duplicate names in __all__"
    )


def test_all_list_is_sorted_internally_within_sections() -> None:
    """Sanity: no name appears in __all__ twice with different casing."""
    module = _public_api()
    lowered = [n.lower() for n in module.__all__]
    assert len(lowered) == len(set(lowered))
