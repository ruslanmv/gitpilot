"""gitpilot/auth_store.py

Local token/session persistence for GitPilot UI.

This module is intentionally lightweight and optional.

It enables "login once" behavior for *local UI deployments*:

- After a successful GitHub OAuth (web flow) or Device Flow login, GitPilot
  persists the session (access token + user) to a local file.
- On restart / browser refresh, the UI can ask the backend for the cached
  session and avoid forcing the user through GitHub authorization again.
- "Logout" deletes the cached session.

Security notes
--------------
- This is intended for local / single-user usage.
- The cache file is created with restrictive permissions where possible.

Env overrides
-------------
GITPILOT_AUTH_CACHE_PATH:
    Override the cache location (default: ~/.gitpilot/auth.json)
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


def _cache_path() -> Path:
    override = os.getenv("GITPILOT_AUTH_CACHE_PATH")
    if override:
        return Path(os.path.expanduser(override)).resolve()
    return Path(os.path.expanduser("~/.gitpilot/auth.json")).resolve()


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Best effort: tighten dir perms (may fail on Windows)
    try:
        os.chmod(path.parent, 0o700)
    except Exception:
        pass


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    _ensure_parent_dir(path)

    payload = json.dumps(data, indent=2, sort_keys=True)
    tmp_dir = str(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=".auth.", suffix=".json", dir=tmp_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())

        # Best effort: tighten file perms
        try:
            os.chmod(tmp_name, 0o600)
        except Exception:
            pass

        os.replace(tmp_name, path)

        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    finally:
        try:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
        except Exception:
            pass


def save_session(session: Dict[str, Any]) -> None:
    """Persist an AuthSession-like dict."""
    if not isinstance(session, dict):
        raise ValueError("session must be a dict")
    if not session.get("access_token"):
        raise ValueError("session.access_token is required")

    # Add/update saved timestamp (useful for debugging)
    to_save = dict(session)
    to_save["saved_at"] = int(time.time())
    _atomic_write_json(_cache_path(), to_save)


def load_session() -> Optional[Dict[str, Any]]:
    """Load persisted session dict (if exists)."""
    path = _cache_path()
    if not path.exists():
        return None

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("access_token"):
            return data
    except Exception:
        # If corrupted, don't crash the app
        return None

    return None


def clear_session() -> None:
    """Delete persisted session if present."""
    path = _cache_path()
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass
