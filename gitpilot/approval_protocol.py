# gitpilot/approval_protocol.py
"""
Tool approval protocol for agent execution.

When an agent wants to run a dangerous tool (write_file, run_command,
git_commit), the ApprovalGate pauses execution and waits for the user
to approve via WebSocket, SSE callback, or VS Code postMessage.

Permission modes:
  - "normal"  Ask user before dangerous tools (default)
  - "auto"    Approve everything automatically
  - "plan"    Block all writes and commands (read-only)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Dict, Optional

from .agent_events import AgentEventBus, approval_needed, approval_resolved

logger = logging.getLogger(__name__)

DANGEROUS_TOOLS = frozenset({
    # VS Code local agent tool names
    "write_file",
    "edit_file",
    "run_command",
    "git_commit",
    # CrewAI tool display names
    "Write local file",
    "Delete local file",
    "Run shell command",
    "Git commit",
})


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
        on_checkpoint: Optional[Callable[[str, dict], None]] = None,
    ) -> None:
        self._bus = bus
        self._mode = mode
        self._pending: Dict[str, asyncio.Future] = {}
        self._session_allowed: set[str] = set()
        # Called immediately before a dangerous tool is allowed to run, so a
        # checkpoint exists for every write. This is the only place all three
        # permission modes converge, which is why the hook lives here rather
        # than in each tool.
        self._on_checkpoint = on_checkpoint

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
    ) -> bool:
        """
        Returns True if the tool may proceed. Blocks until user responds.
        """
        if tool_name not in DANGEROUS_TOOLS:
            return True

        if self._mode == "auto":
            # Auto mode is the one that most needs an undo: nobody saw this
            # coming, so the checkpoint is the only thing standing between the
            # user and an edit they never agreed to.
            self._checkpoint(tool_name, tool_args)
            return True

        if self._mode == "plan":
            await self._bus.emit(
                approval_resolved(f"denied-plan-{id(self)}", approved=False)
            )
            return False

        if tool_name in self._session_allowed:
            self._checkpoint(tool_name, tool_args)
            return True

        # Normal mode: ask user
        request_id = f"approval-{id(self)}-{len(self._pending)}"
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future

        risk = "high" if tool_name in ("run_command", "Run shell command") else "medium"

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

        if approved:
            self._checkpoint(tool_name, tool_args)

        await self._bus.emit(approval_resolved(request_id, approved))
        return approved

    def _checkpoint(self, tool_name: str, tool_args: dict) -> None:
        """Snapshot before the tool runs, and never at the cost of the tool.

        A checkpoint that fails must not block the work the user asked for —
        it is a safety net, and a safety net that stops the show is worse
        than one with a hole in it. The failure is logged and execution
        continues.
        """
        if self._on_checkpoint is None:
            return
        try:
            self._on_checkpoint(tool_name, tool_args)
        except Exception as e:
            logger.warning("could not checkpoint before %s: %s", tool_name, e)

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
