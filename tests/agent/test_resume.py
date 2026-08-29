"""Picking a run up where it stopped — Batch V4-F1.

Every test here kills a run by *abandoning* it — the journal is left without a
terminal line, exactly as a `kill -9` leaves it — and then resumes from the file
alone. Nothing is passed from the dead run to the live one except its journal,
because that is the only thing a crash leaves behind.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from gitpilot.agent.contracts import Dialect
from gitpilot.agent.journal import LineType, RunJournal, SeedContext, read_journal
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.providers.base import ProviderResponse, RawToolCall
from gitpilot.agent.resume import (
    ResumeError,
    replay,
    resumable_runs,
    restore,
)
from gitpilot.agent.runner import AgentRunner, RunRequest
from gitpilot.toolkit import ToolCall, ToolResult

from .parity_fixture import fixture_repo  # noqa: F401 — pytest fixture


class _Provider:
    name = "fake"
    model = "gpt-4o-mini"
    supports_native_tools = True
    supports_parallel_tool_calls = False
    supports_streaming = False

    def __init__(self, responses):
        self._responses = list(responses)
        self.seen_messages = []

    async def complete(self, messages, *, tools=None, system=None, **opts):
        self.seen_messages.append([dict(m) for m in messages])
        return self._responses.pop(0) if self._responses else ProviderResponse(text="done")

    def stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


def _profile(tmp_path, family="openai", model="gpt-4o-mini"):
    return resolve_profile(family, model, cache_path=tmp_path / f"{model}.json")


# ---------------------------------------------------------------------------
# Building an interrupted journal
# ---------------------------------------------------------------------------


def _interrupted(tmp_path, *, session="s", run_id="run_dead", stop_after: str = "result"):
    """Write a journal that stops mid-run, the way a crash leaves one.

    *stop_after* controls where the process 'died' — each value is a state a run
    can genuinely be in when it goes away.
    """
    journal = RunJournal(run_id=run_id, session_id=session, root=tmp_path / "journals")
    journal.run_started(SeedContext(
        task="fix the parser", session_id=session, transcript_index=2,
        topology_id="tool_augmented_react", model="gpt-4o-mini", dialect="native",
    ))
    journal.state_change("running", iteration=1)
    if stop_after == "started":
        return journal

    journal.turn(text="Let me look at the parser.", iteration=1, finality="continue")

    call = ToolCall(id="c1", tool="fs.read", arguments={"path": "README.md"})
    journal.tool_call(call, iteration=1)
    if stop_after == "call":
        return journal

    journal.policy_decision(call, verdict="allow", reason="read-only", risk="safe")
    if stop_after == "decision":
        return journal

    journal.tool_result(call, ToolResult.success(call, "# Fixture repo\n"))
    journal.todo(
        [{"id": "t1", "text": "Read the parser", "status": "in_progress"}],
        transitions=[{"text": "Read the parser", "kind": "added", "from": None, "to": "in_progress"}],
    )
    if stop_after == "result":
        return journal

    if stop_after == "approval":
        write = ToolCall(
            id="c2", tool="fs.write",
            arguments={"path": "parser.py", "content": "x = 1\n"},
        )
        journal.tool_call(write, iteration=2)
        journal.policy_decision(
            write, verdict="ask", reason="fs.write needs approval", risk="approval",
        )
        journal.state_change("awaiting_approval", tool="fs.write", call_id="c2")
        return journal

    if stop_after == "finished":
        journal.finish("completed", summary="all done")
    return journal


# ---------------------------------------------------------------------------
# Reading a journal
# ---------------------------------------------------------------------------


class TestReplay:
    def test_an_interrupted_run_is_resumable(self, tmp_path):
        journal = _interrupted(tmp_path)
        run = replay(journal.path)

        assert run.resumable is True
        assert run.seed.task == "fix the parser"
        assert run.iteration == 1

    def test_a_finished_run_is_not(self, tmp_path):
        """Resumability is the *absence* of a finish line, not a stored flag — a
        flag would have to be written by the process that died."""
        journal = _interrupted(tmp_path, stop_after="finished")
        run = replay(journal.path)

        assert run.status == "completed"
        assert run.resumable is False

    @pytest.mark.parametrize("stop_after", ["started", "call", "decision", "result"])
    def test_a_run_is_readable_however_far_it_got(self, tmp_path, stop_after):
        run = replay(_interrupted(tmp_path, stop_after=stop_after).path)
        assert run.resumable is True

    def test_the_calls_and_results_come_back(self, tmp_path):
        run = replay(_interrupted(tmp_path).path)

        assert [call.tool for call in run.calls] == ["fs.read"]
        assert run.calls[0].resolved is True
        assert run.calls[0].verdict == "allow"
        assert run.files_read == ["README.md"]

    def test_a_half_finished_call_is_marked_unresolved(self, tmp_path):
        """The crash-between-decision-and-result case the journal ordering exists
        to make visible."""
        run = replay(_interrupted(tmp_path, stop_after="decision").path)

        assert run.calls[0].resolved is False
        assert run.calls[0].verdict == "allow"

    def test_the_todo_list_comes_back(self, tmp_path):
        run = replay(_interrupted(tmp_path).path)
        assert [item["text"] for item in run.todos] == ["Read the parser"]

    def test_a_pending_approval_is_found(self, tmp_path):
        run = replay(_interrupted(tmp_path, stop_after="approval").path)

        assert run.awaiting_approval is True
        assert run.pending_approval.tool == "fs.write"

    def test_an_answered_approval_is_not_pending(self, tmp_path):
        journal = _interrupted(tmp_path, stop_after="approval")
        write = ToolCall(id="c2", tool="fs.write", arguments={})
        journal.approval(write, approved=True)

        assert replay(journal.path).awaiting_approval is False

    def test_a_journal_with_no_start_is_not_resumable(self, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text('{"seq": 1, "type": "state_change", "state": "running"}\n')

        run = replay(path)
        assert run.resumable is False
        assert run.seed is None

    def test_a_torn_last_line_does_not_break_the_read(self, tmp_path):
        """A crash mid-write is precisely the case resume exists for."""
        journal = _interrupted(tmp_path)
        with journal.path.open("a", encoding="utf-8") as handle:
            handle.write('{"seq": 99, "type": "tool_res')

        assert replay(journal.path).resumable is True


class TestResumableRuns:
    def test_only_the_unfinished_are_listed(self, tmp_path):
        root = tmp_path / "journals"
        _interrupted(tmp_path, run_id="run_a", stop_after="result")
        _interrupted(tmp_path, run_id="run_b", stop_after="finished")

        found = resumable_runs("s", root)

        assert [run.run_id for run in found] == ["run_a"]

    def test_the_summary_says_what_a_user_needs(self, tmp_path):
        _interrupted(tmp_path, run_id="run_a", stop_after="approval")
        summary = resumable_runs("s", tmp_path / "journals")[0].summary()

        assert summary["task"] == "fix the parser"
        assert summary["awaiting_approval"] is True
        assert summary["pending_tool"] == "fs.write"

    def test_a_session_with_no_runs_lists_nothing(self, tmp_path):
        assert resumable_runs("nobody", tmp_path / "journals") == []


# ---------------------------------------------------------------------------
# Resuming for real
# ---------------------------------------------------------------------------


class TestResume:
    def _resume(self, tmp_path, fixture_repo, journal, provider, profile=None):
        return asyncio.run(AgentRunner().resume(
            journal.path,
            session_id="s",
            workspace_root=fixture_repo.path,
            provider=provider,
            profile=profile or _profile(tmp_path),
        ))

    @pytest.mark.parametrize("stop_after", ["started", "call", "decision", "result"])
    def test_a_run_resumes_from_each_state(self, tmp_path, fixture_repo, stop_after):
        journal = _interrupted(tmp_path, stop_after=stop_after)

        result = self._resume(
            tmp_path, fixture_repo, journal,
            _Provider([ProviderResponse(text="picked it up and finished")]),
        )

        assert result.status == "completed"
        assert result.answer == "picked it up and finished"

    def test_the_journal_continues_rather_than_starting_a_second(self, tmp_path, fixture_repo):
        """One run, one record — or the safety invariants would have to be
        asserted over a merge of two files."""
        journal = _interrupted(tmp_path)
        before = len(journal.read())

        result = self._resume(
            tmp_path, fixture_repo, journal, _Provider([ProviderResponse(text="done")]),
        )

        lines = list(read_journal(journal.path))
        assert len(lines) > before
        assert [line["seq"] for line in lines] == list(range(1, len(lines) + 1))
        assert len([l for l in lines if l["type"] == LineType.RUN_STARTED]) == 1
        assert result.context.journal.path == journal.path

    def test_the_history_is_in_front_of_the_model(self, tmp_path, fixture_repo):
        journal = _interrupted(tmp_path)
        provider = _Provider([ProviderResponse(text="done")])

        self._resume(tmp_path, fixture_repo, journal, provider)

        transcript = json.dumps(provider.seen_messages[0])
        assert "fix the parser" in transcript
        assert "Fixture repo" in transcript, "the journaled observation was not replayed"

    def test_the_ledger_and_file_sets_survive(self, tmp_path, fixture_repo):
        journal = _interrupted(tmp_path)

        result = self._resume(
            tmp_path, fixture_repo, journal, _Provider([ProviderResponse(text="done")]),
        )

        assert "README.md" in result.context.files_read
        assert len(result.context.ledger) >= 1

    def test_the_todo_list_survives(self, tmp_path, fixture_repo):
        journal = _interrupted(tmp_path)

        result = self._resume(
            tmp_path, fixture_repo, journal, _Provider([ProviderResponse(text="done")]),
        )

        assert [item.text for item in result.context.todos] == ["Read the parser"]

    def test_the_budget_is_restored_not_reset(self, tmp_path, fixture_repo):
        """A resume that handed back a full budget would be a way to run forever
        by crashing."""
        journal = _interrupted(tmp_path)

        result = self._resume(
            tmp_path, fixture_repo, journal, _Provider([ProviderResponse(text="done")]),
        )

        assert result.context.budgets.iterations >= 2, (
            "the resumed run should carry the iteration it had already spent"
        )

    def test_a_pending_approval_reprompts_rather_than_executing(
        self, tmp_path, fixture_repo,
    ):
        """The case Batch V4-D3 deferred. Executing would run a call nobody
        approved; hanging would wait on a future nobody holds."""
        journal = _interrupted(tmp_path, stop_after="approval")
        provider = _Provider([ProviderResponse(text="I will ask again")])

        result = self._resume(tmp_path, fixture_repo, journal, provider)

        # The file the pending call would have written does not exist.
        assert not (fixture_repo.path / "parser.py").exists()
        # And the model was told what happened.
        transcript = json.dumps(provider.seen_messages[0])
        assert "interrupted while waiting for approval" in transcript
        assert result.status == "completed"

    def test_a_finished_run_is_refused(self, tmp_path, fixture_repo):
        journal = _interrupted(tmp_path, stop_after="finished")

        with pytest.raises(ResumeError, match="already finished"):
            self._resume(
                tmp_path, fixture_repo, journal, _Provider([ProviderResponse(text="x")]),
            )

    def test_resuming_twice_is_refused_the_second_time(self, tmp_path, fixture_repo):
        journal = _interrupted(tmp_path)

        self._resume(
            tmp_path, fixture_repo, journal, _Provider([ProviderResponse(text="done")]),
        )

        with pytest.raises(ResumeError):
            self._resume(
                tmp_path, fixture_repo, journal, _Provider([ProviderResponse(text="again")]),
            )

    def test_a_journal_with_no_seed_is_refused(self, tmp_path, fixture_repo):
        path = tmp_path / "headless.jsonl"
        path.write_text('{"seq": 1, "type": "state_change", "state": "running"}\n')

        with pytest.raises(ResumeError, match="no start record"):
            asyncio.run(AgentRunner().resume(
                path, session_id="s", workspace_root=fixture_repo.path,
                provider=_Provider([]), profile=_profile(tmp_path),
            ))

    def test_the_resume_is_journaled_and_announced(self, tmp_path, fixture_repo):
        from gitpilot.agent_events import AgentEventBus

        journal = _interrupted(tmp_path)
        bus = AgentEventBus()
        seen = []

        async def drive():
            _sub, queue = bus.subscribe()
            await AgentRunner().resume(
                journal.path, bus=bus, session_id="s",
                workspace_root=fixture_repo.path,
                provider=_Provider([ProviderResponse(text="done")]),
                profile=_profile(tmp_path),
            )
            while not queue.empty():
                seen.append(queue.get_nowait())

        asyncio.run(drive())

        resumed = [
            line for line in read_journal(journal.path)
            if line["type"] == LineType.STATE_CHANGE and "resumed_from_seq" in line
        ]
        assert resumed
        assert any(
            event.data.get("status") == "run_resumed" for event in seen
        ), [event.data.get("status") for event in seen]


class TestDialectChange:
    def test_a_run_resumes_on_a_different_dialect(self, tmp_path, fixture_repo):
        """The reason resume replays *facts* rather than a stored transcript: the
        transcript belongs to the dialect, and the operator can swap the model
        between the crash and the recovery."""
        journal = _interrupted(tmp_path)
        lite = _profile(tmp_path, "ollama", "qwen2.5:1.5b")
        assert lite.dialect is Dialect.LITE

        result = asyncio.run(AgentRunner().resume(
            journal.path, session_id="s", workspace_root=fixture_repo.path,
            provider=_Provider([ProviderResponse(text="the parser reads tokens")]),
            profile=lite,
        ))

        assert result.status in ("completed", "degraded")
        assert result.context.model_profile.dialect is Dialect.LITE

    def test_the_change_is_recorded(self, tmp_path, fixture_repo):
        journal = _interrupted(tmp_path)   # recorded as `native`

        asyncio.run(AgentRunner().resume(
            journal.path, session_id="s", workspace_root=fixture_repo.path,
            provider=_Provider([ProviderResponse(text="ok")]),
            profile=_profile(tmp_path, "ollama", "llama3:8b"),
        ))

        line = next(
            line for line in read_journal(journal.path)
            if line["type"] == LineType.STATE_CHANGE and "recorded_dialect" in line
        )
        assert line["recorded_dialect"] == "native"
        assert line["dialect"] == "react_text"

    def test_lite_replays_its_injected_file_contents(self, tmp_path, fixture_repo):
        """A LITE run's context is prefetched file bodies, journaled as `fs.read`
        results — so replaying the results replays the context."""
        from gitpilot.agent.adapter import build_adapter
        from gitpilot.agent.resume import rebuild_messages

        journal = _interrupted(tmp_path)
        lite = _profile(tmp_path, "ollama", "qwen2.5:1.5b")
        adapter = build_adapter(_Provider([]), lite, None)

        messages = rebuild_messages(replay(journal.path), adapter)

        assert "Fixture repo" in json.dumps(messages)


class TestRestore:
    def test_restore_is_usable_without_a_loop(self, tmp_path):
        """A session listing reads journals too, and should not have to build a
        model client to do it."""
        from gitpilot.agent.adapter import build_adapter
        from gitpilot.agent.context import AgentContext
        from gitpilot.toolkit import ToolExecutionContext

        journal = _interrupted(tmp_path)
        run = replay(journal.path)
        profile = _profile(tmp_path)

        ctx = AgentContext(
            run_id="fresh", task=run.seed.task,
            journal=RunJournal(run_id="fresh", session_id="s", root=tmp_path / "j2"),
            model_profile=profile,
            tool_context=ToolExecutionContext(),
        )
        restore(run, ctx, build_adapter(_Provider([]), profile, None))

        assert ctx.iteration == 1
        assert ctx.files_read == {"README.md"}
        assert len(ctx.ledger) == 1
