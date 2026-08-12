# gitpilot/agent/delegation.py
"""Subagents that actually run — Batch V4-E2.

T2's flow graph has always drawn `Task(...)` edges to an Explorer, a Reviewer and
a Researcher. Those edges spawned nothing: the graph was a picture of an intended
design, rendered by the Agent Workflow view as though it were behaviour. This
module is the behaviour.

A delegation is a **child** :class:`~gitpilot.agent.loop.AgentLoop` with four
properties that make it safe to hand a model:

**A child can never exceed its parent.** The mask is `parent ∩ template`, so a
read-only parent cannot delegate a write. Intersection rather than replacement is
the whole safety argument — a template that granted `fs.write` would otherwise be
an escalation channel reachable by asking nicely.

**Depth is bounded.** `max_depth` comes from the `agent.delegate` qualifier and
defaults to 1, so a child cannot delegate further unless the qualifier says it
may. Without this, "delegate to an explorer" is a fork bomb one prompt away.

**The return is structured.** A subagent returns
`{summary, files, findings, recommendations, status}` and nothing else. Free prose
back into the parent's context is how a delegation costs more than doing the work
inline: the parent pays for the child's whole narration. Returns above the
parent's observation budget are compressed with the path-preserving compressor
from `explorer_summary`, because the file paths are the part the parent needs and
the prose is the part it does not.

**Its journal is its own.** `runs/<run>/sub/<n>.jsonl`, so the parent's journal
stays readable and the safety invariants can be asserted per-run rather than over
an interleaved log. Events carry `parent_call_id` so a UI can nest them.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..toolkit.registry import (
    Risk,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)

logger = logging.getLogger(__name__)

#: How deep a delegation chain may go when the qualifier does not say.
DEFAULT_MAX_DEPTH = 1

#: A child's iteration cap when the caller does not name one.  Lower than a
#: parent's on purpose: a subagent with an open-ended budget is a second run, not
#: a delegation.
DEFAULT_CHILD_ITERATIONS = 12

#: Characters of structured return the parent will accept before compression.
#: Roughly 3k tokens — enough for a real finding list, small enough that three
#: delegations do not fill the parent's window.
MAX_RETURN_CHARS = 12_000


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubagentTemplate:
    """What one kind of subagent may do, and how it is briefed.

    ``capabilities`` is an *upper bound* intersected with the parent's mask, never
    a grant. A template naming ``fs.write`` on a read-only parent still cannot
    write.
    """

    name: str
    title: str
    purpose: str
    capabilities: Dict[str, Any]
    role: str
    max_iterations: int = DEFAULT_CHILD_ITERATIONS
    #: Read-only subagents are the common case and the safe default.
    read_only: bool = True

    def brief(self, task: str, expected: str = "") -> str:
        """The child's task message."""
        parts = [task.strip()]
        if expected:
            parts.append(f"What the caller needs back: {expected.strip()}")
        parts.append(
            "Return your findings through the structured fields. Keep the summary "
            "to what the caller asked for — they cannot see your working."
        )
        return "\n\n".join(parts)


_READ_CAPABILITIES = {
    "fs.read": True, "fs.list": True, "fs.glob": True, "fs.grep": True,
    "git.read": True, "test.detect": True,
}

