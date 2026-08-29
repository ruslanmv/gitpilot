# gitpilot/agent/approvals.py
"""Approvals that can actually be answered — Batch V4-D3.

Two defects meet here.

**F2 — the gate had no caller.** ``ApprovalGate.check()`` has existed, complete
and correct, with nothing invoking it: the executor never called it, so no tool
has ever been gated. The loop's ``ask`` arm is its first real caller.

**F1 — an SSE approval could not be resolved.** ``POST /api/v2/approval/respond``
looked the session's gate up in a dict the SSE path never populated, so the
request 404'd and the pending future timed out at 120 seconds regardless of what
the user clicked. A WebSocket client worked; every other client did not. The fix
is a per-session :class:`ApprovalRegistry` that both transports resolve through.

The registry is deliberately transport-blind. It knows nothing about SSE, sockets
or VS Code — it holds futures keyed by request id and lets anything with the id
answer them, which is what makes "the same approval, answered from a different
transport" work rather than being three parallel implementations.

**A restart while pending denies.** ``awaiting_approval`` persistence arrives in
Batch V4-F1; until then a process that goes away loses its futures, and the
approval card's copy says the request expires. Denying is the safe direction, and
saying so on the card is better than a promise we cannot yet keep.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

#: How long a request waits before it is treated as a refusal.  Per-topology in
#: :class:`~gitpilot.agent.policy.ResolvedPolicy`; this is the fallback.
DEFAULT_TIMEOUT_S = 120.0

#: Scopes a client may answer with.
SCOPE_ONCE = "once"
SCOPE_SESSION = "session"


@dataclass
class PendingApproval:
    """One unanswered request."""

    request_id: str
    session_id: str
    tool: str
    arguments: Dict[str, Any]
    risk: str
    reason: str
    command_class: str = ""
    #: Always set by :meth:`ApprovalRegistry.register`; optional only so the
    #: dataclass can be constructed in a test without an event loop.
    future: Optional["asyncio.Future[Dict[str, Any]]"] = field(repr=False, default=None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "tool": self.tool,
            "arguments": self.arguments,
            "risk": self.risk,
            "reason": self.reason,
            "command_class": self.command_class,
        }


class ApprovalRegistry:
    """Pending approvals, per session, resolvable by any transport (F1).

    One instance per process. Registration happens where the approval is *asked*
    (the loop) rather than where it is answered, so a transport that arrives late
    or a client that reconnects on a different one can still resolve it.
    """

    def __init__(self) -> None:
        self._pending: Dict[str, PendingApproval] = {}
        self._by_session: Dict[str, Set[str]] = {}

    # -- asking ------------------------------------------------------------

    def register(
        self,
        *,
        request_id: str,
        session_id: str,
        tool: str,
        arguments: Optional[Dict[str, Any]] = None,
        risk: str = "approval",
        reason: str = "",
        command_class: str = "",
    ) -> PendingApproval:
        future: "asyncio.Future[Dict[str, Any]]" = asyncio.get_running_loop().create_future()
        pending = PendingApproval(
            request_id=request_id, session_id=session_id, tool=tool,
            arguments=dict(arguments or {}), risk=risk, reason=reason,
            command_class=command_class, future=future,
        )
        self._pending[request_id] = pending
        self._by_session.setdefault(session_id, set()).add(request_id)
        return pending

    async def wait(
        self, pending: PendingApproval, *, timeout: float = DEFAULT_TIMEOUT_S,
    ) -> Dict[str, Any]:
        """Wait for an answer.  A timeout is a refusal, never an approval.

        ``timeout <= 0`` means "do not prompt" (§8.4's ``approval.mode: none``):
        a headless run gets an immediate denial rather than a 120-second stall.
        """
        if timeout <= 0:
            self.discard(pending.request_id)
            return {"approved": False, "scope": SCOPE_ONCE, "reason": "no approver configured"}
        if pending.future is None:  # pragma: no cover - register always sets it
            return {"approved": False, "scope": SCOPE_ONCE, "reason": "no pending future"}
        try:
            return await asyncio.wait_for(asyncio.shield(pending.future), timeout=timeout)
        except asyncio.TimeoutError:
            logger.info(
                "approval %s for %s timed out after %ss; treating it as a refusal",
                pending.request_id, pending.tool, timeout,
            )
            return {"approved": False, "scope": SCOPE_ONCE, "reason": "timed out"}
        finally:
            self.discard(pending.request_id)

    # -- answering ---------------------------------------------------------

    def resolve(self, request_id: str, approved: bool, scope: str = SCOPE_ONCE) -> bool:
        """Answer a pending request.  Returns whether there was one.

        Idempotent: a duplicate answer (the user double-clicks, or two transports
        both deliver it) is dropped rather than raising.
        """
        pending = self._pending.get(request_id)
        if pending is None or pending.future is None or pending.future.done():
            return False
        pending.future.set_result({"approved": bool(approved), "scope": scope})
        return True

    def deny_session(self, session_id: str, *, reason: str = "session closed") -> int:
        """Refuse everything outstanding for a session.  Returns how many."""
        count = 0
        for request_id in list(self._by_session.get(session_id, ())):
            pending = self._pending.get(request_id)
            if pending is not None and pending.future is not None and not pending.future.done():
                pending.future.set_result(
                    {"approved": False, "scope": SCOPE_ONCE, "reason": reason},
                )
                count += 1
        self._by_session.pop(session_id, None)
        return count

    def discard(self, request_id: str) -> None:
        pending = self._pending.pop(request_id, None)
        if pending is not None:
            requests = self._by_session.get(pending.session_id)
            if requests is not None:
                requests.discard(request_id)
                if not requests:
                    self._by_session.pop(pending.session_id, None)

    # -- inspection --------------------------------------------------------

    def pending_for(self, session_id: str) -> list[PendingApproval]:
        return [
            self._pending[rid]
            for rid in sorted(self._by_session.get(session_id, ()))
            if rid in self._pending
        ]

    def get(self, request_id: str) -> Optional[PendingApproval]:
        return self._pending.get(request_id)

    def __len__(self) -> int:
        return len(self._pending)


#: The process-wide registry.  One, so a request answered on any transport
#: reaches the run that is waiting for it.
_registry: Optional[ApprovalRegistry] = None


def get_registry() -> ApprovalRegistry:
    """The process-wide approval registry, created on first use."""
    global _registry
    if _registry is None:
        _registry = ApprovalRegistry()
    return _registry


def reset_registry() -> None:
    """Drop the process registry.  For tests; never call this from a server."""
    global _registry
    _registry = None


# ---------------------------------------------------------------------------
# The loop's approver
# ---------------------------------------------------------------------------


class LoopApprovals:
    """Asks the user, through whatever transport is listening.

    Implements the ``Approvals`` seam Batch V4-C2 left in the loop. The loop owns
    the state transition and the journal lines; this owns the waiting.

    Note what it does *not* do: it never checkpoints. The gate used to, which is
    why the ``ask`` path could snapshot twice; the loop is the single owner now
    (Batch V4-D4).
    """

    def __init__(
        self,
        *,
        session_id: str,
        registry: Optional[ApprovalRegistry] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        gate: Optional[Any] = None,
        on_session_scope: Optional[Any] = None,
    ) -> None:
        self.session_id = session_id
        self.registry = registry if registry is not None else get_registry()
        self.timeout_s = timeout_s
        #: The legacy :class:`~gitpilot.approval_protocol.ApprovalGate`, when one
        #: exists for this session.  Kept in the loop so a WebSocket client that
        #: resolves through ``gate.resolve`` still works.
        self.gate = gate
        self._on_session_scope = on_session_scope

    async def request(self, call: Any, decision: Any, ctx: Any) -> bool:
        pending = self.registry.register(
            request_id=call.id,
            session_id=self.session_id,
            tool=call.tool,
            arguments=dict(getattr(call, "arguments", {}) or {}),
            risk=getattr(decision, "risk", "approval"),
            reason=getattr(decision, "reason", ""),
            command_class=getattr(decision, "command_class", ""),
        )
        if self.gate is not None:
            # Let the gate's own pending map see it too, so a WS client calling
            # gate.resolve(request_id, ...) resolves the same request.
            self._bridge_to_gate(pending)

        answer = await self.registry.wait(pending, timeout=self.timeout_s)
        approved = bool(answer.get("approved"))

        if approved and answer.get("scope") == SCOPE_SESSION and self._on_session_scope:
            self._on_session_scope(call.tool)

        return approved

    def _bridge_to_gate(self, pending: PendingApproval) -> None:
        """Point the gate's pending entry at the registry's future.

        One future, two maps — rather than two futures, which is how a WS answer
        could resolve one and leave the loop waiting on the other.
        """
        if self.gate is None or pending.future is None:
            return
        try:
            self.gate._pending[pending.request_id] = pending.future  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001 - a gate we do not recognise
            logger.debug("could not bridge approval %s to the gate: %s", pending.request_id, exc)
