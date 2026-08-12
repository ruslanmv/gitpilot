"""The AgentLoop — Batch V4-C2."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Dict, List, Optional, Sequence

import pytest

from gitpilot.agent.adapter import ModelAdapter
from gitpilot.agent.context import AgentContext, State
from gitpilot.agent.contracts import AgentTurn, Dialect, Finality, TokenUsage
from gitpilot.agent.journal import LineType, RunJournal
from gitpilot.agent.loop import AgentLoop, Decision, LoopEvents, new_run_id
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.prompts import build_system_prompt
from gitpilot.toolkit import (
    Effect,
    LocalWorkspace,
    Risk,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)

# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


class ScriptedAdapter(ModelAdapter):
    """Yields queued turns, recording what the loop fed back."""

    dialect = Dialect.NATIVE

    def __init__(self, turns: Sequence[AgentTurn], profile=None, registry=None):
        self.provider = _FakeProvider()
        self.profile = profile
        self.registry = registry
        self._turns = list(turns)
        self.seen_messages: List[List[Dict[str, Any]]] = []
        self.concluded: List[bool] = []
        self._conclude_answer = True

    async def generate(self, *, system=None, messages=(), capabilities=None, **opts) -> AgentTurn:
        self.seen_messages.append([dict(m) for m in messages])
        if not self._turns:
            return AgentTurn(text="done", finality=Finality.FINAL)
        return self._turns.pop(0)

    def assistant_message(self, turn):
        if not turn.text and not turn.tool_calls:
            return None
        return {"role": "assistant", "content": turn.text or "(tool call)"}

    def result_messages(self, call, result):
        return [{"role": "tool", "tool_call_id": call.id, "content": result.content}]

    def conclude(self, turn, results):
        answer = self._conclude_answer and all(r.ok for r in results)
        self.concluded.append(answer)
        return answer

    def dropped_tools(self, capabilities=None):
        return []

    def tool_defs(self, capabilities=None):
        return []


class _FakeProvider:
    """Answers anything with one plain sentence.

    Needed because a downgrade replaces the scripted adapter with a *real* one,
    which then talks to this provider — the test would otherwise be asserting on
    a path that cannot run.
    """

    name = "fake"
    model = "fake-model"
    supports_native_tools = True
    supports_parallel_tool_calls = True
    supports_streaming = False

    async def complete(self, messages, *, tools=None, system=None, **opts):
        from gitpilot.agent.providers.base import ProviderResponse

        return ProviderResponse(text="answered after the downgrade")

    def stream(self, *args, **kwargs):
        raise NotImplementedError


READ_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
    "additionalProperties": False,
}


def _registry(handler=None, *, tool_id="fs.read", risk=Risk.SAFE, effects=frozenset()):
    registry = ToolRegistry()

    async def default_handler(call, ctx):
        return ToolResult.success(call, f"content of {call.arguments['path']}")

    registry.register(
        ToolSpec(
            id=tool_id, title="Read", description="Read a file.",
            params_schema=READ_SCHEMA, risk=risk, effects=effects,
        ),
        handler or default_handler,
    )
    return registry


def _context(tmp_path, registry, profile=None, task="do the thing"):
    profile = profile or resolve_profile("claude", "claude-sonnet-4-5", cache_path=tmp_path / "c.json")
    journal = RunJournal(run_id=new_run_id(), session_id="sess", root=tmp_path)
    return AgentContext(
        run_id=journal.run_id,
        task=task,
        journal=journal,
        model_profile=profile,
        tool_context=ToolExecutionContext(
            workspace=LocalWorkspace(root=tmp_path), extras={"registry": registry},
        ),
        session_id="sess",
        messages=[{"role": "user", "content": task}],
    )


class Recorder(LoopEvents):
    def __init__(self):
        self.events: List[tuple] = []
        super().__init__(self._capture)

    async def _capture(self, event_type, payload):
        self.events.append((event_type, payload))

    def types(self):
        return [event for event, _ in self.events]

    def payload(self, event_type):
        return next(payload for event, payload in self.events if event == event_type)


def _call(tool="fs.read", **arguments):
    return ToolCall(id="c1", tool=tool, arguments=arguments or {"path": "a.py"})


# ---------------------------------------------------------------------------
# Termination
# ---------------------------------------------------------------------------


class TestTermination:
    def test_final_returns_immediately_without_dispatching(self, tmp_path):
        registry = _registry()
        adapter = ScriptedAdapter([AgentTurn(text="the answer", finality=Finality.FINAL)])
        ctx = _context(tmp_path, registry)

        result = asyncio.run(AgentLoop(adapter, registry).run(ctx))

        assert result.status == "completed"
        assert result.answer == "the answer"
        assert len(ctx.ledger) == 0
        assert ctx.state is State.COMPLETED

    def test_continue_iterates_until_final(self, tmp_path):
        registry = _registry()
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call()], finality=Finality.CONTINUE),
            AgentTurn(text="now I know", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)

        result = asyncio.run(AgentLoop(adapter, registry).run(ctx))

        assert result.answer == "now I know"
        assert ctx.iteration == 2
        assert len(ctx.ledger) == 1

    def test_final_after_tools_ends_when_conclude_says_so(self, tmp_path):
        registry = _registry()
        adapter = ScriptedAdapter([
            AgentTurn(text="applied", tool_calls=[_call()], finality=Finality.FINAL_AFTER_TOOLS),
        ])
        ctx = _context(tmp_path, registry)

        result = asyncio.run(AgentLoop(adapter, registry).run(ctx))

        assert result.status == "completed"
        assert adapter.concluded == [True]

    def test_final_after_tools_keeps_going_when_conclude_says_no(self, tmp_path):
        registry = _registry()
        adapter = ScriptedAdapter([
            AgentTurn(text="tried", tool_calls=[_call()], finality=Finality.FINAL_AFTER_TOOLS),
            AgentTurn(text="second attempt worked", finality=Finality.FINAL),
        ])
        adapter._conclude_answer = False
        ctx = _context(tmp_path, registry)

        result = asyncio.run(AgentLoop(adapter, registry).run(ctx))

        assert result.answer == "second attempt worked"
        assert ctx.iteration == 2


class TestBudgets:
    def test_iteration_exhaustion_returns_partial_results(self, tmp_path):
        """A runaway run stops with what it has rather than being killed."""
        registry = _registry()
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call()], finality=Finality.CONTINUE) for _ in range(10)
        ])
        ctx = _context(tmp_path, registry)
        ctx.budgets = replace(ctx.budgets, max_iterations=3)

        result = asyncio.run(AgentLoop(adapter, registry).run(ctx))

        assert result.status == "budget_exceeded"
        assert ctx.iteration == 3
        assert any("max_iterations" in note for note in result.skipped)

    def test_tool_call_limit_is_enforced(self, tmp_path):
        registry = _registry()
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call()], finality=Finality.CONTINUE) for _ in range(10)
        ])
        ctx = _context(tmp_path, registry)
        ctx.budgets = replace(ctx.budgets, max_tool_calls=2)

        result = asyncio.run(AgentLoop(adapter, registry).run(ctx))

        assert result.status == "budget_exceeded"
        assert len(ctx.ledger) == 2

    def test_wall_clock_limit_is_enforced(self, tmp_path):
        registry = _registry()
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call()], finality=Finality.CONTINUE) for _ in range(10)
        ])
        ctx = _context(tmp_path, registry)
        ctx.budgets = replace(ctx.budgets, max_runtime_seconds=5)

        ticks = iter([0.0, 1.0, 2.0, 99.0, 100.0, 101.0])
        loop = AgentLoop(adapter, registry, clock=lambda: next(ticks, 200.0))

        result = asyncio.run(loop.run(ctx))
        assert result.status == "budget_exceeded"

    def test_usage_accumulates_across_turns(self, tmp_path):
        registry = _registry()
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call()], finality=Finality.CONTINUE,
                      usage=TokenUsage(prompt_tokens=100, completion_tokens=10)),
            AgentTurn(text="done", finality=Finality.FINAL,
                      usage=TokenUsage(prompt_tokens=150, completion_tokens=20)),
        ])
        ctx = _context(tmp_path, registry)

        asyncio.run(AgentLoop(adapter, registry).run(ctx))

        assert ctx.budgets.total_tokens == 280


# ---------------------------------------------------------------------------
# The ordering the invariants depend on
# ---------------------------------------------------------------------------


class TestJournalOrdering:
    def test_the_decision_is_journaled_before_the_result(self, tmp_path):
        registry = _registry()
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call()], finality=Finality.CONTINUE),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)

        asyncio.run(AgentLoop(adapter, registry).run(ctx))

        types = [line["type"] for line in ctx.journal.read()]
        decision_at = types.index(LineType.POLICY_DECISION)
        result_at = types.index(LineType.TOOL_RESULT)
        assert decision_at < result_at

    def test_every_call_gets_a_decision_even_when_allowed(self, tmp_path):
        """The Phase D invariants have nothing to assert against otherwise."""
        registry = _registry()
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call(), _call("fs.read", path="b.py")],
                      finality=Finality.CONTINUE),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)

        asyncio.run(AgentLoop(adapter, registry).run(ctx))

        decisions = [l for l in ctx.journal.read() if l["type"] == LineType.POLICY_DECISION]
        results = [l for l in ctx.journal.read() if l["type"] == LineType.TOOL_RESULT]
        assert len(decisions) == len(results) == 2
        assert all(d["verdict"] == "allow" for d in decisions)

    def test_a_mutating_tool_requests_a_checkpoint(self, tmp_path):
        """The stub already reports it so the ordering is exercised before D4."""
        registry = _registry(
            tool_id="fs.write", effects=frozenset({Effect.WRITES_FS}), risk=Risk.APPROVAL,
        )
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call("fs.write", path="a.py")], finality=Finality.CONTINUE),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)

        asyncio.run(AgentLoop(adapter, registry).run(ctx))

        decision = [l for l in ctx.journal.read() if l["type"] == LineType.POLICY_DECISION][0]
        assert decision["requires_checkpoint"] is True

    def test_the_checkpoint_is_journaled_before_the_result(self, tmp_path):
        class _Checkpointer:
            async def create(self, call, ctx):
                return "ckpt-1"

        registry = _registry(tool_id="fs.write", effects=frozenset({Effect.WRITES_FS}))
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call("fs.write", path="a.py")], finality=Finality.CONTINUE),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)

        asyncio.run(AgentLoop(adapter, registry, checkpointer=_Checkpointer()).run(ctx))

        types = [line["type"] for line in ctx.journal.read()]
        assert types.index(LineType.CHECKPOINT_REF) < types.index(LineType.TOOL_RESULT)

    def test_a_failing_checkpointer_does_not_block_the_work(self, tmp_path):
        class _Broken:
            async def create(self, call, ctx):
                raise RuntimeError("disk full")

        registry = _registry(tool_id="fs.write", effects=frozenset({Effect.WRITES_FS}))
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call("fs.write", path="a.py")], finality=Finality.CONTINUE),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)

        result = asyncio.run(AgentLoop(adapter, registry, checkpointer=_Broken()).run(ctx))

        assert result.status == "completed"
        assert len(ctx.ledger) == 1


# ---------------------------------------------------------------------------
# Feeding results back
# ---------------------------------------------------------------------------


class TestObservations:
    def test_a_denial_reaches_the_model_as_an_observation(self, tmp_path):
        class _Denier:
            async def authorize(self, call, ctx):
                return Decision(verdict="deny", reason="git.push not granted")

        registry = _registry()
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call()], finality=Finality.CONTINUE),
            AgentTurn(text="understood, another way then", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)

        result = asyncio.run(AgentLoop(adapter, registry, policy=_Denier()).run(ctx))

        assert result.status == "completed"
        second_turn_messages = adapter.seen_messages[1]
        assert any("Denied" in str(m.get("content")) for m in second_turn_messages)
        assert ctx.ledger.entries[0].result.denied is True

    def test_invalid_arguments_come_back_as_a_correctable_observation(self, tmp_path):
        registry = _registry()
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[ToolCall(id="c1", tool="fs.read", arguments={})],
                      finality=Finality.CONTINUE),
            AgentTurn(text="corrected", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)

        asyncio.run(AgentLoop(adapter, registry).run(ctx))

        observation = adapter.seen_messages[1][-1]["content"]
        assert "missing required argument 'path'" in observation
        assert ctx.ledger.entries[0].result.error == "invalid_arguments"

    def test_a_tool_that_raises_does_not_kill_the_run(self, tmp_path):
        async def explode(call, ctx):
            raise RuntimeError("backend on fire")

        registry = _registry(explode)
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call()], finality=Finality.CONTINUE),
            AgentTurn(text="recovered", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)

        result = asyncio.run(AgentLoop(adapter, registry).run(ctx))

        assert result.status == "completed"
        assert ctx.errors[0].where == "fs.read"

    def test_a_blocking_hook_prevents_execution(self, tmp_path):
        executed = []

        async def handler(call, ctx):
            executed.append(call)
            return ToolResult.success(call, "ran")

        class _Blocking:
            async def fire(self, event, **payload):
                return "policy says no" if event == "pre_tool_use" else None

        registry = _registry(handler)
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call()], finality=Finality.CONTINUE),
            AgentTurn(text="fine", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)

        asyncio.run(AgentLoop(adapter, registry, hooks=_Blocking()).run(ctx))

        assert executed == []
        assert ctx.ledger.entries[0].result.denied is True


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestCancellation:
    def test_cancel_between_calls_stops_the_run(self, tmp_path):
        """The executor this replaces could only check between phases."""
        registry = _registry()
        loop_holder = {}

        async def handler(call, ctx):
            loop_holder["loop"].cancel()
            return ToolResult.success(call, "ran")

        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call(), _call("fs.read", path="b.py")],
                      finality=Finality.CONTINUE),
            AgentTurn(text="never reached", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, _registry(handler))
        loop = AgentLoop(adapter, _registry(handler))
        loop_holder["loop"] = loop

        result = asyncio.run(loop.run(ctx))

        assert result.status == "cancelled"
        assert len(ctx.ledger) == 1, "the second call in the same turn was not run"

    def test_an_outer_cancellation_is_recorded_not_swallowed(self, tmp_path):
        registry = _registry()

        class _Cancelling(ScriptedAdapter):
            async def generate(self, **kwargs):
                raise asyncio.CancelledError()

        ctx = _context(tmp_path, registry)
        adapter = _Cancelling([])

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(AgentLoop(adapter, registry).run(ctx))

        assert ctx.state is State.CANCELLED
        statuses = [l.get("status") for l in ctx.journal.read() if l["type"] == LineType.RUN_FINISHED]
        assert statuses == ["cancelled"]


# ---------------------------------------------------------------------------
# The degradation ladder
# ---------------------------------------------------------------------------


class TestDegradationLadder:
    def _malformed(self):
        return AgentTurn(text="garbage", finality=Finality.FINAL, meta={"parse_error": "unparseable"})

    def test_a_parse_error_downgrades_at_once(self, tmp_path):
        """The dialect already spent its own repair budget before reporting one,
        and its "final" is our fallback to prose rather than the model finishing."""
        registry = _registry()
        adapter = ScriptedAdapter([
            self._malformed(),
            AgentTurn(text="worked after downgrade", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)
        events = Recorder()

        result = asyncio.run(AgentLoop(adapter, registry, events=events).run(ctx))

        assert "dialect_downgraded" in events.types()
        payload = events.payload("dialect_downgraded")
        assert payload["from"] == "native" and payload["to"] == "react_text"
        assert result.status == "completed"

    def test_a_parse_error_final_does_not_end_the_run_while_a_rung_remains(self, tmp_path):
        """Ending here would report a failure to communicate as a finished task."""
        registry = _registry()
        adapter = ScriptedAdapter([self._malformed()])
        ctx = _context(tmp_path, registry)

        result = asyncio.run(AgentLoop(adapter, registry).run(ctx))

        assert result.answer != "garbage"
        assert ctx.iteration >= 2, "the run continued in the weaker dialect"

    def test_an_unreadable_provider_response_gets_one_retry_first(self, tmp_path):
        """A gateway hiccup is plausible; retrying the request is cheap."""
        from gitpilot.agent.providers.base import MalformedResponse

        class _Flaky(ScriptedAdapter):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.attempts = 0

            async def generate(self, **kwargs):
                self.attempts += 1
                if self.attempts == 1:
                    raise MalformedResponse("gateway returned html")
                return AgentTurn(text="second attempt fine", finality=Finality.FINAL)

        registry = _registry()
        adapter = _Flaky([])
        ctx = _context(tmp_path, registry)
        events = Recorder()

        result = asyncio.run(AgentLoop(adapter, registry, events=events).run(ctx))

        assert adapter.attempts == 2
        assert "dialect_downgraded" not in events.types()
        assert result.answer == "second attempt fine"

    def test_repeated_unreadable_responses_downgrade(self, tmp_path):
        from gitpilot.agent.providers.base import MalformedResponse

        class _Broken(ScriptedAdapter):
            async def generate(self, **kwargs):
                raise MalformedResponse("gateway returned html")

        registry = _registry()
        ctx = _context(tmp_path, registry)
        events = Recorder()

        result = asyncio.run(AgentLoop(_Broken([]), registry, events=events).run(ctx))

        assert "dialect_downgraded" in events.types()
        assert result.status in ("completed", "degraded")

    def test_the_downgrade_is_journaled(self, tmp_path):
        registry = _registry()
        adapter = ScriptedAdapter([
            self._malformed(),
            AgentTurn(text="ok", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)

        asyncio.run(AgentLoop(adapter, registry).run(ctx))

        downgrades = [
            line for line in ctx.journal.read() if line.get("dialect_downgraded")
        ]
        assert downgrades and downgrades[0]["dialect_downgraded"] == "react_text"

    def test_reaching_the_lite_floor_finishes_degraded_and_names_what_was_skipped(self, tmp_path):
        """LITE exposes only lite-compatible tools; claiming success would lie."""
        registry = ToolRegistry()

        async def handler(call, ctx):
            return ToolResult.success(call, "ran")

        registry.register(
            ToolSpec(
                id="terminal.run", title="Run", description="Run a command.",
                params_schema={"type": "object", "properties": {}},
                lite_compatible=False,
            ),
            handler,
        )

        profile = resolve_profile("ollama", "llama3:8b", cache_path=tmp_path / "c.json")
        adapter = ScriptedAdapter([
            self._malformed(),
            AgentTurn(text="did what I could", finality=Finality.FINAL),
        ], profile=profile, registry=registry)
        ctx = _context(tmp_path, registry, profile=profile)

        result = asyncio.run(AgentLoop(adapter, registry).run(ctx))

        assert result.status == "degraded"
        assert any("terminal.run" in note for note in result.skipped)
        assert ctx.state is State.DEGRADED

    def test_lite_has_no_further_rung(self, tmp_path):
        registry = _registry()
        profile = resolve_profile("ollama", "qwen2.5:1.5b", cache_path=tmp_path / "c.json")
        adapter = ScriptedAdapter(
            [self._malformed(), self._malformed()],
            profile=profile, registry=registry,
        )
        ctx = _context(tmp_path, registry, profile=profile)
        events = Recorder()

        result = asyncio.run(AgentLoop(adapter, registry, events=events).run(ctx))

        assert "dialect_downgraded" not in events.types()
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class TestEvents:
    def test_the_loop_narrates_what_it_is_doing(self, tmp_path):
        registry = _registry()
        adapter = ScriptedAdapter([
            AgentTurn(text="looking", tool_calls=[_call()], finality=Finality.CONTINUE),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)
        events = Recorder()

        asyncio.run(AgentLoop(adapter, registry, events=events).run(ctx))

        types = events.types()
        assert types[0] == "run_started"
        for expected in ("iteration", "agent_message", "tool_start", "tool_result"):
            assert expected in types, expected

    def test_file_write_gets_its_first_emitter(self, tmp_path):
        """The factory has existed unused since it was written."""
        async def writer(call, ctx):
            return ToolResult.success(call, "written", data={"paths_written": ["a.py"]})

        registry = _registry(writer, tool_id="fs.write", effects=frozenset({Effect.WRITES_FS}))
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call("fs.write", path="a.py")], finality=Finality.CONTINUE),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)
        events = Recorder()

        asyncio.run(AgentLoop(adapter, registry, events=events).run(ctx))

        assert "file_write" in events.types()
        assert events.payload("file_write")["path"] == "a.py"

    def test_a_broken_event_transport_does_not_kill_the_run(self, tmp_path):
        async def explode(event_type, payload):
            raise RuntimeError("client gone")

        registry = _registry()
        adapter = ScriptedAdapter([AgentTurn(text="done", finality=Finality.FINAL)])
        ctx = _context(tmp_path, registry)

        result = asyncio.run(
            AgentLoop(adapter, registry, events=LoopEvents(explode)).run(ctx)
        )

        assert result.status == "completed"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


class TestPrompts:
    def test_a_small_model_gets_the_lean_prompt(self, tmp_path):
        lean = build_system_prompt(
            resolve_profile("ollama", "qwen2.5:1.5b", cache_path=tmp_path / "c.json")
        )
        standard = build_system_prompt(
            resolve_profile("claude", "claude-sonnet-4-5", cache_path=tmp_path / "c.json")
        )

        assert len(lean) < len(standard)
        assert "Read before you change" in lean

    def test_read_only_runs_say_so(self, tmp_path):
        prompt = build_system_prompt(
            resolve_profile("claude", "claude-sonnet-4-5", cache_path=tmp_path / "c.json"),
            read_only=True,
        )
        assert "read-only" in prompt
        assert "cannot modify files" in prompt

    def test_verification_suffix_is_omitted_for_read_only_runs(self, tmp_path):
        prompt = build_system_prompt(
            resolve_profile("claude", "claude-sonnet-4-5", cache_path=tmp_path / "c.json"),
            read_only=True, require_verification=True,
        )
        assert "run the tests and report" not in prompt


# ---------------------------------------------------------------------------
# The ``ask`` arm
#
# Unreachable while the policy is the permissive stub, so it is driven here with
# a policy that asks.  The point of testing it in C rather than waiting for D3 is
# that ``awaiting_approval`` has to be a real journaled transition owned by the
# loop — if it were not, D3 would have to retrofit state handling into the
# dispatch path instead of just supplying an approver.
# ---------------------------------------------------------------------------


class _AskingPolicy:
    def __init__(self, *, risk="approval", requires_checkpoint=False):
        self._decision = Decision(
            verdict="ask", reason="mutates the checkout",
            risk=risk, requires_checkpoint=requires_checkpoint,
        )

    async def authorize(self, call, ctx):
        return self._decision


class _Approver:
    """Answers, and records the state the loop was in when it was asked."""

    def __init__(self, answer):
        self.answer = answer
        self.state_when_asked = None
        self.calls = 0

    async def request(self, call, decision, ctx):
        self.calls += 1
        self.state_when_asked = ctx.state
        return self.answer


class TestApprovalArm:
    def _adapter(self):
        return ScriptedAdapter([
            AgentTurn(tool_calls=[_call("fs.write", path="a.py")], finality=Finality.CONTINUE),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])

    def _registry(self):
        async def writer(call, ctx):
            return ToolResult.success(call, "written")

        return _registry(writer, tool_id="fs.write", effects=frozenset({Effect.WRITES_FS}))

    def test_an_approval_lets_the_call_run(self, tmp_path):
        registry = self._registry()
        approver = _Approver(True)
        ctx = _context(tmp_path, registry)

        result = asyncio.run(AgentLoop(
            self._adapter(), registry,
            policy=_AskingPolicy(), approvals=approver,
        ).run(ctx))

        assert result.status == "completed"
        assert approver.calls == 1
        assert [entry.result.ok for entry in ctx.ledger] == [True]

    def test_a_refusal_reaches_the_model_as_an_observation(self, tmp_path):
        registry = self._registry()
        ctx = _context(tmp_path, registry)

        asyncio.run(AgentLoop(
            self._adapter(), registry,
            policy=_AskingPolicy(), approvals=_Approver(False),
        ).run(ctx))

        assert [entry.result.ok for entry in ctx.ledger] == [False]
        assert any(
            "denied by user" in str(message.get("content", ""))
            for message in ctx.messages
        )

    def test_the_wait_is_a_journaled_state_that_is_restored_afterwards(self, tmp_path):
        registry = self._registry()
        approver = _Approver(True)
        ctx = _context(tmp_path, registry)

        asyncio.run(AgentLoop(
            self._adapter(), registry,
            policy=_AskingPolicy(), approvals=approver,
        ).run(ctx))

        assert approver.state_when_asked is State.AWAITING_APPROVAL
        states = [
            line.get("state") for line in ctx.journal.read()
            if line["type"] == LineType.STATE_CHANGE
        ]
        assert State.AWAITING_APPROVAL.value in states
        # And the loop went back to running rather than staying parked.
        assert ctx.state is State.COMPLETED

    def test_the_answer_is_journaled_either_way(self, tmp_path):
        for answer in (True, False):
            registry = self._registry()
            ctx = _context(tmp_path, registry)

            asyncio.run(AgentLoop(
                self._adapter(), registry,
                policy=_AskingPolicy(), approvals=_Approver(answer),
            ).run(ctx))

            approvals = [
                line for line in ctx.journal.read() if line["type"] == LineType.APPROVAL
            ]
            assert [line["approved"] for line in approvals] == [answer]

    def test_an_approved_mutating_call_is_still_checkpointed_once(self, tmp_path):
        """The checkpoint is loop-owned, so it must not double up on this path."""
        created = []

        class _Checkpointer:
            async def create(self, call, ctx):
                created.append(call.tool)
                return f"ckpt-{len(created)}"

        registry = self._registry()
        ctx = _context(tmp_path, registry)

        asyncio.run(AgentLoop(
            self._adapter(), registry,
            policy=_AskingPolicy(requires_checkpoint=True),
            approvals=_Approver(True), checkpointer=_Checkpointer(),
        ).run(ctx))

        assert created == ["fs.write"]

    def test_the_stub_approver_refuses_to_guess(self, tmp_path):
        """Better a failed run than one that silently self-approves."""
        registry = self._registry()
        ctx = _context(tmp_path, registry)

        result = asyncio.run(AgentLoop(
            self._adapter(), registry, policy=_AskingPolicy(),
        ).run(ctx))

        assert result.status == "failed"
        assert "no way to ask for approval" in ctx.errors[0].message

    def test_the_request_is_announced_and_resolved(self, tmp_path):
        registry = self._registry()
        events = Recorder()
        ctx = _context(tmp_path, registry)

        asyncio.run(AgentLoop(
            self._adapter(), registry,
            policy=_AskingPolicy(), approvals=_Approver(True), events=events,
        ).run(ctx))

        assert events.payload("approval_needed")["tool"] == "fs.write"
        assert events.payload("approval_resolved")["approved"] is True
