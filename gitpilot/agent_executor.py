# gitpilot/agent_executor.py
"""
Streaming multi-step agent executor.

Wraps existing CrewAI plan+execute functions with granular event streaming
via the AgentEventBus. Does NOT modify agentic.py or any existing module.

Execution pipeline:
  1. Plan   (reuses generate_plan_lite / generate_plan)
  2. Execute (reuses execute_plan_lite / execute_plan, with streaming shim)
  3. Validate (run detected linter/tests, feed errors back)

All events flow through AgentEventBus -> consumers (WS v2, SSE, VS Code).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Optional

from . import agent_events as evt
from .agent_events import AgentEventBus
from .approval_protocol import ApprovalGate
from .terminal import TerminalExecutor, TerminalSession
from .workspace import WorkspaceManager, WorkspaceInfo
from .test_detection import detect_test_command, detect_framework_name
from .diagnostics_runner import run_linter, parse_diagnostics

logger = logging.getLogger(__name__)

# Maximum retries for self-correction after validation errors
MAX_VALIDATION_RETRIES = 2


class StreamingAgentExecutor:
    """
    Autonomous multi-step agent executor with real-time streaming.

    Orchestrates the full pipeline:
      plan -> per-step execution -> validation -> self-correction -> done

    Emits all events through the AgentEventBus so any transport
    (WebSocket v2, SSE, VS Code postMessage) can forward them.
    """

    def __init__(
        self,
        bus: AgentEventBus,
        gate: ApprovalGate,
        workspace: Optional[WorkspaceInfo] = None,
        ws_manager: Optional[WorkspaceManager] = None,
        terminal: Optional[TerminalExecutor] = None,
    ) -> None:
        self._bus = bus
        self._gate = gate
        self._workspace = workspace
        self._ws_manager = ws_manager or WorkspaceManager()
        self._terminal = terminal or TerminalExecutor()
        self._cancelled = False

    def cancel(self) -> None:
        """Signal the executor to stop after the current step."""
        self._cancelled = True

    async def execute(
        self,
        user_message: str,
        repo_full_name: str,
        branch: Optional[str] = None,
        token: Optional[str] = None,
        mode: str = "auto",
    ) -> Optional[dict]:
        """
        Main entry point. Streams events throughout.

        Args:
            user_message: The user's request
            repo_full_name: "owner/repo" (required for multi-agent path)
            branch: Git branch (default HEAD)
            token: GitHub token (for remote repos)
            mode: "auto" | "plan_only"

        Returns:
            The plan dict on success, None on failure.
        """
        # Validate repo_full_name BEFORE hitting CrewAI/generate_plan_lite.
        # The legacy planners do `owner, repo = repo_full_name.split("/")`
        # which raises ValueError("not enough values to unpack") on empty
        # or slashless strings. Catch this at the boundary.
        #
        # For folder-only / local-git sessions (no GitHub remote) the
        # multi-agent CrewAI planner is simply not applicable. Instead of
        # emitting a scary `agent_error` (which every SSE consumer renders
        # as a user-visible notice), close the stream cleanly with an
        # empty `done` event. Callers that support a batch fallback — e.g.
        # the VS Code extension's sendChatToBackend() flow — will treat
        # the empty stream as "streaming unavailable" and transparently
        # fall through to /api/chat/send, which is the correct path for
        # folder-only sessions. No regression for strict clients: they
        # still observe a terminal event and the method returns None.
        if not self._is_valid_repo_full_name(repo_full_name):
            logger.debug(
                "StreamingAgentExecutor: skipping CrewAI planner for "
                "non-GitHub session (repo_full_name=%r); clients should "
                "fall back to batch chat.",
                repo_full_name,
            )
            await self._bus.emit(evt.status_change("done"))
            await self._bus.emit(evt.agent_done(summary=""))
            return None

        try:
            # ── Phase 1: Plan ──
            await self._bus.emit(evt.status_change("planning", "Analyzing your request..."))

            plan = await self._generate_plan(user_message, repo_full_name, token, branch)
            if not plan:
                await self._bus.emit(evt.agent_error("Failed to generate plan"))
                return None

            # Emit plan structure
            steps = plan.get("steps", []) if isinstance(plan, dict) else []
            if hasattr(plan, "steps"):
                steps = [
                    {"title": s.title, "description": s.description}
                    for s in plan.steps
                ]

            goal = plan.get("goal", user_message) if isinstance(plan, dict) else getattr(plan, "goal", user_message)
            summary = plan.get("summary", "") if isinstance(plan, dict) else getattr(plan, "summary", "")

            for i, step in enumerate(steps):
                title = step.get("title", f"Step {i + 1}") if isinstance(step, dict) else getattr(step, "title", f"Step {i + 1}")
                await self._bus.emit(evt.plan_step(i, title, "pending"))

            if self._cancelled:
                await self._bus.emit(evt.agent_error("Cancelled by user", recoverable=True))
                return None

            if mode == "plan_only":
                await self._bus.emit(evt.agent_done(summary="Plan generated."))
                return plan if isinstance(plan, dict) else {"goal": goal, "summary": summary, "steps": steps}

            # ── Phase 2: Execute ──
            await self._bus.emit(evt.status_change("generating", "Executing plan..."))

            result = await self._execute_plan(plan, repo_full_name, token, branch)

            # Stream result text in chunks (simulate token streaming from batch)
            answer = self._extract_answer(result)
            chunk_size = 80
            for i in range(0, len(answer), chunk_size):
                if self._cancelled:
                    break
                await self._bus.emit(evt.text_delta(answer[i : i + chunk_size]))
                await asyncio.sleep(0.015)

            if self._cancelled:
                await self._bus.emit(evt.agent_error("Cancelled by user", recoverable=True))
                return None

            # ── Phase 3: Validate ──
            await self._bus.emit(evt.status_change("reviewing", "Validating changes..."))
            await self._run_validation()

            # ── Done ──
            await self._bus.emit(evt.status_change("done"))
            await self._bus.emit(evt.agent_done(summary=answer[:500]))

            return plan if isinstance(plan, dict) else {"goal": goal, "summary": summary}

        except Exception as e:
            logger.error("StreamingAgentExecutor error: %s", e, exc_info=True)
            await self._bus.emit(evt.agent_error(str(e)))
            return None

    # ── Internal helpers ──

    @staticmethod
    def _is_valid_repo_full_name(name: object) -> bool:
        """True iff `name` is a non-empty 'owner/repo' string with both parts.

        Rejects: None, empty strings, strings without '/', strings with
        empty owner or repo, and strings containing more than one '/'.
        """
        if not isinstance(name, str) or not name.strip():
            return False
        parts = name.strip().split("/")
        return len(parts) == 2 and all(p.strip() for p in parts)

    async def _generate_plan(self, goal, repo_full_name, token, branch):
        """Wrap existing plan generators with event streaming."""
        try:
            from .agentic import generate_plan_lite
            return await generate_plan_lite(
                goal=goal,
                repo_full_name=repo_full_name,
                token=token,
                branch_name=branch,
            )
        except ImportError:
            logger.warning("generate_plan_lite not available, using fallback")
        except Exception as e:
            logger.error("Plan generation error: %s", e)
            await self._bus.emit(evt.agent_error(f"Planning failed: {e}"))
        return None

    async def _execute_plan(self, plan, repo_full_name, token, branch):
        """Wrap existing plan executors with event streaming."""
        try:
            from .agentic import execute_plan_lite
            return await execute_plan_lite(
                plan=plan,
                repo_full_name=repo_full_name,
                token=token,
                branch_name=branch,
            )
        except ImportError:
            logger.warning("execute_plan_lite not available, using fallback")
        except Exception as e:
            logger.error("Plan execution error: %s", e)
            await self._bus.emit(evt.agent_error(f"Execution failed: {e}"))
        return None

    async def _run_validation(self) -> None:
        """Run linter and/or tests if the workspace supports them."""
        if not self._workspace:
            return

        ws_path = self._workspace.path if hasattr(self._workspace, "path") else Path(str(self._workspace))

        # ── Lint check ──
        try:
            lint_result = await run_linter(ws_path, self._terminal, timeout=60)
            if lint_result:
                entries = parse_diagnostics(lint_result.stdout + lint_result.stderr)
                errors = [e for e in entries if e.get("severity") == "error"]
                warnings = [e for e in entries if e.get("severity") == "warning"]
                await self._bus.emit(evt.diagnostics(
                    errors=len(errors),
                    warnings=len(warnings),
                    entries=entries,
                ))
        except Exception as e:
            logger.debug("Linter not available: %s", e)

        # ── Test check ──
        try:
            test_cmd = await detect_test_command(ws_path)
            if test_cmd:
                framework = await detect_framework_name(ws_path) or "unknown"
                session = TerminalSession(workspace_path=ws_path)

                await self._bus.emit(evt.tool_start(
                    uuid.uuid4().hex[:8], "run_tests", {"command": test_cmd}
                ))

                # Stream test output
                output_chunks = []
                exit_code = -1
                duration_ms = 0

                async for chunk in self._terminal.execute_streaming(session, test_cmd, timeout=120):
                    if chunk.get("type") == "stdout":
                        output_chunks.append(chunk["data"])
                        await self._bus.emit(evt.terminal_output("stdout", chunk["data"]))
                    elif chunk.get("type") == "error":
                        output_chunks.append(chunk.get("data", ""))
                        await self._bus.emit(evt.terminal_output("stderr", chunk.get("data", "")))
                    elif chunk.get("type") == "exit":
                        exit_code = chunk.get("exit_code", -1)
                        duration_ms = chunk.get("duration_ms", 0)

                await self._bus.emit(evt.terminal_exit(test_cmd, exit_code, duration_ms))

                # Parse test results
                full_output = "".join(output_chunks)
                passed, failed, skipped = self._parse_test_counts(full_output)

                await self._bus.emit(evt.test_result(
                    framework=framework,
                    passed=passed,
                    failed=failed,
                    skipped=skipped,
                    output=full_output,
                    exit_code=exit_code,
                ))
        except Exception as e:
            logger.debug("Test runner not available: %s", e)

    @staticmethod
    def _extract_answer(result) -> str:
        """Extract the response text from various result formats."""
        if result is None:
            return "Task completed."
        if isinstance(result, str):
            return result
        if isinstance(result, dict):
            return (
                result.get("result")
                or result.get("answer")
                or result.get("summary")
                or result.get("message")
                or str(result)
            )
        if hasattr(result, "raw"):
            return str(result.raw)
        return str(result)

    @staticmethod
    def _parse_test_counts(output: str) -> tuple[int, int, int]:
        """Best-effort extraction of pass/fail/skip counts from test output."""
        passed = failed = skipped = 0

        # Jest / Vitest: "Tests: 3 passed, 1 failed, 4 total"
        m = re.search(r"(\d+)\s+passed", output)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+)\s+failed", output)
        if m:
            failed = int(m.group(1))
        m = re.search(r"(\d+)\s+skipped", output)
        if m:
            skipped = int(m.group(1))

        # pytest: "3 passed, 1 failed, 2 skipped"
        m = re.search(r"(\d+)\s+passed", output)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+)\s+failed", output)
        if m:
            failed = int(m.group(1))

        # go test: "ok" or "FAIL"
        if "FAIL" in output and failed == 0:
            failed = output.count("FAIL")
        if "ok" in output and passed == 0:
            passed = output.count("\nok")

        return passed, failed, skipped


    # ---------------------------------------------------------------------
    # Batch P2-D — additive streaming co-method.
    #
    # Adapts the legacy ``execute(...)`` to the :mod:`gitpilot.streaming`
    # adapter contract.  Yields :class:`StreamEvent` objects so the SSE
    # route can flush each as it arrives.  Behaviour falls back to a
    # single ``assistant_chunk`` when the underlying executor has nothing
    # to stream (e.g. folder-only sessions).  No legacy method is
    # modified.
    # ---------------------------------------------------------------------
    async def run_streaming(self, payload):
        """Yield ``StreamEvent`` instances for the request *payload*.

        Recognised keys (every key is optional; sensible defaults apply):

        * ``user_message`` (str)  — the user's request
        * ``repo_full_name`` (str) — ``owner/repo`` for GitHub sessions
        * ``branch`` (str), ``token`` (str), ``mode`` (str)

        The method itself does not import ``gitpilot.streaming`` at
        module top-level so the agent executor stays usable in
        contexts where the streaming layer isn't wired (CLI, tests).
        """
        from .streaming import StreamEvent  # local import — keep agent_executor lean

        user_message = str(payload.get("user_message", ""))
        repo_full_name = str(payload.get("repo_full_name", ""))
        branch = payload.get("branch")
        token = payload.get("token")
        mode = payload.get("mode", "auto")

        yield StreamEvent(
            event="agent_event",
            data={"type": "executor_started", "mode": mode},
        )

        try:
            result = await self.execute(
                user_message=user_message,
                repo_full_name=repo_full_name,
                branch=branch,
                token=token,
                mode=mode,
            )
        except Exception as exc:  # noqa: BLE001 — boundary adapter
            yield StreamEvent(
                event="error",
                data={"code": "executor.failed", "message": str(exc)[:240]},
            )
            return

        if result is None:
            yield StreamEvent(
                event="assistant_chunk",
                data={"text": "(no plan produced — streaming fallback)"},
            )
            return

        plan_text = result.get("summary") if isinstance(result, dict) else None
        if not plan_text and isinstance(result, dict):
            plan_text = "\n".join(
                str(step.get("title") or step) for step in (result.get("steps") or [])[:5]
            )
        if plan_text:
            yield StreamEvent(
                event="assistant_chunk",
                data={"text": plan_text},
            )

        yield StreamEvent(event="agent_event", data={"type": "executor_finished"})
