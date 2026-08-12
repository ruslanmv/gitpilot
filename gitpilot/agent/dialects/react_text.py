# gitpilot/agent/dialects/react_text.py
"""The REACT_TEXT dialect — Batch V4-B4.

For capable local models — roughly 7B to 70B — whose provider has no reliable
tool API.  The grammar is deliberately the one CrewAI trained the ecosystem on::

    Thought: <why this step>
    Action: fs.read
    Action Input: {"path": "Makefile"}

...ending with ``Final Answer:``.  Local models have seen enormous amounts of
that shape, so it is the cheapest grammar to get right.  The difference from
before is that *GitPilot* parses it, in this module, where the failures can be
measured and repaired — rather than inside a framework whose parser cannot be
instrumented and whose failure mode was the ``<think>``-tag breakage catalogued
in ``agentic._INCOMPATIBLE_MODEL_PATTERNS``.

Parsing is tolerant on purpose, in this order:

1. strip reasoning tags (a ``<think>`` block regularly contains a *draft*
   Action, and parsing that instead of the real one is the classic bug);
2. find the last ``Action:`` — models often restate the grammar before using it;
3. extract Action Input as fenced or bare JSON, then repair single quotes,
   Python literals and trailing commas;
4. fuzzy-match the tool id against the registry;
5. on failure, **one** reformat retry carrying an error-specific instruction.

An unknown tool never crashes a turn: it becomes a corrective observation
listing the valid ids, which is a thing the model can act on.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...toolkit.registry import CapabilitySet, ToolCall, ToolResult
from ..adapter import ModelAdapter, synthesize_call_id
from ..contracts import AgentTurn, Dialect, Finality

logger = logging.getLogger(__name__)

FINAL_ANSWER_MARKER = "final answer:"

_ACTION_RE = re.compile(r"^\s*action\s*:\s*(?P<tool>[^\n]+)$", re.IGNORECASE | re.MULTILINE)
_ACTION_INPUT_RE = re.compile(
    r"action\s*input\s*:\s*(?P<body>.*?)(?=\n\s*(?:thought|action|observation|final answer)\s*:|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_FINAL_RE = re.compile(
    r"final\s*answer\s*:\s*(?P<body>.*)\Z", re.IGNORECASE | re.DOTALL,
)
_THOUGHT_RE = re.compile(
    r"thought\s*:\s*(?P<body>.*?)(?=\n\s*(?:action|final answer)\s*:|\Z)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class ParsedAction:
    """What one turn's text yielded."""

    thought: str = ""
    tool: str = ""
    arguments: Dict[str, Any] = None      # type: ignore[assignment]
    final_answer: Optional[str] = None
    error: str = ""
    raw: str = ""

    def __post_init__(self) -> None:
        if self.arguments is None:
            self.arguments = {}


