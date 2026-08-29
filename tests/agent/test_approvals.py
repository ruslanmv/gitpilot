"""Approvals that can be answered — Batch V4-D3."""
from __future__ import annotations

import asyncio

import pytest

from gitpilot.agent.approvals import (
    ApprovalRegistry,
    LoopApprovals,
    get_registry,
    reset_registry,
)
from gitpilot.agent.policy import Decision
from gitpilot.toolkit import ToolCall


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_registry()
    yield
    reset_registry()


def _call(tool="fs.write", **arguments):
    return ToolCall(id="req-1", tool=tool, arguments=arguments or {"path": "a.py"})


# ---------------------------------------------------------------------------
# The registry (F1)
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_a_request_can_be_answered(self):
        async def drive():
            registry = ApprovalRegistry()
            pending = registry.register(
                request_id="r1", session_id="s", tool="fs.write",
            )
            waiter = asyncio.create_task(registry.wait(pending, timeout=5))
            await asyncio.sleep(0)

            assert registry.resolve("r1", True) is True
            return await waiter

        assert asyncio.run(drive())["approved"] is True

    def test_a_denial_comes_back_as_a_denial(self):
        async def drive():
            registry = ApprovalRegistry()
            pending = registry.register(request_id="r1", session_id="s", tool="fs.write")
            waiter = asyncio.create_task(registry.wait(pending, timeout=5))
            await asyncio.sleep(0)
            registry.resolve("r1", False)
            return await waiter

        assert asyncio.run(drive())["approved"] is False

    def test_a_timeout_denies(self):
        """Deny is the safe direction, and it must not be an approval by
        omission."""
        async def drive():
            registry = ApprovalRegistry()
            pending = registry.register(request_id="r1", session_id="s", tool="fs.write")
            return await registry.wait(pending, timeout=0.01)

        answer = asyncio.run(drive())
        assert answer["approved"] is False
        assert answer["reason"] == "timed out"

    def test_a_zero_timeout_denies_at_once(self):
        """`approval.mode: none` — a headless run has no user, so waiting 120
        seconds to arrive at the same answer helps nobody."""
        async def drive():
            registry = ApprovalRegistry()
            pending = registry.register(request_id="r1", session_id="s", tool="fs.write")
            return await registry.wait(pending, timeout=0)

        answer = asyncio.run(drive())
        assert answer["approved"] is False
        assert "no approver" in answer["reason"]

    def test_resolving_an_unknown_request_says_so(self):
        assert ApprovalRegistry().resolve("nope", True) is False

    def test_a_duplicate_answer_is_dropped(self):
        async def drive():
            registry = ApprovalRegistry()
            pending = registry.register(request_id="r1", session_id="s", tool="fs.write")
            first = registry.resolve("r1", True)
            second = registry.resolve("r1", False)
            await registry.wait(pending, timeout=5)
            return first, second

        first, second = asyncio.run(drive())
        assert (first, second) == (True, False)

    def test_closing_a_session_denies_everything_outstanding(self):
        async def drive():
            registry = ApprovalRegistry()
            a = registry.register(request_id="a", session_id="s", tool="fs.write")
            b = registry.register(request_id="b", session_id="s", tool="git.push")
            other = registry.register(request_id="c", session_id="other", tool="fs.write")

            assert registry.deny_session("s") == 2

            answers = [await registry.wait(p, timeout=1) for p in (a, b)]
            assert not other.future.done()
            return answers

        assert all(answer["approved"] is False for answer in asyncio.run(drive()))

    def test_pending_requests_are_listed_per_session(self):
        async def drive():
            registry = ApprovalRegistry()
            registry.register(request_id="a", session_id="s", tool="fs.write")
            registry.register(request_id="b", session_id="other", tool="fs.write")
            return [p.request_id for p in registry.pending_for("s")]

        assert asyncio.run(drive()) == ["a"]

    def test_the_process_registry_is_one_object(self):
        assert get_registry() is get_registry()


# ---------------------------------------------------------------------------
# The loop's approver (F2)
# ---------------------------------------------------------------------------


