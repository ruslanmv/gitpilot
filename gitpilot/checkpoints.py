# gitpilot/checkpoints.py
"""Project checkpointing via a shadow git repository.

A checkpoint is a three-part snapshot taken before a mutating tool
call:

1.  A git commit in a shadow repo at
    ``~/.gitpilot/history/<workspace-hash>``.  This commit contains a
    copy of all tracked files (plus untracked, ignoring ``.git/``).
2.  The conversation transcript up to that point, serialised as JSON.
3.  A descriptor of the tool call that was about to run.

Restoring a checkpoint copies the snapshot files back into the
workspace and re-emits the saved transcript so the conversation can be
resumed deterministically.

The module is opt-in and side-effect-free until :meth:`CheckpointStore.snapshot`
is called.  It deliberately uses Python's ``git`` CLI rather than a
library to keep dependencies minimal.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

HISTORY_ROOT = Path.home() / ".gitpilot" / "history"
META_DIR = "meta"
SNAP_DIR = "snapshot"
TRANSCRIPT_FILE = "transcript.json"
DESCRIPTOR_FILE = "tool_call.json"


@dataclass
class CheckpointRecord:
    """Lightweight checkpoint summary returned to callers."""

    id: str
    timestamp: float
    tool_name: str
    target_path: Optional[str] = None
    note: str = ""
    files_changed: int = 0
    commit_sha: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ToolCallDescriptor:
    """The tool call that was about to run when the checkpoint was made."""

    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    target_path: Optional[str] = None
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CheckpointStore:
    """Manage checkpoints for a single workspace."""

    def __init__(self, workspace_path: Path, history_root: Optional[Path] = None) -> None:
        self.workspace_path = workspace_path.resolve()
        root = history_root or HISTORY_ROOT
        self.history_dir = root / _workspace_hash(self.workspace_path)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def init(self) -> None:
        self.history_dir.mkdir(parents=True, exist_ok=True)
        snap = self.history_dir / SNAP_DIR
        snap.mkdir(exist_ok=True)
        if not (snap / ".git").exists():
            self._git(snap, "init", "-q")
            self._git(snap, "config", "user.email", "checkpoints@gitpilot.local")
            self._git(snap, "config", "user.name", "GitPilot Checkpoints")
        (self.history_dir / META_DIR).mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # Snapshot / restore
    # ------------------------------------------------------------------
    def snapshot(
        self,
        descriptor: ToolCallDescriptor,
        transcript: Optional[List[Dict[str, Any]]] = None,
    ) -> CheckpointRecord:
        """Capture the workspace + transcript + tool call descriptor."""
        self.init()
        snap = self.history_dir / SNAP_DIR
        files_changed = _mirror_workspace(self.workspace_path, snap)
        ts = time.time()
        ckpt_id = _format_id(ts, descriptor)
        meta_dir = self.history_dir / META_DIR / ckpt_id
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / TRANSCRIPT_FILE).write_text(
            json.dumps(transcript or [], indent=2), encoding="utf-8"
        )
        (meta_dir / DESCRIPTOR_FILE).write_text(
            json.dumps(descriptor.to_dict(), indent=2), encoding="utf-8"
        )
        commit_sha: Optional[str] = None
        try:
            self._git(snap, "add", "-A")
            res = self._git(snap, "commit", "-q", "--allow-empty", "-m", ckpt_id, capture=True)
            commit_sha = self._git(snap, "rev-parse", "HEAD", capture=True).strip() or None
            _ = res
        except Exception as e:
            logger.warning("checkpoint commit failed: %s", e)
        record = CheckpointRecord(
            id=ckpt_id,
            timestamp=ts,
            tool_name=descriptor.name,
            target_path=descriptor.target_path,
            note=descriptor.note,
            files_changed=files_changed,
            commit_sha=commit_sha,
        )
        (meta_dir / "record.json").write_text(
            json.dumps(record.to_dict(), indent=2), encoding="utf-8"
        )
        return record

    def list(self) -> List[CheckpointRecord]:
        out: List[CheckpointRecord] = []
        meta_root = self.history_dir / META_DIR
        if not meta_root.exists():
            return out
        for child in sorted(meta_root.iterdir(), reverse=True):
            record_file = child / "record.json"
            if not record_file.exists():
                continue
            try:
                data = json.loads(record_file.read_text(encoding="utf-8"))
                out.append(CheckpointRecord(**data))
            except Exception as e:
                logger.debug("could not load checkpoint %s: %s", child, e)
        return out

    def restore(self, checkpoint_id: str) -> Dict[str, Any]:
        """Restore files for ``checkpoint_id`` and return the transcript."""
        meta_dir = self.history_dir / META_DIR / checkpoint_id
        if not meta_dir.exists():
            raise FileNotFoundError(f"unknown checkpoint: {checkpoint_id}")
        snap = self.history_dir / SNAP_DIR
        record_path = meta_dir / "record.json"
        if not record_path.exists():
            raise FileNotFoundError("missing record.json")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        sha = record.get("commit_sha")
        if sha:
            try:
                self._git(snap, "checkout", "-q", sha, "--", ".")
            except Exception as e:
                logger.warning("checkout of %s failed: %s", sha, e)
        # Mirror snapshot files back into the workspace (additive only —
        # we never delete files the user may have created since).
        _restore_workspace(snap, self.workspace_path)
        transcript_path = meta_dir / TRANSCRIPT_FILE
        descriptor_path = meta_dir / DESCRIPTOR_FILE
        return {
            "record": record,
            "transcript": json.loads(transcript_path.read_text(encoding="utf-8"))
            if transcript_path.exists() else [],
            "tool_call": json.loads(descriptor_path.read_text(encoding="utf-8"))
            if descriptor_path.exists() else {},
        }

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    def prune(self, keep_last: int = 50) -> int:
        records = self.list()
        if len(records) <= keep_last:
            return 0
        removed = 0
        for record in records[keep_last:]:
            target = self.history_dir / META_DIR / record.id
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
                removed += 1
        return removed

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _git(self, cwd: Path, *args: str, capture: bool = False) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"git {args[0]} failed")
        return proc.stdout if capture else ""


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

_DEFAULT_IGNORES = {".git", ".gitpilot", "__pycache__", "node_modules", ".venv", ".tox"}


def _workspace_hash(workspace: Path) -> str:
    return hashlib.sha1(str(workspace).encode("utf-8")).hexdigest()[:12]


def _format_id(ts: float, descriptor: ToolCallDescriptor) -> str:
    iso = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(ts))
    tool = descriptor.name.replace("/", "_")
    suffix = f"-{Path(descriptor.target_path).name}" if descriptor.target_path else ""
    return f"{iso}-{tool}{suffix}"[:120]


def _mirror_workspace(src: Path, dst: Path) -> int:
    """Copy ``src`` into ``dst`` (overwriting), skipping ignored paths."""
    count = 0
    # Wipe existing snapshot content (but keep its .git/).
    for entry in list(dst.iterdir()):
        if entry.name == ".git":
            continue
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            try:
                entry.unlink()
            except OSError:
                pass
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if any(part in _DEFAULT_IGNORES for part in rel.parts):
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            count += 1
        except OSError:
            continue
    return count


def _restore_workspace(src: Path, dst: Path) -> None:
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if rel.parts and rel.parts[0] == ".git":
            continue
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        except OSError:
            continue
