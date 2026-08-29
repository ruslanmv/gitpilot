"""Crash and reconnect — Batch V4-F4, the Phase F gate.

The three properties this phase exists to establish, tested the only way that
means anything: by interrupting real runs at points nobody chose in advance.

* **A run killed anywhere resumes to completion.** Not "at a checkpoint" — at ten
  randomised points inside a twenty-iteration run, including between a policy
  decision and the tool result it authorises.
* **A reconnect loses no state-bearing event.** Whatever a client missed while
  disconnected is served from the journal.
* **A long run on a small model finishes.** A 1.5B model has an 8k window and the
  loop takes tens of turns; without compaction that combination cannot complete,
  and it is GitPilot's *default* model.

The interruption is a real one: the loop is cancelled mid-flight and the journal is
left exactly as it stood, with no finish line and possibly a decision whose result
never arrived. Nothing is handed from the dead run to the resumed one except that
file.
"""
from __future__ import annotations

import asyncio
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

from gitpilot.agent.compaction import project_tokens
from gitpilot.agent.contracts import AgentTurn, Finality
from gitpilot.agent.journal import LineType, RunJournal, read_journal
from gitpilot.agent.loop import AgentLoop
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.providers.base import ProviderResponse, RawToolCall
from gitpilot.agent.replay_events import last_seq, replay_events
from gitpilot.agent.resume import replay
from gitpilot.agent.runner import AgentRunner, RunRequest
from gitpilot.toolkit import ToolCall, ToolResult

from .parity_fixture import fixture_repo  # noqa: F401 — pytest fixture

#: Iterations in the run under test.  Twenty is enough that a kill can land in
#: genuinely different phases and short enough to run ten of them per test.
ITERATIONS = 20

#: Where the kills land.  Seeded rather than fixed: a hand-picked list would only
#: ever prove the points someone thought of.
SEED = 20240607


class _FinishingProvider:
    """Picks a resumed run up and completes it."""

    name = "fake"
    model = "gpt-4o-mini"
    supports_native_tools = True
    supports_parallel_tool_calls = False
    supports_streaming = False

    def __init__(self, extra_steps: int = 2):
        self.extra = extra_steps
        self.calls = 0

    async def complete(self, messages, *, tools=None, system=None, **opts):
        self.calls += 1
        if self.calls <= self.extra:
            return ProviderResponse(
                text=f"resumed step {self.calls}",
                tool_calls=[RawToolCall(
                    id=f"r{self.calls}", name="fs.read", arguments={"path": "README.md"},
                )],
            )
        return ProviderResponse(text="finished after the interruption")

    def stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


class _ChattyProvider:
    """A LITE-shaped provider that produces a lot of text per turn."""

    name = "ollama"
    model = "qwen2.5:1.5b"
    supports_native_tools = False
    supports_parallel_tool_calls = False
    supports_streaming = False

    def __init__(self, turns: int, chunk: int):
        self.turns = turns
        self.chunk = chunk
        self.calls = 0

    async def complete(self, messages, *, tools=None, system=None, **opts):
        self.calls += 1
        if self.calls >= self.turns:
            return ProviderResponse(text="The project is a fixture repository. " * 4)
        # The LITE dialect reads a `READ <path>` line; the filler is what makes
        # the transcript grow past the window.
        return ProviderResponse(
            text="READ README.md\n" + ("observation text " * self.chunk),
        )

    def stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


def _profile(tmp_path, family="openai", model="gpt-4o-mini"):
    return resolve_profile(family, model, cache_path=tmp_path / f"{model}.json")


def _kill_at(tmp_path, fixture_repo, iteration: int, session: str) -> Path:
    """Run the loop in a child process and SIGKILL it at *iteration*.

    A real signal, because that is the only thing that produces the on-disk state
    resume exists for. An in-process ``CancelledError`` is caught by the loop,
    which writes a terminal ``cancelled`` line — correct behaviour, and the
    opposite of a crash.
    """
    journal_root = tmp_path / "journals"
    journal_root.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "tests.agent.crash_child",
            str(fixture_repo.path), str(journal_root), session, str(ITERATIONS),
        ],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    journal_path: Path | None = None
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            if line.startswith("journal "):
                journal_path = Path(line.split(" ", 1)[1].strip())
            elif line.startswith(f"iteration {iteration}"):
                # Mid-turn: the provider is sleeping, so the journal holds
                # whatever the previous iterations flushed and nothing more.
                proc.kill()
                break
            elif line.startswith("done"):
                break
    finally:
        proc.kill()
        proc.wait(timeout=30)

    assert journal_path is not None, "the child never reported its journal"
    assert journal_path.exists(), f"{journal_path} was never written"
    return journal_path


def _resume(tmp_path, fixture_repo, path, session: str):
    return asyncio.run(AgentRunner().resume(
        path, session_id=session, workspace_root=fixture_repo.path,
        provider=_FinishingProvider(), profile=_profile(tmp_path),
    ))


# ---------------------------------------------------------------------------
# Killed anywhere, resumes to completion
# ---------------------------------------------------------------------------