class TestLoopApprovals:
    def _approver(self, **kwargs):
        return LoopApprovals(session_id="s", **kwargs)

    def test_an_approval_is_relayed_to_the_loop(self):
        async def drive():
            approver = self._approver(timeout_s=5)
            task = asyncio.create_task(
                approver.request(_call(), Decision(verdict="ask"), None),
            )
            await asyncio.sleep(0)
            get_registry().resolve("req-1", True)
            return await task

        assert asyncio.run(drive()) is True

    def test_the_session_scope_is_recorded(self):
        """"Allow for session" has to reach the policy engine, or the next call
        asks again and the answer meant nothing."""
        granted = []

        async def drive():
            approver = self._approver(timeout_s=5, on_session_scope=granted.append)
            task = asyncio.create_task(
                approver.request(_call(), Decision(verdict="ask"), None),
            )
            await asyncio.sleep(0)
            get_registry().resolve("req-1", True, "session")
            return await task

        assert asyncio.run(drive()) is True
        assert granted == ["fs.write"]

    def test_a_once_scope_does_not_grant_the_session(self):
        granted = []

        async def drive():
            approver = self._approver(timeout_s=5, on_session_scope=granted.append)
            task = asyncio.create_task(
                approver.request(_call(), Decision(verdict="ask"), None),
            )
            await asyncio.sleep(0)
            get_registry().resolve("req-1", True, "once")
            return await task

        asyncio.run(drive())
        assert granted == []

    def test_the_request_carries_what_the_card_needs(self):
        async def drive():
            approver = self._approver(timeout_s=5)
            decision = Decision(
                verdict="ask", reason="this command changes files (MUTATING)",
                risk="approval", command_class="MUTATING",
            )
            task = asyncio.create_task(
                approver.request(_call("terminal.run", command="pip install x"), decision, None),
            )
            await asyncio.sleep(0)
            pending = get_registry().get("req-1")
            payload = pending.to_dict() if pending else {}
            get_registry().resolve("req-1", False)
            await task
            return payload

        payload = asyncio.run(drive())
        assert payload["tool"] == "terminal.run"
        assert payload["command_class"] == "MUTATING"
        assert "MUTATING" in payload["reason"]
        assert payload["arguments"]["command"] == "pip install x"

    def test_a_websocket_answer_resolves_the_same_request(self):
        """The gate keeps its `resolve` for the WS path; one future in two maps,
        rather than two futures where answering one leaves the loop waiting."""
        from gitpilot.agent_events import AgentEventBus
        from gitpilot.approval_protocol import ApprovalGate

        async def drive():
            gate = ApprovalGate(AgentEventBus())
            approver = self._approver(timeout_s=5, gate=gate)
            task = asyncio.create_task(
                approver.request(_call(), Decision(verdict="ask"), None),
            )
            await asyncio.sleep(0)
            gate.resolve("req-1", True)
            return await task

        assert asyncio.run(drive()) is True


# ---------------------------------------------------------------------------
# The gate, after DANGEROUS_TOOLS
# ---------------------------------------------------------------------------


class TestGateAfterTheNameList:
    def test_the_hardcoded_name_list_is_gone(self):
        """It recognised `write_file` and `Write local file` but not `fs.write`,
        so a canonically-named tool fell through as safe."""
        import gitpilot.approval_protocol as protocol

        assert not hasattr(protocol, "DANGEROUS_TOOLS")

    def test_a_canonically_named_tool_now_reaches_the_prompt(self):
        from gitpilot.agent_events import AgentEventBus
        from gitpilot.approval_protocol import ApprovalGate

        async def drive():
            gate = ApprovalGate(AgentEventBus(), mode="normal")
            task = asyncio.create_task(gate.check("fs.write", {"path": "a.py"}))
            await asyncio.sleep(0)
            assert gate._pending, "the gate should be waiting on an answer"
            gate.cancel_all()
            return await task

        assert asyncio.run(drive()) is False

    def test_the_gate_no_longer_checkpoints(self):
        """One owner for snapshots, so the ask path cannot double-snapshot."""
        from gitpilot.agent_events import AgentEventBus
        from gitpilot.approval_protocol import ApprovalGate

        gate = ApprovalGate(AgentEventBus())
        assert not hasattr(gate, "_on_checkpoint")
        assert not hasattr(gate, "_checkpoint")
