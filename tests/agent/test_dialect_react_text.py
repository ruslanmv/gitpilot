"""The REACT_TEXT dialect — Batch V4-B4.

The parsing corpus is the point of this file: local models mangle the grammar in
a small number of recurring ways, and each one costs a whole turn if it is not
repaired here.
"""
from __future__ import annotations

import asyncio

import pytest

from gitpilot.agent.contracts import Finality
from gitpilot.agent.dialects.react_text import (
    ReactTextAdapter,
    parse_action_input,
    parse_react_output,
)
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.providers.base import ProviderResponse
from gitpilot.toolkit import ToolCall, ToolResult
from gitpilot.toolkit.builtins import build_default_registry

KNOWN = ["fs.read", "fs.grep", "terminal.run", "git.diff"]


@pytest.fixture()
def registry():
    return build_default_registry()


@pytest.fixture()
def profile(tmp_path):
    return resolve_profile("ollama", "llama3:8b", cache_path=tmp_path / "c.json")


class _ScriptedProvider:
    """Returns queued texts, recording what it was asked."""

    name = "ollama"
    supports_native_tools = False
    supports_parallel_tool_calls = False
    supports_streaming = False

    def __init__(self, texts):
        self.model = "llama3:8b"
        self._texts = list(texts)
        self.requests = []

    async def complete(self, messages, *, tools=None, system=None, **opts):
        self.requests.append({"messages": [dict(m) for m in messages], "system": system, "tools": tools})
        return ProviderResponse(text=self._texts.pop(0))

    def stream(self, *args, **kwargs):  # pragma: no cover - not used in this dialect
        raise NotImplementedError


# ---------------------------------------------------------------------------
# The parsing corpus
# ---------------------------------------------------------------------------

WELL_FORMED = """Thought: I should read the Makefile.
Action: fs.read
Action Input: {"path": "Makefile"}"""

CORPUS = {
    "well_formed": (WELL_FORMED, "fs.read", {"path": "Makefile"}),
    "single_quotes": (
        "Thought: check\nAction: fs.read\nAction Input: {'path': 'Makefile'}",
        "fs.read", {"path": "Makefile"},
    ),
    "fenced_json": (
        'Thought: check\nAction: fs.read\nAction Input:\n```json\n{"path": "Makefile"}\n```',
        "fs.read", {"path": "Makefile"},
    ),
    "trailing_comma": (
        'Thought: check\nAction: fs.read\nAction Input: {"path": "Makefile",}',
        "fs.read", {"path": "Makefile"},
    ),
    "python_literals": (
        'Thought: check\nAction: git.diff\nAction Input: {"staged": True}',
        "git.diff", {"staged": True},
    ),
    "backticked_tool_id": (
        'Thought: check\nAction: `fs.read`\nAction Input: {"path": "Makefile"}',
        "fs.read", {"path": "Makefile"},
    ),
    "underscored_tool_id": (
        'Thought: check\nAction: fs_read\nAction Input: {"path": "Makefile"}',
        "fs.read", {"path": "Makefile"},
    ),
    "uppercased_tool_id": (
        'Thought: check\nAction: FS.READ\nAction Input: {"path": "Makefile"}',
        "fs.read", {"path": "Makefile"},
    ),
    "bare_leaf_name": (
        'Thought: check\nAction: grep\nAction Input: {"pattern": "def greet"}',
        "fs.grep", {"pattern": "def greet"},
    ),
    "trailing_prose_after_json": (
        'Thought: check\nAction: fs.read\nAction Input: {"path": "Makefile"} — then I will look further',
        "fs.read", {"path": "Makefile"},
    ),
    "template_restated_before_use": (
        "You should reply like:\nAction: <tool>\nAction Input: {}\n\n"
        'Thought: now really\nAction: fs.read\nAction Input: {"path": "Makefile"}',
        "fs.read", {"path": "Makefile"},
    ),
    "think_block_with_a_draft_action": (
        '<think>Maybe Action: terminal.run with rm -rf /</think>\n'
        'Thought: read it first\nAction: fs.read\nAction Input: {"path": "Makefile"}',
        "fs.read", {"path": "Makefile"},
    ),
    "no_arguments_tool": (
        "Thought: look at the diff\nAction: git.diff",
        "git.diff", {},
    ),
}