class ReactTextAdapter(ModelAdapter):
    """A text grammar we parse ourselves."""

    dialect = Dialect.REACT_TEXT

    #: One retry, and only one.  A model that cannot follow the grammar after a
    #: targeted correction will not follow it after five, and each attempt is a
    #: whole turn of latency and tokens.
    MAX_REFORMAT_RETRIES = 1

    async def generate(
        self,
        *,
        system: Any = None,
        messages: Sequence[Dict[str, Any]],
        capabilities: Optional[CapabilitySet] = None,
        on_text: Optional[Any] = None,
        **opts: Any,
    ) -> AgentTurn:
        manual = self.render_tool_manual(capabilities)
        prompt_system = _join_system(system, manual)
        attempt_messages = [dict(message) for message in messages]

        retries = 0
        while True:
            response = await self._complete(
                attempt_messages, prompt_system,
                # Only the first attempt streams — see `_complete`.
                on_text if retries == 0 else None,
                **opts,
            )
            parsed = parse_react_output(
                response.text, known_tools=self._known_tool_ids(capabilities),
            )

            if not parsed.error:
                return self._to_turn(parsed, response)

            if retries >= self.MAX_REFORMAT_RETRIES:
                # Out of retries: treat the text as prose rather than losing it.
                logger.debug("react_text: giving up on grammar repair (%s)", parsed.error)
                return AgentTurn(
                    text=parsed.raw.strip(),
                    finality=Finality.FINAL,
                    usage=response.usage,
                    meta={"parse_error": parsed.error, "retries": retries},
                )

            retries += 1
            attempt_messages = [
                *attempt_messages,
                {"role": "assistant", "content": response.text},
                {"role": "user", "content": _reformat_instruction(parsed.error)},
            ]

    # -- the tool manual --------------------------------------------------

    def render_tool_manual(self, capabilities: Optional[CapabilitySet] = None) -> str:
        """The tool list, at the verbosity the profile allows.

        This is where the schema budget earns its keep: a 16k-window model handed
        forty full JSON Schemas has no room left for the repository.
        """
        tools = self.tool_defs(capabilities)
        if not tools:
            return _GRAMMAR_BLOCK

        lines = ["You can use exactly these tools:"]
        for tool in tools:
            if self.profile.schema_verbosity == "full":
                lines.append(
                    f"- {tool.name}: {tool.description}\n"
                    f"  arguments: {json.dumps(tool.parameters, sort_keys=True)}"
                )
            else:
                properties = (tool.parameters or {}).get("properties") or {}
                required = set((tool.parameters or {}).get("required") or [])
                args = ", ".join(
                    f"{name}: {spec.get('type', 'string')}"
                    + ("" if name in required else " (optional)")
                    for name, spec in properties.items()
                )
                lines.append(f"- {tool.name}({args}) — {tool.description}")

        return _GRAMMAR_BLOCK + "\n\n" + "\n".join(lines)

    def _known_tool_ids(self, capabilities: Optional[CapabilitySet] = None) -> List[str]:
        if self.registry is None:
            return []
        return [spec.id for spec in self.registry.specs(capabilities)]

    # -- turn assembly ----------------------------------------------------

    async def _complete(
        self,
        messages: Sequence[Dict[str, Any]],
        system: Any,
        on_text: Optional[Any],
        **opts: Any,
    ) -> Any:
        """One provider call, streamed **per line** where possible — V4-E4.

        Per line rather than per token, because this dialect's grammar is
        line-structured: a half-emitted ``Action Input:`` line is not something a
        client can render, and showing it would let the user watch the model write
        a tool call as though it were prose. A whole line is the smallest
        increment that means anything here.

        A retry attempt is never streamed: the user has already seen the first
        try, and replaying a repaired version of the same turn reads as the model
        saying everything twice.
        """
        streamable = (
            on_text is not None
            and getattr(self.profile, "supports_streaming", False)
            and getattr(self.provider, "supports_streaming", False)
        )
        if not streamable:
            return await self.provider.complete(
                messages, tools=None, system=system, **opts,
            )

        from ..providers.base import ProviderResponse

        buffer: List[str] = []
        pending = ""
        usage = None
        try:
            async for chunk in self.provider.stream(
                messages, tools=None, system=system, **opts,
            ):
                if getattr(chunk, "usage", None) is not None:
                    usage = chunk.usage
                if not chunk.text:
                    continue
                buffer.append(chunk.text)
                pending += chunk.text
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    await self._emit_line(on_text, line)
        except NotImplementedError:
            # A provider that declares streaming and does not implement it.
            return await self.provider.complete(
                messages, tools=None, system=system, **opts,
            )
        if pending.strip():
            await self._emit_line(on_text, pending)

        return ProviderResponse(text="".join(buffer), usage=usage)

    async def _emit_line(self, on_text: Any, line: str) -> None:
        """Send a completed line, unless it is grammar rather than prose.

        ``Thought:`` is the model's visible reasoning about the task and belongs
        on screen; ``Action:`` and ``Action Input:`` are the protocol, and a client
        renders those as a tool card built from the parsed turn instead.
        """
        stripped = line.strip()
        if not stripped:
            return
        lowered = stripped.lower()
        if lowered.startswith(("action:", "action input:", "observation:")):
            return
        if lowered.startswith("thought:"):
            stripped = stripped[len("thought:"):].strip()
            if not stripped:
                return
        await on_text(stripped + "\n")

    def _to_turn(self, parsed: ParsedAction, response: Any) -> AgentTurn:
        if parsed.final_answer is not None:
            return AgentTurn(
                text=parsed.final_answer.strip(),
                finality=Finality.FINAL,
                usage=getattr(response, "usage", None),
                meta={"thought": parsed.thought},
            )

        call = ToolCall(
            id=synthesize_call_id("react", 0),
            tool=parsed.tool,
            arguments=parsed.arguments,
        )
        return AgentTurn(
            # The thought is the model's own prose about what it is doing; the
            # loop shows it, and it is the only user-visible text in this dialect
            # until a Final Answer.
            text=parsed.thought.strip(),
            tool_calls=[call],
            finality=Finality.CONTINUE,
            usage=getattr(response, "usage", None),
            meta={"raw": parsed.raw},
        )

    # -- message shapes ---------------------------------------------------

    def assistant_message(self, turn: AgentTurn) -> Optional[Dict[str, Any]]:
        """Replay the grammar back, not a native tool-call structure.

        The model's next turn has to see a conversation in the shape it is being
        asked to produce; handing it an OpenAI ``tool_calls`` array would teach
        it the wrong format.
        """
        if turn.tool_calls:
            call = turn.tool_calls[0]
            body = json.dumps(call.arguments, sort_keys=True)
            text = ""
            if turn.text:
                text += f"Thought: {turn.text}\n"
            text += f"Action: {call.tool}\nAction Input: {body}"
            return {"role": "assistant", "content": text}
        if turn.text:
            return {"role": "assistant", "content": f"Final Answer: {turn.text}"}
        return None

    def result_messages(self, call: ToolCall, result: ToolResult) -> List[Dict[str, Any]]:
        """Observations come back as a user turn — there is no tool role here."""
        return [{"role": "user", "content": f"Observation: {result.content}"}]


