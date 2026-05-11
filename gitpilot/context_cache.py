# gitpilot/context_cache.py
"""In-process LRU memoisation for the workspace context pack.

Batch P2-C — additive.  :func:`gitpilot.context_pack.build_context_pack`
re-scans the workspace on every turn, which is the right behaviour for
correctness but wasteful when nothing has changed: most turns reuse
the same conventions, the same active use case, and the same vault
chunks.

``build_cached`` wraps the original builder with an LRU keyed on the
workspace path, the active mode slug, the query string, and a digest
of the *mtimes* of the files that contribute to the pack.  Because the
key incorporates mtimes, edits to the relevant files invalidate the
cache automatically.  Callers must not edit files via the cache layer
itself — touching ``AGENTS.md`` or ``.gitpilot/*`` is enough.

Behaviour matrix
----------------
* ``context_cache`` flag off (default) → straight passthrough to
  :func:`gitpilot.context_pack.build_context_pack`.  Zero new state.
* Flag on → memoised; cache size capped to keep memory bounded.

The cache is *strict per workspace*: cross-workspace contamination is
impossible because the workspace path is part of the key.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from . import flags

logger = logging.getLogger(__name__)

FLAG_CONTEXT_CACHE = "context_cache"
DEFAULT_CACHE_SIZE = 32

# Files that contribute to the cache key — touching any of these
# invalidates the entry on the next call.
_FINGERPRINT_FILES: Tuple[str, ...] = (
    "AGENTS.md",
    ".gitpilot/AGENTS.md",
    ".gitpilot/GITPILOT.md",
    ".gitpilot/modes.yaml",
    ".gitpilotrules",
)
_FINGERPRINT_DIRS: Tuple[str, ...] = (
    ".gitpilot/rules",
    ".gitpilot/skills",
    ".gitpilot/uploads",
)


# ----------------------------------------------------------------------
# Stats
# ----------------------------------------------------------------------

@dataclass
class CacheStats:
    """Snapshot of the in-process cache state."""

    size: int
    capacity: int
    hits: int
    misses: int

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total) if total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "size": self.size,
            "capacity": self.capacity,
            "hits": self.hits,
            "misses": self.misses,
            "hit_ratio": round(self.hit_ratio, 4),
        }


# ----------------------------------------------------------------------
# Cache
# ----------------------------------------------------------------------

class _LRUCache:
    """Tiny LRU keyed on ``(workspace, mode, query, mtime_digest)``."""

    def __init__(self, capacity: int = DEFAULT_CACHE_SIZE) -> None:
        self._capacity = max(1, int(capacity))
        self._store: "OrderedDict[Tuple[str, Optional[str], str, str], str]" = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    def get(self, key: Tuple[str, Optional[str], str, str]) -> Optional[str]:
        with self._lock:
            value = self._store.get(key)
            if value is None:
                self._misses += 1
                return None
            self._store.move_to_end(key)
            self._hits += 1
            return value

    def put(self, key: Tuple[str, Optional[str], str, str], value: str) -> None:
        with self._lock:
            self._store[key] = value
            self._store.move_to_end(key)
            while len(self._store) > self._capacity:
                self._store.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> CacheStats:
        with self._lock:
            return CacheStats(
                size=len(self._store),
                capacity=self._capacity,
                hits=self._hits,
                misses=self._misses,
            )


_CACHE = _LRUCache()


def get_cache_stats() -> CacheStats:
    """Return a snapshot of the global cache state."""
    return _CACHE.stats()


def clear_cache() -> None:
    """Drop every cached entry.  Useful for tests and ``/admin`` hooks."""
    _CACHE.clear()


def set_capacity(capacity: int) -> None:
    """Resize the cache.  Effective on the next ``put``."""
    global _CACHE
    new_cache = _LRUCache(capacity=capacity)
    # Preserve recent entries up to the new capacity.
    with _CACHE._lock:
        for key, value in list(_CACHE._store.items())[-capacity:]:
            new_cache.put(key, value)
        new_cache._hits = _CACHE._hits
        new_cache._misses = _CACHE._misses
    _CACHE = new_cache


# ----------------------------------------------------------------------
# Public builder
# ----------------------------------------------------------------------

def build_cached(
    workspace_path: Path,
    query: str = "",
    *,
    mode_slug: Optional[str] = None,
    enabled: Optional[bool] = None,
    **builder_kwargs: object,
) -> str:
    """Memoised wrapper around :func:`context_pack.build_context_pack`.

    When the ``context_cache`` flag is off (or ``enabled=False``)
    this calls the underlying builder directly and returns its output —
    nothing is cached.  Otherwise the result is keyed on
    ``(workspace, mode_slug, query, mtime_digest)`` and reused on hit.
    """
    from .context_pack import build_context_pack  # local import (avoid cycle)

    flag_on = enabled if enabled is not None else flags.is_on(FLAG_CONTEXT_CACHE)
    if not flag_on:
        return build_context_pack(workspace_path, query=query, **builder_kwargs)  # type: ignore[arg-type]

    workspace_path = workspace_path.resolve()
    digest = _mtimes_digest(workspace_path)
    key = (str(workspace_path), mode_slug, query, digest)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    value = build_context_pack(workspace_path, query=query, **builder_kwargs)  # type: ignore[arg-type]
    _CACHE.put(key, value)
    return value


# ----------------------------------------------------------------------
# Mtime digest
# ----------------------------------------------------------------------

def _mtimes_digest(workspace_path: Path) -> str:
    """SHA-256 of (path, mtime_ns) pairs for the fingerprint set."""
    h = hashlib.sha256()
    for rel in _FINGERPRINT_FILES:
        path = workspace_path / rel
        if path.exists() and path.is_file():
            try:
                stat = path.stat()
            except OSError:
                continue
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            h.update(str(stat.st_mtime_ns).encode("ascii"))
            h.update(b"\0")
            h.update(str(stat.st_size).encode("ascii"))
            h.update(b"\0")
    for rel in _FINGERPRINT_DIRS:
        directory = workspace_path / rel
        if not directory.is_dir():
            continue
        for child in sorted(_walk_files(directory)):
            try:
                stat = child.stat()
            except OSError:
                continue
            h.update(str(child).encode("utf-8"))
            h.update(b"\0")
            h.update(str(stat.st_mtime_ns).encode("ascii"))
            h.update(b"\0")
    return h.hexdigest()[:32]


def _walk_files(directory: Path) -> Iterable[Path]:
    for child in directory.rglob("*"):
        if child.is_file():
            yield child


# ----------------------------------------------------------------------
# Maintenance utilities (mostly for tests / admin)
# ----------------------------------------------------------------------

def warm(workspace_path: Path, queries: Iterable[str], *, mode_slug: Optional[str] = None) -> int:
    """Pre-populate the cache for a list of common queries.  Returns the
    number of entries inserted (cache may already contain some)."""
    inserted = 0
    for q in queries:
        start_size = _CACHE.stats().size
        build_cached(workspace_path, q, mode_slug=mode_slug, enabled=True)
        if _CACHE.stats().size != start_size:
            inserted += 1
    return inserted


def now() -> float:
    """Wall-clock helper used by tests that want monotonic timestamps."""
    return time.monotonic()
