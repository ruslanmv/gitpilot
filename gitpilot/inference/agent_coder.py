"""Generic CLI coding-agent coder.

The repair pipeline's "coder" is duck-typed — ``fast`` / ``code`` / ``review``,
where ``code`` returns a unified diff. The built-in coder asks a model for that
diff in one shot. This module lets an *agentic CLI* (Claude Code, Codex, or any
other headless coding agent) play the same role: it runs the agent inside the
already-prepared workspace and then reads ``git diff`` back out as the patch.

That keeps every guardrail intact — the agent only ever edits a throwaway clone,
and the pipeline still decides whether the resulting patch is applied, sandboxed,
risk-scored, or turned into a PR. This module NEVER commits, pushes, or opens a
PR; it produces a diff and nothing else.

Agents are configured, not hard-coded: :class:`AgentSpec` describes the argv and
the read-only flag, so a new agent is a preset (or an env var), never a new
class. Presets ship for ``claude_code`` and ``codex``; ``GITPILOT_AGENT_CMD``
defines an arbitrary one.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 900  # agents explore + edit; give them room, but bound it.

# Placeholder substituted with the task text in an agent's argv template.
PROMPT_TOKEN = "{prompt}"


@dataclass(frozen=True)
class AgentSpec:
    """How to invoke one headless coding agent."""

    name: str
    # argv template; PROMPT_TOKEN is replaced with the task text.
    command: list[str]
    # Extra argv appended when the run must not modify files (dry-run/plan).
    read_only_flags: list[str] = field(default_factory=list)
    # Extra argv appended when the run may edit files in the workspace.
    write_flags: list[str] = field(default_factory=list)
    # Env var holding the agent's credential, if it needs one.
    credential_env: str | None = None
    timeout: int = DEFAULT_TIMEOUT


# Built-in presets. Flags map the pipeline's dry_run onto each agent's own
# read-only mode, so "preview only" is enforced by the agent as well as by us.
AGENT_PRESETS: dict[str, AgentSpec] = {
    "claude_code": AgentSpec(
        name="claude_code",
        command=["claude", "-p", PROMPT_TOKEN, "--output-format", "json"],
        read_only_flags=["--permission-mode", "plan"],
        write_flags=["--permission-mode", "acceptEdits"],
        credential_env="ANTHROPIC_API_KEY",
    ),
    "codex": AgentSpec(
        name="codex",
        command=["codex", "exec", PROMPT_TOKEN, "--json"],
        read_only_flags=["--sandbox", "read-only"],
        write_flags=["--sandbox", "workspace-write"],
        credential_env="OPENAI_API_KEY",
    ),
}


def _env_agent_spec(provider: str) -> AgentSpec | None:
    """Build a spec from the environment for a fully generic agent.

    ``GITPILOT_AGENT_CMD`` (shell-quoted, containing ``{prompt}``) defines the
    command; the read-only/write flags are optional. This is how an operator
    plugs in an agent GitPilot has never heard of.
    """
    raw = os.getenv("GITPILOT_AGENT_CMD", "").strip()
    if not raw:
        return None
    return AgentSpec(
        name=provider,
        command=shlex.split(raw),
        read_only_flags=shlex.split(os.getenv("GITPILOT_AGENT_READONLY_FLAGS", "")),
        write_flags=shlex.split(os.getenv("GITPILOT_AGENT_WRITE_FLAGS", "")),
        credential_env=os.getenv("GITPILOT_AGENT_CREDENTIAL_ENV") or None,
        timeout=int(os.getenv("GITPILOT_AGENT_TIMEOUT", str(DEFAULT_TIMEOUT))),
    )


def resolve_agent_spec(provider: str) -> AgentSpec | None:
    """Look up a preset, else an env-defined generic agent. None = not an agent."""
    key = (provider or "").strip().lower().replace("-", "_")
    if key in AGENT_PRESETS:
        return AGENT_PRESETS[key]
    if key in {"agent", "custom_agent"} or os.getenv("GITPILOT_AGENT_CMD"):
        return _env_agent_spec(key or "agent")
    return None


class AgentCoder:
    """Runs a headless coding agent in the workspace and returns its diff.

    Satisfies the same duck-typed coder surface as the built-in client, so the
    pipeline is unchanged: only ``code`` does real work, because an agent does
    its own inspection and self-review internally.
    """

    def __init__(self, spec: AgentSpec, demo_mode: bool = False) -> None:
        self.spec = spec
        self.demo_mode = demo_mode

    # -- coder surface --------------------------------------------------------

    def fast(self, user: str, system: str | None = None, **opts: Any) -> str:
        """Agents inspect the repository themselves; nothing to pre-compute."""
        return f"{self.spec.name}: inspection is performed by the agent in-workspace."

    def review(self, user: str, system: str | None = None, **opts: Any) -> str:
        """The agent reviews its own work as it goes; the pipeline still scores
        risk and the platform still requires approval."""
        return f"Patch produced by the {self.spec.name} agent; pending review."

    def code(self, user: str, system: str | None = None, **opts: Any) -> str:
        """Run the agent in the workspace and return the resulting unified diff."""
        workspace = opts.get("workspace")
        dry_run = bool(opts.get("dry_run", True))

        if self.demo_mode:
            return ""
        if not workspace or not os.path.isdir(workspace):
            # No prepared checkout => nothing an agent can safely edit. Return an
            # empty patch instead of guessing; the pipeline reports preview-only.
            log.warning("%s: no workspace available; skipping agent run", self.spec.name)
            return ""

        argv = self._argv(user, dry_run=dry_run)
        try:
            proc = subprocess.run(
                argv,
                cwd=workspace,
                capture_output=True,
                timeout=self.spec.timeout,
                env=self._env(),
                check=False,
            )
        except FileNotFoundError:
            log.warning("%s: agent executable not found (%s)", self.spec.name, argv[0])
            return ""
        except subprocess.TimeoutExpired:
            log.warning("%s: agent timed out after %ss", self.spec.name, self.spec.timeout)
            return ""

        if proc.returncode != 0:
            log.warning(
                "%s: agent exited %s: %s",
                self.spec.name,
                proc.returncode,
                self._redact(proc.stderr.decode("utf-8", "replace"))[:300],
            )
            # Fall through: the agent may still have produced partial edits.

        return self._workspace_diff(workspace)

    # -- internals ------------------------------------------------------------

    def _argv(self, prompt: str, *, dry_run: bool) -> list[str]:
        argv = [PROMPT_TOKEN if a == PROMPT_TOKEN else a for a in self.spec.command]
        argv = [prompt if a == PROMPT_TOKEN else a for a in argv]
        argv += self.spec.read_only_flags if dry_run else self.spec.write_flags
        return argv

    def _env(self) -> dict[str, str]:
        """Inherit the environment so the agent finds its own credential; the key
        is never read into GitPilot state or echoed anywhere."""
        return dict(os.environ)

    def _redact(self, text: str) -> str:
        out = text or ""
        for var in (self.spec.credential_env, "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
            secret = os.getenv(var or "", "")
            if secret:
                out = out.replace(secret, "***")
        return out

    @staticmethod
    def _workspace_diff(workspace: str) -> str:
        """The agent's edits, as a unified diff. Read-only: never commits."""
        try:
            # Include untracked files so a newly created file shows up in the diff.
            subprocess.run(
                ["git", "-C", workspace, "add", "-AN"],
                capture_output=True, timeout=30, check=False,
            )
            proc = subprocess.run(
                ["git", "-C", workspace, "diff"],
                capture_output=True, timeout=60, check=False,
            )
            return proc.stdout.decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 - a diff failure is not fatal
            log.warning("could not read workspace diff: %s", exc)
            return ""
