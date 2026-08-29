# gitpilot/agent/contracts.py
"""Turn-level contracts shared by the dialects and the loop — Batch V4-B3.

These live in their own module because both directions need them: the dialect
adapters produce an :class:`AgentTurn`, and the loop (Batch V4-C2) consumes one.
Putting them in either would make the other import a module it has no business
depending on.

:class:`Finality` is the piece worth reading twice.  A turn is not simply "final
or not": the LITE dialect emits act-turns whose finality depends on whether the
actions it requested actually applied, which is only knowable *after* the loop
has dispatched them.  Hence three states rather than a boolean, and
``adapter.conclude()`` to resolve the third.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Dialect(str, Enum):
    """How a model is spoken to.

    One loop, three renderings — the property that makes the same engine work
    on a frontier API and on a 1.5B model running locally.
    """

    NATIVE = "native"          # provider tool-calling APIs
    REACT_TEXT = "react_text"  # Thought:/Action:/Action Input: grammar we parse
    LITE = "lite"              # constrained line protocol with pre-fetched context


class Finality(str, Enum):
    """Whether the loop may return after this turn."""

    CONTINUE = "continue"
    FINAL = "final"
    #: Done *if* this turn's tool calls apply cleanly.  Resolved by
    #: ``adapter.conclude(turn, results)`` after dispatch (LITE act-turns).
    FINAL_AFTER_TOOLS = "final_after_tools"


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class AgentTurn:
    """One model turn, normalised across dialects.

    ``text`` is what the user should see; reasoning-model scratchpads are
    stripped before they reach here (see :mod:`gitpilot.reasoning_normalizer`),
    which is also why nothing downstream can accidentally persist them.
    """

    text: str = ""
    tool_calls: List[Any] = field(default_factory=list)   # list[ToolCall]
    finality: Finality = Finality.CONTINUE
    usage: Optional[TokenUsage] = None
    #: Dialect-specific bookkeeping the loop passes back to ``conclude()``.
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_final(self) -> bool:
        """Whether the loop can stop without dispatching anything.

        Deliberately not true for ``FINAL_AFTER_TOOLS``: those turns still have
        calls to run.
        """
        return self.finality is Finality.FINAL
