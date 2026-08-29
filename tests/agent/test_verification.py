"""The verification policy — Batch V4-E3.

The point is not "run the tests". It is that the model *sees* the result and can
react to it. The shape this replaces — a validation phase after the run — produced
a red suite the model never learned about, and a summary the user had to read to
find out.
"""
from __future__ import annotations

import asyncio

import pytest

from gitpilot.agent.contracts import AgentTurn, Finality
from gitpilot.agent.journal import LineType
from gitpilot.agent.loop import AgentLoop, Verification
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.toolkit import Effect, LocalWorkspace, ToolCall, ToolExecutionContext, ToolResult
from gitpilot.toolkit.builtins import build_default_registry

from .test_loop import Recorder, ScriptedAdapter, _context


@pytest.fixture
def tested_repo(tmp_path):
    """A checkout with a real test framework marker."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n",
    )
    (root / "app.py").write_text("x = 1\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text("def test_x():\n    assert True\n")
    return root


@pytest.fixture
def untested_repo(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "notes.md").write_text("no tests here\n")
    return root


def _registry(*, writes=True, tests=True):
    registry = build_default_registry(
        include_git=False, include_forge=False, include_terminal=False,
        include_testing=tests, include_todo=False,
    )
    if not writes:
        return registry
    return registry


def _ctx(tmp_path, registry, workspace, profile=None):
    ctx = _context(tmp_path, registry, profile=profile)
    ctx.tool_context = ToolExecutionContext(
        workspace=LocalWorkspace(root=workspace), extras={"registry": registry},
    )
    return ctx


def _write_turn(call_id="w1", path="app.py"):
    return AgentTurn(
        tool_calls=[ToolCall(
            id=call_id, tool="fs.write",
            arguments={"path": path, "content": "x = 2\n"},
        )],
        finality=Finality.CONTINUE,
    )


def _final(text="all done"):
    return AgentTurn(text=text, finality=Finality.FINAL)


def _run(ctx, registry, turns, verification, events=None):
    adapter = ScriptedAdapter(turns, profile=ctx.model_profile, registry=registry)
    loop = AgentLoop(
        adapter, registry, verification=verification, events=events or Recorder(),
    )
    return asyncio.run(loop.run(ctx)), adapter


# ---------------------------------------------------------------------------
# required
# ---------------------------------------------------------------------------


class TestRequired:
    def test_an_unverified_final_is_refused(self, tmp_path, tested_repo):
        """The model is told what is missing and left to act — the whole point."""
        registry = _registry()
        ctx = _ctx(tmp_path, registry, tested_repo)

        result, adapter = _run(
            ctx, registry,
            [_write_turn(), _final(), _final("now with tests")],
            Verification(tests="required"),
        )

        # The refusal reached the model as a message it can read.
        prompts = [
            message["content"] for message in ctx.messages
            if message.get("role") == "user"
        ]
        assert any("have not run the tests" in text for text in prompts)
        # And a model that never complies does not get to report success: the
        # scripted turns here refuse twice and then give up.
        assert result.status == "degraded"
        assert result.verified is False

    def test_the_refusal_names_the_command(self, tmp_path, tested_repo):
        registry = _registry()
        ctx = _ctx(tmp_path, registry, tested_repo)

        _run(
            ctx, registry, [_write_turn(), _final(), _final()],
            Verification(tests="required"),
        )

        nudge = next(
            message["content"] for message in ctx.messages
            if message.get("role") == "user" and "have not run" in message["content"]
        )
        assert "pytest" in nudge

    def test_a_run_that_tested_is_not_blocked(self, tmp_path, tested_repo):
        registry = _registry()
        ctx = _ctx(tmp_path, registry, tested_repo)

        async def fake_test(call, ctx_):
            return ToolResult.success(
                call, "Tests: 1 passed, 0 failed, 0 skipped",
                data={"framework": "pytest", "passed": 1, "failed": 0,
                      "skipped": 0, "green": True, "command": "pytest",
                      "exit_code": 0},
            )

        registry._handlers["test.run"] = fake_test   # noqa: SLF001

        result, _adapter = _run(
            ctx, registry,
            [
                _write_turn(),
                AgentTurn(
                    tool_calls=[ToolCall(id="t1", tool="test.run", arguments={})],
                    finality=Finality.CONTINUE,
                ),
                _final(),
            ],
            Verification(tests="required"),
        )

        assert result.status == "completed"
        assert result.verified is True
        assert result.answer == "all done"

    def test_a_read_only_run_is_never_blocked(self, tmp_path, tested_repo):
        """Nothing changed, so there is nothing to verify."""
        registry = _registry()
        ctx = _ctx(tmp_path, registry, tested_repo)

        result, _adapter = _run(
            ctx, registry, [_final("nothing to change")],
            Verification(tests="required"),
        )

        assert result.status == "completed"
        assert result.verified is True

    def test_the_fix_cycle_cap_is_honoured(self, tmp_path, tested_repo):
        """A model that will not run the tests must not loop forever."""
        registry = _registry()
        ctx = _ctx(tmp_path, registry, tested_repo)

        result, _adapter = _run(
            ctx, registry, [_write_turn()] + [_final()] * 8,
            Verification(tests="required", max_fix_cycles=2),
        )

        nudges = [
            message for message in ctx.messages
            if message.get("role") == "user" and "have not run" in str(message.get("content"))
        ]
        assert len(nudges) == 2
        assert result.verified is False

    def test_the_unverified_note_reaches_the_answer(self, tmp_path, tested_repo):
        """A caveat only in the metadata is a caveat nobody sees."""
        registry = _registry()
        ctx = _ctx(tmp_path, registry, tested_repo)

        result, _adapter = _run(
            ctx, registry, [_write_turn()] + [_final("I changed the file")] * 4,
            Verification(tests="required", max_fix_cycles=1),
        )

        assert "not been verified" in result.answer
        assert result.verified is False
        assert any("unverified" in note for note in result.skipped)

    def test_it_is_journaled(self, tmp_path, tested_repo):
        registry = _registry()
        ctx = _ctx(tmp_path, registry, tested_repo)

        _run(
            ctx, registry, [_write_turn(), _final(), _final()],
            Verification(tests="required"),
        )

        states = [
            line for line in ctx.journal.read()
            if line["type"] == LineType.STATE_CHANGE and "verification" in line
        ]
        assert states
        assert states[0]["cycle"] == 1

    def test_the_event_says_which_cycle(self, tmp_path, tested_repo):
        registry = _registry()
        ctx = _ctx(tmp_path, registry, tested_repo)
        events = Recorder()

        _run(
            ctx, registry, [_write_turn(), _final(), _final()],
            Verification(tests="required"), events=events,
        )

        payload = events.payload("verification_required")
        assert payload["cycle"] == 1
        assert payload["max_cycles"] == 2
        assert "pytest" in payload["command"]


# ---------------------------------------------------------------------------
# auto and off
# ---------------------------------------------------------------------------


class TestAutoAndOff:
    def test_auto_skips_a_project_with_no_tests(self, tmp_path, untested_repo):
        """A repository without tests must not be blocked on running tests it
        does not have."""
        registry = _registry()
        ctx = _ctx(tmp_path, registry, untested_repo)

        result, _adapter = _run(
            ctx, registry, [_write_turn(), _final()],
            Verification(tests="auto"),
        )

        assert result.status == "completed"
        assert result.verified is True
        assert result.skipped == []

    def test_auto_enforces_where_there_are_tests(self, tmp_path, tested_repo):
        registry = _registry()
        ctx = _ctx(tmp_path, registry, tested_repo)

        _run(
            ctx, registry, [_write_turn(), _final(), _final()],
            Verification(tests="auto"),
        )

        assert any(
            "have not run the tests" in str(message.get("content"))
            for message in ctx.messages
        )

    def test_off_never_blocks(self, tmp_path, tested_repo):
        registry = _registry()
        ctx = _ctx(tmp_path, registry, tested_repo)

        result, _adapter = _run(
            ctx, registry, [_write_turn(), _final()], Verification(tests="off"),
        )

        assert result.status == "completed"
        assert result.verified is True

    def test_required_without_tests_says_so_rather_than_blocking(
        self, tmp_path, untested_repo,
    ):
        """`required` on a project with no suite cannot be satisfied; reporting
        that beats an infinite nudge."""
        registry = _registry()
        ctx = _ctx(tmp_path, registry, untested_repo)

        result, _adapter = _run(
            ctx, registry, [_write_turn(), _final()],
            Verification(tests="required"),
        )

        assert result.verified is False
        assert any("no test suite" in note for note in result.skipped)

    def test_a_run_with_no_workspace_is_not_blocked(self, tmp_path):
        registry = _registry()
        ctx = _context(tmp_path, registry)
        ctx.tool_context = ToolExecutionContext(extras={"registry": registry})

        result, _adapter = _run(
            ctx, registry, [_write_turn(), _final()],
            Verification(tests="required"),
        )
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# LITE — the engine runs the tests itself
# ---------------------------------------------------------------------------


class TestLiteEngineIssued:
    def test_the_engine_issues_the_run(self, tmp_path, tested_repo):
        """Design rule 5: a policy that only holds for frontier models is not a
        policy. A 1.5B model told "run the tests" answers with prose about
        running the tests."""
        profile = resolve_profile("ollama", "qwen2.5:1.5b", cache_path=tmp_path / "c.json")
        registry = _registry()
        ctx = _ctx(tmp_path, registry, tested_repo, profile=profile)

        ran = []

        async def fake_test(call, ctx_):
            ran.append(call.id)
            return ToolResult.success(
                call, "Tests: 1 passed, 0 failed, 0 skipped",
                data={"framework": "pytest", "passed": 1, "failed": 0,
                      "skipped": 0, "green": True, "command": "pytest",
                      "exit_code": 0},
            )

        registry._handlers["test.run"] = fake_test   # noqa: SLF001

        result, _adapter = _run(
            ctx, registry, [_write_turn(), _final(), _final()],
            Verification(tests="required"),
        )

        assert ran, "the engine did not run the tests for the LITE dialect"
        assert result.verified is True

    def test_the_engine_issued_call_is_authorized_and_journaled(
        self, tmp_path, tested_repo,
    ):
        """Through `_dispatch` rather than around it: an engine-issued call that
        skipped the policy engine would be a hole in invariant 1 with the
        engine's own name on it."""
        profile = resolve_profile("ollama", "qwen2.5:1.5b", cache_path=tmp_path / "c.json")
        registry = _registry()
        ctx = _ctx(tmp_path, registry, tested_repo, profile=profile)

        async def fake_test(call, ctx_):
            return ToolResult.success(
                call, "Tests: 1 passed",
                data={"framework": "pytest", "passed": 1, "failed": 0,
                      "skipped": 0, "green": True, "command": "pytest",
                      "exit_code": 0},
            )

        registry._handlers["test.run"] = fake_test   # noqa: SLF001

        _run(
            ctx, registry, [_write_turn(), _final(), _final()],
            Verification(tests="required"),
        )

        lines = ctx.journal.read()
        decisions = {
            line["call_id"] for line in lines
            if line["type"] == LineType.POLICY_DECISION
        }
        results = [
            line for line in lines
            if line["type"] == LineType.TOOL_RESULT and line["tool"] == "test.run"
        ]
        assert results, "the engine-issued run left no result line"
        assert results[0]["call_id"] in decisions, (
            "the engine-issued call executed with no authorization line"
        )

    def test_a_native_run_is_told_rather_than_driven(self, tmp_path, tested_repo):
        """A model that can call tools gets an observation; only LITE gets the
        call made for it."""
        registry = _registry()
        ctx = _ctx(tmp_path, registry, tested_repo)

        _run(
            ctx, registry, [_write_turn(), _final(), _final()],
            Verification(tests="required"),
        )

        assert not ctx.ledger.any_call_to("test.run")


class TestPolicyShape:
    @pytest.mark.parametrize(("tests", "enabled"), [
        ("off", False), ("auto", True), ("required", True), ("", False),
    ])
    def test_enabled_reflects_the_setting(self, tests, enabled):
        assert Verification(tests=tests).enabled is enabled

    def test_the_prompt_asks_for_it_when_required(self, tmp_path):
        from gitpilot.agent.prompts import build_system_prompt

        prompt = build_system_prompt(
            resolve_profile("claude", "claude-sonnet-4-5", cache_path=tmp_path / "c.json"),
            require_verification=True,
        )
        assert "run the tests and report" in prompt
