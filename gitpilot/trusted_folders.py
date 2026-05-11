# gitpilot/trusted_folders.py
"""Trust gate for workspace folders.

A workspace must be explicitly trusted before GitPilot will read its
files or execute commands on its behalf.  Trust decisions are
persisted in ``~/.gitpilot/trusted.json`` with a fingerprint of the
folder's contents so that a malicious replacement of the directory
contents revokes trust automatically.

This module is additive: callers that don't check trust keep working
exactly as before.  The recommended pattern is::

    from gitpilot.trusted_folders import TrustStore, TrustStatus

    store = TrustStore.default()
    if store.status(workspace) != TrustStatus.TRUSTED:
        # Prompt the user, then:
        store.trust(workspace)
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_STORE = Path.home() / ".gitpilot" / "trusted.json"
FINGERPRINT_FILES = (
    "package.json", "pyproject.toml", "Cargo.toml", "go.mod",
    "Gemfile", "pom.xml", "Makefile", "README.md", "AGENTS.md",
    ".gitpilot/modes.yaml", ".gitpilot/mcp.json",
)


class TrustStatus(str, Enum):
    """Result of asking a :class:`TrustStore` about a workspace path."""

    TRUSTED = "trusted"
    UNKNOWN = "unknown"
    FINGERPRINT_MISMATCH = "fingerprint_mismatch"


@dataclass
class TrustEntry:
    path: str
    fingerprint: str
    trusted_at: float
    note: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "path": self.path,
            "fingerprint": self.fingerprint,
            "trusted_at": self.trusted_at,
            "note": self.note,
        }


@dataclass
class TrustStore:
    """Persistent trust decisions."""

    path: Path
    entries: Dict[str, TrustEntry] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def default(cls) -> "TrustStore":
        return cls.load(DEFAULT_STORE)

    @classmethod
    def load(cls, path: Path) -> "TrustStore":
        store = cls(path=path)
        if not path.exists():
            return store
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("could not read trust store %s: %s", path, e)
            return store
        for entry in data.get("entries", []):
            try:
                store.entries[entry["path"]] = TrustEntry(**entry)
            except Exception:
                continue
        return store

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------
    def status(self, workspace: Path) -> TrustStatus:
        key = str(workspace.resolve())
        entry = self.entries.get(key)
        if entry is None:
            return TrustStatus.UNKNOWN
        if entry.fingerprint != fingerprint(workspace):
            return TrustStatus.FINGERPRINT_MISMATCH
        return TrustStatus.TRUSTED

    def trust(self, workspace: Path, *, note: str = "") -> TrustEntry:
        key = str(workspace.resolve())
        entry = TrustEntry(
            path=key,
            fingerprint=fingerprint(workspace),
            trusted_at=time.time(),
            note=note,
        )
        self.entries[key] = entry
        self._persist()
        return entry

    def revoke(self, workspace: Path) -> bool:
        key = str(workspace.resolve())
        existed = key in self.entries
        self.entries.pop(key, None)
        if existed:
            self._persist()
        return existed

    def all(self) -> List[TrustEntry]:
        return list(self.entries.values())

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": [e.to_dict() for e in self.entries.values()]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------
# Fingerprinting
# ----------------------------------------------------------------------

def fingerprint(workspace: Path) -> str:
    """Return a stable digest of the workspace's key files.

    The fingerprint covers a small, fixed set of structural files —
    enough to detect a wholesale swap of the directory without scanning
    a deep tree.  When none of the files exist the fingerprint encodes
    just the absolute path so a freshly initialised folder still has a
    deterministic identity.
    """
    workspace = workspace.resolve()
    h = hashlib.sha256()
    h.update(str(workspace).encode("utf-8"))
    for rel in FINGERPRINT_FILES:
        path = workspace / rel
        if not path.exists() or not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()[:32]
