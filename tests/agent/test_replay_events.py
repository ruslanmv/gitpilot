"""SSE replay for a reconnecting client — Batch V4-F2 (closes F12).

The guarantee is precise, and worth stating before the tests: **no state-bearing
event is lost**. It is not "byte-identical replay", and the difference is
deliberate. In-flight `text_delta` increments are not journaled — the turn's text
is — so a replay serves one delta carrying the whole turn. That is the same
information without pretending to re-stream the past. Keepalives carry no state and
are never replayed; twenty of them would tell a reconnecting client that twenty
things happened.
"""
from __future__ import annotations

import pytest

from gitpilot.agent.journal import (
    EPHEMERAL_EVENTS,
    PROJECTION,
    LineType,
    RunJournal,
    SeedContext,
)
from gitpilot.agent.replay_events import (
    last_seq,
    parse_last_event_id,
    replay_events,
    sse_lines,
)
from gitpilot.toolkit import ToolCall, ToolResult


@pytest.fixture
def journal(tmp_path):
    """A journal covering every line type a run can produce."""
    journal = RunJournal(run_id="run_x", session_id="s", root=tmp_path / "j")
    journal.run_started(SeedContext(
        task="fix the parser", session_id="s", model="gpt-4o-mini", dialect="native",
    ))
    journal.state_change("running", iteration=1)
    journal.turn(text="Let me look.", iteration=1, finality="continue")

    read = ToolCall(id="c1", tool="fs.read", arguments={"path": "app.py"})
    journal.tool_call(read, iteration=1)
    journal.policy_decision(read, verdict="allow", reason="read-only", risk="safe")
    journal.tool_result(read, ToolResult.success(read, "x = 1\n"))

    write = ToolCall(id="c2", tool="fs.write", arguments={"path": "app.py"})
    journal.tool_call(write, iteration=2)
    journal.policy_decision(
        write, verdict="ask", reason="fs.write needs approval", risk="approval",
    )
    journal.approval(write, approved=True)
    journal.checkpoint_ref(write, "ckpt-1")
    journal.tool_result(write, ToolResult.success(
        write, "written", data={"paths_written": ["app.py"]},
    ))

    tests = ToolCall(id="c3", tool="test.run", arguments={})
    journal.tool_call(tests, iteration=3)
    journal.policy_decision(tests, verdict="allow", reason="", risk="high_risk")
    journal.tool_result(tests, ToolResult.success(tests, "Tests: 3 passed", data={
        "framework": "pytest", "passed": 3, "failed": 0, "skipped": 0, "exit_code": 0,
    }))

    journal.todo([{"id": "t1", "text": "Fix it", "status": "completed"}])
    journal.compaction(before_tokens=9000, after_tokens=4000, folded=8)
    journal.finish("completed", summary="the parser is fixed")
    return journal


def _types(events):
    return [event.type.value for event in events]


# ---------------------------------------------------------------------------
# Reconnecting at any point
# ---------------------------------------------------------------------------


