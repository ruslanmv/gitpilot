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

_DEFAULT_IGNORES = {
    ".git", ".gitpilot", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", "dist", "build", "out", ".next", "target", ".mypy_cache",
    ".pytest_cache", ".ruff_cache",
}

#: Snapshots copy the tree, so a checkpoint is only cheap while the tree is
#: small. Past this the store degrades to transcript-only rather than
#: quietly writing hundreds of megabytes on every tool call.
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024


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
    #: False when the workspace was too large to copy, so restore() will
    #: return the transcript but leave the files alone.
    has_files: bool = True

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

    def __init__(
        self,
        workspace_path: Path,
        history_root: Optional[Path] = None,
        max_bytes: int = MAX_SNAPSHOT_BYTES,
    ) -> None:
        self.workspace_path = workspace_path.resolve()
        root = history_root or HISTORY_ROOT
        self.history_dir = root / _workspace_hash(self.workspace_path)
        self.max_bytes = max_bytes

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
        """Capture the workspace + transcript + tool call descriptor.

        A workspace over :data:`MAX_SNAPSHOT_BYTES` records the transcript and
        the tool call but not the files, and says so on the record. Half a
        checkpoint that is honest about being half beats a UI that offers a
        rewind it cannot perform.
        """
        self.init()
        snap = self.history_dir / SNAP_DIR

        size = measure_workspace(self.workspace_path, self.max_bytes)
        files_only = size > self.max_bytes
        if files_only:
            logger.warning(
                "workspace is %.0f MB, over the %.0f MB checkpoint limit; "
                "snapshotting the transcript only",
                size / 1e6,
                self.max_bytes / 1e6,
            )
            files_changed = 0
        else:
            files_changed = _mirror_workspace(self.workspace_path, snap)
        ts = time.time()
        ckpt_id = _format_id(ts, descriptor)
        meta_root = self.history_dir / META_DIR
        # Overwriting an existing checkpoint would destroy the very rewind the
        # user is most likely to reach for, so collisions get a suffix.
        if (meta_root / ckpt_id).exists():
            for n in range(2, 1000):
                if not (meta_root / f"{ckpt_id}~{n}").exists():
                    ckpt_id = f"{ckpt_id}~{n}"
                    break
        meta_dir = meta_root / ckpt_id
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
            has_files=not files_only,
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
                known = {f for f in CheckpointRecord.__dataclass_fields__}
                out.append(CheckpointRecord(**{k: v for k, v in data.items() if k in known}))
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
        if not record.get("has_files", True):
            return {
                "record": record,
                "removed": [],
                "files_restored": False,
                "transcript": _read_json(meta_dir / TRANSCRIPT_FILE, []),
                "tool_call": _read_json(meta_dir / DESCRIPTOR_FILE, {}),
            }

        sha = record.get("commit_sha")
        if sha:
            try:
                # `checkout -- .` leaves files the commit does not contain, so
                # reset the whole tree: the snapshot has to be the snapshot.
                self._git(snap, "reset", "-q", "--hard", sha)
                self._git(snap, "clean", "-qfd")
            except Exception as e:
                logger.warning("restore of %s failed: %s", sha, e)
        removed = _restore_workspace(snap, self.workspace_path)
        transcript_path = meta_dir / TRANSCRIPT_FILE
        descriptor_path = meta_dir / DESCRIPTOR_FILE
        return {
            "record": record,
            "removed": removed,
            "files_restored": True,
            "transcript": _read_json(transcript_path, []),
            "tool_call": _read_json(descriptor_path, {}),
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

def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def _is_ignored(rel: Path) -> bool:
    return any(part in _DEFAULT_IGNORES for part in rel.parts)


def _workspace_hash(workspace: Path) -> str:
    return hashlib.sha1(str(workspace).encode("utf-8")).hexdigest()[:12]


def measure_workspace(workspace: Path, limit: int = MAX_SNAPSHOT_BYTES) -> int:
    """Total size of the snapshottable tree, stopping once ``limit`` is passed.

    Short-circuits so that deciding *not* to snapshot a huge repository does
    not itself cost a full walk of it.
    """
    total = 0
    for path in workspace.rglob("*"):
        rel = path.relative_to(workspace)
        if _is_ignored(rel) or not path.is_file():
            continue
        try:
            total += path.stat().st_size
        except OSError:
            continue
        if total > limit:
            return total
    return total


def _format_id(ts: float, descriptor: ToolCallDescriptor) -> str:
    """A sortable, unique id for one checkpoint.

    Milliseconds matter: an agent can easily make two edits inside the same
    second, and a second-resolution id made the two checkpoints collide — the
    later snapshot overwrote the earlier one's transcript and record, so the
    rewind the user actually wanted had quietly ceased to exist.
    """
    iso = time.strftime("%Y%m%dT%H%M%S", time.gmtime(ts))
    millis = int((ts % 1) * 1000)
    tool = descriptor.name.replace("/", "_")
    suffix = f"-{Path(descriptor.target_path).name}" if descriptor.target_path else ""
    return f"{iso}.{millis:03d}Z-{tool}{suffix}"[:120]


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
        if _is_ignored(rel):
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


def _restore_workspace(src: Path, dst: Path) -> List[str]:
    """Make ``dst`` look like ``src`` again. Returns the paths deleted.

    Restoring used to be additive, which meant a checkpoint could not undo the
    one thing users most want undone: a file the agent should never have
    created. Anything the snapshot does not contain is now removed — with the
    same ignore list the snapshot was taken under, so ``.git``, virtualenvs
    and dependency directories are never touched.
    """
    kept: set[Path] = set()

    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if _is_ignored(rel):
            continue
        kept.add(rel)
        target = dst / rel
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        except OSError:
            continue

    removed: List[str] = []
    for path in sorted(dst.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        rel = path.relative_to(dst)
        if _is_ignored(rel) or rel in kept:
            continue
        try:
            if path.is_dir():
                # Only an empty directory: its files were removed above, and a
                # non-empty one means something ignored lives inside it.
                path.rmdir()
            else:
                path.unlink()
            removed.append(str(rel))
        except OSError:
            continue

    return removed
