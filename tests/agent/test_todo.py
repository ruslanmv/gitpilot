"""The TODO list — Batch V4-E1."""
from __future__ import annotations

import asyncio

import pytest

from gitpilot.agent.contracts import AgentTurn, Finality
from gitpilot.agent.journal import LineType
from gitpilot.agent.loop import AgentLoop
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.toolkit import ToolCall
from gitpilot.toolkit.builtins import build_default_registry
from gitpilot.toolkit.todo import (
    MAX_ITEMS,
    diff,
    normalise,
    phase_todo,
    summarise,
)

from .test_loop import Recorder, ScriptedAdapter, _context


def _registry():
    return build_default_registry(
        include_git=False, include_forge=False, include_terminal=False,
    )


def _write_call(items, call_id="t1"):
    return ToolCall(id=call_id, tool="todo.write", arguments={"items": items})


def _run(tmp_path, turns, profile=None):
    registry = _registry()
    adapter = ScriptedAdapter(turns, profile=profile, registry=registry)
    ctx = _context(tmp_path, registry, profile=profile)
    events = Recorder()
    asyncio.run(AgentLoop(adapter, registry, events=events).run(ctx))
    return ctx, events


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestNormalise:
    def test_a_well_formed_list_passes_through(self):
        items, problems = normalise([
            {"text": "read the config", "status": "completed"},
            {"text": "fix the parser", "status": "in_progress"},
        ])
        assert problems == []
        assert [item["status"] for item in items] == ["completed", "in_progress"]

    def test_a_bare_string_is_accepted_as_pending(self):
        """An unambiguous mistake, so correcting it beats a round trip."""
        items, problems = normalise(["do the thing"])
        assert items == [{"text": "do the thing", "status": "pending"}]
        assert problems == []

    @pytest.mark.parametrize("alias", ["task", "title"])
    def test_the_common_key_aliases_are_read(self, alias):
        items, _problems = normalise([{alias: "do it", "status": "pending"}])
        assert items[0]["text"] == "do it"

    def test_an_unknown_status_is_reported(self):
        items, problems = normalise([{"text": "x", "status": "doing"}])
        assert items == []
        assert "doing" in problems[0]

    def test_an_item_without_text_is_reported(self):
        _items, problems = normalise([{"status": "pending"}])
        assert "no `text`" in problems[0]

    def test_a_non_list_is_reported(self):
        _items, problems = normalise("read the file")
        assert "must be an array" in problems[0]

    def test_an_empty_list_is_reported(self):
        _items, problems = normalise([])
        assert "empty" in problems[0]

    def test_an_absurd_list_is_trimmed_and_reported(self):
        items, problems = normalise(
            [{"text": f"step {n}", "status": "pending"} for n in range(MAX_ITEMS + 10)],
        )
        assert len(items) == MAX_ITEMS
        assert "too many" in problems[0]

    def test_two_in_progress_is_noted_but_not_rejected(self):
        """A model doing two things at once is describing something real."""
        items, problems = normalise([
            {"text": "a", "status": "in_progress"},
            {"text": "b", "status": "in_progress"},
        ])
        assert len(items) == 2
        assert "one at a time" in problems[0]


class TestDiff:
    def test_a_status_change_is_a_transition(self):
        before = [{"text": "a", "status": "pending"}]
        after = [{"text": "a", "status": "in_progress"}]

        assert [t.to_dict() for t in diff(before, after)] == [
            {"text": "a", "kind": "changed", "from": "pending", "to": "in_progress"},
        ]

    def test_a_new_item_is_an_addition(self):
        transitions = diff([], [{"text": "a", "status": "pending"}])
        assert transitions[0].kind == "added"

    def test_a_dropped_item_is_a_removal(self):
        transitions = diff([{"text": "a", "status": "pending"}], [])
        assert transitions[0].kind == "removed"

    def test_reordering_is_not_a_rewrite(self):
        """Matched on text, because positional matching would report a reorder as
        every item changing."""
        before = [{"text": "a", "status": "pending"}, {"text": "b", "status": "pending"}]
        after = [{"text": "b", "status": "pending"}, {"text": "a", "status": "pending"}]

        assert diff(before, after) == []

    def test_an_unchanged_list_produces_nothing(self):
        items = [{"text": "a", "status": "completed"}]
        assert diff(items, items) == []


