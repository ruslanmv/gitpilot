# gitpilot/toolkit/todo.py
"""The TODO list — Batch V4-E1.

First-class, and deliberately **not** a plan. The difference matters: a plan is
produced once by a planner and then executed, which is the architecture this
programme retires. A TODO list is the model's own working memory, rewritten
whenever its understanding changes, and nothing enforces that it be followed.

So this tool submits the *whole* list every time. The engine diffs it against
what it had, journals the transitions, and emits one event. Submitting the whole
list rather than patches is what makes the "rewrite it when you learn something"
instruction possible to obey — a patch protocol would quietly make the first
list authoritative.

In LITE the tool is not exposed at all: a 1.5B model asked to maintain
structured state alongside its actual work does neither well. The engine
maintains a coarse investigate → act → verify list instead, from the phase the
dialect is already tracking, so the UI is identical whichever model is running.
That is the "same UX at both ends" rule applied to a place where it would have
been easy to just leave small models with a blank panel.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .registry import Effect, Risk, ToolCall, ToolExecutionContext, ToolRegistry, ToolResult, ToolSpec

logger = logging.getLogger(__name__)

#: The statuses a task may hold.  ``blocked`` exists because the degradation
#: ladder needs somewhere to put work a dialect can no longer do (§6.6).
STATUSES = ("pending", "in_progress", "completed", "blocked")

#: Public alias — ``STATUSES`` is too generic a name for an exported symbol.
TODO_STATUSES = STATUSES

#: More than this and the list has stopped being a working memory.
MAX_ITEMS = 40

TODO_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "description": "The complete list, in order. Replaces whatever was there before.",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "What this task is, imperative and specific.",
                    },
                    "status": {
                        "type": "string",
                        "enum": list(STATUSES),
                        "description": "pending | in_progress | completed | blocked",
                    },
                },
                "required": ["text", "status"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}

TODO_DESCRIPTION = """\
Record what you are doing, as a checklist the user can see.

Submit the complete list every time — it replaces the previous one. Mark a task
`in_progress` before you start it and `completed` as soon as it is done, one at a
time. If what you learn changes the shape of the work, rewrite the list; that is
expected and better than following a list you no longer believe in.

Use it for work with several distinct steps. A single-step task does not need one.\
"""


@dataclass(frozen=True)
class TodoTransition:
    """One item's change between two versions of the list."""

    text: str
    before: Optional[str]
    after: Optional[str]

    @property
    def kind(self) -> str:
        if self.before is None:
            return "added"
        if self.after is None:
            return "removed"
        return "changed"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text, "kind": self.kind,
            "from": self.before, "to": self.after,
        }


def normalise(items: Sequence[Any]) -> Tuple[List[Dict[str, str]], List[str]]:
    """Coerce whatever the model sent into a list, reporting what was wrong.

    Returns ``(items, problems)``.  Problems are returned rather than raised so
    the caller can hand the model a corrective observation — a rejected TODO must
    teach the format, not end the run.
    """
    problems: List[str] = []
    out: List[Dict[str, str]] = []

    if not isinstance(items, (list, tuple)):
        return [], ["`items` must be an array of {text, status} objects"]

    for index, raw in enumerate(items):
        if isinstance(raw, str):
            # A plain string is a plausible mistake and unambiguous; accept it.
            out.append({"text": raw.strip(), "status": "pending"})
            continue
        if not isinstance(raw, dict):
            problems.append(f"item {index} is not an object")
            continue

        text = str(raw.get("text") or raw.get("task") or raw.get("title") or "").strip()
        if not text:
            problems.append(f"item {index} has no `text`")
            continue

        status = str(raw.get("status") or "pending").strip().lower()
        if status not in STATUSES:
            problems.append(
                f"item {index} has status {status!r}; expected one of {', '.join(STATUSES)}"
            )
            continue
        out.append({"text": text, "status": status})

    if not out and not problems:
        problems.append("the list is empty; send at least one item or do not call this tool")
    if len(out) > MAX_ITEMS:
        problems.append(
            f"{len(out)} items is too many to be useful; keep the list under {MAX_ITEMS}"
        )
        out = out[:MAX_ITEMS]

    in_progress = [item for item in out if item["status"] == "in_progress"]
    if len(in_progress) > 1:
        # Not an error: a model that marks two things in progress is describing
        # something real. It is worth saying so, since one-at-a-time is what
        # makes the panel readable.
        problems.append(
            f"{len(in_progress)} tasks are in_progress; work on one at a time"
        )

    return out, problems


