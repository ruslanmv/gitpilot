# gitpilot/agent/compaction.py
"""Keeping a long run inside its window — Batch V4-F3.

``ContextBudgetManager`` has existed since Phase 1 with one caller: an
endpoint-entry hook that condenses the *session transcript* before a request
starts. That is the wrong moment for an agentic loop, which can spend forty
iterations inside a single request and blow its window without the endpoint ever
being consulted again. This is its first runtime caller.

The order of operations matters and is not arbitrary:

1. **Stub oversized observations first.** A single 200 KB test log is usually the
   whole problem, and dropping it costs nothing a later turn needs — the full text
   is still in the journal and the ledger.
2. **Only then fold older turns** into one pinned summary, keeping the most recent
   six. Folding first would summarise away the reasoning while leaving the log
   that caused the overflow.

Two invariants this has to hold that generic condensation does not:

**File paths survive.** A small model's context is mostly the file list it was
given, and a summary that loses `src/parser.py` produces a model confidently
editing a file it can no longer name. Paths seen in the folded messages are
re-attached to the summary.

**The truth is not lost.** Compaction edits the *transcript* — what the model
sees. The ledger and the journal keep the uncompacted record, so "what did this
run actually do?" is still answerable afterwards. Anything else would make
compaction a way to lose an audit trail.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Set, Tuple, cast

from ..context_budget import (
    BudgetPolicy,
    ContextBudgetManager,
    Message,
    Role,
    estimate_tokens,
)

logger = logging.getLogger(__name__)

#: Fraction of the usable window at which compaction starts.  Matches
#: ``BudgetPolicy.condense_at_ratio``; named here because the loop's threshold is
#: over ``window − reserve`` rather than over the whole window.
COMPACT_AT_RATIO = 0.70

#: Tokens held back for the system prompt, the tool schemas and the model's own
#: reply.  A threshold computed against the raw window would start compacting only
#: once the request was already too big to send.
DEFAULT_RESERVE_TOKENS = 2_000

#: Anything that looks like a path, so it can be carried into the summary.
_PATH_RE = re.compile(r"\b[\w./\-]+\.(?:py|js|jsx|ts|tsx|go|rs|rb|java|kt|c|h|cpp|toml|cfg|ini|yaml|yml|json|md|sh)\b")

#: Marks a message the summariser has already folded, so a second pass does not
#: fold a summary into a summary.
PINNED = "pinned"


@dataclass
class CompactionReport:
    """What one compaction did."""

    before_tokens: int = 0
    after_tokens: int = 0
    folded: int = 0
    stubbed: int = 0
    paths_kept: List[str] = field(default_factory=list)

    @property
    def freed(self) -> int:
        return max(self.before_tokens - self.after_tokens, 0)

    @property
    def changed(self) -> bool:
        return self.folded > 0 or self.stubbed > 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "before_tokens": self.before_tokens,
            "after_tokens": self.after_tokens,
            "folded": self.folded,
            "stubbed": self.stubbed,
            "freed": self.freed,
        }


def usable_window(window: int, reserve: int = DEFAULT_RESERVE_TOKENS) -> int:
    """The part of the window a transcript may occupy.

    Never returns less than a floor: an 8k window with a 2k reserve leaves 6k, but
    a misconfigured 1k window must not produce a threshold of zero and compact on
    every single iteration.
    """
    return max(window - reserve, 1_000)


def threshold(window: int, reserve: int = DEFAULT_RESERVE_TOKENS) -> int:
    """Projected tokens at which a run starts compacting."""
    return int(usable_window(window, reserve) * COMPACT_AT_RATIO)


def project_tokens(messages: Sequence[Dict[str, Any]]) -> int:
    """Estimated tokens for a provider-shaped message list."""
    return sum(estimate_tokens(_text_of(message)) for message in messages)


def _text_of(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Anthropic-shaped content blocks.
        return "\n".join(
            str(block.get("text") or block.get("content") or "")
            for block in content if isinstance(block, dict)
        )
    return "" if content is None else str(content)


def paths_in(text: str) -> List[str]:
    """Every path-looking token in *text*, in first-seen order."""
    seen: Set[str] = set()
    found: List[str] = []
    for match in _PATH_RE.findall(text or ""):
        if match not in seen:
            seen.add(match)
            found.append(match)
    return found


def compact(
    messages: Sequence[Dict[str, Any]],
    *,
    window: int,
    reserve: int = DEFAULT_RESERVE_TOKENS,
    keep_recent_turns: int = 6,
    large_tool_output_tokens: int = 4_000,
) -> Tuple[List[Dict[str, Any]], CompactionReport]:
    """Bring *messages* back under the threshold.

    Returns the new list and a report. The first message is always kept whatever
    it is: it carries the task, and a run that forgot what it was asked is worse
    than one that runs out of window.
    """
    original = [dict(message) for message in messages]
    report = CompactionReport(before_tokens=project_tokens(original))

    if not original:
        report.after_tokens = 0
        return original, report

    limit = threshold(window, reserve)
    if report.before_tokens <= limit:
        report.after_tokens = report.before_tokens
        return original, report

    head, tail = original[:1], original[1:]

    # 1. Stub the oversized observations.
    stubbed = 0
    for message in tail:
        if _is_pinned(message) or message.get("role") not in ("tool", "user"):
            continue
        text = _text_of(message)
        if estimate_tokens(text) < large_tool_output_tokens:
            continue
        kept = paths_in(text)
        message["content"] = (
            "[earlier output dropped to stay inside the context window"
            + (f"; it referred to {', '.join(kept[:12])}]" if kept else "]")
        )
        message.setdefault("meta", {})
        stubbed += 1
    report.stubbed = stubbed

    if project_tokens(head + tail) <= limit:
        result = head + tail
        report.after_tokens = project_tokens(result)
        return result, report

    # 2. Fold what is left, oldest first, through the shared manager.
    keep = tail[-keep_recent_turns:] if keep_recent_turns else []
    foldable = tail[:-keep_recent_turns] if keep_recent_turns else list(tail)
    already_pinned = [message for message in foldable if _is_pinned(message)]
    foldable = [message for message in foldable if not _is_pinned(message)]

    if not foldable:
        result = head + already_pinned + keep
        report.after_tokens = project_tokens(result)
        return result, report

    paths: List[str] = []
    for message in foldable:
        for path in paths_in(_text_of(message)):
            if path not in paths:
                paths.append(path)

    manager = ContextBudgetManager(BudgetPolicy(
        max_tokens=max(limit, 1),
        keep_recent_turns=0,
        large_tool_output_tokens=large_tool_output_tokens,
    ))
    for message in foldable:
        manager.add(Message(
            role=_budget_role(message.get("role")),
            content=_text_of(message),
        ))
    summary_text = _summary_of(manager, foldable)

    if paths:
        # The invariant a generic summariser breaks: a small model's context is
        # mostly the file list, and losing it produces a model confidently editing
        # a file it can no longer name.
        summary_text += "\n\nFiles referred to above: " + ", ".join(paths[:40])
    report.paths_kept = paths[:40]

    summary = {
        "role": "assistant",
        "content": f"[Earlier steps, condensed]\n{summary_text}",
        "meta": {PINNED: "1", "summary": "1"},
    }
    report.folded = len(foldable)

    result = head + already_pinned + [summary] + keep
    report.after_tokens = project_tokens(result)
    return result, report


def _summary_of(manager: ContextBudgetManager, foldable: Sequence[Dict[str, Any]]) -> str:
    """Fold through the shared manager, falling back to its own summariser."""
    try:
        manager.condense()
        for folded in manager.messages():
            if folded.meta.get("summary") == "1":
                return folded.content.split("\n\n", 1)[-1]
    except Exception as exc:  # noqa: BLE001 - a compaction must not fail a run
        logger.warning("condensation failed (%s); using a plain recap", exc)

    lines = []
    for entry in foldable[:40]:
        first = _text_of(entry).strip().splitlines()
        if first:
            lines.append(f"- ({entry.get('role')}) {first[0][:140]}")
    return "\n".join(lines) or "_earlier steps omitted_"


def _budget_role(role: Any) -> Role:
    text = str(role or "user")
    if text in ("system", "user", "assistant", "tool"):
        return cast(Role, text)
    return "user"


def _is_pinned(message: Dict[str, Any]) -> bool:
    meta = message.get("meta")
    return bool(isinstance(meta, dict) and meta.get(PINNED))
