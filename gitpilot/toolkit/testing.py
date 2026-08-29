# gitpilot/toolkit/testing.py
"""Test tools — Batch V4-A6.

``test.detect`` answers "how do I run the tests here?" and ``test.run`` runs
them and reports the counts.  Two tools rather than making the model rediscover
``pytest`` versus ``npm test`` versus ``cargo test`` from the file listing every
session — a guess costs a failed ``terminal.run`` turn, and on a small model a
wrong guess tends to repeat.

Both wrap what the post-hoc validation phase already uses:
:func:`gitpilot.test_detection.detect_test_command` for discovery,
:mod:`gitpilot.sandbox_routing` for execution, and
:func:`gitpilot.test_detection.parse_test_counts` for the numbers — so the tool
the model calls and the validation the server runs cannot report different
results for the same suite.

The difference from that validation phase is who is driving: there, tests run
after execution and the model never sees them; here the model runs them, reads
the failures, and fixes the code.  Batch V4-E3 turns that into an enforced
policy.
"""
from __future__ import annotations

from typing import Any, List, Tuple

from ..sandbox_routing import (
    MAX_TIMEOUT_SEC,
    MIN_TIMEOUT_SEC,
    SandboxFallback,
    coerce_timeout,
    run_command_in_workspace,
)
from ..test_detection import detect_framework_name, detect_test_command, parse_test_counts
from .registry import (
    Effect,
    Risk,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)

#: Test suites are slower than ordinary commands; the default reflects that
#: without letting a hung suite hold the loop open indefinitely.
DEFAULT_TEST_TIMEOUT_SEC = 300


# ---------------------------------------------------------------------------
# test.detect
# ---------------------------------------------------------------------------

DETECT_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


async def _test_detect(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    workspace = ctx.require_workspace()
    command = await detect_test_command(workspace.root)
    framework = await detect_framework_name(workspace.root)

    if not command:
        return ToolResult.success(
            call,
            "No test framework detected. Looked for pytest/jest/vitest/mocha/cargo/go/"
            "mix/rake markers, a [tool.pytest] section in pyproject.toml, and a test "
            "script in package.json.",
            data={"framework": None, "command": None, "commands": []},
        )

    # A scoped form matters in practice: running one file is how a fix gets
    # verified quickly, and the syntax differs per runner.
    commands = [command]
    scoped = _scoped_form(framework, command)
    if scoped:
        commands.append(scoped)

    lines = [f"Framework: {framework}", f"Command: {command}"]
    if scoped:
        lines.append(f"Single target: {scoped}")
    return ToolResult.success(
        call,
        "\n".join(lines),
        data={"framework": framework, "command": command, "commands": commands},
    )


def _scoped_form(framework: str | None, command: str) -> str | None:
    """The same command narrowed to one path, as a template."""
    base = command.replace(" 2>&1", "")
    if framework in ("pytest",):
        return f"{base} <path>"
    if framework in ("jest", "vitest", "mocha"):
        return f"{base} <path>"
    if framework == "cargo":
        return "cargo test <name> 2>&1".replace(" 2>&1", "")
    if framework == "go":
        return "go test ./<package>/..."
    return None


# ---------------------------------------------------------------------------
# test.run
# ---------------------------------------------------------------------------

RUN_SCHEMA = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "description": (
                "Optional file, directory or test name to narrow the run, e.g. "
                "'tests/test_checkpoint.py'. Omit to run the whole suite."
            ),
        },
        "timeout": {
            "type": "integer",
            "description": f"Seconds before the run is killed (default {DEFAULT_TEST_TIMEOUT_SEC}).",
            "minimum": MIN_TIMEOUT_SEC,
            "maximum": MAX_TIMEOUT_SEC,
        },
    },
    "additionalProperties": False,
}


async def _test_run(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    workspace = ctx.require_workspace()
    target = call.arguments.get("target")
    timeout = coerce_timeout(call.arguments.get("timeout", DEFAULT_TEST_TIMEOUT_SEC))

    command = await detect_test_command(workspace.root)
    if not command:
        return ToolResult.failure(
            call,
            "No test framework detected, so there is nothing to run. Use test.detect "
            "to see what was looked for, or terminal.run with an explicit command.",
            error="no_framework",
        )

    framework = await detect_framework_name(workspace.root)
    full_command = _with_target(command, target)

    try:
        output = await run_command_in_workspace(full_command, timeout, workspace.root)
    except SandboxFallback as exc:
        return ToolResult.failure(
            call,
            f"Error: sandbox unavailable ({exc}); refusing to run tests outside it.",
            error="sandbox_unavailable",
        )

    exit_code = _exit_code_from(output)
    passed, failed, skipped = parse_test_counts(output)

    # A failing suite is a successful tool call: the failure is the observation
    # the model needs, and reporting it as a tool error would invite a retry of
    # the tool instead of a fix to the code.
    headline = f"{passed} passed, {failed} failed, {skipped} skipped"
    return ToolResult.success(
        call,
        f"Tests: {headline}\n{output}",
        data={
            "framework": framework,
            "command": full_command,
            "target": target,
            "exit_code": exit_code,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "green": exit_code == 0,
        },
    )


def _with_target(command: str, target: str | None) -> str:
    """Insert *target* before the trailing redirect the detected commands carry."""
    if not target:
        return command
    if command.endswith(" 2>&1"):
        return f"{command[:-len(' 2>&1')]} {target} 2>&1"
    return f"{command} {target}"


def _exit_code_from(output: str) -> int | None:
    for line in output.splitlines():
        if line.startswith("Exit code: "):
            try:
                return int(line[len("Exit code: "):].strip())
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

SPECS: List[Tuple[ToolSpec, Any]] = [
    (
        ToolSpec(
            id="test.detect",
            title="Detect the test command",
            description=(
                "Report which test framework this repository uses and the command that "
                "runs its suite. Call this instead of guessing."
            ),
            params_schema=DETECT_SCHEMA,
            risk=Risk.SAFE,
            effects=frozenset({Effect.READS_FS}),
            priority=25,
        ),
        _test_detect,
    ),
    (
        ToolSpec(
            id="test.run",
            title="Run tests",
            description=(
                "Run the repository's tests, optionally narrowed to one path, and report "
                "pass/fail/skip counts with the output. A failing suite is a normal "
                "result: read the failure and fix the code."
            ),
            params_schema=RUN_SCHEMA,
            risk=Risk.HIGH_RISK,
            effects=frozenset({Effect.EXECUTES, Effect.READS_FS}),
            timeout_s=MAX_TIMEOUT_SEC + 30,
            priority=18,
        ),
        _test_run,
    ),
]


def register(registry: ToolRegistry) -> None:
    """Add the test tools to *registry*."""
    for spec, handler in SPECS:
        registry.register(spec, handler)