def diff(before: Sequence[Any], after: Sequence[Dict[str, str]]) -> List[TodoTransition]:
    """What changed between two versions of the list.

    Matched on text, because the model has no stable ids to give us and
    positional matching would report a reordering as a mass rewrite.
    """
    old = {_text_of(item): _status_of(item) for item in before}
    new = {item["text"]: item["status"] for item in after}

    transitions: List[TodoTransition] = []
    for text, status in new.items():
        previous = old.get(text)
        if previous != status:
            transitions.append(TodoTransition(text=text, before=previous, after=status))
    for text, status in old.items():
        if text not in new:
            transitions.append(TodoTransition(text=text, before=status, after=None))
    return transitions


def _text_of(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("text", ""))
    return str(getattr(item, "text", ""))


def _status_of(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("status", "pending"))
    return str(getattr(item, "status", "pending"))


def summarise(items: Sequence[Any]) -> str:
    """One line for the model's observation: what it now has on its list."""
    if not items:
        return "The list is empty."
    counts: Dict[str, int] = {}
    for item in items:
        status = _status_of(item)
        counts[status] = counts.get(status, 0) + 1
    parts = [f"{counts[status]} {status}" for status in STATUSES if status in counts]
    current = next(
        (_text_of(item) for item in items if _status_of(item) == "in_progress"), "",
    )
    line = f"{len(items)} task(s): {', '.join(parts)}."
    return f"{line} Current: {current}" if current else line


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------


TODO_WRITE = ToolSpec(
    id="todo.write",
    title="Update the task list",
    description=TODO_DESCRIPTION,
    params_schema=TODO_SCHEMA,
    capability="todo.write",
    risk=Risk.SAFE,
    effects=frozenset(),          # it changes the run's own state, not the world
    lite_compatible=False,        # the engine maintains it instead — see the module docstring
    priority=20,                  # survives schema pruning: a visible plan is worth the tokens
)


async def _write(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """Replace the list.  The engine holding the state does the diffing."""
    items, problems = normalise(call.arguments.get("items") or [])

    handler = (ctx.extras or {}).get("todo_sink")
    if handler is None:
        # No run holding a list — the registry is being exercised standalone.
        return ToolResult.success(call, summarise(items), data={"items": items})

    if problems and not items:
        return ToolResult.failure(
            call,
            "The task list was not updated. " + " ".join(problems),
            error="invalid_todo",
            data={"problems": problems},
        )

    transitions = await handler(items)
    content = summarise(items)
    if problems:
        content += " Note: " + " ".join(problems)
    return ToolResult.success(
        call, content,
        data={"items": items, "transitions": [t.to_dict() for t in transitions]},
    )


def register(registry: ToolRegistry) -> None:
    registry.register(TODO_WRITE, _write)


# ---------------------------------------------------------------------------
# The engine-maintained list, for dialects without the tool
# ---------------------------------------------------------------------------

#: The coarse list a LITE run gets: the dialect's own phase names, paired with
#: what each one means to someone watching.
#:
#: Keyed on ``answer`` rather than the design's "verify" because that is the phase
#: the LITE adapter actually enters — a key it never emits would silently resolve
#: to the first row and show every run as though it had just started. The *label*
#: keeps the design's meaning: for an action intent the third phase is where the
#: run reports on what it did.
LITE_PHASES: Tuple[Tuple[str, str], ...] = (
    ("investigate", "Read the relevant files"),
    ("act", "Make the change"),
    ("answer", "Check the result"),
)


def phase_todo(phase: str, *, intent: str = "action") -> List[Dict[str, str]]:
    """The engine-maintained list for a LITE run in *phase*.

    A question needs no "make the change" row, so the list follows the intent
    the dialect classified rather than always showing three steps and leaving one
    permanently pending.
    """
    phases = [
        (name, text) for name, text in LITE_PHASES
        if intent == "action" or name != "act"
    ]
    known = [name for name, _ in phases]
    if phase not in known:
        # An unrecognised phase means this function and the dialect have drifted.
        # Reporting "just started" would be a plausible-looking lie, so say so.
        logger.warning(
            "unknown lite phase %r; the engine TODO and the dialect have drifted",
            phase,
        )
        return [{"text": text, "status": "pending"} for _name, text in phases]
    position = known.index(phase)

    items: List[Dict[str, str]] = []
    for index, (_name, text) in enumerate(phases):
        if index < position:
            status = "completed"
        elif index == position:
            status = "in_progress"
        else:
            status = "pending"
        items.append({"text": text, "status": status})
    return items