TEMPLATES: Dict[str, SubagentTemplate] = {
    "explorer": SubagentTemplate(
        name="explorer",
        title="Repository Explorer",
        purpose=(
            "Map an unfamiliar part of the codebase: which files matter, how they "
            "relate, what the conventions are."
        ),
        capabilities=dict(_READ_CAPABILITIES),
        role=(
            "You are exploring a repository for someone else. Find the files that "
            "matter and say how they fit together. Name real paths — a summary "
            "without paths cannot be acted on. Do not change anything."
        ),
        max_iterations=14,
    ),
    "reviewer": SubagentTemplate(
        name="reviewer",
        title="Code Reviewer",
        purpose="Review a change or a file for correctness, security and clarity.",
        capabilities={**_READ_CAPABILITIES, "git.read": True},
        role=(
            "You are reviewing code. Report what is wrong, where, and why it "
            "matters — file and line for each finding. Say so plainly when "
            "something is fine. Do not change anything."
        ),
        max_iterations=12,
    ),
    "researcher": SubagentTemplate(
        name="researcher",
        title="Researcher",
        purpose=(
            "Answer a specific question about the project — how something works, "
            "where a behaviour comes from, what a symbol is used for."
        ),
        capabilities=dict(_READ_CAPABILITIES),
        role=(
            "Answer the question you were given from what is actually in the "
            "repository. Cite the files you read. If the answer is not there, say "
            "that rather than inferring."
        ),
        max_iterations=10,
    ),
    "test_analyst": SubagentTemplate(
        name="test_analyst",
        title="Test Analyst",
        purpose="Work out why tests fail, and what would fix them.",
        capabilities={
            **_READ_CAPABILITIES,
            "test.run": True,
            "terminal.run": {"network": False},
        },
        role=(
            "Diagnose failing tests. Run them, read the failures, and find the "
            "cause. Report the diagnosis and the fix you would make — do not make "
            "it yourself."
        ),
        max_iterations=16,
        read_only=True,
    ),
}


def template_for(name: str) -> Optional[SubagentTemplate]:
    """The template for *name*, or ``None`` if there is no such subagent."""
    return TEMPLATES.get((name or "").strip().lower())


def template_names() -> List[str]:
    """Every subagent a model may delegate to, sorted."""
    return sorted(TEMPLATES)


# ---------------------------------------------------------------------------
# The structured return
# ---------------------------------------------------------------------------

RETURN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "files": {"type": "array", "items": {"type": "string"}},
        "findings": {"type": "array", "items": {"type": "string"}},
        "recommendations": {"type": "array", "items": {"type": "string"}},
        "status": {
            "type": "string",
            "enum": ["completed", "partial", "failed"],
        },
    },
    "required": ["summary", "status"],
    "additionalProperties": False,
}


@dataclass
class SubagentResult:
    """What a delegation gives back.  Structured, or it is not a delegation."""

    summary: str = ""
    files: List[str] = field(default_factory=list)
    findings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    status: str = "completed"
    #: Set when the child could not finish, so the parent reads a reason rather
    #: than an empty summary.
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "summary": self.summary,
            "files": self.files,
            "findings": self.findings,
            "recommendations": self.recommendations,
            "status": self.status,
        }
        if self.note:
            payload["note"] = self.note
        return payload

    def render(self, *, budget: int = MAX_RETURN_CHARS) -> str:
        """The observation the parent's model reads.

        Compressed rather than truncated when it is too long: the path-preserving
        compressor keeps the file list, which is what the parent needs to act,
        and drops the prose, which is what it does not.
        """
        lines = [self.summary.strip()] if self.summary.strip() else []
        if self.findings:
            lines.append("Findings:")
            lines.extend(f"- {item}" for item in self.findings)
        if self.recommendations:
            lines.append("Recommended:")
            lines.extend(f"- {item}" for item in self.recommendations)
        if self.files:
            lines.append("Files: " + ", ".join(self.files))
        if self.status != "completed":
            lines.append(f"(status: {self.status}{f' — {self.note}' if self.note else ''})")

        text = "\n".join(lines)
        if len(text) <= budget:
            return text
        return _compress(text, budget=budget, files=self.files)


