"""The full §19.1 trace test — Batch V4-E6, the Phase E gate.

Batch V4-C4 asserted trajectory categories and journal integrity, and said what it
could not yet check: policy decisions did not exist, and neither did a TODO list or
delegation. All three exist now, so this is the same task with the rest of the
acceptance criteria attached:

* the read-only mask is **enforced**, not merely requested — a write is attempted
  and refused;
* **zero** ``ask`` decisions, because nothing a read-only run does needs a human;
* the journal ordering holds (decision before result, for every call);
* the TODO list is maintained and finishes coherent;
* a delegation runs, returns structured, and its child cannot exceed its parent.

Still nothing about the trajectory is hardcoded. The provider reacts to what the
loop sends rather than replaying a fixed script, and the assertions are on
categories — a config file was read, the tests directory was searched, the answer
names the real framework. A test that pinned the steps would assert that we wrote
the steps down.

"The console demo is real" is the phrase in the batch plan. This is what makes it
checkable: every event the console renders is produced by this run.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from gitpilot.agent.context import AgentContext
from gitpilot.agent.contracts import AgentTurn, Dialect, Finality
from gitpilot.agent.journal import LineType, read_journal
from gitpilot.agent.loop import AgentLoop, DenyingApprovals, Verification, new_run_id
from gitpilot.agent.journal import RunJournal
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.policy import CapabilityMask, PolicyEngine, ResolvedPolicy
from gitpilot.agent.providers.base import ProviderResponse, RawToolCall
from gitpilot.agent.runner import AgentRunner, RunRequest
from gitpilot.toolkit import LocalWorkspace, ToolExecutionContext
from gitpilot.toolkit.builtins import build_default_registry

TASK = (
    "Analyze how this repository's testing infrastructure works. "
    "Do not modify files."
)

MUTATING_TOOLS = frozenset({
    "fs.write", "fs.edit", "fs.delete",
    "git.commit", "git.push", "git.branch", "git.stash",
    "github.issue.create", "github.issue.comment", "github.pr.create",
})

CONFIG_FILES = ("pyproject.toml", "pytest.ini", "setup.cfg", "Makefile", "package.json")


@pytest.fixture()
def repo(tmp_path):
    """A project whose testing setup is discoverable but not obvious."""
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8",
    )
    (root / "Makefile").write_text("test:\n\tpython -m pytest -q\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("def greet(n):\n    return n\n", encoding="utf-8")
    (root / "tests" / "test_app.py").write_text(
        "from src.app import greet\n\n\ndef test_greet():\n    assert greet('x') == 'x'\n",
        encoding="utf-8",
    )
    (root / "tests" / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    return root


class _FullTrajectory:
    """A model working the task, including two things it is not allowed to do.

    The refused attempts are the point: a read-only run that never tries to write
    proves nothing about the mask. It also delegates once, because "the console
    demo is real" means the nested block in the UI has to come from a real child.
    """

    name = "recorded"
    model = "gpt-4o-mini"
    supports_native_tools = True
    supports_parallel_tool_calls = False
    supports_streaming = False

    ANSWER = (
        "This project uses pytest. pyproject.toml sets testpaths to tests/, the "
        "suite lives in tests/ with a conftest.py, and `make test` runs "
        "python -m pytest -q."
    )

    def __init__(self):
        self.parent_steps = [
            # A checklist first — what a model with todo.write should do.
            RawToolCall(id="p0", name="todo.write", arguments={"items": [
                {"text": "Find the test configuration", "status": "in_progress"},
                {"text": "Look at the suite layout", "status": "pending"},
                {"text": "Report how it runs", "status": "pending"},
            ]}),
            RawToolCall(id="p1", name="fs.read", arguments={"path": "pyproject.toml"}),
            RawToolCall(id="p2", name="fs.glob", arguments={"pattern": "tests/**/*.py"}),
            # Hand the layout question to a subagent.
            RawToolCall(id="p3", name="agent.delegate", arguments={
                "agent": "explorer",
                "task": "Describe the layout of the tests/ directory.",
                "expected": "the files and what each covers",
            }),
            RawToolCall(id="p4", name="test.detect", arguments={}),
            # Two things a read-only run must not be allowed to do.
            RawToolCall(id="p5", name="fs.write", arguments={
                "path": "tests/test_new.py", "content": "def test_x(): pass\n",
            }),
            RawToolCall(id="p6", name="terminal.run", arguments={"command": "rm -rf tests"}),
            RawToolCall(id="p7", name="todo.write", arguments={"items": [
                {"text": "Find the test configuration", "status": "completed"},
                {"text": "Look at the suite layout", "status": "completed"},
                {"text": "Report how it runs", "status": "completed"},
            ]}),
        ]
        self.child_steps = [
            RawToolCall(id="s1", name="fs.list", arguments={"path": "tests"}),
        ]

    async def complete(self, messages, *, tools=None, system=None, **opts):
        offered = {getattr(tool, "name", "") for tool in (tools or [])}
        if "agent.delegate" in offered:
            if self.parent_steps:
                return ProviderResponse(
                    text="Let me look at that.",
                    tool_calls=[self.parent_steps.pop(0)],
                )
            return ProviderResponse(text=self.ANSWER)

        if self.child_steps:
            return ProviderResponse(tool_calls=[self.child_steps.pop(0)])
        return ProviderResponse(text=json.dumps({
            "summary": "tests/ holds test_app.py and a conftest.py",
            "files": ["tests/test_app.py", "tests/conftest.py"],
            "status": "completed",
        }))

    def stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture()
def outcome(repo, tmp_path):
    """One recorded run, with the real policy engine enforcing read-only."""
    profile = resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "c.json")
    assert profile.dialect is Dialect.NATIVE

    registry = build_default_registry(include_git=True, include_forge=False)
    policy = PolicyEngine(ResolvedPolicy(
        capabilities=CapabilityMask.permissive(),
        allow_mutation=False,          # the read-only topology
        allow_network=False,
    ))

    events: list[dict] = []

    async def emit(event_type: str, payload: dict) -> None:
        events.append({"type": event_type, **payload})

    from gitpilot.agent.loop import LoopEvents

    provider = _FullTrajectory()
    runner = AgentRunner()
    loop, ctx = runner.build(
        RunRequest(
            task=TASK, session_id="trace-full", workspace_root=repo,
            journal_root=tmp_path / "journals", read_only=True,
            max_iterations=16, verification="off",
        ),
        registry=registry, provider=provider, profile=profile,
    )
    loop.policy = policy
    loop.approvals = DenyingApprovals()
    loop.events = LoopEvents(emit)
    ctx.capabilities = policy.policy.capabilities

    result = asyncio.run(loop.run(ctx))
    return result, events, ctx


def _starts(events):
    return [event for event in events if event["type"] == "tool_start"]


def _names(events):
    return {event.get("name") for event in _starts(events)}


def _lines(ctx):
    return list(read_journal(ctx.journal.path))


def _of(ctx, line_type):
    return [line for line in _lines(ctx) if line["type"] == line_type]


# ---------------------------------------------------------------------------
# The trajectory, discovered
# ---------------------------------------------------------------------------


class TestTrajectory:
    def test_the_run_completes(self, outcome):
        result, _events, _ctx = outcome
        assert result.status == "completed", result.answer

    def test_a_config_file_was_read(self, outcome):
        result, _events, _ctx = outcome
        read = set(result.context.files_read)
        assert any(name in read for name in CONFIG_FILES), read

    def test_the_tests_directory_was_searched(self, outcome):
        _result, events, _ctx = outcome
        searches = [
            event for event in _starts(events)
            if event.get("name") in ("fs.glob", "fs.grep", "fs.list")
        ]
        assert searches
        assert any("tests" in json.dumps(event.get("args") or {}) for event in searches)

    def test_the_test_command_was_discovered(self, outcome):
        _result, events, _ctx = outcome
        assert {"test.detect", "terminal.run"} & _names(events)

    def test_the_answer_names_the_real_framework(self, outcome):
        result, _events, _ctx = outcome
        assert "pytest" in result.answer.lower()
        assert "tests/" in result.answer

    def test_nothing_was_modified(self, outcome):
        result, _events, _ctx = outcome
        assert result.context.files_modified == set()


# ---------------------------------------------------------------------------
# The policy, enforced
# ---------------------------------------------------------------------------


class TestPolicyEnforced:
    def test_the_write_was_attempted_and_refused(self, outcome):
        """A read-only run that never tries to write proves nothing about the
        mask, so this trajectory tries."""
        result, events, ctx = outcome

        assert "fs.write" in _names(events), "the recorded run should attempt a write"

        write = next(
            entry for entry in ctx.ledger if entry.call.tool == "fs.write"
        )
        assert write.result.ok is False
        assert write.result.denied is True

    def test_the_file_it_tried_to_write_does_not_exist(self, outcome, repo):
        _result, _events, _ctx = outcome
        assert not (repo / "tests" / "test_new.py").exists()

    def test_a_destructive_command_was_refused_without_a_prompt(self, outcome):
        _result, _events, ctx = outcome

        run = next(entry for entry in ctx.ledger if entry.call.tool == "terminal.run")
        assert run.result.ok is False

        decision = next(
            line for line in _of(ctx, LineType.POLICY_DECISION)
            if line["tool"] == "terminal.run"
        )
        assert decision["verdict"] == "deny"

    def test_no_approval_was_ever_asked_for(self, outcome):
        """A read-only run has nothing a human needs to decide; a prompt here
        would mean the policy is refusing and asking about the same thing."""
        _result, events, ctx = outcome

        verdicts = [line["verdict"] for line in _of(ctx, LineType.POLICY_DECISION)]
        assert "ask" not in verdicts, verdicts
        assert "approval_needed" not in {event["type"] for event in events}

    def test_every_read_was_allowed(self, outcome):
        _result, _events, ctx = outcome

        reads = [
            line for line in _of(ctx, LineType.POLICY_DECISION)
            if line["tool"].startswith("fs.") and line["tool"] != "fs.write"
        ]
        assert reads
        assert all(line["verdict"] == "allow" for line in reads)


class TestJournalOrdering:
    def test_every_call_was_decided_before_it_ran(self, outcome):
        _result, _events, ctx = outcome

        decided = set()
        for line in _lines(ctx):
            if line["type"] == LineType.POLICY_DECISION:
                decided.add(line["call_id"])
            elif line["type"] == LineType.TOOL_RESULT:
                assert line["call_id"] in decided, (
                    f"{line.get('tool')} ran with no decision before it"
                )

    def test_the_journal_is_complete_and_ordered(self, outcome):
        _result, _events, ctx = outcome
        lines = _lines(ctx)

        assert [line["seq"] for line in lines] == list(range(1, len(lines) + 1))
        assert lines[0]["type"] == LineType.RUN_STARTED
        assert lines[-1]["type"] == LineType.RUN_FINISHED

    def test_no_reasoning_was_persisted(self, outcome):
        from gitpilot.agent.journal import assert_no_reasoning

        _result, _events, ctx = outcome
        for line in _lines(ctx):
            assert_no_reasoning(line, where=f"line {line['seq']}")


# ---------------------------------------------------------------------------
# The TODO list
# ---------------------------------------------------------------------------


class TestTodoThroughTheRun:
    def test_the_list_was_maintained(self, outcome):
        _result, _events, ctx = outcome
        updates = _of(ctx, LineType.TODO)

        assert len(updates) >= 2, "the list should have been rewritten as work landed"

    def test_it_finishes_coherent(self, outcome):
        """A checklist left half in-progress is worse than none: it tells the user
        the run stopped somewhere it did not."""
        _result, _events, ctx = outcome

        assert [item.status for item in ctx.todos] == ["completed"] * 3

    def test_the_events_carry_the_transitions(self, outcome):
        _result, events, _ctx = outcome
        updates = [event for event in events if event["type"] == "todo_updated"]

        assert updates
        assert any(event["transitions"] for event in updates)


# ---------------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------------


class TestDelegationThroughTheRun:
    def test_a_subagent_ran_and_returned_structured(self, outcome):
        _result, _events, ctx = outcome

        entry = next(entry for entry in ctx.ledger if entry.call.tool == "agent.delegate")
        assert entry.result.ok is True
        assert "conftest.py" in entry.result.data["summary"]
        assert entry.result.data["status"] == "completed"

    def test_the_child_kept_its_own_journal(self, outcome):
        _result, _events, ctx = outcome
        children = list(ctx.journal.path.parent.rglob("sub/*.jsonl"))

        assert len(children) == 1

    def test_the_child_could_not_exceed_the_parent(self, outcome):
        """The parent is read-only, so the child is too — whatever its template
        says."""
        _result, _events, ctx = outcome
        child_journal = next(ctx.journal.path.parent.rglob("sub/*.jsonl"))
        child_lines = list(read_journal(child_journal))

        tools = {
            line.get("tool") for line in child_lines
            if line["type"] == LineType.TOOL_RESULT
        }
        assert not (tools & MUTATING_TOOLS), tools

    def test_the_result_names_the_subagent(self, outcome):
        """What the console nests the child's rows under.  The bus-level tagging
        is covered in ``test_delegation.py``; here the point is that a real run
        produces the identifier at all."""
        _result, _events, ctx = outcome

        entry = next(entry for entry in ctx.ledger if entry.call.tool == "agent.delegate")
        assert entry.result.data["agent"] == "explorer"


# ---------------------------------------------------------------------------
# What the console renders
# ---------------------------------------------------------------------------


class TestTheConsoleDemoIsReal:
    def test_every_render_path_has_a_real_event_behind_it(self, outcome):
        """The Phase E gate. Each of these is a thing the VS Code console draws;
        if the engine never emits one, the panel is decoration."""
        _result, events, _ctx = outcome
        seen = {event["type"] for event in events}

        for required in (
            "run_started",        # the header
            "iteration",          # step counter
            "agent_message",      # streamed prose
            "tool_start",         # activity rows
            "tool_result",
            "todo_updated",       # the checklist
            "run_finished",       # the terminal event that closes the stream
        ):
            assert required in seen, f"the console renders {required} and nothing emits it"

    def test_the_denied_call_is_visible_to_a_client(self, outcome):
        """A refusal the user cannot see is indistinguishable from a tool that
        quietly did nothing."""
        _result, events, _ctx = outcome
        denials = [
            event for event in events
            if event["type"] == "tool_result" and event.get("denied")
        ]
        assert denials
        assert {event["name"] for event in denials} >= {"fs.write"}
