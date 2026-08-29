# gitpilot/toolkit/terminal.py
"""Terminal tools — Batch V4-A4.

``terminal.run`` executes a command in the workspace, ``terminal.run_snippet``
runs a self-contained program in an ephemeral directory, and
``terminal.which`` / ``terminal.env`` answer the two questions a model
otherwise wastes a whole ``run`` turn discovering.

Sandbox-first, with no way around it: every path goes through
:mod:`gitpilot.sandbox_routing` to the configured backend (``off`` /
``subprocess`` / ``matrixlab``), which jails the working directory to the
workspace, scrubs credentials from the environment and enforces the command
denylist.  The legacy ``TerminalExecutor`` fallback that ``local_tools`` keeps
for import failures is deliberately *not* reproduced here: it is a weaker
executor, and silently downgrading isolation is the opposite of what a sandbox
is for.  If the sandbox cannot be constructed the tool says so.

``terminal.run`` is registered as HIGH_RISK.  Command-level classification —
telling ``pytest`` from ``pip install`` from ``rm -rf`` — arrives with the
policy engine in Batch V4-D2; until then the risk class is the blunt
instrument, and it is set to the most cautious reading.
"""
from __future__ import annotations

from typing import Any, List, Tuple

from ..sandbox_routing import (
    DEFAULT_TIMEOUT_SEC,
    MAX_TIMEOUT_SEC,
    MIN_TIMEOUT_SEC,
    SandboxFallback,
    coerce_timeout,
    run_command_in_workspace,
    run_snippet,
)
from .registry import (
    Effect,
    Risk,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)

#: Languages ``/api/sandbox/run`` accepts.  Declared in the schema as an enum so
#: an unsupported language is refused by argument validation, before any
#: network call, with a message naming the alternatives.
SNIPPET_LANGUAGES = ["python", "javascript", "js", "node", "bash", "sh", "shell"]

_TIMEOUT_PROPERTY = {
    "type": "integer",
    "description": f"Seconds before the command is killed (default {DEFAULT_TIMEOUT_SEC}).",
    "minimum": MIN_TIMEOUT_SEC,
    "maximum": MAX_TIMEOUT_SEC,
}


def _exit_code_from(output: str) -> int | None:
    """Recover the exit code from the rendered block for the ``data`` payload.

    The rendered text is the contract shared with the CrewAI tools, so it is
    what the backends return; parsing one line out of it is cheaper than
    threading a second return value through every backend, and the shape is
    ours, not a third party's.
    """
    for line in output.splitlines():
        if line.startswith("Exit code: "):
            try:
                return int(line[len("Exit code: "):].strip())
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# terminal.run
# ---------------------------------------------------------------------------

RUN_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "Shell command to run in the workspace, e.g. 'pytest tests/ -q'.",
        },
        "timeout": _TIMEOUT_PROPERTY,
    },
    "required": ["command"],
    "additionalProperties": False,
}


async def _terminal_run(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    command = call.arguments["command"]
    timeout = coerce_timeout(call.arguments.get("timeout", DEFAULT_TIMEOUT_SEC))
    workspace = ctx.require_workspace()

    try:
        output = await run_command_in_workspace(command, timeout, workspace.root)
    except SandboxFallback as exc:
        # Honest failure rather than a quiet downgrade to the unjailed executor.
        return ToolResult.failure(
            call,
            f"Error: sandbox unavailable ({exc}); refusing to run the command "
            "outside it.",
            error="sandbox_unavailable",
        )

    exit_code = _exit_code_from(output)
    return ToolResult.success(
        call,
        output,
        data={"command": command, "exit_code": exit_code, "timeout": timeout},
    )


# ---------------------------------------------------------------------------
# terminal.run_snippet
# ---------------------------------------------------------------------------

SNIPPET_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string", "enum": SNIPPET_LANGUAGES},
        "code": {"type": "string", "description": "A self-contained program."},
        "timeout": _TIMEOUT_PROPERTY,
    },
    "required": ["language", "code"],
    "additionalProperties": False,
}