# ---------------------------------------------------------------------------
# The grammar block
# ---------------------------------------------------------------------------

_GRAMMAR_BLOCK = """\
Work in steps. For each step emit exactly this, and nothing else:

Thought: one sentence on what you are doing next
Action: the tool id, exactly as written below
Action Input: a single JSON object

Then stop and wait. You will be given the result as "Observation:".
When you have the answer, emit instead:

Final Answer: your answer

Rules: one Action per reply. Action Input must be one JSON object on one line, \
with double quotes. Never invent a tool id."""


def _reformat_instruction(error: str) -> str:
    """A correction that names what was wrong.

    A generic "invalid format" retry gets the same output back; naming the defect
    is what makes the one retry worth spending.
    """
    return (
        f"That reply could not be parsed: {error}\n\n"
        "Reply again using exactly this format and nothing else:\n"
        "Thought: <one sentence>\n"
        "Action: <tool id>\n"
        'Action Input: {"key": "value"}\n\n'
        "Or, if you are done:\nFinal Answer: <your answer>"
    )


def _join_system(system: Any, manual: str) -> str:
    from ..providers.openai_compat import _system_text

    base = _system_text(system) if system else ""
    return f"{base}\n\n{manual}".strip() if base else manual


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_react_output(text: str, known_tools: Sequence[str] = ()) -> ParsedAction:
    """Read one model turn.  Never raises; failures come back as ``error``."""
    raw = text or ""
    cleaned = _strip_reasoning(raw)

    final = _FINAL_RE.search(cleaned)
    action = _last_action(cleaned)

    # A Final Answer *after* the last Action means the model finished; before it,
    # the model restated the template and then acted, so the Action wins.
    if final and (not action or final.start() > action.start()):
        return ParsedAction(
            thought=_thought(cleaned), final_answer=final.group("body"), raw=raw,
        )

    if not action:
        if cleaned.strip():
            # Prose with no grammar at all: treat it as an answer rather than
            # forcing a retry the model will probably fail the same way.
            return ParsedAction(thought="", final_answer=cleaned.strip(), raw=raw)
        return ParsedAction(error="the reply was empty", raw=raw)

    tool_name = _clean_tool_name(action.group("tool"))
    resolved = _resolve_tool(tool_name, known_tools)
    if resolved is None:
        available = ", ".join(known_tools[:12]) or "none"
        return ParsedAction(
            error=f"unknown tool {tool_name!r}; available tools are: {available}",
            raw=raw,
        )

    body_match = _ACTION_INPUT_RE.search(cleaned[action.start():])
    if body_match is None:
        # No Action Input at all is valid for a no-argument tool.
        return ParsedAction(
            thought=_thought(cleaned), tool=resolved, arguments={}, raw=raw,
        )

    arguments, error = parse_action_input(body_match.group("body"))
    if error:
        return ParsedAction(error=error, raw=raw)

    return ParsedAction(
        thought=_thought(cleaned), tool=resolved, arguments=arguments, raw=raw,
    )