class TestParsingCorpus:
    @pytest.mark.parametrize("case", list(CORPUS), ids=list(CORPUS))
    def test_each_malformation_parses_or_repairs(self, case):
        text, expected_tool, expected_args = CORPUS[case]

        parsed = parse_react_output(text, known_tools=KNOWN)

        assert not parsed.error, parsed.error
        assert parsed.tool == expected_tool
        assert parsed.arguments == expected_args

    def test_a_think_block_draft_is_never_executed(self):
        """The classic bug: parsing the scratchpad runs a call the model dropped."""
        text, _, _ = CORPUS["think_block_with_a_draft_action"]
        parsed = parse_react_output(text, known_tools=KNOWN)

        assert parsed.tool == "fs.read"
        assert "rm -rf" not in str(parsed.arguments)

    def test_final_answer_ends_the_turn(self):
        parsed = parse_react_output("Final Answer: the tests live in tests/", known_tools=KNOWN)

        assert parsed.final_answer.strip() == "the tests live in tests/"
        assert not parsed.error

    def test_an_action_after_a_restated_final_answer_still_wins(self):
        text = (
            "Reply with Final Answer: <answer> when done.\n"
            'Thought: not yet\nAction: fs.read\nAction Input: {"path": "Makefile"}'
        )
        parsed = parse_react_output(text, known_tools=KNOWN)

        assert parsed.tool == "fs.read"
        assert parsed.final_answer is None

    def test_unknown_tool_lists_the_valid_ids(self):
        parsed = parse_react_output(
            'Thought: x\nAction: fs.telepathy\nAction Input: {}', known_tools=KNOWN,
        )

        assert "unknown tool" in parsed.error
        assert "fs.read" in parsed.error

    def test_ambiguous_leaf_name_is_refused_rather_than_guessed(self):
        parsed = parse_react_output(
            'Thought: x\nAction: read\nAction Input: {}',
            known_tools=["fs.read", "web.read"],
        )
        assert "unknown tool" in parsed.error

    def test_prose_with_no_grammar_becomes_an_answer(self):
        """Forcing a retry usually yields the same prose again."""
        parsed = parse_react_output("The tests are in tests/, run with make test.", known_tools=KNOWN)

        assert parsed.final_answer.startswith("The tests are in")
        assert not parsed.error

    def test_empty_output_is_an_error(self):
        assert "empty" in parse_react_output("", known_tools=KNOWN).error


class TestActionInputParsing:
    def test_bare_scalar_becomes_a_value_key(self):
        """The registry then names the key it wanted — a usable correction."""
        arguments, error = parse_action_input('"Makefile"')
        assert not error and arguments == {"value": "Makefile"}

    def test_unrepairable_body_reports_an_error(self):
        arguments, error = parse_action_input("path = Makefile (probably)")
        assert error and arguments == {}

    def test_empty_body_is_valid_for_a_no_argument_tool(self):
        assert parse_action_input("") == ({}, "")


# ---------------------------------------------------------------------------
# The turn
# ---------------------------------------------------------------------------


