# gitpilot/context_budget.py
"""Conversation context budgeting and auto-condensation.

Strategy (additive — opt-in via :class:`BudgetPolicy` or the global default):

* Maintain a running token total per session.
* When the total crosses ``condense_at`` (default 70 % of ``max_tokens``)
  fold the oldest non-essential messages into a single summary block,
  preserving:
    - system instructions
    - tool definitions
    - the AGENTS.md block
    - the last N turns
* Drop oversize tool outputs first — they're the cheapest to lose and the
  costliest to keep.
* Provide a stable :class:`ContextStats` snapshot that the API surfaces as
  ``{prompt_tokens, max_tokens, ratio}`` so the web UI can render a live
  token counter.

The token estimator is best-effort: it uses ``tiktoken`` when available
and falls back to a ``len(text) / 4`` heuristic.  Counts do not need to
be exact — they only steer condensation timing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

Role = Literal["system", "user", "assistant", "tool"]
Importance = Literal["pinned", "normal", "drop-first"]


# ----------------------------------------------------------------------
# Token estimation
# ----------------------------------------------------------------------

_TIKTOKEN: Any = None
try:  # pragma: no cover - depends on environment
    import tiktoken

    _TIKTOKEN = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - tiktoken optional
    _TIKTOKEN = None


def estimate_tokens(text: str) -> int:
    """Return an estimated token count for ``text``."""
    if not text:
        return 0
    if _TIKTOKEN is not None:
        try:
            return len(_TIKTOKEN.encode(text))
        except Exception:
            pass
    # Heuristic fallback — close enough to steer condensation thresholds.
    return max(1, len(text) // 4)


# ----------------------------------------------------------------------
# Data model
# ----------------------------------------------------------------------

@dataclass
class Message:
    """One conversation turn or fragment."""

    role: Role
    content: str
    importance: Importance = "normal"
    tokens: int = 0
    meta: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tokens:
            self.tokens = estimate_tokens(self.content)


@dataclass
class BudgetPolicy:
    """Knobs for context budgeting."""

    max_tokens: int = 200_000
    condense_at_ratio: float = 0.70
    keep_recent_turns: int = 6
    large_tool_output_tokens: int = 4_000
    summary_label: str = "Conversation summary (older turns condensed)"

    @property
    def condense_at(self) -> int:
        return int(self.max_tokens * self.condense_at_ratio)


@dataclass
class ContextStats:
    """Snapshot suitable for surfacing in the chat UI."""

    prompt_tokens: int
    max_tokens: int
    ratio: float
    condensations: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "max_tokens": self.max_tokens,
            "ratio": round(self.ratio, 4),
            "condensations": self.condensations,
        }


# ----------------------------------------------------------------------
# Budget manager
# ----------------------------------------------------------------------

SummariseFn = Callable[[List[Message]], str]


def _default_summariser(messages: List[Message]) -> str:
    """Deterministic, dependency-free fallback summariser.

    Produces a compact bulleted recap.  Production deployments can pass a
    smarter summariser that delegates to an LLM.
    """
    bullets: List[str] = []
    for m in messages:
        first_line = m.content.strip().splitlines()[0] if m.content.strip() else ""
        if not first_line:
            continue
        truncated = first_line[:140] + ("…" if len(first_line) > 140 else "")
        bullets.append(f"- ({m.role}) {truncated}")
        if len(bullets) >= 40:
            break
    return "\n".join(bullets) or "_no older content to summarise_"


class ContextBudgetManager:
    """Track token usage and condense history when the budget is tight."""

    def __init__(
        self,
        policy: Optional[BudgetPolicy] = None,
        summariser: Optional[SummariseFn] = None,
    ) -> None:
        self.policy = policy or BudgetPolicy()
        self._summariser = summariser or _default_summariser
        self._messages: List[Message] = []
        self._condensations = 0

    # ------------------------------------------------------------------
    # Mutation API
    # ------------------------------------------------------------------
    def add(self, message: Message) -> None:
        self._messages.append(message)

    def add_text(self, role: Role, content: str, **kwargs: Any) -> None:
        self.add(Message(role=role, content=content, **kwargs))

    def extend(self, messages: List[Message]) -> None:
        self._messages.extend(messages)

    def clear(self) -> None:
        self._messages.clear()
        self._condensations = 0

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------
    def total_tokens(self) -> int:
        return sum(m.tokens for m in self._messages)

    def stats(self) -> ContextStats:
        total = self.total_tokens()
        return ContextStats(
            prompt_tokens=total,
            max_tokens=self.policy.max_tokens,
            ratio=total / self.policy.max_tokens if self.policy.max_tokens else 0.0,
            condensations=self._condensations,
        )

    def messages(self) -> List[Message]:
        return list(self._messages)

    # ------------------------------------------------------------------
    # Condensation
    # ------------------------------------------------------------------
    def needs_condense(self) -> bool:
        return self.total_tokens() >= self.policy.condense_at

    def condense(self) -> int:
        """Fold older non-essential messages into a single summary entry.

        Returns the number of tokens removed.  A no-op when nothing
        eligible is found, which leaves the running total unchanged.
        """
        if not self._messages:
            return 0

        before = self.total_tokens()

        # 1. Drop oversize tool outputs first.
        for m in self._messages:
            if (
                m.role == "tool"
                and m.importance != "pinned"
                and m.tokens >= self.policy.large_tool_output_tokens
            ):
                replacement = "_tool output dropped to free context budget_"
                m.content = replacement
                m.tokens = estimate_tokens(replacement)
                m.meta = {**m.meta, "condensed": "1"}

        if self.total_tokens() < self.policy.condense_at:
            self._condensations += 1
            return before - self.total_tokens()

        # 2. Split keep-recent vs. condensable.
        pinned: List[Message] = [m for m in self._messages if m.importance == "pinned"]
        rest: List[Message] = [m for m in self._messages if m.importance != "pinned"]
        keep_n = max(0, self.policy.keep_recent_turns)
        condensable = rest[:-keep_n] if keep_n else rest
        kept_recent = rest[-keep_n:] if keep_n else []

        if not condensable:
            self._condensations += 1
            return before - self.total_tokens()

        summary_text = self._summariser(condensable)
        summary_msg = Message(
            role="system",
            content=f"## {self.policy.summary_label}\n\n{summary_text}",
            importance="pinned",
            meta={"summary": "1"},
        )

        self._messages = pinned + [summary_msg] + kept_recent
        self._condensations += 1
        return before - self.total_tokens()

    def maybe_condense(self) -> int:
        """Condense iff the budget is over the threshold."""
        if self.needs_condense():
            return self.condense()
        return 0