def _strip_reasoning(text: str) -> str:
    """Remove ``<think>`` blocks before parsing.

    Not cosmetic: a reasoning model routinely drafts an Action inside its
    scratchpad and then emits a different one. Parsing the draft executes a call
    the model changed its mind about.
    """
    from ...reasoning_normalizer import strip_reasoning_content

    cleaned, _ = strip_reasoning_content(text)
    return cleaned or ""


def _last_action(text: str):
    matches = list(_ACTION_RE.finditer(text))
    return matches[-1] if matches else None


def _thought(text: str) -> str:
    match = _THOUGHT_RE.search(text)
    return match.group("body").strip() if match else ""


def _clean_tool_name(raw: str) -> str:
    name = (raw or "").strip()
    # Models wrap the id in backticks, quotes, or trailing punctuation.
    name = name.strip("`\"'*").rstrip(".,;:")
    if name.lower().startswith("tool "):
        name = name[5:].strip()
    return name


def _resolve_tool(name: str, known_tools: Sequence[str]) -> Optional[str]:
    """Fuzzy-match a tool id.

    Local models mangle ids predictably — ``fs_read``, ``Fs.Read``, ``fs-read``,
    ``read`` — and each corrective observation costs a whole turn, so the cheap
    normalisations are worth doing here.
    """
    if not known_tools:
        return name or None
    if name in known_tools:
        return name

    def canon(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())

    target = canon(name)
    if not target:
        return None

    for tool in known_tools:
        if canon(tool) == target:
            return tool
    # A bare leaf name (``read`` for ``fs.read``) is unambiguous only if one
    # tool ends with it; two matches means guessing, which is worse than asking.
    suffix_matches = [tool for tool in known_tools if canon(tool).endswith(target)]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    return None


def parse_action_input(body: str) -> Tuple[Dict[str, Any], str]:
    """Extract a JSON object from an Action Input body.

    Returns ``(arguments, error)``; a non-empty error means the caller should ask
    for a reformat.
    """
    text = (body or "").strip()
    if not text:
        return {}, ""

    text = _unfence(text)
    if not text:
        return {}, ""

    candidate = _first_json_object(text) or text

    for attempt in (candidate, _repair_json(candidate)):
        if not attempt:
            continue
        try:
            parsed = json.loads(attempt)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed, ""
        # A bare scalar where an object belongs: the model gave the single
        # argument's value. The registry's validation will name the key it
        # wanted, which is a better message than anything guessed here.
        return {"value": parsed}, ""

    return {}, "Action Input was not a JSON object"


def _unfence(text: str) -> str:
    """Strip a markdown fence, reusing the stripper the planner path uses."""
    from ...agentic import _strip_markdown_fences

    return (_strip_markdown_fences(text) or text).strip()


def _first_json_object(text: str) -> Optional[str]:
    """The first balanced ``{...}`` span, so trailing prose is ignored."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _repair_json(text: str) -> str:
    """The three malformations local models actually produce.

    Single-quoted keys and values, Python's ``True``/``False``/``None``, and a
    trailing comma before the closing brace.  Nothing cleverer: a repair that
    guesses at structure can turn a refusable error into a wrong tool call.
    """
    repaired = re.sub(r",\s*([}\]])", r"\1", text)
    repaired = re.sub(r"\bTrue\b", "true", repaired)
    repaired = re.sub(r"\bFalse\b", "false", repaired)
    repaired = re.sub(r"\bNone\b", "null", repaired)
    if '"' not in repaired and "'" in repaired:
        repaired = repaired.replace("'", '"')
    return repaired
