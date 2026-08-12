# gitpilot/agent/dialects/lite.py
"""The LITE dialect — Batch V4-B5.

For models under about 7B, and as the last rung of the degradation ladder.
GitPilot ships configured for ``qwen2.5:1.5b``, so this is the *default* path
for a fresh install — not an edge case.

The insight this dialect is built on comes from the existing Lite Mode: a small
model cannot hold a tool manual, choose a tool, and format arguments in one
turn, but it can reliably answer one narrow question at a time when the context
it needs is already in the prompt.  So the engine still iterates — a micro-loop
— but the *adapter* picks the shape of each turn rather than the model:

* **investigate** — the model names files it wants, one ``READ path`` per line,
  at most three, for a bounded number of rounds;
* **act** — the model emits the ``ACTION filepath`` protocol Lite Mode already
  proved, followed by fenced content;
* **answer** — plain prose, which ends the run.

Everything ``agentic.py`` learned about small models is retained rather than
rewritten: regex intent classification, validation of every path against the
real file list (a hallucinated path is dropped, never applied), the fuzzy
extraction fallback for models that ignore the protocol, ``plan_guards``'
refusal detection, and the lean prompt budgets with known facts at the tail —
where a small model weights them most heavily.

Two things are new and load-bearing:

* **Every path the model touches becomes a synthesized tool call.**  Prefetch
  and ``READ`` lines produce ``fs.read`` calls; ``ACTION`` lines produce
  ``fs.write``/``fs.delete``.  In this batch they execute through the registry;
  Batch V4-D1 routes them through the policy engine, which is what finally puts
  ``blocked_paths`` in front of a small model's context — today's Lite Mode
  reads whatever it likes.  It is also what makes a LITE run resumable, since
  the journal then contains the file contents the model was shown.
* **``FINAL_AFTER_TOOLS``** — an act-turn is only finished if its actions
  actually applied, which the loop knows and the adapter confirms in
  :meth:`conclude`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ...toolkit.registry import CapabilitySet, ToolCall, ToolResult
from ..adapter import ModelAdapter, synthesize_call_id
from ..contracts import AgentTurn, Dialect, Finality

logger = logging.getLogger(__name__)

#: Phases the adapter drives.  The model is never asked which phase it is in.
PHASE_INVESTIGATE = "investigate"
PHASE_ACT = "act"
PHASE_ANSWER = "answer"

#: Bounds that keep a small model from thrashing.  Three paths per round is
#: about as much content as an 8k window tolerates alongside a task.
MAX_READS_PER_TURN = 3
MAX_INVESTIGATE_ROUNDS = 3
#: Matches ``_lite_prefetch_context``'s per-file budget.
SNIPPET_CHAR_LIMIT = 1500

_ACTION_LINE_RE = re.compile(r"^(CREATE|MODIFY|DELETE)\s+(\S+)", re.MULTILINE)
_READ_LINE_RE = re.compile(r"^\s*READ\s+(\S+)\s*$", re.MULTILINE | re.IGNORECASE)


@dataclass
class LiteState:
    """Where the micro-loop has got to.

    Held by the adapter rather than inferred from the transcript: a small model's
    output is not a reliable record of what it was asked, and the loop's journal
    (Batch V4-C1) is what persists this across a resume.
    """

    intent: str = "question"
    phase: str = PHASE_INVESTIGATE
    rounds_used: int = 0
    files_seen: Set[str] = field(default_factory=set)
    known_files: List[str] = field(default_factory=list)
    prefetched: bool = False


class LiteAdapter(ModelAdapter):
    """A constrained line protocol with pre-fetched context."""

    dialect = Dialect.LITE

    def __init__(self, provider, profile, registry=None) -> None:
        super().__init__(provider, profile, registry)
        self.state = LiteState()
        #: The last phase label sent, so a repeated phase does not repeat itself.
        self._last_label: Optional[str] = None

    # -- setup ------------------------------------------------------------

    def prepare(self, task: str, known_files: Sequence[str]) -> None:
        """Classify intent and record the real file list.

        The file list is the safety mechanism this whole dialect leans on: every
        path the model produces is checked against it, so a hallucinated file is
        dropped rather than created.
        """
        self.state = LiteState(
            intent=classify_intent(task),
            phase=PHASE_INVESTIGATE,
            known_files=list(known_files),
        )

    def prefetch_calls(self, paths: Sequence[str]) -> List[ToolCall]:
        """``fs.read`` calls for the context to inject before the first turn.

        Synthesized rather than read directly so prefetch passes the same policy
        and journal path as anything else — the difference between a small-model
        session that respects ``blocked_paths`` and one that does not.
        """
        chosen = [path for path in paths if path in set(self.state.known_files)]
        self.state.prefetched = True
        return [
            ToolCall(id=synthesize_call_id("lite_prefetch", index), tool="fs.read",
                     arguments={"path": path})
            for index, path in enumerate(chosen[:MAX_READS_PER_TURN])
        ]

    # -- the turn ---------------------------------------------------------

    #: What each phase is called on screen — Batch V4-E4.
    #:
    #: LITE emits one event per phase rather than per token. A small model's
    #: output is a constrained line protocol (``READ path``, ``ACTION path``), so
    #: streaming it verbatim would show the user protocol rather than progress.
    #: Naming the phase is the honest signal: it says what is happening without
    #: pretending the model is narrating.
    PHASE_LABELS = {
        PHASE_INVESTIGATE: "Reading the relevant files…",
        PHASE_ACT: "Making the change…",
        PHASE_ANSWER: "Writing the answer…",
    }

    async def generate(
        self,
        *,
        system: Any = None,
        messages: Sequence[Dict[str, Any]],
        capabilities: Optional[CapabilitySet] = None,
        on_text: Optional[Any] = None,
        **opts: Any,
    ) -> AgentTurn:
        phase = self._next_phase()
        instruction = self._instruction_for(phase)

        if on_text is not None:
            label = self.PHASE_LABELS.get(phase)
            if label and label != self._last_label:
                self._last_label = label
                await on_text(label + "\n")

        prompt_messages = [dict(message) for message in messages]
        prompt_messages.append({"role": "user", "content": instruction})

        response = await self.provider.complete(
            prompt_messages, tools=None, system=_system_text(system), **opts,
        )
        text = response.text or ""

        # A refusal or a stock hallucinated plan is worth catching before it
        # becomes tool calls; ``plan_guards`` already knows the phrasings.
        if _is_refusal(text):
            return AgentTurn(
                text=text.strip(),
                finality=Finality.FINAL,
                usage=response.usage,
                meta={"phase": phase, "refusal": True},
            )

        if phase == PHASE_INVESTIGATE:
            return self._investigate_turn(text, response)
        if phase == PHASE_ACT:
            return self._act_turn(text, response)
        return AgentTurn(
            text=text.strip(),
            finality=Finality.FINAL,
            usage=response.usage,
            meta={"phase": PHASE_ANSWER},
        )

    def _next_phase(self) -> str:
        if self.state.phase == PHASE_INVESTIGATE:
            if self.state.rounds_used >= MAX_INVESTIGATE_ROUNDS:
                # Budget spent: stop looking and do the thing.
                return PHASE_ACT if self.state.intent == "action" else PHASE_ANSWER
            return PHASE_INVESTIGATE
        return self.state.phase

    def _investigate_turn(self, text: str, response: Any) -> AgentTurn:
        self.state.rounds_used += 1
        requested = extract_read_paths(text, self.state.known_files)
        fresh = [path for path in requested if path not in self.state.files_seen]

        if not fresh:
            # Nothing more it wants to see: move on.
            self.state.phase = PHASE_ACT if self.state.intent == "action" else PHASE_ANSWER
            return AgentTurn(
                text=text.strip(),
                finality=Finality.CONTINUE,
                usage=response.usage,
                meta={"phase": PHASE_INVESTIGATE, "reads": []},
            )

        self.state.files_seen.update(fresh)
        calls = [
            ToolCall(
                id=synthesize_call_id(f"lite_read{self.state.rounds_used}", index),
                tool="fs.read",
                arguments={"path": path},
            )
            for index, path in enumerate(fresh[:MAX_READS_PER_TURN])
        ]
        return AgentTurn(
            text="",
            tool_calls=calls,
            finality=Finality.CONTINUE,
            usage=response.usage,
            meta={"phase": PHASE_INVESTIGATE, "reads": [c.arguments["path"] for c in calls]},
        )

    def _act_turn(self, text: str, response: Any) -> AgentTurn:
        actions = parse_action_lines(text, self.state.known_files)
        blocks = extract_content_blocks(text)

        if not actions:
            # No valid actions: degrade to Q&A rather than inventing work.  The
            # existing Lite Mode does the same, and it is the right call — a
            # small model that could not follow the protocol has still usually
            # produced a usable answer.
            self.state.phase = PHASE_ANSWER
            return AgentTurn(
                text=text.strip(),
                finality=Finality.FINAL,
                usage=response.usage,
                meta={"phase": PHASE_ACT, "actions": [], "degraded_to_answer": True},
            )

        calls: List[ToolCall] = []
        for index, (verb, path) in enumerate(actions):
            if verb == "DELETE":
                calls.append(ToolCall(
                    id=synthesize_call_id("lite_act", index),
                    tool="fs.delete",
                    arguments={"path": path, "commit_message": f"Delete {path}"},
                ))
                continue
            content = blocks.get(path)
            if content is None:
                # A CREATE/MODIFY with no body is not applicable; dropping it is
                # better than writing an empty file over someone's source.
                logger.debug("lite: %s %s has no content block; skipped", verb, path)
                continue
            calls.append(ToolCall(
                id=synthesize_call_id("lite_act", index),
                tool="fs.write",
                arguments={
                    "path": path,
                    "content": content,
                    "commit_message": f"{verb.title()} {path}",
                },
            ))

        if not calls:
            self.state.phase = PHASE_ANSWER
            return AgentTurn(
                text=text.strip(),
                finality=Finality.FINAL,
                usage=response.usage,
                meta={"phase": PHASE_ACT, "actions": actions, "degraded_to_answer": True},
            )

        return AgentTurn(
            text=_human_readable(text),
            tool_calls=calls,
            finality=Finality.FINAL_AFTER_TOOLS,
            usage=response.usage,
            meta={"phase": PHASE_ACT, "actions": actions},
        )

    # -- finality ---------------------------------------------------------

    def conclude(self, turn: AgentTurn, results: Sequence[ToolResult]) -> bool:
        """An act-turn finished only if every one of its actions applied.

        A denied or failed write means there is more to do — the loop keeps
        going, and the model gets to see why.
        """
        if turn.finality is not Finality.FINAL_AFTER_TOOLS:
            return False
        if not results:
            return False
        if all(result.ok for result in results):
            return True

        failed = [result.tool for result in results if not result.ok]
        logger.debug("lite: act turn not final, %s failed", ", ".join(failed))
        self.state.phase = PHASE_ACT
        return False

    # -- message shapes ---------------------------------------------------

    def assistant_message(self, turn: AgentTurn) -> Optional[Dict[str, Any]]:
        if turn.text:
            return {"role": "assistant", "content": turn.text}
        reads = turn.meta.get("reads") or []
        if reads:
            return {"role": "assistant", "content": "\n".join(f"READ {p}" for p in reads)}
        return None

    def result_messages(self, call: ToolCall, result: ToolResult) -> List[Dict[str, Any]]:
        """Inject file contents as a user turn, truncated to the snippet budget.

        The budget is the same one ``_lite_prefetch_context`` uses: past it, a
        small model loses the instruction it was given.
        """
        content = result.content or ""
        if len(content) > SNIPPET_CHAR_LIMIT:
            content = content[:SNIPPET_CHAR_LIMIT] + "\n… truncated"
        return [{"role": "user", "content": content}]

    # -- prompts ----------------------------------------------------------

    def _instruction_for(self, phase: str) -> str:
        if phase == PHASE_INVESTIGATE:
            listing = _file_listing(self.state.known_files)
            return (
                "Which files do you need to read? Reply with at most "
                f"{MAX_READS_PER_TURN} lines, each exactly:\nREAD path\n"
                "Use only paths from the list. If you need nothing more, reply: READY\n\n"
                # Known facts last: a small model weights the tail most heavily.
                f"Files in this repository:\n{listing}"
            )
        if phase == PHASE_ACT:
            listing = _file_listing(self.state.known_files)
            return (
                "Reply with ONLY file actions, one per line.\n"
                "Format: ACTION filepath\n"
                "ACTION is one of: CREATE, MODIFY, DELETE\n\n"
                "For CREATE and MODIFY, follow the line with the full new content in a "
                "fenced block whose info line is the same path:\n"
                "```path/to/file.py\n<content>\n```\n\n"
                "Rules:\n"
                "- MODIFY or DELETE only files that exist in the list below.\n"
                "- CREATE only files that are not in the list.\n"
                "- No explanations.\n\n"
                f"Files in this repository:\n{listing}"
            )
        return "Answer the request directly and concisely."


# ---------------------------------------------------------------------------
# Parsing and classification
# ---------------------------------------------------------------------------


def classify_intent(goal: str) -> str:
    """'action' or 'question', by regex — no LLM call.

    Delegates to ``agentic._classify_lite_intent`` so the pattern tables stay in
    one place.
    """
    from ...agentic import _classify_lite_intent

    return _classify_lite_intent(goal or "")


def extract_read_paths(text: str, known_files: Sequence[str]) -> List[str]:
    """``READ path`` lines, validated against the real file list.

    Unknown paths are dropped silently, exactly as Lite Mode drops hallucinated
    action targets — telling a 1.5B model it hallucinated tends to produce
    another hallucination rather than a correction.
    """
    real = set(known_files)
    out: List[str] = []
    for match in _READ_LINE_RE.finditer(text or ""):
        path = match.group(1).strip().strip("`\"',")
        if path in real and path not in out:
            out.append(path)
        elif path not in real:
            logger.debug("lite: dropped READ for unknown path %r", path)
    return out


def parse_action_lines(
    text: str, known_files: Sequence[str],
) -> List[Tuple[str, str]]:
    """``ACTION filepath`` lines, validated against the real file list.

    MODIFY and DELETE must name a file that exists; CREATE must not.  Both rules
    come from the existing Lite Mode and both matter: a MODIFY of a nonexistent
    path silently created files, and a CREATE over an existing one overwrote
    them.
    """
    real = set(known_files)
    seen: Set[Tuple[str, str]] = set()
    out: List[Tuple[str, str]] = []

    for verb, raw_path in _ACTION_LINE_RE.findall(text or ""):
        path = raw_path.strip().rstrip(",-:").strip("`\"'")
        if not path:
            continue
        if verb in ("MODIFY", "DELETE") and path not in real:
            logger.debug("lite: dropped %s for unknown path %r", verb, path)
            continue
        if verb == "CREATE" and path in real:
            # It exists: the model meant to change it.
            verb = "MODIFY"
        key = (verb, path)
        if key not in seen:
            seen.add(key)
            out.append(key)

    return out


def extract_content_blocks(text: str) -> Dict[str, str]:
    """Fenced blocks keyed by the path on their info line.

    The same ```` ```lang path ```` convention folder mode already uses, so a
    model that has seen GitPilot's other prompts produces the right shape.
    """
    blocks: Dict[str, str] = {}
    pattern = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
    for info, body in pattern.findall(text or ""):
        tokens = [token for token in info.strip().split() if token]
        if not tokens:
            continue
        # The path is whichever token looks like one; the info line may be
        # "python src/app.py" or just "src/app.py".
        path = next((token for token in reversed(tokens) if "/" in token or "." in token), "")
        if path:
            blocks[path.strip("`\"'")] = body
    return blocks


def _human_readable(text: str) -> str:
    """The prose left after removing protocol lines and content blocks."""
    without_blocks = re.sub(r"```[^\n`]*\n.*?```", "", text or "", flags=re.DOTALL)
    without_actions = re.sub(
        r"^(CREATE|MODIFY|DELETE)\s+\S+.*$", "", without_blocks, flags=re.MULTILINE,
    )
    return without_actions.strip()


def _is_refusal(text: str) -> bool:
    from ...plan_guards import detect_refusal

    try:
        return bool(detect_refusal(text))
    except Exception:  # noqa: BLE001 - guard failures must not break a turn
        return False


def _file_listing(known_files: Sequence[str], limit: int = 60) -> str:
    """A bounded file list.  60 paths is what ``explorer_summary`` settled on."""
    listed = list(known_files[:limit])
    suffix = f"\n… and {len(known_files) - limit} more" if len(known_files) > limit else ""
    return "\n".join(listed) + suffix


def _system_text(system: Any) -> str:
    from ..providers.openai_compat import _system_text as flatten

    return flatten(system) if system else ""