def _compress(text: str, *, budget: int, files: Sequence[str]) -> str:
    """Shrink an oversized return, keeping the paths.

    Delegates to ``explorer_summary.compress_exploration_report`` — the
    path-preserving compressor written for exactly this shape of content — and
    falls back to a hard trim that re-appends the file list, because losing the
    paths is the one failure mode that makes the whole return useless.
    """
    try:
        from ..explorer_summary import compress_exploration_report

        compressed, _report = compress_exploration_report(
            text, token_budget=max(budget // 4, 256),
        )
        # The compressor is written for the explorer's *sectioned* report format,
        # so on free-form text it can produce a tidy summary with the paths
        # stripped out. "Path-preserving" therefore has to be checked rather than
        # assumed — losing the paths is the one failure that makes the whole
        # return useless to the parent.
        if compressed and len(compressed) <= budget and _keeps(compressed, files):
            return compressed
        if compressed and _keeps(compressed, files):
            text = compressed
    except Exception as exc:  # noqa: BLE001 - compression is best effort
        logger.debug("could not compress a subagent return: %s", exc)

    keep = "\n".join(["Files: " + ", ".join(files)]) if files else ""
    room = max(budget - len(keep) - 24, 200)
    trimmed = text[:room].rstrip()
    return f"{trimmed}\n… (truncated)\n{keep}".strip()


def _keeps(text: str, files: Sequence[str]) -> bool:
    """Whether *text* still names every path it is supposed to."""
    return all(path in text for path in files)


def parse_return(payload: Any) -> SubagentResult:
    """Read a child's answer into the structured shape.

    A child that answered in prose still produces a usable result — the prose
    becomes the summary and the status says ``partial``. Refusing it outright
    would make one dialect's formatting failure look like a delegation that found
    nothing.
    """
    if isinstance(payload, SubagentResult):
        return payload

    data: Any = payload
    if isinstance(payload, str):
        text = payload.strip()
        block = _json_block(text)
        if block is None:
            return SubagentResult(
                summary=text, status="partial" if text else "failed",
                note="" if text else "the subagent returned nothing",
            )
        data = block

    if not isinstance(data, dict):
        return SubagentResult(summary=str(payload), status="partial")

    return SubagentResult(
        summary=str(data.get("summary") or "").strip(),
        files=[str(item) for item in _as_list(data.get("files"))],
        findings=[str(item) for item in _as_list(data.get("findings"))],
        recommendations=[str(item) for item in _as_list(data.get("recommendations"))],
        status=_status(data.get("status")),
        note=str(data.get("note") or ""),
    )


def _json_block(text: str) -> Optional[Dict[str, Any]]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _status(value: Any) -> str:
    text = str(value or "completed").strip().lower()
    return text if text in ("completed", "partial", "failed") else "partial"


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------


DELEGATE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "agent": {
            "type": "string",
            "enum": template_names(),
            "description": "Which kind of subagent to run.",
        },
        "task": {
            "type": "string",
            "description": "What it should do, self-contained — it cannot see this conversation.",
        },
        "expected": {
            "type": "string",
            "description": "What you need back, so it does not report the wrong thing.",
        },
        "max_iterations": {
            "type": "integer",
            "minimum": 1,
            "maximum": 30,
            "description": "Cap on the child's steps. Lower is faster and cheaper.",
        },
    },
    "required": ["agent", "task"],
    "additionalProperties": False,
}

DELEGATE_DESCRIPTION = """\
Hand a self-contained piece of work to a focused subagent and get a structured
answer back.

Worth it when the work is separable and would otherwise fill your own context:
mapping an unfamiliar subsystem, reviewing a file, diagnosing a test failure. Not
worth it for something you can do in one or two tool calls yourself.

The subagent cannot see this conversation, so describe the task completely. It
can never do more than you can — a read-only run delegates read-only work.\
"""

DELEGATE = ToolSpec(
    id="agent.delegate",
    title="Delegate to a subagent",
    description=DELEGATE_DESCRIPTION,
    params_schema=DELEGATE_SCHEMA,
    capability="agent.delegate",
    risk=Risk.SAFE,          # the child's own calls are authorized individually
    effects=frozenset(),
    lite_compatible=False,   # a small model gets more from doing the work itself
    priority=40,
)


class DelegationError(RuntimeError):
    """The delegation could not be attempted at all."""


