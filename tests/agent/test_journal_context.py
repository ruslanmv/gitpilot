"""Run journal and context — Batch V4-C1."""
from __future__ import annotations

import json

import pytest

from gitpilot.agent.context import (
    AgentContext,
    BudgetExceeded,
    BudgetState,
    State,
    TodoItem,
)
from gitpilot.agent.journal import (
    ALL_LINE_TYPES,
    DURABLE_LINE_TYPES,
    EPHEMERAL_EVENTS,
    MAX_INLINE_CONTENT_BYTES,
    PROJECTION,
    JournalError,
    LineType,
    RunJournal,
    SeedContext,
    assert_no_reasoning,
    project_events,
    read_seed,
    read_spill,
    terminal_status,
)
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.toolkit import ToolCall, ToolExecutionContext, ToolResult


@pytest.fixture()
def journal(tmp_path):
    return RunJournal(run_id="run-1", session_id="sess-1", root=tmp_path)


def _seed(**kwargs):
    defaults = dict(
        task="Analyze the tests", session_id="sess-1", transcript_index=4,
        topology_id="tool_augmented_react", model="claude-sonnet-4-5", dialect="native",
    )
    defaults.update(kwargs)
    return SeedContext(**defaults)


def _call(tool="fs.read", **arguments):
    return ToolCall(id="c1", tool=tool, arguments=arguments or {"path": "a.py"})


# ---------------------------------------------------------------------------
# Sequencing and durability
# ---------------------------------------------------------------------------


class TestSequencing:
    def test_seq_is_monotonic_across_line_types(self, journal):
        journal.run_started(_seed())
        journal.state_change("running")
        journal.turn(text="thinking", iteration=1, finality="continue")

        seqs = [line["seq"] for line in journal.read()]
        assert seqs == [1, 2, 3]

    def test_every_line_carries_type_ts_and_run_id(self, journal):
        journal.run_started(_seed())
        line = journal.read()[0]

        assert line["type"] == LineType.RUN_STARTED
        assert line["run_id"] == "run-1"
        assert line["ts"]

    def test_unknown_line_type_is_refused(self, journal):
        with pytest.raises(JournalError, match="unknown journal line type"):
            journal.append("gossip", {})

    def test_appending_after_finish_is_refused(self, journal):
        journal.run_started(_seed())
        journal.finish("completed")

        with pytest.raises(JournalError, match="closed"):
            journal.state_change("running")

    def test_state_changing_lines_are_fsynced(self, journal, monkeypatch):
        """"The journal says awaiting_approval" has to survive a hard kill."""
        synced = []
        real_fsync = __import__("os").fsync

        def spy(fd):
            synced.append(fd)
            return real_fsync(fd)

        monkeypatch.setattr("gitpilot.agent.journal.os.fsync", spy)

        journal.run_started(_seed())          # durable
        journal.turn(text="x", iteration=1, finality="continue")   # not durable

        assert len(synced) == 1
        assert LineType.TURN not in DURABLE_LINE_TYPES

    def test_reopen_continues_the_sequence(self, tmp_path):
        """A resumed run appends; a second file would have to be merged."""
        first = RunJournal(run_id="run-2", session_id="s", root=tmp_path)
        first.run_started(_seed())
        first.turn(text="a", iteration=1, finality="continue")

        second = RunJournal(run_id="run-2", session_id="s", root=tmp_path).reopen()
        second.state_change("running")

        assert [line["seq"] for line in second.read()] == [1, 2, 3]

    def test_a_torn_final_line_does_not_break_reading(self, journal):
        """Resume is exactly the case where the process died mid-write."""
        journal.run_started(_seed())
        with journal.path.open("a", encoding="utf-8") as handle:
            handle.write('{"seq": 2, "type": "tur')

        assert len(journal.read()) == 1


# ---------------------------------------------------------------------------
# The seed
# ---------------------------------------------------------------------------


class TestSeed:
    def test_seed_round_trips(self, journal):
        journal.run_started(_seed(messages=[{"role": "user", "content": "go"}]))

        recovered = read_seed(journal.path)

        assert recovered.task == "Analyze the tests"
        assert recovered.transcript_index == 4
        assert recovered.messages == [{"role": "user", "content": "go"}]
        assert recovered.dialect == "native"

    def test_the_seed_references_the_transcript_rather_than_copying_it(self, journal):
        """The session file is the source of truth; a copy would drift."""
        journal.run_started(_seed())
        seed = read_seed(journal.path)

        assert seed.session_id == "sess-1"
        assert seed.transcript_index == 4
        assert seed.messages == []

    def test_missing_seed_reads_as_none(self, journal):
        journal.state_change("running")
        assert read_seed(journal.path) is None


# ---------------------------------------------------------------------------
# Ordering guarantee
# ---------------------------------------------------------------------------


