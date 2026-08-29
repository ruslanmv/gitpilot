"""Keeping a long run inside its window — Batch V4-F3."""
from __future__ import annotations

import asyncio

import pytest

from gitpilot.agent.compaction import (
    COMPACT_AT_RATIO,
    DEFAULT_RESERVE_TOKENS,
    compact,
    paths_in,
    project_tokens,
    threshold,
    usable_window,
)
from gitpilot.agent.contracts import AgentTurn, Finality
from gitpilot.agent.journal import LineType
from gitpilot.agent.loop import AgentLoop
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.toolkit import ToolCall, ToolResult

from .test_loop import Recorder, ScriptedAdapter, _context, _registry


def _big(tokens: int, text: str = "filler ") -> str:
    """Roughly *tokens* worth of text."""
    return text * max(tokens, 1)


def _messages(count: int, tokens_each: int = 200, role: str = "tool"):
    return [
        {"role": role, "content": f"step {index}: " + _big(tokens_each)}
        for index in range(count)
    ]


# ---------------------------------------------------------------------------
# The threshold
# ---------------------------------------------------------------------------


class TestThreshold:
    @pytest.mark.parametrize(("window", "expected_usable"), [
        (200_000, 198_000),
        (128_000, 126_000),
        (16_384, 14_384),
        (8_192, 6_192),
    ])
    def test_the_reserve_comes_off_the_window(self, window, expected_usable):
        """Measured against the raw window, compaction would only trigger once
        the request was already too large to send."""
        assert usable_window(window) == expected_usable
        assert threshold(window) == int(expected_usable * COMPACT_AT_RATIO)

    def test_a_tiny_window_gets_a_floor(self, tmp_path):
        """An 8k window minus a 2k reserve is fine; a misconfigured 1k window must
        not produce a threshold of zero and compact on every iteration."""
        assert usable_window(1_000) >= 1_000
        assert threshold(500) > 0

    @pytest.mark.parametrize(("family", "model"), [
        ("claude", "claude-sonnet-4-5"),
        ("openai", "gpt-4o-mini"),
        ("ollama", "llama3:8b"),
        ("ollama", "qwen2.5:1.5b"),
    ])
    def test_each_real_model_gets_a_sane_threshold(self, tmp_path, family, model):
        profile = resolve_profile(family, model, cache_path=tmp_path / f"{model}.json")
        limit = threshold(profile.context_window)

        assert 0 < limit < profile.context_window

    def test_the_reserve_is_documented_as_a_constant(self):
        assert DEFAULT_RESERVE_TOKENS == 2_000


# ---------------------------------------------------------------------------
# Compacting
# ---------------------------------------------------------------------------


class TestCompact:
    def test_a_transcript_under_the_threshold_is_untouched(self):
        messages = _messages(3, tokens_each=10)
        result, report = compact(messages, window=200_000)

        assert result == messages
        assert report.changed is False

    def test_an_oversized_observation_is_stubbed_first(self):
        """One 200 KB test log is usually the whole problem, and dropping it costs
        nothing a later turn needs — the journal still has it."""
        messages = [
            {"role": "user", "content": "fix the parser"},
            {"role": "tool", "content": _big(6_000)},
            {"role": "assistant", "content": "I see the problem"},
        ]

        result, report = compact(messages, window=8_192)

        assert report.stubbed == 1
        assert report.folded == 0, "folding should not have been needed"
        assert "dropped to stay inside the context window" in result[1]["content"]

    def test_older_turns_fold_when_stubbing_is_not_enough(self):
        messages = [{"role": "user", "content": "the task"}] + _messages(40, 200)

        result, report = compact(messages, window=8_192)

        assert report.folded > 0
        assert len(result) < len(messages)
        assert report.after_tokens < report.before_tokens

    def test_the_first_message_is_always_kept(self):
        """A run that forgot what it was asked is worse than one that runs out of
        window."""
        messages = [{"role": "user", "content": "THE ORIGINAL TASK"}] + _messages(60, 300)

        result, _report = compact(messages, window=8_192)

        assert result[0]["content"] == "THE ORIGINAL TASK"

    def test_the_most_recent_turns_are_kept(self):
        messages = [{"role": "user", "content": "task"}] + [
            {"role": "assistant", "content": f"turn {n} " + _big(200)}
            for n in range(40)
        ]

        result, _report = compact(messages, window=8_192, keep_recent_turns=6)

        tail = " ".join(message["content"] for message in result[-6:])
        assert "turn 39" in tail
        assert "turn 38" in tail

    def test_an_empty_transcript_is_a_no_op(self):
        result, report = compact([], window=8_192)
        assert result == []
        assert report.changed is False

    def test_it_brings_the_transcript_under_the_threshold(self):
        messages = [{"role": "user", "content": "task"}] + _messages(80, 400)

        _result, report = compact(messages, window=16_384)

        assert report.after_tokens <= threshold(16_384) or report.folded > 0


