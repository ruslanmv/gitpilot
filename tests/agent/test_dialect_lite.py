"""The LITE dialect — Batch V4-B5.

GitPilot ships configured for qwen2.5:1.5b, so this is the default path for a
fresh install rather than an edge case.
"""
from __future__ import annotations

import asyncio

import pytest

from gitpilot.agent.contracts import Finality
from gitpilot.agent.dialects.lite import (
    MAX_INVESTIGATE_ROUNDS,
    MAX_READS_PER_TURN,
    PHASE_ACT,
    PHASE_ANSWER,
    PHASE_INVESTIGATE,
    LiteAdapter,
    classify_intent,
    extract_content_blocks,
    extract_read_paths,
    parse_action_lines,
)
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.providers.base import ProviderResponse
from gitpilot.toolkit import ToolCall, ToolResult
from gitpilot.toolkit.builtins import build_default_registry

REPO_FILES = ["README.md", "src/app.py", "src/util.py", "tests/test_app.py"]


@pytest.fixture()
def registry():
    return build_default_registry()


@pytest.fixture()
def profile(tmp_path):
    return resolve_profile("ollama", "qwen2.5:1.5b", cache_path=tmp_path / "c.json")


class _ScriptedProvider:
    name = "ollama"
    supports_native_tools = False
    supports_parallel_tool_calls = False
    supports_streaming = False

    def __init__(self, texts):
        self.model = "qwen2.5:1.5b"
        self._texts = list(texts)
        self.requests = []

    async def complete(self, messages, *, tools=None, system=None, **opts):
        self.requests.append({"messages": [dict(m) for m in messages], "system": system})
        return ProviderResponse(text=self._texts.pop(0) if self._texts else "")

    def stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


def _adapter(texts, profile, registry, task="explain the app", files=REPO_FILES):
    adapter = LiteAdapter(_ScriptedProvider(texts), profile, registry)
    adapter.prepare(task, files)
    return adapter


# ---------------------------------------------------------------------------
# Protocol parsing
# ---------------------------------------------------------------------------


class TestProtocolParsing:
    def test_hallucinated_read_path_is_dropped(self):
        """Telling a 1.5B model it hallucinated tends to produce another one."""
        paths = extract_read_paths("READ src/app.py\nREAD src/imaginary.py", REPO_FILES)

        assert paths == ["src/app.py"]

    def test_hallucinated_action_target_is_dropped(self):
        actions = parse_action_lines("MODIFY src/nope.py\nDELETE src/util.py", REPO_FILES)

        assert actions == [("DELETE", "src/util.py")]

    def test_create_over_an_existing_file_becomes_modify(self):
        """A CREATE on an existing path used to overwrite it silently."""
        assert parse_action_lines("CREATE src/app.py", REPO_FILES) == [("MODIFY", "src/app.py")]

    def test_create_of_a_new_file_is_allowed(self):
        assert parse_action_lines("CREATE src/new.py", REPO_FILES) == [("CREATE", "src/new.py")]

    def test_duplicate_actions_are_collapsed(self):
        assert parse_action_lines(
            "DELETE src/util.py\nDELETE src/util.py", REPO_FILES,
        ) == [("DELETE", "src/util.py")]

    def test_content_blocks_are_keyed_by_path(self):
        text = "CREATE src/new.py\n```python src/new.py\nx = 1\n```"
        assert extract_content_blocks(text) == {"src/new.py": "x = 1\n"}

    def test_content_block_with_only_a_path_on_the_info_line(self):
        assert extract_content_blocks("```src/new.py\ny = 2\n```") == {"src/new.py": "y = 2\n"}

    def test_intent_classification_uses_the_shared_regex_tables(self):
        assert classify_intent("delete the demo files") == "action"
        assert classify_intent("what does this project do?") == "question"


# ---------------------------------------------------------------------------
# The micro-loop
# ---------------------------------------------------------------------------


class TestInvestigate:
    def test_read_lines_become_journalable_fs_read_calls(self, registry, profile):
        """Every path the model touches passes the registry, so the journal has it
        and Batch V4-D1 can put policy in front of it."""
        adapter = _adapter(["READ src/app.py\nREAD README.md"], profile, registry)

        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "explain"}]))

        assert [c.tool for c in turn.tool_calls] == ["fs.read", "fs.read"]
        assert [c.arguments["path"] for c in turn.tool_calls] == ["src/app.py", "README.md"]
        assert turn.finality is Finality.CONTINUE

    def test_reads_are_capped_per_turn(self, registry, profile):
        adapter = _adapter(["\n".join(f"READ {p}" for p in REPO_FILES)], profile, registry)

        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "explain"}]))

        assert len(turn.tool_calls) == MAX_READS_PER_TURN

    def test_investigate_rounds_are_bounded(self, registry, profile):
        """An unbounded micro-loop on a small model is how a run burns its budget."""
        adapter = _adapter(
            ["READ src/app.py", "READ src/util.py", "READ tests/test_app.py", "final answer"],
            profile, registry,
        )

        for _ in range(MAX_INVESTIGATE_ROUNDS):
            asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))

        assert adapter.state.rounds_used == MAX_INVESTIGATE_ROUNDS
        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))
        assert turn.finality is Finality.FINAL, "budget spent, so answer instead of looking again"

    def test_ready_moves_on_without_reads(self, registry, profile):
        adapter = _adapter(["READY", "the app greets people"], profile, registry)

        first = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))
        assert not first.tool_calls
        assert adapter.state.phase == PHASE_ANSWER

    def test_already_seen_files_are_not_reread(self, registry, profile):
        adapter = _adapter(["READ src/app.py", "READ src/app.py"], profile, registry)

        asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))
        second = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))

        assert not second.tool_calls

    def test_prefetch_produces_fs_read_calls_too(self, registry, profile):
        adapter = _adapter([], profile, registry)

        calls = adapter.prefetch_calls(["README.md", "src/imaginary.py"])

        assert [c.tool for c in calls] == ["fs.read"]
        assert calls[0].arguments["path"] == "README.md"

    def test_file_list_is_at_the_prompt_tail(self, registry, profile):
        """A small model weights the last segment most heavily."""
        adapter = _adapter(["READY"], profile, registry)
        asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))

        instruction = adapter._instruction_for(PHASE_INVESTIGATE)
        assert instruction.rstrip().endswith("tests/test_app.py")