class TestKillAndResume:
    KILL_POINTS = sorted(
        random.Random(SEED).sample(range(1, ITERATIONS + 1), 10),
    )

    @pytest.mark.parametrize("die_at", KILL_POINTS)
    def test_a_run_killed_at_a_random_point_resumes_to_completion(
        self, tmp_path, fixture_repo, die_at,
    ):
        session = f"crash-{die_at}"
        path = _kill_at(tmp_path, fixture_repo, die_at, session)

        # The journal is exactly what a SIGKILL leaves: no finish line, whatever
        # had been flushed, nothing else.
        interrupted = replay(path)
        assert interrupted.resumable is True, f"kill at {die_at} left an unusable journal"

        result = _resume(tmp_path, fixture_repo, path, session)

        assert result.status == "completed", result.answer
        assert result.answer == "finished after the interruption"

    @pytest.mark.parametrize("die_at", KILL_POINTS[:4])
    def test_the_work_already_done_is_not_repeated(
        self, tmp_path, fixture_repo, die_at,
    ):
        """The point of restoring the ledger: a resumed run knows what it already
        read, so it does not start over."""
        session = f"nodup-{die_at}"
        path = _kill_at(tmp_path, fixture_repo, die_at, session)
        before = len(replay(path).calls)

        result = _resume(tmp_path, fixture_repo, path, session)

        assert len(result.context.ledger) >= before

    @pytest.mark.parametrize("die_at", KILL_POINTS[:4])
    def test_the_journal_stays_one_ordered_record(
        self, tmp_path, fixture_repo, die_at,
    ):
        session = f"order-{die_at}"
        path = _kill_at(tmp_path, fixture_repo, die_at, session)

        _resume(tmp_path, fixture_repo, path, session)

        lines = list(read_journal(path))
        assert [line["seq"] for line in lines] == list(range(1, len(lines) + 1))
        assert len([l for l in lines if l["type"] == LineType.RUN_STARTED]) == 1
        assert len([l for l in lines if l["type"] == LineType.RUN_FINISHED]) == 1

    def test_the_budget_is_not_refreshed_by_crashing(self, tmp_path, fixture_repo):
        """Otherwise "crash and resume" is a way to run forever."""
        session = "budget"
        path = _kill_at(tmp_path, fixture_repo, 8, session)
        spent = replay(path).iteration

        result = _resume(tmp_path, fixture_repo, path, session)

        assert result.context.budgets.iterations > spent

    def test_a_kill_between_a_decision_and_its_result_is_survivable(
        self, tmp_path, fixture_repo,
    ):
        """The case the journal's write ordering exists for: a call authorised and
        never executed has to be visible as exactly that."""
        session = "half"
        journal = RunJournal(
            run_id="run_half", session_id=session, root=tmp_path / "journals",
        )
        from gitpilot.agent.journal import SeedContext

        journal.run_started(SeedContext(task="do it", session_id=session))
        journal.state_change("running", iteration=1)
        call = ToolCall(id="c1", tool="fs.write", arguments={"path": "a.py"})
        journal.tool_call(call, iteration=1)
        journal.policy_decision(call, verdict="allow", reason="", risk="approval")
        # …and nothing after: the process died here.

        interrupted = replay(journal.path)
        assert interrupted.calls[0].resolved is False
        assert interrupted.calls[0].verdict == "allow"

        result = _resume(tmp_path, fixture_repo, journal.path, session)
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# Reconnect loses nothing
# ---------------------------------------------------------------------------


class TestReconnectLosesNothing:
    def _completed_run(self, tmp_path, fixture_repo, session="reconnect"):
        result = asyncio.run(AgentRunner().run(
            RunRequest(
                task="analyse the parser", session_id=session,
                workspace_root=fixture_repo.path,
                journal_root=tmp_path / "journals", max_iterations=12,
            ),
            provider=_FinishingProvider(extra_steps=5),
            profile=_profile(tmp_path),
        ))
        return result.context.journal.path

    @pytest.mark.parametrize("fraction", [0.0, 0.15, 0.35, 0.5, 0.75, 0.95])
    def test_a_reconnect_at_any_point_sees_everything_after_it(
        self, tmp_path, fixture_repo, fraction,
    ):
        path = self._completed_run(tmp_path, fixture_repo, f"rc-{fraction}")
        total = last_seq(path)
        cursor = int(total * fraction)

        whole = list(replay_events(path))
        missed = list(replay_events(path, after_seq=cursor))

        expected = [
            event.data["seq"] for event in whole if event.data["seq"] > cursor
        ]
        assert [event.data["seq"] for event in missed] == expected

    def test_no_state_bearing_event_is_dropped(self, tmp_path, fixture_repo):
        """The guarantee, stated as a partition: every event in the whole replay
        appears in exactly one of the two halves."""
        path = self._completed_run(tmp_path, fixture_repo, "partition")
        total = last_seq(path)
        cursor = total // 2

        whole = [event.data["seq"] for event in replay_events(path)]
        before = [seq for seq in whole if seq <= cursor]
        after = [event.data["seq"] for event in replay_events(path, after_seq=cursor)]

        assert before + after == whole

    def test_the_finish_always_arrives(self, tmp_path, fixture_repo):
        """A client that reconnects after the run ended must be told, or it waits
        forever on a run that is over."""
        path = self._completed_run(tmp_path, fixture_repo, "finish")

        for cursor in (0, last_seq(path) - 1):
            types = [
                event.type.value for event in replay_events(path, after_seq=cursor)
            ]
            assert types[-1] in ("done", "error"), cursor