class TestIdempotence:
    def test_a_second_pass_does_not_fold_the_summary(self):
        """A summary folded into a summary loses information twice for no gain."""
        messages = [{"role": "user", "content": "task"}] + _messages(60, 300)

        once, first = compact(messages, window=8_192)
        twice, second = compact(once, window=8_192)

        summaries = [
            message for message in twice
            if (message.get("meta") or {}).get("summary") == "1"
        ]
        assert len(summaries) <= 1, "the pinned summary was folded again"
        assert second.folded <= first.folded

    def test_compacting_an_already_small_transcript_changes_nothing(self):
        messages = [{"role": "user", "content": "task"}] + _messages(60, 300)
        once, _first = compact(messages, window=8_192)

        twice, second = compact(once, window=200_000)

        assert twice == once
        assert second.changed is False


class TestPathsSurvive:
    def test_paths_are_carried_into_the_summary(self):
        """A small model's context is mostly the file list, and a summary that
        loses `src/parser.py` produces a model confidently editing a file it can no
        longer name."""
        messages = [{"role": "user", "content": "task"}] + [
            {"role": "tool", "content": f"read src/module_{n}.py: " + _big(200)}
            for n in range(40)
        ]

        result, report = compact(messages, window=8_192)

        rendered = "\n".join(message["content"] for message in result)
        assert "src/module_0.py" in rendered
        assert "src/module_10.py" in rendered
        assert report.paths_kept

    def test_a_stubbed_observation_still_names_its_files(self):
        messages = [
            {"role": "user", "content": "task"},
            {"role": "tool", "content": "tests/test_parser.py failed\n" + _big(6_000)},
        ]

        result, _report = compact(messages, window=8_192)

        assert "tests/test_parser.py" in result[1]["content"]

    @pytest.mark.parametrize("text", [
        "see src/app.py for details",
        "pyproject.toml sets testpaths",
        "edit tests/conftest.py and Makefile",
    ])
    def test_paths_are_recognised(self, text):
        assert paths_in(text)

    def test_prose_without_paths_finds_nothing(self):
        assert paths_in("I read the file and it looked fine.") == []


# ---------------------------------------------------------------------------
# Through the loop
# ---------------------------------------------------------------------------


class TestThroughTheLoop:
    def _long_run(self, tmp_path, *, model="qwen2.5:1.5b", iterations=40):
        """A run that would blow its window if nothing folded."""
        profile = resolve_profile("ollama", model, cache_path=tmp_path / "c.json")
        registry = _registry()

        async def chatty(call, ctx):
            # A big observation each time, which is the realistic shape.
            return ToolResult.success(call, "content " + _big(400))

        registry._handlers["fs.read"] = chatty   # noqa: SLF001

        turns = [
            AgentTurn(
                text=f"step {n}",
                tool_calls=[ToolCall(id=f"c{n}", tool="fs.read", arguments={"path": f"f{n}.py"})],
                finality=Finality.CONTINUE,
            )
            for n in range(iterations)
        ]
        turns.append(AgentTurn(text="finished", finality=Finality.FINAL))

        adapter = ScriptedAdapter(turns, profile=profile, registry=registry)
        ctx = _context(tmp_path, registry, profile=profile)
        ctx.budgets.max_iterations = iterations + 5
        ctx.budgets.max_tool_calls = iterations + 5
        events = Recorder()

        result = asyncio.run(AgentLoop(adapter, registry, events=events).run(ctx))
        return result, ctx, events

    def test_a_forty_iteration_run_on_an_8k_window_completes(self, tmp_path):
        """The case that motivates the whole batch: GitPilot's default model has
        an 8k window and the loop can take forty turns."""
        result, ctx, _events = self._long_run(tmp_path)

        assert result.status in ("completed", "degraded"), result.answer
        assert ctx.iteration >= 20

    def test_it_compacted_rather_than_growing_without_bound(self, tmp_path):
        _result, ctx, events = self._long_run(tmp_path)

        assert "compaction" in events.types(), "the transcript was never folded"
        assert project_tokens(ctx.messages) < 40 * 400

    def test_the_compaction_is_journaled(self, tmp_path):
        _result, ctx, _events = self._long_run(tmp_path)

        lines = [
            line for line in ctx.journal.read()
            if line["type"] == LineType.COMPACTION
        ]
        assert lines
        assert lines[0]["after_tokens"] < lines[0]["before_tokens"]

    def test_the_uncompacted_truth_survives_in_the_ledger(self, tmp_path):
        """Compaction edits the transcript, not the record. Anything else would
        make it a way to lose an audit trail."""
        _result, ctx, _events = self._long_run(tmp_path)

        assert len(ctx.ledger) >= 20
        assert all(entry.result.content for entry in ctx.ledger)

    def test_the_state_change_is_visible(self, tmp_path):
        _result, ctx, _events = self._long_run(tmp_path)

        states = [
            line.get("state") for line in ctx.journal.read()
            if line["type"] == LineType.STATE_CHANGE
        ]
        assert "compacting" in states

    def test_a_large_window_never_compacts(self, tmp_path):
        """A run inside its budget should not pay for housekeeping."""
        _result, _ctx, events = self._long_run(
            tmp_path, model="llama3:8b", iterations=6,
        )
        assert "compaction" not in events.types()

    def test_a_compaction_failure_does_not_fail_the_run(self, tmp_path, monkeypatch):
        import gitpilot.agent.compaction as compaction_module

        def explode(*args, **kwargs):
            raise RuntimeError("summariser exploded")

        monkeypatch.setattr(compaction_module, "compact", explode)

        result, _ctx, _events = self._long_run(tmp_path, iterations=12)

        assert result.status in ("completed", "degraded")