class TestAct:
    def _action_adapter(self, texts, profile, registry):
        return _adapter(texts, profile, registry, task="delete the util module")

    def test_actions_become_write_and_delete_calls(self, registry, profile):
        adapter = self._action_adapter(
            ["READY", "DELETE src/util.py\nCREATE src/new.py\n```src/new.py\nx = 1\n```"],
            profile, registry,
        )
        asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))

        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))

        assert [c.tool for c in turn.tool_calls] == ["fs.delete", "fs.write"]
        assert turn.tool_calls[1].arguments["content"] == "x = 1\n"
        assert turn.finality is Finality.FINAL_AFTER_TOOLS

    def test_a_write_without_content_is_skipped_not_emptied(self, registry, profile):
        """Writing an empty file over someone's source is worse than doing nothing."""
        adapter = self._action_adapter(["READY", "MODIFY src/app.py"], profile, registry)
        asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))

        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))

        assert not turn.tool_calls
        assert turn.meta["degraded_to_answer"] is True

    def test_no_valid_actions_degrades_to_qa(self, registry, profile):
        adapter = self._action_adapter(
            ["READY", "I think you should remove the util module."], profile, registry,
        )
        asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))

        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))

        assert turn.finality is Finality.FINAL
        assert turn.meta["degraded_to_answer"] is True
        assert "util module" in turn.text

    def test_conclude_is_true_when_every_action_applied(self, registry, profile):
        adapter = self._action_adapter(["READY", "DELETE src/util.py"], profile, registry)
        asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))
        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))

        call = turn.tool_calls[0]
        assert adapter.conclude(turn, [ToolResult.success(call, "Deleted: True")]) is True

    def test_conclude_is_false_when_an_action_failed(self, registry, profile):
        """A denied or failed write means there is more to do."""
        adapter = self._action_adapter(["READY", "DELETE src/util.py"], profile, registry)
        asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))
        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))

        call = turn.tool_calls[0]
        denied = ToolResult.denied_for(call, "fs.delete not granted")

        assert adapter.conclude(turn, [denied]) is False
        assert adapter.state.phase == PHASE_ACT, "stay in act so the model can react"

    def test_conclude_ignores_turns_that_are_not_final_after_tools(self, registry, profile):
        from gitpilot.agent.contracts import AgentTurn

        adapter = _adapter([], profile, registry)
        assert adapter.conclude(AgentTurn(finality=Finality.CONTINUE), []) is False


class TestGuards:
    def test_a_refusal_ends_the_turn_rather_than_becoming_actions(self, registry, profile):
        adapter = _adapter(
            ["I'm sorry, but I cannot assist with that request."], profile, registry,
        )

        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))

        assert turn.finality is Finality.FINAL
        assert turn.meta.get("refusal") is True
        assert not turn.tool_calls


class TestMessageShapes:
    def test_file_contents_are_truncated_to_the_snippet_budget(self, registry, profile):
        """Past this a small model loses the instruction it was given."""
        from gitpilot.agent.dialects.lite import SNIPPET_CHAR_LIMIT

        adapter = _adapter([], profile, registry)
        call = ToolCall(id="c1", tool="fs.read", arguments={"path": "src/app.py"})

        messages = adapter.result_messages(call, ToolResult.success(call, "x" * 5000))

        assert len(messages[0]["content"]) < SNIPPET_CHAR_LIMIT + 100
        assert messages[0]["content"].endswith("truncated")
        assert messages[0]["role"] == "user"

    def test_read_requests_are_recorded_in_history(self, registry, profile):
        adapter = _adapter(["READ src/app.py"], profile, registry)
        turn = asyncio.run(adapter.generate(messages=[{"role": "user", "content": "x"}]))

        assert adapter.assistant_message(turn) == {
            "role": "assistant", "content": "READ src/app.py",
        }


class TestProfileIntegration:
    def test_the_default_install_lands_here(self, profile):
        """qwen2.5:1.5b is what a fresh GitPilot is configured for."""
        from gitpilot.agent.contracts import Dialect

        assert profile.dialect is Dialect.LITE
        assert profile.max_tool_schemas <= 6