class TestReconnect:
    def test_a_fresh_client_gets_the_whole_run(self, journal):
        events = list(replay_events(journal.path))

        types = _types(events)
        for required in (
            "status_change", "text_delta", "tool_start", "tool_result",
            "approval_needed", "approval_resolved", "file_write", "test_result",
            "done",
        ):
            assert required in types, f"{required} was never replayed"

    @pytest.mark.parametrize("cursor", [0, 1, 3, 5, 8, 11, 14, 17])
    def test_a_reconnect_at_any_point_replays_only_what_it_missed(self, journal, cursor):
        events = list(replay_events(journal.path, after_seq=cursor))

        seqs = [event.data["seq"] for event in events]
        assert all(seq > cursor for seq in seqs), seqs

    def test_a_cursor_past_the_end_replays_nothing(self, journal):
        assert list(replay_events(journal.path, after_seq=999)) == []

    def test_the_run_finish_is_replayed_so_the_stream_can_close(self, journal):
        """Without it a reconnecting client waits on a run that already ended."""
        events = list(replay_events(journal.path, after_seq=last_seq(journal.path) - 1))
        assert _types(events) == ["done"]

    def test_a_failed_run_replays_as_an_error(self, tmp_path):
        journal = RunJournal(run_id="r", session_id="s", root=tmp_path / "j")
        journal.run_started(SeedContext(task="x", session_id="s"))
        journal.finish("failed", summary="the provider went away")

        events = list(replay_events(journal.path))
        assert _types(events)[-1] == "error"

    def test_every_event_carries_its_cursor(self, journal):
        """The client cannot advance its `Last-Event-ID` otherwise, so the next
        reconnect starts from zero."""
        for event in replay_events(journal.path):
            assert event.data["seq"] > 0

    def test_replayed_events_are_marked_as_such(self, journal):
        """A client that shows a spinner per tool row needs to know these already
        happened."""
        assert all(event.data["replayed"] for event in replay_events(journal.path))


# ---------------------------------------------------------------------------
# No duplicated side effects
# ---------------------------------------------------------------------------