class TestSummarise:
    def test_it_names_what_is_in_progress(self):
        text = summarise([
            {"text": "read", "status": "completed"},
            {"text": "fix the parser", "status": "in_progress"},
        ])
        assert "1 completed" in text
        assert "fix the parser" in text

    def test_an_empty_list_says_so(self):
        assert "empty" in summarise([])


# ---------------------------------------------------------------------------
# Through the loop
# ---------------------------------------------------------------------------


class TestThroughTheLoop:
    def test_the_list_reaches_the_context_and_the_journal(self, tmp_path):
        ctx, _events = _run(tmp_path, [
            AgentTurn(
                tool_calls=[_write_call([
                    {"text": "read the config", "status": "in_progress"},
                    {"text": "fix the parser", "status": "pending"},
                ])],
                finality=Finality.CONTINUE,
            ),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])

        assert [item.text for item in ctx.todos] == ["read the config", "fix the parser"]
        lines = [line for line in ctx.journal.read() if line["type"] == LineType.TODO]
        assert len(lines) == 1
        assert len(lines[0]["items"]) == 2

    def test_the_transitions_are_journaled(self, tmp_path):
        ctx, _events = _run(tmp_path, [
            AgentTurn(
                tool_calls=[_write_call([{"text": "a", "status": "pending"}], "t1")],
                finality=Finality.CONTINUE,
            ),
            AgentTurn(
                tool_calls=[_write_call([{"text": "a", "status": "completed"}], "t2")],
                finality=Finality.CONTINUE,
            ),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])

        lines = [line for line in ctx.journal.read() if line["type"] == LineType.TODO]
        assert lines[0]["transitions"][0]["kind"] == "added"
        assert lines[1]["transitions"] == [
            {"text": "a", "kind": "changed", "from": "pending", "to": "completed"},
        ]

    def test_ids_are_stable_across_a_rewrite(self, tmp_path):
        """So a UI can animate a status change instead of redrawing the panel."""
        ctx, _events = _run(tmp_path, [
            AgentTurn(
                tool_calls=[_write_call([
                    {"text": "a", "status": "pending"},
                    {"text": "b", "status": "pending"},
                ], "t1")],
                finality=Finality.CONTINUE,
            ),
            AgentTurn(
                tool_calls=[_write_call([
                    {"text": "a", "status": "completed"},
                    {"text": "b", "status": "in_progress"},
                ], "t2")],
                finality=Finality.CONTINUE,
            ),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])

        assert [item.id for item in ctx.todos] == ["t1", "t2"]

    def test_an_ill_formed_list_comes_back_as_a_correction(self, tmp_path):
        """The model has to be able to fix it, so the observation teaches the
        format rather than ending the run."""
        ctx, _events = _run(tmp_path, [
            AgentTurn(
                tool_calls=[_write_call([{"text": "x", "status": "doing"}])],
                finality=Finality.CONTINUE,
            ),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])

        entry = ctx.ledger.entries[0]
        assert entry.result.ok is False
        assert "doing" in entry.result.content
        assert ctx.todos == []

    def test_the_event_carries_the_list_and_the_transitions(self, tmp_path):
        _ctx, events = _run(tmp_path, [
            AgentTurn(
                tool_calls=[_write_call([{"text": "a", "status": "in_progress"}])],
                finality=Finality.CONTINUE,
            ),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])

        payload = events.payload("todo_updated")
        assert payload["source"] == "model"
        assert payload["items"] == [{"id": "t1", "text": "a", "status": "in_progress"}]
        assert payload["transitions"][0]["to"] == "in_progress"


