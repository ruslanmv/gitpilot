# gitpilot/session.py
"""Session persistence, resumption, and checkpoint management.

Sessions track the full conversation and workspace state.  Checkpoints
snapshot the workspace at key moments so users can rewind.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SESSION_ROOT = Path.home() / ".gitpilot" / "sessions"


@dataclass
class Message:
    role: str          # user | assistant | system
    content: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Checkpoint:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    message_index: int = 0
    description: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )
    snapshot_path: str | None = None


@dataclass
class Task:
    """One AI invocation recorded for the right-sidebar Tasks panel.

    Append-only.  Created with ``status="running"`` at the start of a
    user-facing operation (Plan or Execute), mutated in place once on
    completion, and never edited again.  The shape intentionally
    mirrors what Claude Code surfaces in its tasks list: title + kind
    + status + duration + token usage.

    Cost / cache / payload size are deferred to a later cut — v1 ships
    only what GitPilot can compute honestly across every supported
    provider.
    """
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    kind: str = "plan"           # plan | execute | (future: explore, code_write…)
    title: str = ""
    status: str = "running"      # running | completed | failed
    started_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )
    completed_at: str | None = None
    duration_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    name: str | None = None
    repo_full_name: str | None = None
    branch: str | None = None
    messages: list[Message] = field(default_factory=list)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
    )
    pr_number: int | None = None
    status: str = "active"  # active | paused | completed
    metadata: dict[str, Any] = field(default_factory=dict)

    # Session mode fields
    mode: str | None = None        # "folder" | "local_git" | "github"
    folder_path: str | None = None
    repo_root: str | None = None

    # Multi-repo context support
    # Each entry: {"full_name": "owner/repo", "branch": "main", "mode": "read"|"write"}
    repos: list[dict[str, Any]] = field(default_factory=list)
    active_repo: str | None = None  # full_name of the write-target repo

    # Right-sidebar Tasks panel (Claude-Code-style trace of every AI
    # invocation in this session).  Append-only.  Backwards-compatible
    # default for sessions that pre-date this field.
    tasks: list[Task] = field(default_factory=list)

    def add_message(self, role: str, content: str, **meta):
        self.messages.append(Message(role=role, content=content, metadata=meta))
        self.updated_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        data = dict(data)  # shallow copy
        data["messages"] = [Message(**m) for m in data.get("messages", [])]
        data["checkpoints"] = [Checkpoint(**c) for c in data.get("checkpoints", [])]
        # Backwards-compatible: sessions saved before the tasks field
        # existed simply load with an empty list.
        data["tasks"] = [Task(**t) for t in data.get("tasks", [])]

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

        # Session mode fields (backwards-compatible)
        data.setdefault("mode", None)
        data.setdefault("folder_path", None)
        data.setdefault("repo_root", None)

        return cls(**data)


class SessionManager:
    """Manage session lifecycle: create, save, load, list, fork, rewind."""

    def __init__(self, root: Path | None = None):
        self.root = root or SESSION_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        # Per-instance cache for list_sessions() (see list_sessions() docstring)
        self._list_cache: dict[str, Any] = {}

    def _session_path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    def create(
        self,
        repo_full_name: str | None = None,
        branch: str | None = None,
        name: str | None = None,
    ) -> Session:
        session = Session(
            repo_full_name=repo_full_name, branch=branch, name=name,
        )
        self.save(session)
        return session

    def create_folder_session(
        self, folder_path: str, name: str | None = None,
    ) -> Session:
        """Create a session for folder-only mode (no git required)."""
        folder_name = os.path.basename(os.path.normpath(folder_path))
        session_name = name or f"Folder: {folder_name}"
        session = self.create(name=session_name)
        session.mode = "folder"
        session.folder_path = folder_path
        self.save(session)
        return session

    def create_local_git_session(
        self, repo_root: str, branch: str | None = None, name: str | None = None,
    ) -> Session:
        """Create a session for local git mode."""
        repo_name = os.path.basename(os.path.normpath(repo_root))
        session_name = name or f"Local Git: {repo_name}"
        if branch:
            session_name += f" ({branch})"
        session = self.create(name=session_name)
        session.mode = "local_git"
        session.repo_root = repo_root
        session.folder_path = repo_root
        session.branch = branch
        self.save(session)
        return session

    def create_github_session(
        self, repo_full_name: str, branch: str | None = None, name: str | None = None,
    ) -> Session:
        """Create a session for GitHub mode."""
        session_name = name or f"GitHub: {repo_full_name}"
        if branch:
            session_name += f" ({branch})"
        session = self.create(
            name=session_name,
            repo_full_name=repo_full_name
        )
        session.mode = "github"
        session.branch = branch
        self.save(session)
        return session

    def save(self, session: Session):
        path = self._session_path(session.id)
        path.write_text(json.dumps(session.to_dict(), indent=2))
        self.invalidate_list_cache()

    def load(self, session_id: str) -> Session:
        path = self._session_path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session not found: {session_id}")
        return Session.from_dict(json.loads(path.read_text()))

    @staticmethod
    def _recency(data: dict[str, Any], path: Path) -> float:
        """When a session was last worked on, as a POSIX timestamp.

        ``updated_at`` is the truth — it moves on every message. ``created_at``
        covers a session that has been created but not yet written to, which is
        exactly the session the user just opened and is about to type into.
        The file mtime is the last resort for a hand-edited or pre-historic
        file, so a missing timestamp sinks to its real age instead of to 1970.
        """
        for key in ("updated_at", "created_at"):
            stamp = data.get(key)
            if not stamp:
                continue
            try:
                parsed = datetime.fromisoformat(str(stamp))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.timestamp()
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def _summarize(data: dict[str, Any]) -> dict[str, Any]:
        """The list-row shape. One definition, so every caller agrees."""
        return {
            "id": data["id"],
            "name": data.get("name"),
            "repo": data.get("repo_full_name"),
            "branch": data.get("branch"),
            "message_count": len(data.get("messages", [])),
            "status": data.get("status", "active"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "mode": data.get("mode"),
            "pr_number": data.get("pr_number"),
            "repos": data.get("repos", []),
            "active_repo": data.get("active_repo"),
        }

    def _list_sessions_dir_fingerprint(self) -> tuple[float, int]:
        """Cheap fingerprint of the sessions directory — (mtime, file_count).
        If either changes, the cache is stale.
        """
        try:
            stat = self.root.stat()
            files = list(self.root.glob("*.json"))
            # Also check any file mtime that's newer than dir mtime
            # (WSL sometimes doesn't update dir mtime on file edits)
            max_file_mtime = max(
                (f.stat().st_mtime for f in files),
                default=stat.st_mtime,
            )
            return (max(stat.st_mtime, max_file_mtime), len(files))
        except Exception:
            return (0.0, 0)

    def _all_sessions_newest_first(self) -> list[dict[str, Any]]:
        """Every session on disk, most recently worked on first.

        Cached against a fingerprint of the sessions directory. The cache holds
        the *whole* ordered list rather than one query's answer, so a sidebar
        filtered to one repo and an admin view of everything share it instead
        of evicting each other.
        """
        fingerprint = self._list_sessions_dir_fingerprint()
        cached = self._list_cache.get("entry")
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

        rows: list[tuple[float, dict[str, Any]]] = []
        for path in self.root.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                rows.append((self._recency(data, path), self._summarize(data)))
            except Exception:
                logger.debug("Failed to read session file %s", path, exc_info=True)
                continue

        rows.sort(key=lambda row: row[0], reverse=True)
        sessions = [summary for _, summary in rows]
        self._list_cache["entry"] = (fingerprint, sessions)
        return sessions

    def list_sessions(
        self,
        repo_full_name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Sessions for the picker, newest first.

        Ordering is the contract, not a side effect: the session you just
        created — or just sent a message to — is the one you are about to open,
        so it belongs at the top. Filenames are random ids, so the previous
        filename sort was effectively arbitrary, and because ``limit`` was
        applied while scanning rather than after ordering, a new session could
        be cut from the list entirely on a machine with a long history.

        Filter and limit therefore both apply *after* ordering: the caller gets
        the newest ``limit`` sessions matching the filter, never the newest
        ``limit`` files thinned down to nothing.
        """
        sessions = self._all_sessions_newest_first()
        if repo_full_name:
            sessions = [s for s in sessions if s["repo"] == repo_full_name]
        if limit is not None and limit >= 0:
            sessions = sessions[:limit]
        return sessions

    def summary(self, session: Session) -> dict[str, Any]:
        """One session in the same shape :meth:`list_sessions` returns.

        Lets a caller that just created a session show it immediately, in the
        row format the list already uses, without a second round trip.
        """
        return self._summarize(session.to_dict())

    def invalidate_list_cache(self) -> None:
        """Explicitly invalidate the list_sessions cache.

        Called after save/delete to ensure the next list returns fresh data
        even if the filesystem mtime hasn't updated yet (WSL edge case).
        """
        self._list_cache.pop("entry", None)

    def delete(self, session_id: str) -> bool:
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
            self.invalidate_list_cache()
            return True
        return False

    def fork(self, session_id: str, at_message: int | None = None) -> Session:
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
        workspace_path: Path | None = None,
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
        workspace_path: Path | None = None,
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