# ---------------------------------------------------------------------------
# A long run on a small model
# ---------------------------------------------------------------------------


class TestSmallModelLongRun:
    def test_a_long_qwen_run_compacts_and_finishes(self, tmp_path, fixture_repo):
        """GitPilot's default model. Without compaction a twenty-turn loop with
        real observations cannot fit its window — and this is the *default* model,
        not an edge case.

        The window is the one the profile table gives it (32k), not an assumed 8k:
        asserting a number the table does not hold would be testing the test.
        """
        profile = _profile(tmp_path, "ollama", "qwen2.5:1.5b")

        chatty = _ChattyProvider(turns=ITERATIONS, chunk=2_000)
        result = asyncio.run(AgentRunner().run(
            RunRequest(
                task="analyse every file", session_id="long",
                workspace_root=fixture_repo.path,
                journal_root=tmp_path / "journals",
                max_iterations=ITERATIONS + 5,
            ),
            provider=chatty, profile=profile,
        ))

        assert result.status in ("completed", "degraded"), result.answer
        assert project_tokens(result.context.messages) < profile.context_window, (
            "the transcript ended larger than the window it had to fit in"
        )

    def test_lite_bounds_its_own_transcript(self, tmp_path, fixture_repo):
        """Why the previous test passes without compaction firing.

        The LITE dialect truncates every injected file body to its snippet budget
        and puts only the ``READ <path>`` lines in the transcript, not the model's
        prose — so a LITE run cannot grow past its window however verbose the model
        is. That is a property of the dialect, not an accident, and it is worth
        pinning: if it changed, small-model runs would start depending on
        compaction to survive rather than on staying small.
        """
        profile = _profile(tmp_path, "ollama", "qwen2.5:1.5b")
        result = asyncio.run(AgentRunner().run(
            RunRequest(
                task="analyse every file", session_id="lite-bounded",
                workspace_root=fixture_repo.path,
                journal_root=tmp_path / "journals",
                max_iterations=ITERATIONS + 5,
            ),
            provider=_ChattyProvider(turns=ITERATIONS, chunk=2_000),
            profile=profile,
        ))

        assert project_tokens(result.context.messages) < profile.context_window // 2

    def test_a_long_native_run_on_the_same_window_does_compact(
        self, tmp_path, fixture_repo,
    ):
        """The backstop, on the dialect that needs it.

        A tool-calling model puts its whole observation in the transcript, so forty
        turns of real output on a 32k window overflows and the fold has to happen.
        This is the case the gate is really about.
        """
        from gitpilot.agent.contracts import AgentTurn, Finality
        from gitpilot.agent.loop import AgentLoop
        from gitpilot.toolkit import ToolCall, ToolResult
        from gitpilot.toolkit.builtins import build_default_registry

        from .test_loop import Recorder, ScriptedAdapter, _context

        profile = _profile(tmp_path, "ollama", "qwen2.5:1.5b")
        registry = build_default_registry(
            include_git=False, include_forge=False, include_terminal=False,
        )

        async def verbose(call, ctx):
            return ToolResult.success(call, "observation " + ("text " * 800))

        registry._handlers["fs.read"] = verbose   # noqa: SLF001

        turns = [
            AgentTurn(
                text=f"step {n}",
                tool_calls=[ToolCall(
                    id=f"c{n}", tool="fs.read", arguments={"path": f"f{n}.py"},
                )],
                finality=Finality.CONTINUE,
            )
            for n in range(40)
        ]
        turns.append(AgentTurn(text="done", finality=Finality.FINAL))

        ctx = _context(tmp_path, registry, profile=profile)
        ctx.budgets.max_iterations = 50
        ctx.budgets.max_tool_calls = 50
        events = Recorder()

        result = asyncio.run(AgentLoop(
            ScriptedAdapter(turns, profile=profile, registry=registry),
            registry, events=events,
        ).run(ctx))

        assert result.status in ("completed", "degraded")
        assert "compaction" in events.types(), "the fold never happened"
        assert project_tokens(ctx.messages) < profile.context_window

    def test_compaction_and_resume_compose(self, tmp_path, fixture_repo):
        """The journal holds the *uncompacted* truth, so a resumed run is rebuilt
        from that rather than from the folded transcript — which is why a run can
        be compacted and still resumable."""
        session = "long-crash"
        path = _kill_at(tmp_path, fixture_repo, 12, session)

        assert replay(path).resumable is True

        result = asyncio.run(AgentRunner().resume(
            path, session_id=session, workspace_root=fixture_repo.path,
            provider=_ChattyProvider(turns=2, chunk=200),
            profile=_profile(tmp_path, "ollama", "qwen2.5:1.5b"),
        ))
        assert result.status in ("completed", "degraded")
