# gitpilot/approval_protocol.py
"""
Tool approval protocol for agent execution.

The gate pauses execution and waits for the user to approve, via WebSocket, SSE
callback, or VS Code postMessage.

Permission modes:
  - "normal"  Ask user before risky tools (default)
  - "auto"    Approve everything automatically
  - "plan"    Block all writes and commands (read-only)

Two things moved out in Batch V4-D3/D4, and their absence is deliberate:

* **``DANGEROUS_TOOLS`` is gone.** It was a hardcoded frozenset mixing VS Code
  tool names with CrewAI display strings, which meant it recognised
  ``"write_file"`` and ``"Write local file"`` but not ``fs.write`` — a tool
  named in the canonical scheme fell straight through as safe. Risk now comes
  from ``ToolSpec.risk``, command classification and capability qualifiers,
  computed by :mod:`gitpilot.agent.policy`. The caller decides what to gate;
  the gate's job is to ask.
* **``on_checkpoint`` is gone.** The loop owns checkpointing
  (:mod:`gitpilot.agent.checkpointing`), so the ``ask`` path cannot snapshot
  twice and every permission mode snapshots the same way.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict

from .agent_events import AgentEventBus, approval_needed, approval_resolved

logger = logging.getLogger(__name__)

class ApprovalGate:
    """
    Async approval gate between the agent and tool execution.

    Flow:
      1. Agent calls gate.check(tool, args, summary)
      2. Gate emits APPROVAL_NEEDED event to the bus
      3. Gate creates an asyncio.Future and waits
      4. Client sends approval response
      5. resolve() sets the Future result, agent proceeds or skips
    """

    def __init__(
        self,
        bus: AgentEventBus,
        mode: str = "normal",
    ) -> None:
        self._bus = bus
        self._mode = mode
        self._pending: Dict[str, asyncio.Future] = {}
        self._session_allowed: set[str] = set()

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        self._mode = value

    async def check(
        self,
        tool_name: str,
        tool_args: dict,
        summary: str = "",
        diff_preview: str | None = None,
        risk: str = "medium",
    ) -> bool:
        """
        Returns True if the tool may proceed. Blocks until user responds.

        Every call reaching here is one the caller decided needs a decision —
        the gate no longer second-guesses that against a name list, because the
        list could not recognise a canonically-named tool.
        """
        if self._mode == "auto":
            return True

        if self._mode == "plan":
            await self._bus.emit(
                approval_resolved(f"denied-plan-{id(self)}", approved=False)
            )
            return False

        if tool_name in self._session_allowed:
            return True

        # Normal mode: ask user
        request_id = f"approval-{id(self)}-{len(self._pending)}"
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        await self._bus.emit(
            approval_needed(
                request_id=request_id,
                tool=tool_name,
                args=tool_args,
                summary=summary
                or f"{tool_name}({', '.join(f'{k}={v!r}' for k, v in list(tool_args.items())[:3])})",
                diff_preview=diff_preview,
                risk=risk,
            )
        )

        try:
            result = await asyncio.wait_for(future, timeout=120.0)
        except asyncio.TimeoutError:
            logger.warning(
                "Approval timed out for %s (request %s)", tool_name, request_id
            )
            self._pending.pop(request_id, None)
            return False

        self._pending.pop(request_id, None)

        approved = result.get("approved", False)
        scope = result.get("scope", "once")

        if approved and scope == "session":
            self._session_allowed.add(tool_name)

        await self._bus.emit(approval_resolved(request_id, approved))
        return approved

    def resolve(
        self, request_id: str, approved: bool, scope: str = "once"
    ) -> None:
        """Called by the transport layer when the user responds."""
        future = self._pending.get(request_id)
        if future and not future.done():
            future.set_result({"approved": approved, "scope": scope})

    def cancel_all(self) -> None:
        """Deny all pending approvals (e.g., on session close)."""
        for future in self._pending.values():
            if not future.done():
                future.set_result({"approved": False, "scope": "once"})
        self._pending.clear()
        self._session_allowed.clear()