async def _delegate(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """Run a subagent.  The runner installs the spawner on the tool context."""
    spawn = (ctx.extras or {}).get("delegate_spawn")
    if spawn is None:
        return ToolResult.failure(
            call,
            "Delegation is not available in this run.",
            error="delegation_unavailable",
        )

    name = str(call.arguments.get("agent") or "").strip().lower()
    template = template_for(name)
    if template is None:
        return ToolResult.failure(
            call,
            f"Unknown subagent {name!r}. Available: {', '.join(template_names())}.",
            error="unknown_agent",
        )

    task = str(call.arguments.get("task") or "").strip()
    if not task:
        return ToolResult.failure(
            call, "A subagent needs a task to do.", error="empty_task",
        )

    try:
        result = await spawn(
            call=call,
            template=template,
            task=task,
            expected=str(call.arguments.get("expected") or ""),
            max_iterations=call.arguments.get("max_iterations"),
        )
    except DelegationError as exc:
        # A refusal the model can act on — depth exhausted, capability withheld.
        return ToolResult.failure(call, str(exc), error="delegation_refused")
    except Exception as exc:  # noqa: BLE001 - a child must not take the parent down
        logger.warning("delegation to %s failed: %s", name, exc, exc_info=True)
        return ToolResult.failure(
            call,
            f"The {name} subagent failed: {exc}. Carry on without it or do the "
            "work yourself.",
            error="delegation_failed",
        )

    outcome = parse_return(result)
    data = {"agent": name, **outcome.to_dict()}
    if outcome.status == "failed":
        # `ok` is what the model reads first. A failed child reported as a
        # successful tool call invites the parent to build on nothing.
        return ToolResult.failure(
            call,
            outcome.render() or f"The {name} subagent produced no usable result.",
            error="subagent_failed", data=data,
        )
    return ToolResult.success(call, outcome.render(), data=data)


def register(registry: ToolRegistry) -> None:
    registry.register(DELEGATE, _delegate)


# ---------------------------------------------------------------------------
# Masks and depth
# ---------------------------------------------------------------------------


def child_capabilities(parent: Any, template: SubagentTemplate) -> Any:
    """``parent ∩ template`` — the safety property this module rests on.

    A template is a ceiling, never a grant: intersecting means a child cannot
    write when its parent cannot, whatever the template says. Getting this
    backwards would make `agent.delegate` a privilege-escalation tool that the
    model can reach by asking politely.
    """
    from .policy import CapabilityMask

    ceiling = CapabilityMask.from_mapping(template.capabilities)
    if parent is None:
        return ceiling
    if isinstance(parent, CapabilityMask):
        return parent.intersect(ceiling)

    # A parent mask from another implementation (the permissive stand-in, say):
    # keep only what it allows, so an unknown mask cannot widen the ceiling.
    granted = {
        key: value for key, value in template.capabilities.items()
        if _allows(parent, key)
    }
    return CapabilityMask.from_mapping(granted)


def _allows(mask: Any, capability: str) -> bool:
    checker = getattr(mask, "allows", None)
    return bool(checker(capability)) if callable(checker) else True


def remaining_depth(parent_policy: Any, current_depth: int) -> int:
    """How many more levels of delegation are permitted."""
    limit = DEFAULT_MAX_DEPTH
    capabilities = getattr(parent_policy, "capabilities", None)
    qualifier = getattr(capabilities, "qualifier", None)
    if callable(qualifier):
        found = qualifier("agent.delegate")
        if found is not None and getattr(found, "max_depth", None) is not None:
            limit = int(found.max_depth)
    return max(limit - current_depth, 0)


def child_journal_path(parent_journal: Any, index: int) -> Path:
    """``runs/<run>/sub/<n>.jsonl`` — a child's own record.

    Separate rather than interleaved so the parent's journal stays readable and
    the safety invariants can be asserted per run instead of over a merged log.
    """
    parent_path = Path(getattr(parent_journal, "path", "run.jsonl"))
    return parent_path.parent / parent_path.stem / "sub" / f"{index}.jsonl"
