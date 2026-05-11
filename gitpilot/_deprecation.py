# gitpilot/_deprecation.py
"""Deprecation helpers used by :mod:`gitpilot.public_api` — Batch P4-C.

This module is intentionally internal (leading underscore) and tiny.
It provides one decorator and one alias factory so that every
deprecated symbol on the stable surface behaves the same way:

* a single :class:`DeprecationWarning` is emitted at the first call
  through that symbol (per process, to avoid log spam)
* the warning text follows a fixed template:
  ``"<old> is deprecated; use <new> instead (will be removed in vX.Y)"``
* original behaviour is preserved — no breaking change to callers

Use it from the public-API package like this::

    from gitpilot._deprecation import deprecated_alias

    parse_mentions = deprecated_alias(
        "parse_mentions", expand_mentions,
        replacement="gitpilot.public_api.expand_mentions",
        removed_in="2.0",
    )

The corresponding entry in :doc:`API_STABILITY.md` documents the
removal milestone.
"""
from __future__ import annotations

import functools
import threading
import warnings
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


_WARNED: set[str] = set()
_LOCK = threading.RLock()


def _emit_once(key: str, message: str, stacklevel: int = 3) -> None:
    """Emit ``DeprecationWarning(message)`` at most once per key."""
    with _LOCK:
        if key in _WARNED:
            return
        _WARNED.add(key)
    warnings.warn(message, DeprecationWarning, stacklevel=stacklevel)


def deprecated(
    *,
    replacement: str,
    removed_in: str,
    legacy_name: str | None = None,
) -> Callable[[F], F]:
    """Decorator: emit a :class:`DeprecationWarning` on first call.

    Parameters
    ----------
    replacement
        Dotted path the caller should use instead, e.g.
        ``"gitpilot.public_api.run_wizard"``.
    removed_in
        Version that will drop the symbol, e.g. ``"2.0"``.  Surfaces in
        the warning text so users can plan the migration.
    legacy_name
        Override for the symbol's display name; defaults to the
        wrapped function's ``__qualname__``.
    """

    def _wrap(fn: F) -> F:
        name = legacy_name or fn.__qualname__

        @functools.wraps(fn)
        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            _emit_once(
                key=f"call:{name}",
                message=(
                    f"{name} is deprecated; use {replacement} instead "
                    f"(will be removed in v{removed_in})"
                ),
            )
            return fn(*args, **kwargs)

        # Surface the deprecation metadata for tooling / docs generation.
        _wrapper.__gitpilot_deprecated__ = {  # type: ignore[attr-defined]
            "legacy_name": name,
            "replacement": replacement,
            "removed_in": removed_in,
        }
        return _wrapper  # type: ignore[return-value]

    return _wrap


def deprecated_alias(
    legacy_name: str,
    target: F,
    *,
    replacement: str,
    removed_in: str,
) -> F:
    """Build a deprecated alias that delegates to ``target``.

    Use this when you keep two names for the same callable for
    backwards compatibility — the alias warns on use; the canonical
    name does not.
    """
    return deprecated(
        replacement=replacement,
        removed_in=removed_in,
        legacy_name=legacy_name,
    )(target)


def reset_deprecation_log_for_tests() -> None:
    """Forget every emit-once key.  Test-only."""
    with _LOCK:
        _WARNED.clear()