class TestNoDuplicateSideEffects:
    def test_a_file_write_is_replayed_once_per_journaled_result(self, journal):
        events = list(replay_events(journal.path))
        writes = [event for event in events if event.type.value == "file_write"]

        assert len(writes) == 1
        assert writes[0].data["path"] == "app.py"

    def test_side_effects_come_from_the_record_not_from_inference(self, tmp_path):
        """A replay that re-derived "this wrote a file" could invent one."""
        journal = RunJournal(run_id="r", session_id="s", root=tmp_path / "j")
        journal.run_started(SeedContext(task="x", session_id="s"))
        call = ToolCall(id="c1", tool="fs.write", arguments={"path": "a.py"})
        journal.tool_call(call, iteration=1)
        journal.policy_decision(call, verdict="deny", reason="read-only run")
        journal.tool_result(call, ToolResult.denied_for(call, "read-only run"))

        events = list(replay_events(journal.path))

        assert not [e for e in events if e.type.value == "file_write"], (
            "a denied write must not replay as a file change"
        )

    def test_a_reconnect_does_not_duplicate_what_it_already_saw(self, journal):
        first = list(replay_events(journal.path, after_seq=0))
        cursor = first[len(first) // 2].data["seq"]
        second = list(replay_events(journal.path, after_seq=cursor))

        seen = {event.data["seq"] for event in first if event.data["seq"] <= cursor}
        assert not (seen & {event.data["seq"] for event in second})

    def test_a_test_result_replays_its_numbers_once(self, journal):
        results = [
            event for event in replay_events(journal.path)
            if event.type.value == "test_result"
        ]
        assert len(results) == 1
        assert results[0].data["passed"] == 3


# ---------------------------------------------------------------------------
# The projection contract
# ---------------------------------------------------------------------------


class TestProjectionContract:
    def test_every_line_type_projects_to_something_or_is_declared_empty(self):
        """The table has been shipped as data since Batch V4-C1 so this batch
        implements a mapping rather than inventing one."""
        from gitpilot.agent.journal import ALL_LINE_TYPES

        assert set(PROJECTION) == set(ALL_LINE_TYPES)

    def test_every_state_bearing_line_produces_an_event(self, journal):
        """The guarantee, checked against a journal that contains every type."""
        from gitpilot.agent.journal import read_journal

        produced = {event.data["seq"] for event in replay_events(journal.path)}
        for line in read_journal(journal.path):
            if line["type"] == LineType.POLICY_DECISION and line.get("verdict") != "ask":
                continue   # an `allow` is not a client-visible event
            assert line["seq"] in produced, (
                f"{line['type']} at seq {line['seq']} produced nothing"
            )

    def test_in_flight_text_increments_are_declared_ephemeral(self):
        assert "text_delta_increment" in EPHEMERAL_EVENTS
        assert "replay serves the journaled turn text" in (
            EPHEMERAL_EVENTS["text_delta_increment"]
        )

    def test_a_turn_replays_as_one_delta_not_many(self, journal):
        deltas = [
            event for event in replay_events(journal.path)
            if event.type.value == "text_delta"
        ]
        assert len(deltas) == 1
        assert deltas[0].data["text"] == "Let me look."

    def test_keepalives_are_never_replayed(self, journal):
        assert "keepalive" in EPHEMERAL_EVENTS
        statuses = [
            event.data.get("status") for event in replay_events(journal.path)
        ]
        assert "keepalive" not in statuses

    def test_an_allow_decision_is_not_a_prompt(self, journal):
        """Replaying every decision as an approval card would show the user cards
        for calls that never needed one."""
        cards = [
            event for event in replay_events(journal.path)
            if event.type.value == "approval_needed"
        ]
        assert len(cards) == 1
        assert cards[0].data["tool"] == "fs.write"

    def test_a_card_asked_before_the_cursor_is_still_replayed_in_context(self, journal):
        """Reconnecting between the ask and the answer must not show a resolution
        for a request the client never saw."""
        from gitpilot.agent.journal import read_journal

        approval_seq = next(
            line["seq"] for line in read_journal(journal.path)
            if line["type"] == LineType.APPROVAL
        )
        events = list(replay_events(journal.path, after_seq=approval_seq - 1))

        assert "approval_resolved" in _types(events)


# ---------------------------------------------------------------------------
# The header and the wire format
# ---------------------------------------------------------------------------


class TestHeader:
    @pytest.mark.parametrize(("raw", "expected"), [
        ("7", 7), (" 12 ", 12), ("0", 0), ("-4", 0),
        (None, 0), ("", 0), ("nonsense", 0), ("3.5", 0),
    ])
    def test_an_unreadable_cursor_replays_everything(self, raw, expected):
        """Too much beats a silent gap."""
        assert parse_last_event_id(raw) == expected

    def test_frames_carry_an_id_so_the_cursor_advances(self, journal):
        frames = list(sse_lines(journal.path))

        assert frames
        assert all(frame.startswith("id: ") for frame in frames)
        assert all("\ndata: " in frame for frame in frames)

    def test_the_last_id_is_the_journals_last_seq(self, journal):
        frames = list(sse_lines(journal.path))
        final = int(frames[-1].split("\n", 1)[0].removeprefix("id: "))

        assert final == last_seq(journal.path)


class TestMultipleReaders:
    def test_a_second_process_can_serve_the_replay(self, journal):
        """The reader opens the file and reads — a worker that did not run the run
        can serve its replay, which is what makes this work behind more than one
        uvicorn worker."""
        import subprocess
        import sys

        code = (
            "from gitpilot.agent.replay_events import replay_events;"
            f"print(len(list(replay_events({str(journal.path)!r}))))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        )

        assert int(out.stdout.strip()) == len(list(replay_events(journal.path)))

    def test_reading_while_the_run_appends_sees_a_consistent_prefix(self, tmp_path):
        """A journal is append-only, so a reader mid-write sees fewer lines, never
        wrong ones.

        Uses a *live* journal rather than the finished fixture: a journal that has
        written its finish line refuses further appends, which is the right
        behaviour and makes it the wrong subject for this test.
        """
        journal = RunJournal(run_id="live", session_id="s", root=tmp_path / "j")
        journal.run_started(SeedContext(task="x", session_id="s"))
        journal.state_change("running", iteration=1)
        before = list(replay_events(journal.path))

        journal.state_change("running", iteration=9)
        after = list(replay_events(journal.path))

        assert len(after) > len(before)
        assert [e.data["seq"] for e in after][: len(before)] == [
            e.data["seq"] for e in before
        ]