# ---------------------------------------------------------------------------
# LITE — the engine keeps the list
# ---------------------------------------------------------------------------


class TestLiteEngineTodo:
    def test_the_tool_is_not_offered(self, tmp_path):
        """A 1.5B model asked to maintain structured state alongside its work
        does neither well."""
        registry = _registry()
        render = registry.render(lite_only=True)

        assert "todo.write" not in {schema["name"] for schema in render.schemas}
        assert ("todo.write", "not-lite-compatible") in {
            (item.id, item.reason) for item in render.dropped
        }

    @pytest.mark.parametrize(("phase", "expected"), [
        ("investigate", ["in_progress", "pending", "pending"]),
        ("act", ["completed", "in_progress", "pending"]),
        ("answer", ["completed", "completed", "in_progress"]),
    ])
    def test_the_phase_maps_to_a_list(self, phase, expected):
        items = phase_todo(phase, intent="action")
        assert [item["status"] for item in items] == expected

    def test_a_question_gets_no_change_step(self):
        """Showing a permanently-pending "make the change" row for a question is
        a fiction about what the run is doing."""
        items = phase_todo("investigate", intent="question")
        assert [item["text"] for item in items] == [
            "Read the relevant files", "Check the result",
        ]

    def test_the_engine_maintains_it_during_a_run(self, tmp_path):
        profile = resolve_profile("ollama", "qwen2.5:1.5b", cache_path=tmp_path / "c.json")
        registry = _registry()
        adapter = ScriptedAdapter(
            [AgentTurn(text="the answer", finality=Finality.FINAL)],
            profile=profile, registry=registry,
        )
        adapter.state = type("_S", (), {"phase": "investigate", "intent": "action"})()
        ctx = _context(tmp_path, registry, profile=profile)
        events = Recorder()

        asyncio.run(AgentLoop(adapter, registry, events=events).run(ctx))

        assert [item.text for item in ctx.todos] == [
            "Read the relevant files", "Make the change", "Check the result",
        ]
        assert events.payload("todo_updated")["source"] == "engine"

    def test_an_unchanged_phase_does_not_redraw(self, tmp_path):
        profile = resolve_profile("ollama", "qwen2.5:1.5b", cache_path=tmp_path / "c.json")
        registry = _registry()
        adapter = ScriptedAdapter(
            [
                AgentTurn(text="thinking", finality=Finality.CONTINUE),
                AgentTurn(text="done", finality=Finality.FINAL),
            ],
            profile=profile, registry=registry,
        )
        adapter.state = type("_S", (), {"phase": "investigate", "intent": "question"})()
        ctx = _context(tmp_path, registry, profile=profile)
        events = Recorder()

        asyncio.run(AgentLoop(adapter, registry, events=events).run(ctx))

        assert events.types().count("todo_updated") == 1

    def test_a_native_run_gets_no_engine_list(self, tmp_path):
        """The model owns its list when it has the tool; two owners would fight."""
        ctx, events = _run(tmp_path, [AgentTurn(text="done", finality=Finality.FINAL)])

        assert ctx.todos == []
        assert "todo_updated" not in events.types()


class TestPromptGuidance:
    def test_the_instruction_is_about_visibility_not_planning(self, tmp_path):
        from gitpilot.agent.prompts import build_system_prompt

        prompt = build_system_prompt(
            resolve_profile("claude", "claude-sonnet-4-5", cache_path=tmp_path / "c.json"),
            with_todo=True,
        )

        assert "todo.write" in prompt
        assert "rewrite the list" in prompt
        assert "not a plan you owe the user" in prompt

    def test_lite_is_never_told_about_a_tool_it_does_not_have(self, tmp_path):
        from gitpilot.agent.prompts import build_system_prompt

        prompt = build_system_prompt(
            resolve_profile("ollama", "qwen2.5:1.5b", cache_path=tmp_path / "c.json"),
            with_todo=True,
        )

        assert "todo.write" not in prompt
