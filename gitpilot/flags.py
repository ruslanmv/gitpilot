# gitpilot/flags.py
"""Feature-flag service — single source of truth for opt-in code paths.

Lookup precedence (first hit wins): explicit override → ``GITPILOT_FLAGS``
env (``name=1,other=0``) → ``<ws>/.gitpilot/flags.json`` →
``~/.gitpilot/flags.json`` → call-site default.  Lazy, cached, RLock-safe,
zero third-party deps.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional

logger = logging.getLogger(__name__)

ENV_VAR = "GITPILOT_FLAGS"
PROJECT_FLAGS_REL = Path(".gitpilot") / "flags.json"
USER_FLAGS_PATH = Path.home() / ".gitpilot" / "flags.json"

_TRUE = {"1", "true", "yes", "on", "y", "t"}
_FALSE = {"0", "false", "no", "off", "n", "f"}

_lock = threading.RLock()
_overrides: Dict[str, bool] = {}
_cache: Optional[Dict[str, bool]] = None
_workspace: Optional[Path] = None


def _coerce(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _TRUE:
            return True
        if v in _FALSE:
            return False
    return None


def _parse_env(raw: str) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "=" in piece:
            name, _, value = piece.partition("=")
            parsed = _coerce(value)
        else:
            name, parsed = piece, True
        name = name.strip()
        if not name or parsed is None:
            continue
        out[name] = parsed
    return out


def _load_file(path: Path) -> Dict[str, bool]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - logged, returns empty
        logger.warning("could not parse %s: %s", path, exc)
        return {}
    if not isinstance(data, Mapping):
        return {}
    out: Dict[str, bool] = {}
    for key, value in data.items():
        parsed = _coerce(value)
        if parsed is not None and isinstance(key, str):
            out[key.strip()] = parsed
    return out


def _build_cache() -> Dict[str, bool]:
    merged: Dict[str, bool] = {}
    merged.update(_load_file(USER_FLAGS_PATH))
    if _workspace is not None:
        merged.update(_load_file(_workspace / PROJECT_FLAGS_REL))
    env_raw = os.environ.get(ENV_VAR, "")
    if env_raw:
        merged.update(_parse_env(env_raw))
    merged.update(_overrides)
    return merged


def _ensure_cache() -> Dict[str, bool]:
    global _cache
    if _cache is None:
        _cache = _build_cache()
    return _cache


# --- public API --------------------------------------------------------

def set_workspace(workspace: Optional[Path]) -> None:
    """Register the active workspace so its ``.gitpilot/flags.json`` loads."""
    global _workspace
    with _lock:
        _workspace = workspace.resolve() if workspace is not None else None
        _invalidate()


def is_on(name: str, default: bool = False) -> bool:
    """Return whether feature flag *name* is enabled."""
    with _lock:
        return _ensure_cache().get(name, default)


def enabled_flags() -> Dict[str, bool]:
    """Return a snapshot of the currently merged flag map."""
    with _lock:
        return dict(_ensure_cache())


def set_override(name: str, value: bool) -> None:
    """Set a runtime override that beats every other source (tests, REPL)."""
    with _lock:
        _overrides[name] = bool(value)
        _invalidate()


def clear_override(name: str) -> None:
    """Remove a previously registered override."""
    with _lock:
        _overrides.pop(name, None)
        _invalidate()


def clear_all_overrides() -> None:
    """Drop every runtime override.  Mostly useful for test teardown."""
    with _lock:
        _overrides.clear()
        _invalidate()


def reload() -> Dict[str, bool]:
    """Reread environment + files.  Returns the new merged map."""
    with _lock:
        _invalidate()
        return dict(_ensure_cache())


def iter_known(defaults: Mapping[str, bool]) -> Iterator[tuple[str, bool, bool]]:
    """Yield ``(name, current, default)`` for every flag in *defaults*."""
    snapshot = enabled_flags()
    for name, default in defaults.items():
        yield name, snapshot.get(name, default), default


def _invalidate() -> None:
    global _cache
    _cache = None