class TestGenerate:
    def test_a_parsed_action_becomes_a_tool_call(self, registry, profile):
        provider = _ScriptedProvider([WELL_FORMED])
        adapter = ReactTextAdapter(provider, profile, registry)

        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "go"}]))

        assert turn.tool_calls[0].tool == "fs.read"
        assert turn.finality is Finality.CONTINUE
        assert turn.text == "I should read the Makefile."

    def test_no_tools_array_is_sent(self, registry, profile):
        """This dialect exists because the provider has no usable tool API."""
        provider = _ScriptedProvider([WELL_FORMED])
        adapter = ReactTextAdapter(provider, profile, registry)

        asyncio.run(adapter.generate(messages=[{"role": "user", "content": "go"}]))

        assert provider.requests[0]["tools"] is None

    def test_the_manual_is_rendered_into_the_system_prompt(self, registry, profile):
        provider = _ScriptedProvider([WELL_FORMED])
        adapter = ReactTextAdapter(provider, profile, registry)

        asyncio.run(adapter.generate(messages=[{"role": "user", "content": "go"}], system="BASE"))

        system = provider.requests[0]["system"]
        assert "BASE" in system
        assert "Action Input" in system
        assert "fs.read(" in system, "compact verbosity renders a signature line"

    def test_the_manual_respects_the_schema_budget(self, registry, tmp_path):
        """A 16k-window model cannot afford forty tool descriptions."""
        profile = resolve_profile("ollama", "llama3:8b", cache_path=tmp_path / "c.json")
        adapter = ReactTextAdapter(_ScriptedProvider([WELL_FORMED]), profile, registry)

        manual = adapter.render_tool_manual()

        listed = [line for line in manual.splitlines() if line.startswith("- ")]
        assert len(listed) == profile.max_tool_schemas

    def test_one_retry_on_a_malformed_turn_and_only_one(self, registry, profile):
        provider = _ScriptedProvider(["Action: nope\nAction Input: {}", WELL_FORMED])
        adapter = ReactTextAdapter(provider, profile, registry)

        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "go"}]))

        assert len(provider.requests) == 2
        assert turn.tool_calls[0].tool == "fs.read"
        retry_prompt = provider.requests[1]["messages"][-1]["content"]
        assert "could not be parsed" in retry_prompt
        assert "unknown tool" in retry_prompt, "the correction must name the defect"

    def test_an_unparseable_action_input_also_earns_the_retry(self, registry, profile):
        provider = _ScriptedProvider([
            "Action: fs.read\nAction Input: path = Makefile (probably)", WELL_FORMED,
        ])
        adapter = ReactTextAdapter(provider, profile, registry)

        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "go"}]))

        assert len(provider.requests) == 2
        assert "not a JSON object" in provider.requests[1]["messages"][-1]["content"]
        assert turn.tool_calls[0].arguments == {"path": "Makefile"}

    def test_an_inline_action_mention_is_treated_as_prose_not_a_call(self, registry, profile):
        """"...garbled Action: nope" is a model talking about the format, not using
        it; a retry there usually returns the same prose."""
        provider = _ScriptedProvider(["I could use an Action: nope here but won't."])
        adapter = ReactTextAdapter(provider, profile, registry)

        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "go"}]))

        assert len(provider.requests) == 1, "no retry spent on prose"
        assert turn.finality is Finality.FINAL
        assert not turn.tool_calls

    def test_a_second_failure_falls_back_to_prose_rather_than_looping(self, registry, profile):
        provider = _ScriptedProvider(["Action: nope", "Action: still-nope"])
        adapter = ReactTextAdapter(provider, profile, registry)

        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "go"}]))

        assert len(provider.requests) == 2, "one retry, not five"
        assert turn.finality is Finality.FINAL
        assert turn.meta["parse_error"]

    def test_final_answer_produces_a_final_turn(self, registry, profile):
        adapter = ReactTextAdapter(
            _ScriptedProvider(["Final Answer: run make test"]), profile, registry,
        )

        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "go"}]))

        assert turn.finality is Finality.FINAL
        assert turn.text == "run make test"


class TestMessageShapes:
    def test_history_is_replayed_in_the_grammar_the_model_must_produce(self, registry, profile):
        """Handing it an OpenAI tool_calls array would teach the wrong format."""
        adapter = ReactTextAdapter(_ScriptedProvider([WELL_FORMED]), profile, registry)
        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "go"}]))

        message = adapter.assistant_message(turn)

        assert message["role"] == "assistant"
        assert "Action: fs.read" in message["content"]
        assert "tool_calls" not in message

    def test_observations_come_back_as_a_user_turn(self, registry, profile):
        adapter = ReactTextAdapter(_ScriptedProvider([]), profile, registry)
        call = ToolCall(id="react_0", tool="fs.read", arguments={"path": "a"})

        messages = adapter.result_messages(call, ToolResult.success(call, "body"))

        assert messages == [{"role": "user", "content": "Observation: body"}]
