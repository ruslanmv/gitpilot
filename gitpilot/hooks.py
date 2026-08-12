# gitpilot/hooks.py
"""Event hook system for workflow automation.

Allows users to register shell commands or Python callables that fire
on specific lifecycle events.  Hooks are defined in .gitpilot/hooks.json
or programmatically via the API.

Events
------
- session_start     Session begins
- session_end       Session ends
- pre_tool_use      Before a tool runs (blocking hooks can cancel)
- post_tool_use     After a tool completes
- pre_edit          Before file edit (blocking hooks can cancel)
- post_edit         After file edit
- pre_commit        Before git commit (blocking hooks can cancel)
- post_commit       After git commit
- pre_push          Before git push (blocking hooks can cancel)
- user_message      When the user sends a message
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class HookEvent(str, Enum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    PRE_EDIT = "pre_edit"
    POST_EDIT = "post_edit"
    PRE_COMMIT = "pre_commit"
    POST_COMMIT = "post_commit"
    PRE_PUSH = "pre_push"
    USER_MESSAGE = "user_message"


@dataclass
class HookDefinition:
    event: HookEvent
    name: str
    command: Optional[str] = None
    handler: Optional[Callable] = None
    blocking: bool = False
    timeout: int = 30


@dataclass
class HookResult:
    hook_name: str
    event: HookEvent
    success: bool
    output: str = ""
    blocked: bool = False


class HookManager:
    """Register and fire lifecycle hooks."""

    def __init__(self) -> None:
        self._hooks: Dict[HookEvent, List[HookDefinition]] = {
            e: [] for e in HookEvent
        }

    def register(self, hook: HookDefinition) -> None:
        self._hooks[hook.event].append(hook)
        logger.info("Registered hook '%s' for event '%s'", hook.name, hook.event)

    def unregister(self, event: HookEvent, name: str) -> None:
        self._hooks[event] = [h for h in self._hooks[event] if h.name != name]

    def list_hooks(self) -> List[Dict[str, Any]]:
        result = []
        for event, hooks in self._hooks.items():
            for h in hooks:
                result.append({
                    "event": event.value,
                    "name": h.name,
                    "command": h.command,
                    "blocking": h.blocking,
                    "timeout": h.timeout,
                })
        return result

    def load_from_file(self, path: Path) -> None:
        """Load hooks from a JSON config file.

        Format::

            [
                {"event": "post_edit", "name": "lint", "command": "ruff check ."},
                {"event": "pre_commit", "name": "test", "command": "pytest", "blocking": true}
            ]
        """
        if not path.exists():
            return
        try:
            hooks = json.loads(path.read_text())
            for h in hooks:
                self.register(HookDefinition(
                    event=HookEvent(h["event"]),
                    name=h["name"],
                    command=h.get("command"),
                    blocking=h.get("blocking", False),
                    timeout=h.get("timeout", 30),
                ))
        except Exception as e:
            logger.warning("Failed to load hooks from %s: %s", path, e)

    async def fire(
        self,
        event: HookEvent,
        context: Optional[Dict[str, Any]] = None,
        cwd: Optional[Path] = None,
    ) -> List[HookResult]:
        results = []
        for hook in self._hooks.get(event, []):
            result = await self._run_hook(hook, context, cwd)
            results.append(result)
            if hook.blocking and not result.success:
                result.blocked = True
                break
        return results

    def is_blocked(self, results: List[HookResult]) -> bool:
        return any(r.blocked for r in results)

    async def _run_hook(
        self,
        hook: HookDefinition,
        context: Optional[Dict[str, Any]],
        cwd: Optional[Path],
    ) -> HookResult:
        try:
            if hook.command:
                return await self._run_command_hook(hook, context, cwd)
            if hook.handler:
                output = hook.handler(context or {})
                return HookResult(
                    hook_name=hook.name, event=hook.event,
                    success=True, output=str(output),
                )
            return HookResult(
                hook_name=hook.name, event=hook.event,
                success=True, output="No action",
            )
        except Exception as e:
            return HookResult(
                hook_name=hook.name, event=hook.event,
                success=False, output=str(e),
            )

    async def _run_command_hook(
        self,
        hook: HookDefinition,
        context: Optional[Dict[str, Any]],
        cwd: Optional[Path],
    ) -> HookResult:
        # Batch V4-D5. This built the child environment from `os.environ`
        # unfiltered — the same defect Batch V4-0C fixed in the terminal
        # executor. It went unnoticed because nothing fired hooks; giving them
        # callers without fixing it would hand every user-authored hook
        # GITHUB_TOKEN and every provider API key.
        from .shell_safety import strip_secret_env

        overrides = {
            f"GITPILOT_HOOK_{key.upper()}": str(value)
            for key, value in (context or {}).items()
        }
        env = strip_secret_env(overrides=overrides)

        proc = await asyncio.create_subprocess_shell(
            hook.command,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=hook.timeout,
            )
            return HookResult(
                hook_name=hook.name, event=hook.event,
                success=proc.returncode == 0,
                output=stdout.decode("utf-8", errors="replace"),
            )
        except asyncio.TimeoutError:
            proc.kill()
            return HookResult(
                hook_name=hook.name, event=hook.event,
                success=False, output="Hook timed out",
            )
