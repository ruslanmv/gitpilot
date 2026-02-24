# gitpilot/session.py
"""Session persistence, resumption, and checkpoint management.

Sessions track the full conversation and workspace state.  Checkpoints
snapshot the workspace at key moments so users can rewind.
"""
from __future__ import annotations

import json
import logging
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SESSION_ROOT = Path.home() / ".gitpilot" / "sessions"


@dataclass
class Message:
    role: str          # user | assistant | system
    content: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Checkpoint:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    message_index: int = 0
    description: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    snapshot_path: Optional[str] = None


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    name: Optional[str] = None
    repo_full_name: Optional[str] = None
    branch: Optional[str] = None
    messages: List[Message] = field(default_factory=list)
    checkpoints: List[Checkpoint] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    pr_number: Optional[int] = None
    status: str = "active"  # active | paused | completed
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Multi-repo context support
    # Each entry: {"full_name": "owner/repo", "branch": "main", "mode": "read"|"write"}
    repos: List[Dict[str, Any]] = field(default_factory=list)
    active_repo: Optional[str] = None  # full_name of the write-target repo

    def add_message(self, role: str, content: str, **meta):
        self.messages.append(Message(role=role, content=content, metadata=meta))
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Session:
        data = dict(data)  # shallow copy
        data["messages"] = [Message(**m) for m in data.get("messages", [])]
        data["checkpoints"] = [Checkpoint(**c) for c in data.get("checkpoints", [])]

        # Backwards-compatible migration: populate repos from legacy single-repo
        if not data.get("repos") and data.get("repo_full_name"):
            data["repos"] = [{
                "full_name": data["repo_full_name"],
                "branch": data.get("branch", "main"),
                "mode": "write",
            }]
            data.setdefault("active_repo", data["repo_full_name"])
        data.setdefault("repos", [])
        data.setdefault("active_repo", None)

        return cls(**data)


class SessionManager:
    """Manage session lifecycle: create, save, load, list, fork, rewind."""

    def __init__(self, root: Optional[Path] = None):
        self.root = root or SESSION_ROOT
        self.root.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    def create(
        self,
        repo_full_name: Optional[str] = None,
        branch: Optional[str] = None,
        name: Optional[str] = None,
    ) -> Session:
        session = Session(
            repo_full_name=repo_full_name, branch=branch, name=name,
        )
        self.save(session)
        return session

    def save(self, session: Session):
        path = self._session_path(session.id)
        path.write_text(json.dumps(session.to_dict(), indent=2))

    def load(self, session_id: str) -> Session:
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        return Session.from_dict(json.loads(path.read_text()))

    def list_sessions(
        self,
        repo_full_name: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        sessions = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text())
                if repo_full_name and data.get("repo_full_name") != repo_full_name:
                    continue
                sessions.append({
                    "id": data["id"],
                    "name": data.get("name"),
                    "repo": data.get("repo_full_name"),
                    "branch": data.get("branch"),
                    "message_count": len(data.get("messages", [])),
                    "status": data.get("status", "active"),
                    "updated_at": data.get("updated_at"),
                    "pr_number": data.get("pr_number"),
                    "repos": data.get("repos", []),
                    "active_repo": data.get("active_repo"),
                })
                if len(sessions) >= limit:
                    break
            except Exception:
                continue
        return sessions

    def delete(self, session_id: str) -> bool:
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def fork(self, session_id: str, at_message: Optional[int] = None) -> Session:
        original = self.load(session_id)
        messages = original.messages
        if at_message is not None:
            messages = messages[: at_message + 1]

        forked = Session(
            repo_full_name=original.repo_full_name,
            branch=original.branch,
            name=f"Fork of {original.name or original.id}",
            messages=list(messages),
            metadata={"forked_from": original.id},
        )
        self.save(forked)
        return forked

    def create_checkpoint(
        self,
        session: Session,
        workspace_path: Optional[Path] = None,
        description: str = "",
    ) -> Checkpoint:
        checkpoint = Checkpoint(
            message_index=len(session.messages),
            description=description or f"Checkpoint at message {len(session.messages)}",
        )
        if workspace_path and workspace_path.exists():
            snap_dir = self.root / "snapshots" / session.id
            snap_dir.mkdir(parents=True, exist_ok=True)
            archive_base = str(snap_dir / checkpoint.id)
            shutil.make_archive(archive_base, "gztar", root_dir=str(workspace_path))
            checkpoint.snapshot_path = archive_base + ".tar.gz"

        session.checkpoints.append(checkpoint)
        self.save(session)
        return checkpoint

    def rewind_to_checkpoint(
        self,
        session: Session,
        checkpoint_id: str,
        workspace_path: Optional[Path] = None,
    ) -> Session:
        checkpoint = None
        for cp in session.checkpoints:
            if cp.id == checkpoint_id:
                checkpoint = cp
                break
        if checkpoint is None:
            raise ValueError(f"Checkpoint not found: {checkpoint_id}")

        session.messages = session.messages[: checkpoint.message_index]

        if checkpoint.snapshot_path and workspace_path:
            snap = Path(checkpoint.snapshot_path)
            if snap.exists():
                if workspace_path.exists():
                    shutil.rmtree(workspace_path)
                workspace_path.mkdir(parents=True, exist_ok=True)
                shutil.unpack_archive(str(snap), str(workspace_path))

        idx = session.checkpoints.index(checkpoint)
        session.checkpoints = session.checkpoints[: idx + 1]
        self.save(session)
        return session
