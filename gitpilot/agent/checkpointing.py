# gitpilot/agent/checkpointing.py
"""Snapshots the loop owns — Batch V4-D4.

The checkpoint used to hang off ``ApprovalGate.on_checkpoint``, which had two
problems. The gate has never had a caller, so no checkpoint was ever taken from
that path; and had it acquired one, the ``ask`` arm would have snapshotted once
on approval and the tool would have run under a different mode's snapshot too.

So the loop owns it, firing from ``Decision.requires_checkpoint`` before the call
executes, in every permission mode, exactly once (§9.5). Three things the old
path never recorded are recorded now:

* **``ToolCallDescriptor.arguments``.** The field has existed since checkpoints
  landed and was left empty by every caller — so "what was this snapshot for?"
  could only be answered by a tool name and a path.
* **``run_id`` and the journal ``seq``.** A checkpoint that cannot say where in
  the run it sits cannot be used to rewind the *run*, only the files.
* **Pruning.** ``CheckpointStore.prune`` has never had a production caller; a
  loop that snapshots before every mutating call would otherwise grow the shadow
  repository without bound.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: Snapshots retained per workspace.  The loop snapshots far more often than a
#: human clicks "checkpoint", so this is what stops the shadow repo from growing
#: without limit.  Pruning happens after a snapshot, never before: losing history
#: to make room for a snapshot that then fails would be the worst of both.
KEEP_LAST_CHECKPOINTS = 50

#: Argument names that name the thing being changed, best first.
_TARGET_KEYS = ("path", "file_path", "filename", "file", "target", "dest", "destination")


def target_of(arguments: Dict[str, Any]) -> Optional[str]:
    """The path a call is about to change, if it names one."""
    for key in _TARGET_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class SessionCheckpointer:
    """Snapshots a session's workspace before a mutating call.

    Satisfies the ``checkpointer`` seam the loop has had since Batch V4-C2:
    ``async create(call, ctx) -> checkpoint_id | None``.  Returning ``None``
    means "nothing was snapshotted", which the loop treats as a non-event rather
    than a failure — a session with no workspace has nothing to restore.
    """

    def __init__(
        self,
        *,
        session_id: str,
        session_manager: Any,
        workspace: Optional[Path] = None,
        keep_last: int = KEEP_LAST_CHECKPOINTS,
    ) -> None:
        self.session_id = session_id
        self.session_manager = session_manager
        self.workspace = Path(workspace) if workspace else None
        self.keep_last = keep_last

    @property
    def enabled(self) -> bool:
        return self.workspace is not None and self.session_manager is not None

    async def create(self, call: Any, ctx: Any) -> Optional[str]:
        if not self.enabled:
            return None

        arguments = dict(getattr(call, "arguments", {}) or {})
        target = target_of(arguments)
        description = f"Before {call.tool}" + (f" · {target}" if target else "")

        # Reloaded every time: the conversation has moved on since the run
        # started, and a checkpoint's value is the transcript it carries.
        session = self._current_session()
        if session is None:
            return None

        checkpoint = self.session_manager.create_checkpoint(
            session,
            workspace_path=self.workspace,
            description=description,
            tool_name=call.tool,
            target_path=target,
            arguments=arguments,
            run_id=getattr(ctx, "run_id", "") or "",
            journal_seq=self._journal_seq(ctx),
        )
        self._prune()
        return getattr(checkpoint, "store_id", None) or getattr(checkpoint, "id", None)

    # -- internals ---------------------------------------------------------

    def _current_session(self) -> Any:
        try:
            return self.session_manager.load(self.session_id)
        except FileNotFoundError:
            logger.debug("session %s has gone; not checkpointing", self.session_id)
            return None
        except Exception as exc:  # noqa: BLE001 - a snapshot must not break a run
            logger.warning("could not load session %s to checkpoint: %s", self.session_id, exc)
            return None

    def _journal_seq(self, ctx: Any) -> int:
        journal = getattr(ctx, "journal", None)
        return int(getattr(journal, "seq", 0) or 0)

    def _prune(self) -> None:
        """Keep the shadow repository bounded.  Failure here is cosmetic."""
        workspace = self.workspace
        if workspace is None:
            return
        try:
            from ..checkpoints import CheckpointStore

            store = CheckpointStore(
                workspace,
                history_root=getattr(self.session_manager, "history_root", None),
            )
            removed = store.prune(keep_last=self.keep_last)
            if removed:
                logger.debug("pruned %d old checkpoint(s)", removed)
        except Exception as exc:  # noqa: BLE001
            logger.debug("checkpoint prune skipped: %s", exc)