class TestDecisionBeforeExecution:
    def test_the_ordering_the_safety_invariants_depend_on(self, journal):
        """policy_decision, then checkpoint_ref, then the result."""
        call = _call("fs.write", path="a.py", content="x")

        journal.policy_decision(call, verdict="allow", requires_checkpoint=True)
        journal.checkpoint_ref(call, "ckpt-1")
        journal.tool_result(call, ToolResult.success(call, "written", data={"paths_written": ["a.py"]}))

        types = [line["type"] for line in journal.read()]
        assert types == [
            LineType.POLICY_DECISION, LineType.CHECKPOINT_REF, LineType.TOOL_RESULT,
        ]

    def test_a_plain_allow_is_journaled_too(self, journal):
        """Journaling only approvals would make the invariant unassertable."""
        journal.policy_decision(_call(), verdict="allow")

        line = journal.read()[0]
        assert line["verdict"] == "allow"
        assert line["type"] == LineType.POLICY_DECISION


# ---------------------------------------------------------------------------
# The projection table
# ---------------------------------------------------------------------------


class TestProjection:
    def test_every_line_type_has_a_projection(self):
        assert set(PROJECTION) == set(ALL_LINE_TYPES)

    def test_state_bearing_wire_events_are_all_projectable_or_declared_ephemeral(self):
        """§21.2's vocabulary, so Batch V4-F2 implements a mapping, not a guess."""
        wire_events = {
            "run_started", "run_resumed", "status_change", "iteration", "text_delta",
            "tool_start", "tool_result", "file_write", "approval_needed",
            "approval_resolved", "todo_updated", "test_result", "terminal_output",
            "terminal_exit", "diagnostics", "checkpoint_created", "compaction",
            "dialect_downgraded", "tools_pruned", "plan_step", "done", "error",
        }
        projectable = {event for events in PROJECTION.values() for event in events}
        declared_ephemeral = set(EPHEMERAL_EVENTS)

        unaccounted = wire_events - projectable - declared_ephemeral
        assert unaccounted == set(), f"no journal line projects to: {sorted(unaccounted)}"

    def test_text_delta_increments_are_explicitly_ephemeral(self):
        """Replay serves the journaled turn text, not a re-stream of the past."""
        assert "text_delta_increment" in EPHEMERAL_EVENTS
        assert "keepalive" in EPHEMERAL_EVENTS

    def test_project_events_reads_a_line(self, journal):
        journal.tool_call(_call(), iteration=1)
        assert project_events(journal.read()[0]) == ["tool_start"]

    def test_resume_from_returns_only_later_lines(self, journal):
        journal.run_started(_seed())
        journal.turn(text="a", iteration=1, finality="continue")
        journal.turn(text="b", iteration=2, finality="final")

        assert [line["seq"] for line in journal.resume_from(1)] == [2, 3]


# ---------------------------------------------------------------------------
# What must never be stored
# ---------------------------------------------------------------------------


class TestReasoningRejection:
    @pytest.mark.parametrize("payload", [
        "<think>secret plan</think>",
        {"text": "before <think>hidden</think> after"},
        {"nested": [{"content": "</thought>"}]},
        {"reasoning": "the model's scratchpad"},
        {"reasoning_content": "also the scratchpad"},
    ])
    def test_reasoning_content_is_refused(self, journal, payload):
        """The architecture must keep working without storing chain-of-thought,
        and the only way to be sure is to make storing it fail."""
        with pytest.raises(JournalError):
            journal.append(LineType.TURN, payload if isinstance(payload, dict) else {"text": payload})

    def test_ordinary_text_is_unaffected(self, journal):
        journal.turn(text="I will read the Makefile first.", iteration=1, finality="continue")
        assert journal.read()[0]["text"].startswith("I will read")

    def test_the_checker_is_usable_directly(self):
        assert_no_reasoning({"content": "plain"})
        with pytest.raises(JournalError):
            assert_no_reasoning({"content": "<think>x</think>"})


# ---------------------------------------------------------------------------
# Oversized payloads
# ---------------------------------------------------------------------------


class TestSpill:
    def test_large_output_spills_to_a_side_file_with_a_reference(self, journal):
        call = _call("terminal.run", command="pytest")
        huge = "x" * (MAX_INLINE_CONTENT_BYTES + 5_000)

        journal.tool_result(call, ToolResult.success(call, huge))
        line = journal.read()[0]

        assert len(line["content"]) < len(huge)
        assert line["content_bytes"] == len(huge)
        assert read_spill(journal.path, line["content_ref"]) == huge

    def test_the_full_output_is_never_discarded(self, journal):
        """A truncated observation is what the *model* saw; the journal holds
        what actually happened."""
        call = _call("terminal.run", command="pytest")
        huge = "line\n" * 10_000

        journal.tool_result(call, ToolResult.success(call, huge))
        line = journal.read()[0]

        assert read_spill(journal.path, line["content_ref"]) == huge

    def test_small_output_stays_inline(self, journal):
        call = _call()
        journal.tool_result(call, ToolResult.success(call, "short"))

        line = journal.read()[0]
        assert line["content"] == "short"
        assert "content_ref" not in line


class TestTerminalStatus:
    def test_a_finished_run_reports_its_status(self, journal):
        journal.run_started(_seed())
        journal.finish("completed", summary="done")

        assert terminal_status(journal.path) == "completed"

    def test_an_unfinished_run_has_none_which_is_what_makes_it_resumable(self, journal):
        journal.run_started(_seed())
        journal.state_change("awaiting_approval")

        assert terminal_status(journal.path) is None


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