async def _terminal_run_snippet(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    language = call.arguments["language"]
    code = call.arguments["code"]
    timeout = coerce_timeout(call.arguments.get("timeout", DEFAULT_TIMEOUT_SEC))

    try:
        output = await run_snippet(language, code, timeout)
    except SandboxFallback as exc:
        return ToolResult.failure(
            call, f"Error: sandbox unavailable ({exc}); cannot run snippet.",
            error="sandbox_unavailable",
        )

    return ToolResult.success(
        call,
        output,
        data={"language": language, "exit_code": _exit_code_from(output), "timeout": timeout},
    )


# ---------------------------------------------------------------------------
# terminal.which
# ---------------------------------------------------------------------------

WHICH_SCHEMA = {
    "type": "object",
    "properties": {
        "program": {"type": "string", "description": "Executable name, e.g. 'pytest'."},
    },
    "required": ["program"],
    "additionalProperties": False,
}


async def _terminal_which(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """Answer "is this installed?" without burning a ``run`` turn on it.

    Goes through the sandbox like everything else, so the answer describes the
    environment the model's commands will actually execute in — which is the
    point, and why this is not a host ``shutil.which``.
    """
    program = call.arguments["program"]
    workspace = ctx.require_workspace()

    # `command -v` is POSIX; `which` is not guaranteed present in slim images.
    try:
        output = await run_command_in_workspace(
            f"command -v {program}", MIN_TIMEOUT_SEC * 15, workspace.root,
        )
    except SandboxFallback as exc:
        return ToolResult.failure(
            call, f"Error: sandbox unavailable ({exc}).", error="sandbox_unavailable",
        )

    exit_code = _exit_code_from(output)
    found = exit_code == 0
    path = ""
    if found:
        lines = output.splitlines()
        try:
            start = lines.index("--- stdout ---") + 1
            path = lines[start].strip() if start < len(lines) else ""
        except ValueError:
            path = ""

    content = f"{program}: {path or 'found'}" if found else f"{program}: not found"
    return ToolResult.success(
        call, content, data={"program": program, "found": found, "path": path},
    )


# ---------------------------------------------------------------------------
# terminal.env
# ---------------------------------------------------------------------------

ENV_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Variable to read. Omit to list the names that are set.",
        },
    },
    "additionalProperties": False,
}


async def _terminal_env(call: ToolCall, ctx: ToolExecutionContext) -> ToolResult:
    """Report the sandbox's environment.

    Names always; a value only when asked for by name.  Credentials are stripped
    by the sandbox before the child starts, so what this reports is genuinely
    what a command would see — and listing names rather than dumping
    ``KEY=value`` pairs keeps an unrelated secret in some other variable out of
    the transcript.
    """
    name = call.arguments.get("name")
    workspace = ctx.require_workspace()
    command = f"printf '%s' \"${name}\"" if name else "env | cut -d= -f1 | sort"

    try:
        output = await run_command_in_workspace(command, MIN_TIMEOUT_SEC * 15, workspace.root)
    except SandboxFallback as exc:
        return ToolResult.failure(
            call, f"Error: sandbox unavailable ({exc}).", error="sandbox_unavailable",
        )

    lines = output.splitlines()
    body = ""
    if "--- stdout ---" in lines:
        body = "\n".join(lines[lines.index("--- stdout ---") + 1:]).strip()

    if name:
        return ToolResult.success(
            call,
            f"{name}={body}" if body else f"{name} is not set",
            data={"name": name, "set": bool(body)},
        )
    names = [entry for entry in body.splitlines() if entry]
    return ToolResult.success(
        call, "\n".join(names) or "(no environment reported)", data={"names": names},
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

SPECS: List[Tuple[ToolSpec, Any]] = [
    (
        ToolSpec(
            id="terminal.run",
            title="Run a command",
            description=(
                "Run a shell command in the repository and return its stdout, stderr "
                "and exit code. Use it to run tests, linters and build steps, then read "
                "the output and act on it."
            ),
            params_schema=RUN_SCHEMA,
            risk=Risk.HIGH_RISK,
            effects=frozenset({Effect.EXECUTES, Effect.READS_FS, Effect.WRITES_FS}),
            timeout_s=MAX_TIMEOUT_SEC + 30,   # outer guard; the sandbox kills first
            priority=10,
        ),
        _terminal_run,
    ),
    (
        ToolSpec(
            id="terminal.run_snippet",
            title="Run a snippet",
            description=(
                "Run a self-contained program in a throwaway directory to check that it "
                "works. Returns stdout, stderr and the exit code, so a failure comes "
                "back as a full trace you can diagnose."
            ),
            params_schema=SNIPPET_SCHEMA,
            risk=Risk.HIGH_RISK,
            effects=frozenset({Effect.EXECUTES}),
            timeout_s=MAX_TIMEOUT_SEC + 30,
            priority=60,
        ),
        _terminal_run_snippet,
    ),
    (
        ToolSpec(
            id="terminal.which",
            title="Locate an executable",
            description="Report whether a program is available, and where.",
            params_schema=WHICH_SCHEMA,
            risk=Risk.SAFE,
            effects=frozenset({Effect.EXECUTES}),
            timeout_s=60,
            priority=50,
        ),
        _terminal_which,
    ),
    (
        ToolSpec(
            id="terminal.env",
            title="Inspect the environment",
            description=(
                "List the environment variables the sandbox exposes, or read one by "
                "name. Credentials are stripped before commands run."
            ),
            params_schema=ENV_SCHEMA,
            risk=Risk.SAFE,
            effects=frozenset({Effect.EXECUTES}),
            timeout_s=60,
            priority=70,
        ),
        _terminal_env,
    ),
]


def register(registry: ToolRegistry) -> None:
    """Add the terminal tools to *registry*."""
    for spec, handler in SPECS:
        registry.register(spec, handler)
