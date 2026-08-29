# gitpilot/agent/hook_bridge.py
"""``HookManager``'s first callers — Batch V4-D5.

Ten lifecycle events have been defined, documented and loadable from
``.gitpilot/hooks.json`` since Phase 1. None of them has ever fired: nothing in
the tree called ``HookManager.fire``. A user could write a ``pre_commit`` hook,
see it listed by the API, and never have it run.

This bridges the loop's hook seam onto the real manager and maps the loop's
vocabulary onto :class:`~gitpilot.hooks.HookEvent`:

======================  ================================================
Loop / tool site        Hook events fired
======================  ================================================
dispatch                ``PRE_TOOL_USE`` → ``POST_TOOL_USE``
``fs.write``/``fs.edit`` ``PRE_EDIT`` → ``POST_EDIT``
``git.commit``           ``PRE_COMMIT`` → ``POST_COMMIT``
``git.push``             ``PRE_PUSH``
run start / end          ``SESSION_START`` / ``SESSION_END``
the task message         ``USER_MESSAGE``
======================  ================================================

Two decisions worth stating.

**A blocking hook produces a denial, not an exception.** The loop turns the
returned reason into a ``ToolResult`` the model reads, so a project's own guard
("no commits without a ticket number") teaches the model instead of killing the
run.

**Hook environments are stripped.** ``HookManager._run_command_hook`` built its
child environment from ``os.environ`` unfiltered — the same defect Batch V4-0C
fixed in the terminal executor, and it mattered less only because nothing fired.
Giving it callers without fixing it would hand every user-authored hook every
API key in the process.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from ..hooks import HookEvent, HookManager

logger = logging.getLogger(__name__)

#: How long all hooks for one event may take before the loop stops waiting.
#: A hook is someone else's code on our critical path; a slow one should show up
#: as a warning rather than as a run that appears to hang.
HOOK_BUDGET_S = 30.0

#: Loop event name → hook event.
_TOOL_EVENTS = {
    "pre_tool_use": HookEvent.PRE_TOOL_USE,
    "post_tool_use": HookEvent.POST_TOOL_USE,
}

#: Tool id → (before, after).  ``None`` means the phase has no event.
_TOOL_LIFECYCLE = {
    "fs.write": (HookEvent.PRE_EDIT, HookEvent.POST_EDIT),
    "fs.edit": (HookEvent.PRE_EDIT, HookEvent.POST_EDIT),
    "fs.delete": (HookEvent.PRE_EDIT, HookEvent.POST_EDIT),
    "git.commit": (HookEvent.PRE_COMMIT, HookEvent.POST_COMMIT),
    "git.push": (HookEvent.PRE_PUSH, None),
}


class LoopHooks:
    """The loop's ``Hooks`` seam, backed by a real :class:`HookManager`."""

    def __init__(
        self,
        manager: Optional[HookManager] = None,
        *,
        workspace: Optional[Path] = None,
        session_id: str = "",
        budget_s: float = HOOK_BUDGET_S,
    ) -> None:
        self.manager: HookManager = manager if manager is not None else HookManager()
        self.workspace = Path(workspace) if workspace else None
        self.session_id = session_id
        self.budget_s = budget_s

    @classmethod
    def for_workspace(
        cls, workspace: Optional[Path], *, session_id: str = "",
    ) -> "LoopHooks":
        """Load ``.gitpilot/hooks.json`` from a checkout, if there is one."""
        manager = HookManager()
        if workspace is not None:
            config = Path(workspace) / ".gitpilot" / "hooks.json"
            try:
                manager.load_from_file(config)
            except Exception as exc:  # noqa: BLE001 - a bad config is not fatal
                logger.warning("could not load hooks from %s: %s", config, exc)
        return cls(manager, workspace=workspace, session_id=session_id)

    # -- the loop's seam ---------------------------------------------------

    async def fire(self, event: str, **payload: Any) -> Optional[str]:
        """Fire *event*.  Returns a reason to block, or ``None`` to proceed."""
        hook_event = _TOOL_EVENTS.get(event)
        if hook_event is None:
            return None

        call = payload.get("call")
        tool = getattr(call, "tool", "") or ""
        before, after = _TOOL_LIFECYCLE.get(tool, (None, None))

        events = [hook_event]
        if event == "pre_tool_use" and before is not None:
            events.append(before)
        elif event == "post_tool_use" and after is not None:
            events.append(after)

        context = self._context(event, payload)
        for each in events:
            blocked = await self._fire_one(each, context)
            if blocked is not None:
                return blocked
        return None

    async def session_start(self, task: str) -> None:
        await self._fire_one(HookEvent.SESSION_START, {"session_id": self.session_id})
        await self._fire_one(
            HookEvent.USER_MESSAGE,
            {"session_id": self.session_id, "message": task},
        )

    async def session_end(self, status: str) -> None:
        await self._fire_one(
            HookEvent.SESSION_END,
            {"session_id": self.session_id, "status": status},
        )

    # -- internals ---------------------------------------------------------

    async def _fire_one(
        self, event: HookEvent, context: Dict[str, Any],
    ) -> Optional[str]:
        if not self.manager._hooks.get(event):  # noqa: SLF001 - cheap emptiness check
            return None

        started = time.monotonic()
        try:
            results = await self.manager.fire(event, context, cwd=self.workspace)
        except Exception as exc:  # noqa: BLE001 - a user's hook must not kill a run
            logger.warning("hook event %s raised: %s", event.value, exc, exc_info=True)
            return None

        elapsed = time.monotonic() - started
        if elapsed > self.budget_s:
            logger.warning(
                "hooks for %s took %.1fs (budget %.0fs); the run was waiting",
                event.value, elapsed, self.budget_s,
            )

        for result in results:
            if result.blocked:
                reason = (result.output or "").strip() or f"blocked by hook {result.hook_name!r}"
                logger.info("hook %s blocked %s: %s", result.hook_name, event.value, reason)
                return f"{result.hook_name}: {reason[:500]}"
        return None

    def _context(self, event: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """The environment a hook command sees, as ``GITPILOT_HOOK_*``."""
        call = payload.get("call")
        result = payload.get("result")
        ctx = payload.get("ctx")

        context: Dict[str, Any] = {
            "event": event,
            "session_id": self.session_id,
            "tool": getattr(call, "tool", "") or "",
        }
        if self.workspace is not None:
            context["workspace"] = str(self.workspace)
        if ctx is not None:
            context["run_id"] = getattr(ctx, "run_id", "") or ""
        arguments = dict(getattr(call, "arguments", {}) or {})
        target = arguments.get("path") or arguments.get("file_path")
        if target:
            context["path"] = str(target)
        if result is not None:
            context["ok"] = "1" if getattr(result, "ok", False) else "0"
        return context
