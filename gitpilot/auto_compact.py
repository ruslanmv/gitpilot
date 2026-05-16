"""Auto-compaction of chat session history (Batch B3).

When a session's persisted conversation crosses 70 % of the active
model's context window, we fold the older non-essential messages into
a single summary entry — same strategy Claude Code, Cursor and
Continue use to keep sessions usable across many turns without
silently truncating mid-stream.

Design notes:

* **Pure Python.**  We reuse :mod:`gitpilot.context_budget`'s
  deterministic ``_default_summariser`` so compaction never depends
  on a live LLM call.  Production deployments can later inject a
  smarter summariser without changing this module's interface.
* **Append-only audit trail.**  Each compaction also lands as a
  ``kind="compact"`` Task in the right-sidebar trace so the user can
  see "Conversation summarised: 24 messages → 1 summary".
* **Idempotent.**  We tag the summary message with
  ``metadata["compacted"] = "1"`` so a no-op pass over already-compact
  history doesn't repeatedly fold the same content.
* **Best-effort.**  Failure to load or save the session must never
  block the user-facing endpoint — log and proceed.  The chat
  continues to work; it just won't shrink this turn.

Wired in at the API boundary in :mod:`gitpilot.api` (``/api/chat/plan``
+ ``/api/chat/execute``), so agentic.py is untouched.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from . import flags
from .context_budget import (
    BudgetPolicy,
    Message as BudgetMessage,
    _default_summariser,
    estimate_tokens,
)
from .session import Message as SessionMessage, Session, SessionManager

logger = logging.getLogger(__name__)

FLAG_AUTO_COMPACT = "auto_compact"

# Tunable knobs.  Centralised so future tuning (or per-provider
# overrides) doesn't require code changes in two places.
DEFAULT_CONDENSE_AT_RATIO = 0.70   # fire at 70 % of window
DEFAULT_KEEP_RECENT_TURNS = 6      # last N messages always preserved
DEFAULT_RESERVED_RESPONSE = 4_096  # mirror context_meter constant
COMPACTED_FLAG = "compacted"
SUMMARY_LABEL = "Conversation summary (older turns condensed)"


@dataclass
class CompactionReport:
    """Returned by :func:`maybe_compact_session` so the caller can log
    a Task entry with concrete before/after numbers."""
    compacted: bool = False
    before_tokens: int = 0
    after_tokens: int = 0
    messages_folded: int = 0
    reason: Optional[str] = None      # human-readable explanation


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------

def _budget_messages_from_session(session: Session) -> list[BudgetMessage]:
    """Bridge SessionMessage → BudgetMessage."""
    out: list[BudgetMessage] = []
    for m in session.messages:
        role = m.role if m.role in ("user", "assistant", "system", "tool") else "user"
        importance = "pinned" if (m.metadata or {}).get(COMPACTED_FLAG) == "1" else "normal"
        # Best-effort role narrowing — BudgetMessage role is a Literal.
        out.append(
            BudgetMessage(
                role=role,  # type: ignore[arg-type]
                content=m.content or "",
                importance=importance,  # type: ignore[arg-type]
            )
        )
    return out


def _session_total_tokens(session: Session) -> int:
    return sum(estimate_tokens(m.content or "") for m in session.messages)


# ----------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------

def maybe_compact_session(
    session_mgr: SessionManager,
    session_id: Optional[str],
    *,
    context_window: int,
    reserved_response: int = DEFAULT_RESERVED_RESPONSE,
    condense_at_ratio: float = DEFAULT_CONDENSE_AT_RATIO,
    keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS,
) -> CompactionReport:
    """Condense the session's history if it's crossed the threshold.

    Returns a :class:`CompactionReport` so the caller can record a
    Task entry with the concrete numbers.  A no-op report
    (``compacted=False``) is returned silently when:

    * the feature flag is off,
    * no session id was supplied,
    * the session can't be loaded,
    * we're below the threshold,
    * there are not enough non-recent messages to fold.
    """
    if not flags.is_on(FLAG_AUTO_COMPACT, default=True):
        return CompactionReport(reason="flag off")
    if not session_id:
        return CompactionReport(reason="no session id")
    if context_window <= 0:
        return CompactionReport(reason="unknown context window")

    try:
        session = session_mgr.load(session_id)
    except Exception as exc:
        logger.debug("[compact] session %s not loadable: %s", session_id, exc)
        return CompactionReport(reason="session not loadable")

    before = _session_total_tokens(session)
    # The user's *effective* budget excludes the reserved response
    # headroom — that's the budget we actually need to keep below.
    effective_window = max(0, context_window - reserved_response)
    threshold = int(effective_window * condense_at_ratio)
    if before < threshold:
        return CompactionReport(
            compacted=False,
            before_tokens=before,
            after_tokens=before,
            reason="below threshold",
        )

    # Fold using the existing deterministic summariser.  We keep:
    #   - any message already marked compacted (pinned)
    #   - the last ``keep_recent_turns`` messages
    # Everything else gets summarised into one system message.
    msgs = session.messages
    if len(msgs) <= keep_recent_turns + 1:
        return CompactionReport(
            compacted=False,
            before_tokens=before,
            after_tokens=before,
            reason="not enough history to fold",
        )

    pinned = [m for m in msgs if (m.metadata or {}).get(COMPACTED_FLAG) == "1"]
    rest = [m for m in msgs if (m.metadata or {}).get(COMPACTED_FLAG) != "1"]
    keep_n = max(0, keep_recent_turns)
    foldable = rest[:-keep_n] if keep_n else rest
    kept = rest[-keep_n:] if keep_n else []

    if not foldable:
        return CompactionReport(
            compacted=False,
            before_tokens=before,
            after_tokens=before,
            reason="nothing foldable",
        )

    # Use BudgetMessage objects for the summariser — the existing
    # summariser was written against that shape.
    budget_foldable = [
        BudgetMessage(
            role=(m.role if m.role in ("user", "assistant", "system", "tool") else "user"),  # type: ignore[arg-type]
            content=m.content or "",
        )
        for m in foldable
    ]
    summary_body = _default_summariser(budget_foldable)
    summary_msg = SessionMessage(
        role="system",
        content=f"## {SUMMARY_LABEL}\n\n{summary_body}",
        metadata={COMPACTED_FLAG: "1"},
    )

    session.messages = pinned + [summary_msg] + kept
    after = _session_total_tokens(session)

    try:
        session_mgr.save(session)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[compact] could not save session %s: %s", session_id, exc)
        return CompactionReport(
            compacted=False,
            before_tokens=before,
            after_tokens=after,
            reason=f"save failed: {exc}",
        )

    return CompactionReport(
        compacted=True,
        before_tokens=before,
        after_tokens=after,
        messages_folded=len(foldable),
        reason=f"folded {len(foldable)} older messages",
    )


__all__ = [
    "FLAG_AUTO_COMPACT",
    "CompactionReport",
    "DEFAULT_CONDENSE_AT_RATIO",
    "DEFAULT_KEEP_RECENT_TURNS",
    "DEFAULT_RESERVED_RESPONSE",
    "SUMMARY_LABEL",
    "maybe_compact_session",
]