def _context(journal, tmp_path):
    from gitpilot.toolkit import LocalWorkspace

    return AgentContext(
        run_id="run-1",
        task="do the thing",
        journal=journal,
        model_profile=resolve_profile("ollama", "llama3:8b", cache_path=tmp_path / "c.json"),
        tool_context=ToolExecutionContext(workspace=LocalWorkspace(root=tmp_path)),
        session_id="sess-1",
    )


class TestContext:
    def test_begin_journals_the_seed_and_enters_running(self, journal, tmp_path):
        ctx = _context(journal, tmp_path)
        ctx.begin(_seed(), now=100.0)

        assert ctx.state is State.RUNNING
        types = [line["type"] for line in journal.read()]
        assert types == [LineType.RUN_STARTED, LineType.STATE_CHANGE]

    def test_state_changes_are_journaled_once(self, journal, tmp_path):
        ctx = _context(journal, tmp_path)
        ctx.set_state(State.RUNNING)
        ctx.set_state(State.RUNNING)

        assert len(journal.read()) == 1

    def test_reads_and_writes_are_tracked_from_structured_data_not_prose(self, journal, tmp_path):
        ctx = _context(journal, tmp_path)

        read_call = _call("fs.read", path="src/app.py")
        ctx.record(read_call, ToolResult.success(read_call, "body", data={"path": "src/app.py"}))

        write_call = _call("fs.write", path="src/new.py", content="x")
        ctx.record(write_call, ToolResult.success(
            write_call, "written", data={"path": "src/new.py", "paths_written": ["src/new.py"]},
        ))

        assert ctx.files_read == {"src/app.py"}
        assert ctx.files_modified == {"src/new.py"}
        assert ctx.mutated is True

    def test_commands_and_tests_are_recorded(self, journal, tmp_path):
        ctx = _context(journal, tmp_path)

        cmd = _call("terminal.run", command="ls")
        ctx.record(cmd, ToolResult.success(cmd, "out", data={"command": "ls", "exit_code": 0}))

        test = ToolCall(id="c2", tool="test.run", arguments={})
        ctx.record(test, ToolResult.success(test, "Tests: 3 passed", data={
            "command": "pytest", "passed": 3, "failed": 0, "skipped": 0, "green": True,
        }))

        assert ctx.commands[0].command == "ls"
        assert ctx.tests[0].green is True

    def test_failures_land_in_the_error_list(self, journal, tmp_path):
        ctx = _context(journal, tmp_path)
        call = _call()

        ctx.record(call, ToolResult.failure(call, "boom", error="read_failed"))

        assert ctx.errors[0].where == "fs.read"
        assert ctx.errors[0].message == "read_failed"

    def test_the_ledger_answers_questions_the_transcript_cannot(self, journal, tmp_path):
        """The transcript gets compacted; "did this run modify anything" must not."""
        ctx = _context(journal, tmp_path)
        call = _call("fs.write", path="a.py", content="x")
        ctx.record(call, ToolResult.success(call, "ok", data={"paths_written": ["a.py"]}))

        ctx.messages.clear()   # simulate compaction throwing the history away

        assert ctx.ledger.any_call_to("fs.write")
        assert ctx.files_modified == {"a.py"}

    def test_todos_are_journaled(self, journal, tmp_path):
        ctx = _context(journal, tmp_path)
        ctx.set_todos([TodoItem(id="1", text="read the Makefile", status="in_progress")])

        line = [entry for entry in journal.read() if entry["type"] == LineType.TODO][0]
        assert line["items"][0]["status"] == "in_progress"

    def test_summary_is_json_serialisable(self, journal, tmp_path):
        ctx = _context(journal, tmp_path)
        assert json.loads(json.dumps(ctx.summary()))["run_id"] == "run-1"


class TestBudgets:
    def test_iteration_limit(self):
        budgets = BudgetState(max_iterations=2)
        budgets.start(0.0)
        budgets.iterations = 2

        with pytest.raises(BudgetExceeded) as exc:
            budgets.check(now=1.0)
        assert exc.value.limit == "max_iterations"

    def test_tool_call_limit(self):
        budgets = BudgetState(max_tool_calls=1)
        budgets.start(0.0)
        budgets.tool_calls = 1

        with pytest.raises(BudgetExceeded):
            budgets.check(now=1.0)

    def test_wall_clock_limit(self):
        budgets = BudgetState(max_runtime_seconds=10)
        budgets.start(100.0)

        budgets.check(now=105.0)
        with pytest.raises(BudgetExceeded) as exc:
            budgets.check(now=111.0)
        assert exc.value.limit == "max_runtime_seconds"

    def test_usage_accumulates(self):
        from gitpilot.agent.contracts import TokenUsage

        budgets = BudgetState()
        budgets.record_usage(TokenUsage(prompt_tokens=10, completion_tokens=5))
        budgets.record_usage(TokenUsage(prompt_tokens=1, completion_tokens=1))

        assert budgets.total_tokens == 17
