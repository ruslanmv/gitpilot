"""Per-repo consent for the local RAG index (Batch B9).

Storage: ``~/.gitpilot/rag/<owner>/<repo>/.consent`` — a small JSON
file with the grant timestamp and identity.  Branch-agnostic on
purpose: building a semantic index is a repo-level decision, and we
want the second branch of the same repo to inherit consent.

Three operations the router needs:

* :func:`has_consent` — fast: returns ``True`` iff the consent file
  exists and is well-formed.
* :func:`grant_consent` — writes the file (idempotent).  Called when
  the user approves an INDEX plan step.
* :func:`revoke_consent` — deletes the file *and* removes the
  persisted index directory.  Called from Settings → Provider.

All paths are sanitised via the same helper the RAG store uses, so
"weird" owner/repo names (with `/`, spaces, etc.) won't poke outside
the consent root.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

from .rag.store import rag_root

logger = logging.getLogger(__name__)

CONSENT_FILE = ".consent"


@dataclass(frozen=True)
class ConsentRecord:
    """Round-trip JSON shape for the consent file."""
    granted_at: str          # ISO-8601 UTC
    granted_by: Optional[str] = None    # username / actor id if available


def _consent_dir(owner: str, repo: str) -> Path:
    """Consent lives at the repo level (no branch segment) so all
    branches of the same repo share the answer."""
    from .rag.store import _sanitize  # local import — sanitiser kept
                                       # private to store.py
    return rag_root() / _sanitize(owner) / _sanitize(repo)


def _consent_path(owner: str, repo: str) -> Path:
    return _consent_dir(owner, repo) / CONSENT_FILE


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def has_consent(owner: str, repo: str) -> bool:
    """Return ``True`` iff the user has previously approved indexing
    for ``owner/repo``.  Malformed / unreadable files count as "no
    consent" — fail closed."""
    if not owner or not repo:
        return False
    path = _consent_path(owner, repo)
    if not path.exists():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    # Minimum shape check.  Anything else weird → no consent.
    return isinstance(raw, dict) and isinstance(raw.get("granted_at"), str)


def grant_consent(
    owner: str,
    repo: str,
    *,
    granted_by: Optional[str] = None,
) -> ConsentRecord:
    """Record consent for ``owner/repo``.  Idempotent: calling twice
    updates the timestamp but doesn't re-prompt the user."""
    if not owner or not repo:
        raise ValueError("grant_consent: owner and repo are required")
    record = ConsentRecord(
        granted_at=datetime.now(UTC).isoformat(),
        granted_by=granted_by,
    )
    cdir = _consent_dir(owner, repo)
    cdir.mkdir(parents=True, exist_ok=True)
    _consent_path(owner, repo).write_text(
        json.dumps(asdict(record), indent=2),
        encoding="utf-8",
    )
    return record


def revoke_consent(owner: str, repo: str) -> bool:
    """Delete the consent file AND the persisted index for the repo.

    Returns ``True`` if anything was actually deleted, ``False`` if
    there was nothing to revoke (already absent).  Never raises on a
    missing path — revocation is intent, not assertion.
    """
    if not owner or not repo:
        return False
    cdir = _consent_dir(owner, repo)
    removed = False
    cpath = _consent_path(owner, repo)
    if cpath.exists():
        try:
            cpath.unlink()
            removed = True
        except OSError as exc:
            logger.debug("[rag-consent] could not unlink %s: %s", cpath, exc)
    # Wipe every per-branch index directory under this repo.  The
    # consent record was repo-level, the indexes are per-branch, so
    # we recurse through immediate subdirectories.
    if cdir.exists():
        try:
            for entry in cdir.iterdir():
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                    removed = True
        except OSError as exc:
            logger.debug("[rag-consent] could not iterate %s: %s", cdir, exc)
    return removed


def load_record(owner: str, repo: str) -> Optional[ConsentRecord]:
    """Return the persisted ConsentRecord, or ``None`` if absent /
    malformed.  Callers that need to surface "consented since 2025-
    01-02" can use this without writing their own JSON parsing."""
    if not has_consent(owner, repo):
        return None
    try:
        raw = json.loads(_consent_path(owner, repo).read_text(encoding="utf-8"))
        return ConsentRecord(
            granted_at=str(raw.get("granted_at", "")),
            granted_by=raw.get("granted_by"),
        )
    except Exception:
        return None


__all__ = [
    "ConsentRecord",
    "grant_consent",
    "has_consent",
    "load_record",
    "revoke_consent",
]
