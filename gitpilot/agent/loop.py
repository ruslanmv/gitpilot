# gitpilot/agent/loop.py
"""The AgentLoop — Batch V4-C2.

The primitive the whole programme exists to add: generate a turn, dispatch the
tools it asked for, feed the results back, repeat until the model says it is
done.  Everything else in this package is in service of these ~120 lines.

The ordering in :meth:`AgentLoop.run` is normative, not incidental:

1. check budgets, so a runaway run stops with partial results rather than being
   killed from outside;
2. generate a turn through the dialect adapter — the loop never branches on
   provider or dialect;
3. return immediately on ``FINAL``;
4. for each call: **authorize and journal the decision, then checkpoint, then
   execute** — in that order, because a crash between authorising and executing
   has to leave a trace, and the safety invariants are ordering checks over those
   lines;
5. resolve ``FINAL_AFTER_TOOLS`` by asking the adapter whether the work landed.

Authorization here is a permissive stub that still classifies, journals and
emits.  Batch V4-D1 replaces its body; every call site, every journal line and
every event stays as it is, which is the point of writing it this way now.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from ..toolkit.registry import ToolCall, ToolRegistry, ToolResult
from .adapter import ModelAdapter
from .context import (
    AgentContext,
    AgentResult,
    BudgetExceeded,
    State,
)
from .contracts import AgentTurn, Dialect, Finality
from .journal import SeedContext
from .model_profile import ModelProfile
from .policy import Decision

logger = logging.getLogger(__name__)

FLAG_AGENT_LOOP = "agent_loop"

#: Unreadable *provider responses* tolerated before the run degrades (§6.6's
#: "malformed tool calls twice").  Two, because a gateway hiccup is plausible and
#: retrying the same request is cheap.
MALFORMED_RESPONSES_BEFORE_DOWNGRADE = 2

#: Appended to the answer of a run that changed files without verifying them.
_UNVERIFIED_NOTE = (
    "Note: these changes have not been verified — the tests were not run "
    "successfully, so treat this as untested."
)


@dataclass(frozen=True)
class Verification:
    """When a run must prove its changes work — Batch V4-E3.

    ``off``       nothing is required.
    ``auto``      required when the project has a test framework, skipped when it
                  does not — so a repository with no tests is not blocked on
                  running tests it does not have.
    ``required``  a mutating run may not finish until tests have run.

    Enforced by refusing the model's ``FINAL`` and telling it why, rather than by
    running tests afterwards and reporting the result to the user. Verification
    the model can see and react to is the point: a post-hoc validation phase — the
    shape this replaces — produces a red test suite the model never learns about.
    """

    tests: str = "off"           # off | auto | required
    max_fix_cycles: int = 2

    @property
    def enabled(self) -> bool:
        return self.tests in ("auto", "required")


# ---------------------------------------------------------------------------
# Policy (stubbed until Batch V4-D1)
# ---------------------------------------------------------------------------


class PermissivePolicy:
    """Allows everything, and records that it did.

    This is the ``agent_policy``-off path.  It shipped in Batch V4-C2 as a stub
    and stays as the flag's other branch, so turning the real
    :class:`~gitpilot.agent.policy.PolicyEngine` off restores exactly the
    behaviour Phase C had rather than something new.

    Deliberately not "skip the check when there is no policy": the journal must
    show a decision for every call, or the Phase D invariants would have nothing
    to assert against for runs recorded before them.
    """

    async def authorize(self, call: ToolCall, ctx: AgentContext) -> Decision:
        spec = None
        registry = getattr(ctx.tool_context, "extras", {}).get("registry")
        if registry is not None:
            try:
                spec = registry.spec(call.tool)
            except Exception:  # noqa: BLE001 - unknown tool is the registry's to report
                spec = None

        risk = getattr(getattr(spec, "risk", None), "value", "safe")
        mutating = bool(
            spec is not None
            and any(
                effect.value in ("writes_fs", "git_local", "git_remote", "forge_write")
                for effect in spec.effects
            )
        )
        return Decision(
            verdict="allow",
            reason="permissive stub (Batch V4-D1 replaces this)",
            risk=risk,
            requires_checkpoint=mutating,
        )


class Approvals:
    """The ``ask`` arm's default: refuse to guess.

    A run configured to ask, with nothing able to answer, must not self-approve.
    :class:`~gitpilot.agent.approvals.LoopApprovals` (Batch V4-D3) is the real
    implementation; a headless run gets :class:`DenyingApprovals` instead, which
    denies rather than failing the run.
    """

    async def request(self, call: ToolCall, decision: Decision, ctx: AgentContext) -> bool:
        raise NotImplementedError(
            "this run has no way to ask for approval; configure an approver or "
            "run with approval.mode = none"
        )


class DenyingApprovals(Approvals):
    """Answers "no" without prompting — ``approval.mode: none`` (§8.4).

    A headless run has no user, so a pending approval would hang until the
    timeout. Denying immediately gives the model an observation it can work
    around and keeps the run honest about what it could not do.
    """

    async def request(self, call: ToolCall, decision: Decision, ctx: AgentContext) -> bool:
        return False


class Hooks:
    """Lifecycle hooks.  Wired to ``HookManager`` in Batch V4-D5."""

    async def fire(self, event: str, **payload: Any) -> Optional[str]:
        """Return a string to block the call, or ``None`` to allow it."""
        return None


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class LoopEvents:
    """Thin async fan-out.

    The loop takes an emitter rather than a bus so it can run headless with no
    transport at all, and so tests can assert on a list.
    """

    def __init__(
        self,
        emit: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    ) -> None:
        self._emit = emit

    async def emit(self, event_type: str, **payload: Any) -> None:
        if self._emit is None:
            return
        try:
            await self._emit(event_type, payload)
        except Exception as exc:  # noqa: BLE001 - a broken transport must not kill a run
            logger.debug("event emission failed (%s): %s", event_type, exc)


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


class AgentLoop:
    """Iterate model → tools → observations until the model is done."""

    def __init__(
        self,
        adapter: ModelAdapter,
        registry: ToolRegistry,
        *,
        policy: Optional[PermissivePolicy] = None,
        approvals: Optional[Approvals] = None,
        hooks: Optional[Hooks] = None,
        events: Optional[LoopEvents] = None,
        checkpointer: Optional[Any] = None,
        verification: Optional[Verification] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.adapter = adapter
        self.registry = registry
        self.verification = verification or Verification()
        self.policy = policy or PermissivePolicy()
        self.approvals = approvals or Approvals()
        self.hooks = hooks or Hooks()
        self.events = events or LoopEvents()
        self.checkpointer = checkpointer
        self._clock = clock
        self._cancelled = False
        #: Consecutive malformed turns in the current dialect.
        self._malformed = 0
        #: How many times verification has sent the model back to fix something.
        self._fix_cycles = 0

    # -- cancellation -----------------------------------------------------

    def cancel(self) -> None:
        """Ask the run to stop.

        Cooperative between tool calls, and a real ``asyncio`` cancellation point
        inside provider streaming — which is what makes this an improvement on
        the executor it replaces, whose flag was only checked between phases and
        so could not interrupt a call in flight.
        """
        self._cancelled = True

    # -- the algorithm ----------------------------------------------------

    async def run(
        self,
        ctx: AgentContext,
        seed: Optional[SeedContext] = None,
        *,
        resumed: bool = False,
    ) -> AgentResult:
        """Drive one run to a terminal state.

        *resumed* skips the opening ceremony: a resumed run's journal already has
        its ``run_started`` line and its seed, and writing a second would make the
        record claim the run began twice (Batch V4-F1).
        """
        ctx.tool_context.extras.setdefault("registry", self.registry)
        # `todo.write` is a tool that mutates the *run*, not the world, so it is
        # handed a callback into this context rather than reaching for a global.
        ctx.tool_context.extras["todo_sink"] = _todo_sink(self, ctx)

        if resumed:
            ctx.budgets.start(self._clock())
            ctx.state = State.RUNNING
        else:
            seed = seed or SeedContext(
                task=ctx.task,
                session_id=ctx.session_id,
                topology_id=ctx.topology_id,
                model=ctx.model_profile.model,
                dialect=ctx.model_profile.dialect.value,
            )
            ctx.begin(seed, now=self._clock())
            await self.events.emit(
                "run_started", run_id=ctx.run_id, task=ctx.task,
                model=ctx.model_profile.model,
                dialect=ctx.model_profile.dialect.value,
            )
        await self._session_hook("session_start", task=ctx.task)

        try:
            return await self._loop(ctx)
        except asyncio.CancelledError:
            # An outer cancellation (the client disconnected) is not a failure.
            ctx.set_state(State.CANCELLED)
            ctx.journal.finish("cancelled")
            await self.events.emit("error", error="cancelled", recoverable=True)
            raise
        except Exception as exc:  # noqa: BLE001 - one run must not take the server down
            logger.exception("agent run %s failed", ctx.run_id)
            ctx.record_error("loop", str(exc))
            ctx.set_state(State.FAILED)
            ctx.journal.finish("failed", summary=str(exc)[:500])
            await self.events.emit("error", error=str(exc), recoverable=False)
            return AgentResult(answer="", status="failed", context=ctx)

    async def _loop(self, ctx: AgentContext) -> AgentResult:
        last_text = ""

        while True:
            if self._cancelled:
                return await self._finish(ctx, State.CANCELLED, "cancelled", last_text)

            try:
                ctx.budgets.check(self._clock())
            except BudgetExceeded as exc:
                ctx.note_skipped(f"stopped at the {exc.limit} limit")
                return await self._finish(
                    ctx, State.BUDGET_EXCEEDED, "budget_exceeded", last_text,
                    detail={"limit": exc.limit, "value": exc.value},
                )

            ctx.iteration += 1
            ctx.budgets.iterations += 1
            ctx.journal.state_change(State.RUNNING.value, iteration=ctx.iteration)
            await self.events.emit("iteration", n=ctx.iteration)

            await self._report_pruned_tools(ctx)
            await self._sync_engine_todos(ctx)
            await self._maybe_compact(ctx)

            turn = await self._generate(ctx)
            if turn is None:
                # The dialect could not be repaired; degradation already applied.
                continue

            ctx.record_turn(turn)
            if turn.text:
                last_text = turn.text

            if turn.finality is Finality.FINAL:
                if await self._needs_verification(ctx):
                    continue
                return await self._finish(ctx, State.COMPLETED, "completed", turn.text or last_text)

            ctx.add_message(self.adapter.assistant_message(turn))

            results = await self._dispatch(ctx, turn)

            if turn.finality is Finality.FINAL_AFTER_TOOLS and self.adapter.conclude(turn, results):
                if await self._needs_verification(ctx):
                    continue
                return await self._finish(ctx, State.COMPLETED, "completed", turn.text or last_text)

    # -- one turn ---------------------------------------------------------

    async def _generate(self, ctx: AgentContext) -> Optional[AgentTurn]:
        """Ask the model for a turn, applying the degradation ladder on failure."""
        from .providers.base import MalformedResponse

        async def on_text(delta: str) -> None:
            await self.events.emit("text_delta", text=delta)

        try:
            try:
                turn = await self.adapter.generate(
                    system=ctx.system_payload,
                    messages=ctx.messages,
                    capabilities=ctx.capabilities,
                    on_text=on_text,
                )
            except TypeError:
                # Dialects that do not stream take no ``on_text``.
                turn = await self.adapter.generate(
                    system=ctx.system_payload,
                    messages=ctx.messages,
                    capabilities=ctx.capabilities,
                )
        except MalformedResponse as exc:
            # An unreadable provider response gets a second chance: a gateway
            # hiccup is plausible and the retry costs one request.
            self._malformed += 1
            if self._malformed >= MALFORMED_RESPONSES_BEFORE_DOWNGRADE:
                if await self._downgrade(ctx, str(exc)):
                    return None
            else:
                logger.debug("unreadable response from %s; retrying", ctx.model_profile.model)
                return None
            raise

        if turn.meta.get("parse_error"):
            # Downgrade at once, and *before* honouring the turn's finality.
            #
            # Two things are going on. A turn carrying a parse error is only
            # "final" because the dialect fell back to prose — that is our
            # decision, not the model's, so treating it as a finished task ends
            # the run on a failure to communicate. And the dialect has already
            # spent its own repair budget (the react adapter's one reformat
            # retry), so another loop-level attempt with the same messages would
            # just be a third identical failure. Getting this ordering wrong is
            # what made the ladder unreachable in the first draft.
            if await self._downgrade(ctx, str(turn.meta["parse_error"])):
                return None
        else:
            self._malformed = 0

        if turn.text:
            await self.events.emit("agent_message", text=turn.text)
        return turn

    async def _downgrade(self, ctx: AgentContext, reason: str) -> bool:
        """Drop one rung down the ladder (§6.6).

        Returns whether a rung was available.  The LITE floor has none, so a run
        that fails there finishes ``degraded`` and says which work it could not
        do — rather than claiming success it did not achieve.
        """
        current = ctx.model_profile
        next_profile = current.downgraded()
        if next_profile.dialect is current.dialect:
            return False

        from .adapter import build_adapter

        ctx.model_profile = next_profile
        self.adapter = build_adapter(self.adapter.provider, next_profile, self.registry)
        self._malformed = 0

        if next_profile.dialect is Dialect.LITE:
            # LITE exposes only lite-compatible tools, so pending work that needs
            # the others genuinely cannot continue. Say so rather than silently
            # dropping it.
            for spec in self.registry.specs(ctx.capabilities):
                if not spec.lite_compatible:
                    ctx.note_skipped(f"{spec.id} is unavailable in the lite dialect")
            for item in ctx.todos:
                if item.status in ("pending", "in_progress"):
                    item.status = "blocked"

        ctx.journal.state_change(
            State.RUNNING.value,
            dialect_downgraded=next_profile.dialect.value,
            reason=reason,
        )
        await self.events.emit(
            "dialect_downgraded",
            **{"from": current.dialect.value, "to": next_profile.dialect.value, "reason": reason},
        )
        logger.info(
            "run %s downgraded %s → %s (%s)",
            ctx.run_id, current.dialect.value, next_profile.dialect.value, reason,
        )
        return True

    # -- dispatch ---------------------------------------------------------

    async def _dispatch(self, ctx: AgentContext, turn: AgentTurn) -> List[ToolResult]:
        results: List[ToolResult] = []

        for call in turn.tool_calls:
            if self._cancelled:
                break

            await self.events.emit(
                "tool_start", id=call.id, name=call.tool, args=call.arguments,
            )

            decision = await self.policy.authorize(call, ctx)
            ctx.journal.tool_call(call, iteration=ctx.iteration)
            ctx.journal.policy_decision(
                call,
                verdict=decision.verdict,
                reason=decision.reason,
                risk=decision.risk,
                requires_checkpoint=decision.requires_checkpoint,
                command_class=getattr(decision, "command_class", ""),
            )

            if decision.verdict == "ask":
                decision = await self._ask(ctx, call, decision)

            if decision.denied:
                result = ToolResult.denied_for(call, decision.reason or "not permitted")
                ctx.record(call, result)
                ctx.add_message(*self.adapter.result_messages(call, result)[:1])
                results.append(result)
                await self.events.emit(
                    "tool_result", id=call.id, name=call.tool, ok=False, denied=True,
                )
                continue

            if decision.requires_checkpoint:
                await self._checkpoint(ctx, call)

            blocked = await self.hooks.fire("pre_tool_use", call=call, ctx=ctx)
            if blocked:
                result = ToolResult.denied_for(call, f"blocked by hook: {blocked}")
                ctx.record(call, result)
                ctx.add_message(*self.adapter.result_messages(call, result)[:1])
                results.append(result)
                continue

            result = await self.registry.execute(call, ctx.tool_context)
            await self.hooks.fire("post_tool_use", call=call, result=result, ctx=ctx)

            ctx.record(call, result)
            for message in self.adapter.result_messages(call, result):
                ctx.add_message(message)
            results.append(result)

            await self._emit_result(call, result)

        return results

    async def _ask(self, ctx: AgentContext, call: ToolCall, decision: Decision) -> Decision:
        """Drive the approval, with ``awaiting_approval`` as a journaled state."""
        previous = ctx.state
        ctx.set_state(State.AWAITING_APPROVAL, tool=call.tool, call_id=call.id)
        await self.events.emit(
            "approval_needed", request_id=call.id, tool=call.tool,
            arguments=call.arguments, risk=decision.risk,
        )
        try:
            approved = await self.approvals.request(call, decision, ctx)
        finally:
            ctx.set_state(previous)

        ctx.journal.approval(call, approved=approved)
        await self.events.emit("approval_resolved", request_id=call.id, approved=approved)
        if approved:
            return Decision(
                verdict="allow", reason="approved by user", risk=decision.risk,
                requires_checkpoint=decision.requires_checkpoint,
            )
        return Decision(verdict="deny", reason="denied by user", risk=decision.risk)

    async def _checkpoint(self, ctx: AgentContext, call: ToolCall) -> None:
        """Snapshot before a mutating call.

        Loop-owned rather than hanging off the approval gate, so it happens in
        every permission mode and cannot fire twice on the ``ask`` path.
        """
        if self.checkpointer is None:
            return
        try:
            checkpoint_id = await self.checkpointer.create(call, ctx)
        except Exception as exc:  # noqa: BLE001 - a snapshot failure must not block work
            logger.warning("checkpoint before %s failed: %s", call.tool, exc)
            return
        if checkpoint_id:
            ctx.journal.checkpoint_ref(call, checkpoint_id)
            await self.events.emit(
                "checkpoint_created", checkpoint_id=checkpoint_id, tool=call.tool,
            )

    async def _emit_result(self, call: ToolCall, result: ToolResult) -> None:
        data = result.data or {}
        await self.events.emit(
            "tool_result", id=call.id, name=call.tool, ok=result.ok,
            error=result.error, data=data,
        )
        # ``file_write`` and ``tool_result`` have had factories and no emitters
        # since they were defined; this is the first thing that sends them.
        for path in data.get("paths_written") or []:
            await self.events.emit("file_write", path=path, action="modify")
        if call.tool == "test.run" and "passed" in data:
            await self.events.emit(
                "test_result",
                framework=data.get("framework"), passed=data.get("passed"),
                failed=data.get("failed"), skipped=data.get("skipped"),
                exit_code=data.get("exit_code"),
            )

    async def _needs_verification(self, ctx: AgentContext) -> bool:
        """Refuse a finish that has not been checked — Batch V4-E3.

        Returns whether the run should keep going.  The remedy differs by dialect
        and that asymmetry is deliberate: a model that can call tools is told what
        is missing and left to act, while a LITE run has the call issued *for* it,
        because a 1.5B model told "run the tests" reliably answers with prose
        about running the tests. Design rule 5 — a policy that only holds for
        frontier models is not a policy.
        """
        if not self.verification.enabled or not ctx.mutated:
            return False
        if self._verified(ctx):
            return False

        command = await self._test_command(ctx)
        if command is None:
            if self.verification.tests == "required":
                ctx.verified = False
                ctx.note_skipped(
                    "the changes were not verified: this project has no test suite "
                    "to run"
                )
            return False

        if self._fix_cycles >= self.verification.max_fix_cycles:
            # Out of attempts.  Say so rather than reporting success: an
            # unverified change described as done is the dishonesty this whole
            # mechanism exists to prevent.
            ctx.verified = False
            ctx.note_skipped(
                f"the changes are unverified: {self._fix_cycles} attempt(s) to run "
                f"`{command}` did not produce a passing result"
            )
            return False

        self._fix_cycles += 1
        ctx.journal.state_change(
            State.RUNNING.value,
            verification="required", cycle=self._fix_cycles, command=command,
        )
        await self.events.emit(
            "verification_required",
            cycle=self._fix_cycles, max_cycles=self.verification.max_fix_cycles,
            command=command,
        )

        if ctx.model_profile.dialect is Dialect.LITE:
            await self._engine_verify(ctx)
            return True

        ctx.add_message({
            "role": "user",
            "content": (
                f"You changed {len(ctx.files_modified)} file(s) but have not run "
                f"the tests. Run them (`{command}`) and read the result before "
                "finishing. If they fail, fix what fails."
            ),
        })
        return True

    def _verified(self, ctx: AgentContext) -> bool:
        """Whether the tests have run since the last change.

        Asked of the ledger rather than the transcript, because the transcript is
        compacted and "did this run test what it changed?" has to survive that.
        """
        if not ctx.tests:
            return False
        last_test = max(record.iteration for record in ctx.tests)
        last_change = max(
            (
                entry.iteration for entry in ctx.ledger
                if entry.result.ok and (entry.result.data or {}).get("paths_written")
            ),
            default=0,
        )
        return last_test >= last_change

    async def _test_command(self, ctx: AgentContext) -> Optional[str]:
        """The project's test command, or ``None`` when it has no tests.

        ``auto`` turns into "off" here rather than at configuration time, because
        whether a project has tests is a fact about the workspace and not about
        the topology that was chosen.
        """
        workspace = getattr(ctx.tool_context, "workspace", None)
        root = getattr(workspace, "root", None)
        if root is None:
            return None
        try:
            from ..test_detection import detect_test_command

            return await detect_test_command(root)
        except Exception as exc:  # noqa: BLE001 - detection is advisory
            logger.debug("could not detect a test command: %s", exc)
            return None

    async def _engine_verify(self, ctx: AgentContext) -> None:
        """Issue ``test.run`` on the model's behalf, through the normal path.

        Through ``_dispatch`` rather than around it, so the call is authorized,
        journaled and checkpointed exactly like a model-issued one. An
        engine-issued call that skipped the policy engine would be a hole in
        invariant 1 with the engine's own name on it.
        """
        from ..toolkit.registry import ToolCall as _ToolCall

        call = _ToolCall(
            id=f"verify-{self._fix_cycles}", tool="test.run", arguments={},
        )
        await self._dispatch(
            ctx,
            AgentTurn(tool_calls=[call], finality=Finality.CONTINUE,
                      meta={"engine_issued": True}),
        )

    async def _maybe_compact(self, ctx: AgentContext) -> None:
        """Fold the transcript when it is close to the window — Batch V4-F3.

        At iteration start, because that is the one moment the next request's size
        is knowable and nothing is half-built. The threshold is over
        ``window − reserve``: measured against the raw window, compaction would
        only trigger once the request was already too large to send.

        Only the *transcript* is edited. The ledger and the journal keep the
        uncompacted record, so a compacted run can still answer "what did you
        change?" — anything else would make this a way to lose an audit trail.
        """
        from .compaction import compact, project_tokens, threshold

        window = int(getattr(ctx.model_profile, "context_window", 0) or 0)
        if window <= 0:
            return

        projected = project_tokens(ctx.messages)
        if projected <= threshold(window):
            return

        previous = ctx.state
        ctx.set_state(State.COMPACTING, projected_tokens=projected, window=window)
        try:
            messages, report = compact(ctx.messages, window=window)
        except Exception as exc:  # noqa: BLE001 - a run must survive its own housekeeping
            logger.warning("compaction failed: %s", exc, exc_info=True)
            ctx.set_state(previous)
            return

        if report.changed:
            ctx.messages = messages
            ctx.journal.compaction(
                before_tokens=report.before_tokens,
                after_tokens=report.after_tokens,
                folded=report.folded,
            )
            await self.events.emit(
                "compaction",
                folded=report.folded, stubbed=report.stubbed,
                freed=report.freed, before=report.before_tokens,
                after=report.after_tokens,
            )
            logger.info(
                "run %s compacted %d → %d tokens (folded %d, stubbed %d)",
                ctx.run_id, report.before_tokens, report.after_tokens,
                report.folded, report.stubbed,
            )
        ctx.set_state(previous)

    async def _sync_engine_todos(self, ctx: AgentContext) -> None:
        """Maintain the TODO list for a dialect that has no ``todo.write``.

        LITE only.  A 1.5B model asked to keep structured state alongside its
        actual work does neither well, so the engine derives a coarse
        investigate → act → verify list from the phase the dialect is already
        tracking. The panel looks the same whichever model is running, which is
        the point — the alternative was leaving small models with a blank one.
        """
        if ctx.model_profile.dialect is not Dialect.LITE:
            return

        state = getattr(self.adapter, "state", None)
        phase = getattr(state, "phase", "")
        if not phase:
            return

        from ..toolkit.todo import phase_todo

        items = phase_todo(phase, intent=getattr(state, "intent", "action"))
        if [(item.text, item.status) for item in ctx.todos] == [
            (entry["text"], entry["status"]) for entry in items
        ]:
            return   # nothing moved; do not redraw the panel

        transitions = ctx.replace_todos(items)
        await self.events.emit(
            "todo_updated",
            items=[item.to_dict() for item in ctx.todos],
            transitions=transitions,
            source="engine",
        )

    async def _session_hook(self, which: str, **payload: Any) -> None:
        """Fire ``session_start``/``session_end`` if the hook bridge supports it.

        Probed rather than required: the loop's ``Hooks`` seam is a two-method
        protocol, and a test double or a stripped runtime should not have to
        implement lifecycle events it has no use for.
        """
        handler = getattr(self.hooks, which, None)
        if not callable(handler):
            return
        try:
            await handler(**payload)
        except Exception as exc:  # noqa: BLE001 - a hook must not take a run down
            logger.warning("%s hook failed: %s", which, exc, exc_info=True)

    async def _report_pruned_tools(self, ctx: AgentContext) -> None:
        dropped = self.adapter.dropped_tools(ctx.capabilities)
        if not dropped:
            return
        await self.events.emit(
            "tools_pruned",
            count=len(dropped),
            reasons=sorted({item.reason for item in dropped}),
        )

    # -- finishing --------------------------------------------------------

    async def _finish(
        self,
        ctx: AgentContext,
        state: State,
        status: str,
        answer: str,
        detail: Optional[Dict[str, Any]] = None,
    ) -> AgentResult:
        if not ctx.verified and state in (State.COMPLETED, State.DEGRADED):
            # The answer is what a user reads; a caveat only in the metadata is a
            # caveat nobody sees.
            #
            # Checked *before* the skipped-notes downgrade below, and against both
            # states: giving up on verification calls `note_skipped`, which turns
            # the state into DEGRADED — so a condition of "COMPLETED only" made
            # this line unreachable in exactly the case it exists for.
            answer = (answer.rstrip() + "\n\n" + _UNVERIFIED_NOTE).strip()

        if ctx.skipped and state is State.COMPLETED:
            # Work was dropped, so the run did not do what it was asked; saying
            # "completed" here is the dishonesty the ladder exists to avoid.
            state, status = State.DEGRADED, "degraded"

        ctx.set_state(state)
        ctx.journal.finish(status, summary=answer[:500], **(detail or {}))
        await self._session_hook("session_end", status=status)
        # A terminal event, not a nicety: an SSE consumer reads until it sees one,
        # so a run that ends quietly leaves the client waiting on keepalives
        # until it times out.
        await self.events.emit(
            "run_finished",
            status=status, answer=answer, skipped=list(ctx.skipped),
            usage={
                "prompt_tokens": ctx.budgets.prompt_tokens,
                "completion_tokens": ctx.budgets.completion_tokens,
            },
            **(detail or {}),
        )
        return AgentResult(
            answer=answer, status=status, context=ctx, skipped=list(ctx.skipped),
            verified=ctx.verified,
        )


def _todo_sink(loop: "AgentLoop", ctx: AgentContext) -> Any:
    """The callback ``todo.write`` invokes, bound to one run.

    Returns the transitions so the tool's own result can report them; the event
    is emitted here, because the tool has no business knowing about a bus.
    """

    async def sink(items: List[Dict[str, str]]) -> List[Any]:
        from ..toolkit.todo import TodoTransition

        transitions = ctx.replace_todos(items)
        await loop.events.emit(
            "todo_updated",
            items=[item.to_dict() for item in ctx.todos],
            transitions=transitions,
            source="model",
        )
        return [
            TodoTransition(
                text=entry["text"], before=entry["from"], after=entry["to"],
            )
            for entry in transitions
        ]

    return sink


def new_run_id() -> str:
    """A fresh run identifier — the journal filename and the event correlator."""
    return f"run_{uuid.uuid4().hex[:12]}"
