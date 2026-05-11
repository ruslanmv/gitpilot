"""Tests for the sandbox abstraction."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import httpx
import pytest

from gitpilot.sandbox import (
    BACKEND_MATRIXLAB,
    BACKEND_OFF,
    BACKEND_SUBPROCESS,
    MatrixLabSandbox,
    NullSandbox,
    SandboxPolicy,
    SandboxUnavailableError,
    SubprocessSandbox,
    get_sandbox,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


# ----------------------------------------------------------------------
# Selection
# ----------------------------------------------------------------------

def test_get_sandbox_defaults_to_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITPILOT_SANDBOX", raising=False)
    sb = get_sandbox()
    assert isinstance(sb, SubprocessSandbox)


def test_get_sandbox_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITPILOT_SANDBOX", BACKEND_MATRIXLAB)
    sb = get_sandbox()
    assert isinstance(sb, MatrixLabSandbox)


def test_get_sandbox_off_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITPILOT_SANDBOX", BACKEND_OFF)
    sb = get_sandbox()
    assert isinstance(sb, NullSandbox)


def test_get_sandbox_settings_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITPILOT_SANDBOX", raising=False)
    sb = get_sandbox(settings={"tools": {"sandbox": BACKEND_MATRIXLAB}})
    assert isinstance(sb, MatrixLabSandbox)


def test_unknown_backend_falls_back_to_subprocess() -> None:
    sb = get_sandbox("does-not-exist")
    assert isinstance(sb, SubprocessSandbox)


# ----------------------------------------------------------------------
# Policy validation
# ----------------------------------------------------------------------

def test_policy_blocks_destructive_commands() -> None:
    policy = SandboxPolicy()
    with pytest.raises(PermissionError):
        policy.validate("rm -rf /")


def test_policy_enforces_allowlist() -> None:
    policy = SandboxPolicy(allowed_commands=["ls", "pwd"])
    policy.validate("ls -la")
    with pytest.raises(PermissionError):
        policy.validate("python -c 'exit(0)'")


# ----------------------------------------------------------------------
# SubprocessSandbox  — real exec
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subprocess_runs_command(workspace: Path) -> None:
    sb = SubprocessSandbox(SandboxPolicy(workspace=workspace, timeout_sec=10))
    result = await sb.run(["echo", "hello"])
    assert result.ok
    assert "hello" in result.stdout


@pytest.mark.asyncio
async def test_subprocess_blocks_pattern(workspace: Path) -> None:
    sb = SubprocessSandbox(SandboxPolicy(workspace=workspace))
    with pytest.raises(PermissionError):
        await sb.run("rm -rf /")


@pytest.mark.asyncio
async def test_subprocess_jails_cwd_to_workspace(
    workspace: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    # Create a directory clearly outside the workspace tree so the
    # sandbox is forced to coerce it back to the workspace root.
    outside = tmp_path_factory.mktemp("outside")
    sb = SubprocessSandbox(SandboxPolicy(workspace=workspace, timeout_sec=10))
    result = await sb.run("pwd", cwd=outside)
    assert result.ok
    assert str(workspace.resolve()) in result.stdout


@pytest.mark.asyncio
async def test_subprocess_strips_secrets(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "should-not-leak")
    sb = SubprocessSandbox(SandboxPolicy(workspace=workspace, timeout_sec=10))
    result = await sb.run('echo "${GITHUB_TOKEN:-empty}"')
    assert "should-not-leak" not in result.stdout
    assert "empty" in result.stdout


@pytest.mark.asyncio
async def test_subprocess_times_out(workspace: Path) -> None:
    sb = SubprocessSandbox(SandboxPolicy(workspace=workspace, timeout_sec=1))
    result = await sb.run("sleep 5")
    assert result.timed_out is True


# ----------------------------------------------------------------------
# MatrixLabSandbox  — protocol shape, with a mock transport
# ----------------------------------------------------------------------

class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, response_factory):
        self.factory = response_factory
        self.last_request: Optional[httpx.Request] = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        return self.factory(request)


def _ok_response(_: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "exit_code": 0,
            "stdout": "ok\n",
            "stderr": "",
            "duration_ms": 17,
            "artifacts": ["build.log"],
            "sandbox_id": "sb-abc",
        },
    )


def _err_response(_: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


@pytest.mark.asyncio
async def test_matrixlab_posts_run_and_parses_response(workspace: Path) -> None:
    transport = _MockTransport(_ok_response)
    client = httpx.AsyncClient(transport=transport, base_url="http://localhost")
    sb = MatrixLabSandbox(
        SandboxPolicy(workspace=workspace, timeout_sec=10),
        base_url="http://lab.example",
        http_client=client,
    )
    result = await sb.run(["pytest", "-q"])
    assert result.backend == BACKEND_MATRIXLAB
    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert result.artifacts == ["build.log"]
    assert result.sandbox_id == "sb-abc"
    assert transport.last_request is not None
    assert transport.last_request.url.path == "/repo/run"
    await sb.aclose()


@pytest.mark.asyncio
async def test_matrixlab_raises_when_unreachable(workspace: Path) -> None:
    transport = _MockTransport(_err_response)
    client = httpx.AsyncClient(transport=transport, base_url="http://localhost")
    sb = MatrixLabSandbox(
        SandboxPolicy(workspace=workspace),
        base_url="http://nope",
        http_client=client,
    )
    with pytest.raises(SandboxUnavailableError):
        await sb.run(["echo", "hi"])
    await sb.aclose()


@pytest.mark.asyncio
async def test_matrixlab_authorisation_header_sent(workspace: Path) -> None:
    transport = _MockTransport(_ok_response)
    client = httpx.AsyncClient(transport=transport, base_url="http://localhost")
    sb = MatrixLabSandbox(
        SandboxPolicy(workspace=workspace),
        base_url="http://lab",
        token="token-xyz",
        http_client=client,
    )
    await sb.run(["true"])
    assert transport.last_request is not None
    assert transport.last_request.headers.get("Authorization") == "Bearer token-xyz"
    await sb.aclose()


# ----------------------------------------------------------------------
# NullSandbox  — passthrough
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_null_sandbox_executes_on_host(workspace: Path) -> None:
    sb = NullSandbox(SandboxPolicy(workspace=workspace, timeout_sec=10))
    result = await sb.run(["echo", "x"])
    assert result.backend == BACKEND_OFF
    assert "x" in result.stdout
