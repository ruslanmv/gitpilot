# gitpilot/sandbox.py
"""Sandboxed tool execution — pluggable, additive, non-destructive.

The default behaviour of GitPilot is unchanged: when no sandbox is
configured, callers fall back to the existing :mod:`gitpilot.terminal`
and :mod:`gitpilot.local_tools` modules which run on the host
filesystem.  Opting in requires only a single line::

    from gitpilot.sandbox import get_sandbox
    sb = get_sandbox()                # honours env + settings
    result = await sb.run(["pytest", "-q"])

Sandbox backends
----------------

* :class:`NullSandbox` — passthrough (legacy behaviour, host FS).
* :class:`SubprocessSandbox` — host subprocess with cwd jail, env
  scrub, output cap, blocked-pattern checks.  Always available.
* :class:`MatrixLabSandbox` — delegates execution to a MatrixLab
  Runner over HTTP (default ``http://localhost:8000``).  Containerised
  isolation: ephemeral filesystem, resource limits, no host access.

Selection precedence::

    explicit ``backend=`` argument
    > GITPILOT_SANDBOX env var ("matrixlab" | "subprocess" | "off")
    > settings.json    ``tools.sandbox``
    > "subprocess"      (the safe default for hosted commands)

Configuration is decoupled from the existing :mod:`gitpilot.terminal`
executor so adopting the sandbox is incremental: switch one tool
invocation at a time.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import httpx

logger = logging.getLogger(__name__)

# Backend identifiers ---------------------------------------------------
BACKEND_OFF = "off"
BACKEND_SUBPROCESS = "subprocess"
BACKEND_MATRIXLAB = "matrixlab"

DEFAULT_BACKEND = BACKEND_SUBPROCESS

ENV_BACKEND = "GITPILOT_SANDBOX"
ENV_MATRIXLAB_URL = "GITPILOT_MATRIXLAB_URL"
ENV_MATRIXLAB_TOKEN = "GITPILOT_MATRIXLAB_TOKEN"
ENV_MATRIXLAB_IMAGE = "GITPILOT_MATRIXLAB_IMAGE"

DEFAULT_TIMEOUT_SEC = 120
MAX_OUTPUT_BYTES = 512_000
DEFAULT_MATRIXLAB_URL = "http://localhost:8000"

# Conservative deny patterns reused across backends.
BLOCKED_PATTERNS: Tuple[str, ...] = (
    "rm -rf /",
    "mkfs",
    "dd if=/dev/zero",
    ":(){ :|:& };:",
    "shutdown -h",
    "shutdown -r",
)


# ----------------------------------------------------------------------
# Result + policy types
# ----------------------------------------------------------------------

@dataclass
class SandboxResult:
    """Outcome of a sandboxed command."""

    backend: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False
    timed_out: bool = False
    artifacts: List[str] = field(default_factory=list)
    sandbox_id: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class SandboxPolicy:
    """Runtime knobs applied uniformly across backends."""

    workspace: Optional[Path] = None
    timeout_sec: int = DEFAULT_TIMEOUT_SEC
    max_output_bytes: int = MAX_OUTPUT_BYTES
    extra_env: Dict[str, str] = field(default_factory=dict)
    allow_network: bool = True
    allowed_commands: Optional[List[str]] = None
    blocked_patterns: Tuple[str, ...] = BLOCKED_PATTERNS
    image: Optional[str] = None  # MatrixLab image override

    def validate(self, command_str: str) -> None:
        lower = command_str.lower().strip()
        for pattern in self.blocked_patterns:
            if pattern in lower:
                raise PermissionError(f"command blocked by sandbox policy: {pattern!r}")
        if self.allowed_commands is not None and lower:
            base = lower.split()[0]
            if base not in self.allowed_commands:
                raise PermissionError(f"command not in allowlist: {base!r}")


# ----------------------------------------------------------------------
# Backend interface
# ----------------------------------------------------------------------

class Sandbox:
    """Abstract sandbox interface.  Subclasses implement :meth:`run`."""

    backend: str = "abstract"

    def __init__(self, policy: Optional[SandboxPolicy] = None) -> None:
        self.policy = policy or SandboxPolicy()

    async def run(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
        stdin: Optional[str] = None,
    ) -> SandboxResult:
        raise NotImplementedError

    async def health(self) -> Dict[str, Any]:
        """Optional liveness probe.  Default: always healthy."""
        return {"backend": self.backend, "ok": True}

    # ------------------------------------------------------------------
    # Helpers shared across backends
    # ------------------------------------------------------------------
    def _resolve_command(self, command: Sequence[str] | str) -> Tuple[str, List[str]]:
        if isinstance(command, str):
            return command, shlex.split(command)
        cmd_list = list(command)
        return shlex.join(cmd_list), cmd_list

    def _truncate(self, data: bytes) -> Tuple[str, bool]:
        cap = self.policy.max_output_bytes
        if len(data) > cap:
            return data[:cap].decode("utf-8", errors="replace"), True
        return data.decode("utf-8", errors="replace"), False


# ----------------------------------------------------------------------
# NullSandbox  — explicit passthrough (legacy)
# ----------------------------------------------------------------------

class NullSandbox(Sandbox):
    """No isolation; runs in the current process via ``asyncio.subprocess``.

    Provided so callers can keep a single :class:`Sandbox`-shaped
    interface even when sandboxing is disabled.  This **does not**
    replace :mod:`gitpilot.terminal`; existing terminal sessions
    continue to work unchanged.
    """

    backend = BACKEND_OFF

    async def run(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
        stdin: Optional[str] = None,
    ) -> SandboxResult:
        command_str, _ = self._resolve_command(command)
        self.policy.validate(command_str)
        full_env = {**os.environ, **self.policy.extra_env, **(env or {})}
        start = time.monotonic()
        proc = await asyncio.create_subprocess_shell(
            command_str,
            cwd=str(cwd or self.policy.workspace or Path.cwd()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if stdin else None,
            env=full_env,
        )
        timed_out = False
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=stdin.encode() if stdin else None),
                timeout=timeout or self.policy.timeout_sec,
            )
        except asyncio.TimeoutError:
            timed_out = True
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            stdout_b, stderr_b = b"", b""
        stdout, truncated_out = self._truncate(stdout_b)
        stderr, truncated_err = self._truncate(stderr_b)
        return SandboxResult(
            backend=self.backend,
            command=command_str,
            exit_code=(proc.returncode if proc.returncode is not None else -1) if not timed_out else -1,
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((time.monotonic() - start) * 1000),
            truncated=truncated_out or truncated_err,
            timed_out=timed_out,
        )


# ----------------------------------------------------------------------
# SubprocessSandbox  — host subprocess with cwd jail
# ----------------------------------------------------------------------

class SubprocessSandbox(Sandbox):
    """Host subprocess constrained to the workspace.

    A pragmatic step up from :class:`NullSandbox`: the cwd is forced
    into ``policy.workspace`` (no escape via ``cd``); the environment
    is scrubbed unless ``allow_network`` is true (``HTTP_PROXY``-style
    vars and ``GITHUB_TOKEN`` are dropped); blocked patterns are
    enforced before launch.

    Real container isolation should use :class:`MatrixLabSandbox`.
    """

    backend = BACKEND_SUBPROCESS

    # Keys removed from the environment when ``allow_network`` is False.
    _NETWORK_ENV_KEYS = (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    )
    # Always stripped — secrets that shouldn't leak into sandboxed runs.
    _STRIP_ALWAYS = (
        "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "WATSONX_API_KEY", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    )

    async def run(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
        stdin: Optional[str] = None,
    ) -> SandboxResult:
        command_str, _ = self._resolve_command(command)
        self.policy.validate(command_str)
        workspace = (self.policy.workspace or Path.cwd()).resolve()
        target_cwd = (cwd or workspace).resolve()
        if not str(target_cwd).startswith(str(workspace)):
            target_cwd = workspace
        full_env = self._build_env(env)
        start = time.monotonic()
        proc = await asyncio.create_subprocess_shell(
            command_str,
            cwd=str(target_cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if stdin else None,
            env=full_env,
        )
        timed_out = False
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=stdin.encode() if stdin else None),
                timeout=timeout or self.policy.timeout_sec,
            )
        except asyncio.TimeoutError:
            timed_out = True
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            stdout_b, stderr_b = b"", b""
        stdout, truncated_out = self._truncate(stdout_b)
        stderr, truncated_err = self._truncate(stderr_b)
        return SandboxResult(
            backend=self.backend,
            command=command_str,
            exit_code=(proc.returncode if proc.returncode is not None else -1) if not timed_out else -1,
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((time.monotonic() - start) * 1000),
            truncated=truncated_out or truncated_err,
            timed_out=timed_out,
        )

    def _build_env(self, overrides: Optional[Mapping[str, str]]) -> Dict[str, str]:
        env: Dict[str, str] = {k: v for k, v in os.environ.items() if k not in self._STRIP_ALWAYS}
        if not self.policy.allow_network:
            for key in self._NETWORK_ENV_KEYS:
                env.pop(key, None)
        env.update(self.policy.extra_env)
        if overrides:
            env.update(overrides)
        return env


# ----------------------------------------------------------------------
# MatrixLabSandbox  — containerised execution via Runner HTTP API
# ----------------------------------------------------------------------

class MatrixLabSandbox(Sandbox):
    """Delegate execution to a MatrixLab Runner over HTTP.

    MatrixLab provides containerised, ephemeral execution suitable for
    untrusted code: each ``run`` is dispatched to a disposable
    container, the workspace is mounted read-write into the container's
    scratch directory, and resource limits are enforced by the runner.

    The runner endpoint defaults to ``http://localhost:8000``; override
    via the ``GITPILOT_MATRIXLAB_URL`` environment variable or by
    passing ``base_url`` to the constructor.

    The protocol used here is the simple Runner API documented for
    MatrixLab: ``POST /repo/run`` with a JSON body ``{cmd, cwd, env,
    timeout, image, stdin}`` and a response containing ``exit_code``,
    ``stdout``, ``stderr``, ``artifacts`` and ``sandbox_id``.  When
    MatrixLab is unreachable, callers should pick a different backend
    (this class deliberately surfaces a clear error instead of silently
    falling back, so security-sensitive runs are never mis-routed).
    """

    backend = BACKEND_MATRIXLAB

    def __init__(
        self,
        policy: Optional[SandboxPolicy] = None,
        *,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__(policy)
        self.base_url = (base_url or os.environ.get(ENV_MATRIXLAB_URL) or DEFAULT_MATRIXLAB_URL).rstrip("/")
        self.token = token or os.environ.get(ENV_MATRIXLAB_TOKEN)
        self._http = http_client
        self._owns_http = http_client is None

    async def health(self) -> Dict[str, Any]:
        try:
            client = await self._client()
            resp = await client.get(f"{self.base_url}/health", timeout=5.0)
            resp.raise_for_status()
            return {"backend": self.backend, "ok": True, "remote": resp.json()}
        except Exception as exc:
            return {"backend": self.backend, "ok": False, "error": str(exc)}

    async def run(
        self,
        command: Sequence[str] | str,
        *,
        cwd: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
        timeout: Optional[int] = None,
        stdin: Optional[str] = None,
    ) -> SandboxResult:
        command_str, _ = self._resolve_command(command)
        self.policy.validate(command_str)
        body: Dict[str, Any] = {
            "cmd": command_str,
            "cwd": str(cwd or self.policy.workspace or "/workspace"),
            "env": {**self.policy.extra_env, **(env or {})},
            "timeout": timeout or self.policy.timeout_sec,
            "image": self.policy.image or os.environ.get(ENV_MATRIXLAB_IMAGE),
            "allow_network": self.policy.allow_network,
        }
        if stdin is not None:
            body["stdin"] = stdin
        if self.policy.workspace is not None:
            body["mount_workspace"] = str(self.policy.workspace)

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        client = await self._client()
        start = time.monotonic()
        try:
            resp = await client.post(
                f"{self.base_url}/repo/run",
                json=body,
                headers=headers,
                timeout=(timeout or self.policy.timeout_sec) + 5,
            )
        except httpx.HTTPError as exc:
            raise SandboxUnavailableError(f"MatrixLab unreachable: {exc}") from exc

        duration_ms = int((time.monotonic() - start) * 1000)
        try:
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPStatusError, json.JSONDecodeError) as exc:
            raise SandboxRunError(f"MatrixLab returned an error: {exc}") from exc

        return SandboxResult(
            backend=self.backend,
            command=command_str,
            exit_code=int(data.get("exit_code", -1)),
            stdout=str(data.get("stdout", ""))[: self.policy.max_output_bytes],
            stderr=str(data.get("stderr", ""))[: self.policy.max_output_bytes],
            duration_ms=int(data.get("duration_ms", duration_ms)),
            truncated=bool(data.get("truncated", False)),
            timed_out=bool(data.get("timed_out", False)),
            artifacts=list(data.get("artifacts", [])),
            sandbox_id=data.get("sandbox_id"),
        )

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient()
        return self._http


# ----------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------

class SandboxError(RuntimeError):
    """Base class for sandbox failures."""


class SandboxUnavailableError(SandboxError):
    """Raised when a backend cannot be reached."""


class SandboxRunError(SandboxError):
    """Raised when the backend processed the request but reported a problem."""


# ----------------------------------------------------------------------
# Resolution / factory
# ----------------------------------------------------------------------

def _resolve_backend_name(explicit: Optional[str], settings: Optional[Mapping[str, Any]]) -> str:
    if explicit:
        return explicit
    env_val = os.environ.get(ENV_BACKEND)
    if env_val:
        return env_val
    if settings:
        configured = settings.get("tools", {}).get("sandbox") if isinstance(settings, Mapping) else None
        if isinstance(configured, str):
            return configured
    return DEFAULT_BACKEND


def get_sandbox(
    backend: Optional[str] = None,
    *,
    policy: Optional[SandboxPolicy] = None,
    settings: Optional[Mapping[str, Any]] = None,
) -> Sandbox:
    """Return an initialised sandbox according to precedence rules."""
    name = _resolve_backend_name(backend, settings)
    name = name.strip().lower()
    if name in {BACKEND_OFF, "false", "0", "none"}:
        return NullSandbox(policy)
    if name == BACKEND_MATRIXLAB:
        return MatrixLabSandbox(policy)
    if name == BACKEND_SUBPROCESS:
        return SubprocessSandbox(policy)
    # Unknown backend → safest default.
    logger.warning("unknown sandbox backend %r, falling back to subprocess", name)
    return SubprocessSandbox(policy)
