"""Right-sidebar Tasks panel — recorder helpers.

Implements the smallest possible contract that lets the chat UI trace
every user-facing AI invocation (Plan, Execute) the way Claude Code's
right-pane tasks list does.

Design notes:

* **Append-only.**  Once a task lands in ``Session.tasks`` it is
  mutated exactly once (on completion) and never edited again.  This
  matches the audit-trail philosophy of the rest of the session
  format.
* **Endpoint-level wrap, not deep in agentic.py.**  ``begin_task`` is
  called at the start of an endpoint, ``finish_task`` in its finally —
  the agent stack itself is untouched, so the cut is trivially
  revertible.
* **Best-effort persistence.**  A failure to write the task back to
  disk must never block the user-facing endpoint — the agent already
  ran, the user already has their result.  We log and move on.
* **Flag-gated.**  When ``tasks_sidebar`` is off, ``begin_task``
  returns ``None`` and ``finish_task`` is a no-op so the backend is
  byte-identical to today.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from time import perf_counter
from typing import Optional

from . import flags
from .session import SessionManager, Task

logger = logging.getLogger(__name__)

FLAG_TASKS_SIDEBAR = "tasks_sidebar"


def begin_task(
    session_mgr: SessionManager,
    session_id: Optional[str],
    *,
    kind: str,
    title: str,
) -> Optional[Task]:
    """Append a ``running`` Task to the session and persist.

    Returns the in-flight Task so the caller can pass it back to
    :func:`finish_task` later.  Returns ``None`` when recording is
    disabled or the session can't be loaded — callers must tolerate
    the absent task gracefully.
    """
    if not flags.is_on(FLAG_TASKS_SIDEBAR, default=True):
        return None
    if not session_id:
        return None
    try:
        session = session_mgr.load(session_id)
    except Exception as exc:
        logger.debug("[tasks] session %s not loadable: %s", session_id, exc)
        return None

    task = Task(kind=kind, title=title, status="running")
    # Attach a perf-counter start tick on the in-memory object so
    # finish_task can compute duration_ms without scanning timestamps.
    task.metadata["_perf_t0"] = perf_counter()
    session.tasks.append(task)
    try:
        session_mgr.save(session)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[tasks] could not persist initial task: %s", exc)
    return task


def finish_task(
    session_mgr: SessionManager,
    session_id: Optional[str],
    task: Optional[Task],
    *,
    status: str = "completed",
    error: Optional[str] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
) -> None:
    """Mark a previously-begun task as completed/failed and persist.

    Idempotent: safe to call with ``task=None`` (when ``begin_task``
    returned None because the flag was off or the session was missing).
    """
    if task is None or not session_id:
        return
    t0 = task.metadata.pop("_perf_t0", None)
    if isinstance(t0, (int, float)):
        task.duration_ms = int((perf_counter() - t0) * 1000)
    task.status = status
    if error is not None:
        task.error = error[:500]
    if prompt_tokens is not None:
        task.prompt_tokens = prompt_tokens
    if completion_tokens is not None:
        task.completion_tokens = completion_tokens
    task.completed_at = datetime.now(UTC).isoformat()

    # Reload the session before saving so we don't clobber any writes
    # the agent stack made in the meantime (e.g. branch persistence on
    # execute) — the running-task entry was already there, we only need
    # to swap its final state in.
    try:
        fresh = session_mgr.load(session_id)
        for i, existing in enumerate(fresh.tasks):
            if existing.id == task.id:
                fresh.tasks[i] = task
                break
        else:
            fresh.tasks.append(task)
        session_mgr.save(fresh)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[tasks] could not persist completed task: %s", exc)
