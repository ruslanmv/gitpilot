"""Shared shell-safety guards — Batch V4-0C.

The point of these tests is that the sandbox and the terminal executor can no
longer disagree.  Before this batch ``shutdown -h`` was refused by one and
accepted by the other, and only the sandbox stripped credentials from the
child environment.
"""
from __future__ import annotations

import pytest

from gitpilot import shell_safety
from gitpilot.sandbox import BLOCKED_PATTERNS as SANDBOX_PATTERNS
from gitpilot.sandbox import SandboxPolicy, SubprocessSandbox
from gitpilot.terminal import BLOCKED_PATTERNS as TERMINAL_PATTERNS
from gitpilot.terminal import TerminalExecutor, TerminalSession


class TestSingleDenylist:
    def test_both_executors_share_one_source(self):
        """One literal, two importers — the drift this batch removes."""
        assert tuple(SANDBOX_PATTERNS) == shell_safety.BLOCKED_PATTERNS
        assert tuple(TERMINAL_PATTERNS) == shell_safety.BLOCKED_PATTERNS

    @pytest.mark.parametrize("command", [
        "rm -rf /",
        "mkfs.ext4 /dev/sda",
        "dd if=/dev/zero of=/dev/sda",
        ":(){ :|:& };:",
        "shutdown -h now",
        "shutdown -r now",
    ])
    def test_every_pattern_refused_by_both(self, command, tmp_path):
        """``shutdown`` is the regression: the terminal list used to omit it."""
        executor = TerminalExecutor()
        with pytest.raises(PermissionError):
            executor._validate_command(command)

        policy = SandboxPolicy(workspace=tmp_path)
        with pytest.raises(PermissionError):
            policy.validate(command)

    def test_blocked_reason_names_the_pattern(self):
        assert shell_safety.blocked_reason("sudo MKFS /dev/sda") == "mkfs"
        assert shell_safety.blocked_reason("pytest -q") is None
        assert shell_safety.is_blocked("RM -RF /") is True

    def test_sandbox_error_quotes_the_matched_pattern(self, tmp_path):
        policy = SandboxPolicy(workspace=tmp_path)
        with pytest.raises(PermissionError, match="mkfs"):
            policy.validate("mkfs /dev/sda")


class TestSecretStripping:
    SECRETS = {
        "GITHUB_TOKEN": "ghp_x",
        "GITPILOT_GITHUB_TOKEN": "ghp_y",
        "OPENAI_API_KEY": "sk-x",
        "ANTHROPIC_API_KEY": "sk-ant-x",
        "WATSONX_API_KEY": "wx-x",
        "AWS_SECRET_ACCESS_KEY": "aws-x",
        "AWS_SESSION_TOKEN": "aws-sess",
    }

    def test_secrets_dropped_from_inherited_env(self, monkeypatch):
        for key, value in self.SECRETS.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("PATH", "/usr/bin")

        env = shell_safety.strip_secret_env()

        assert not (set(env) & set(self.SECRETS)), "credentials leaked to child"
        assert env["PATH"] == "/usr/bin", "benign vars must survive"

    def test_proxy_vars_dropped_only_when_network_denied(self, monkeypatch):
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
        assert "HTTPS_PROXY" in shell_safety.strip_secret_env(allow_network=True)
        assert "HTTPS_PROXY" not in shell_safety.strip_secret_env(allow_network=False)

    def test_explicit_overrides_are_never_filtered(self, monkeypatch):
        """A caller that passes a token means it; ambient inheritance is what we distrust."""
        monkeypatch.setenv("GITHUB_TOKEN", "ambient")
        env = shell_safety.strip_secret_env(overrides={"GITHUB_TOKEN": "deliberate"})
        assert env["GITHUB_TOKEN"] == "deliberate"

    def test_terminal_executor_no_longer_leaks_secrets(self, monkeypatch, tmp_path):
        """The regression: this executor still runs the lint/test validation phase."""
        for key, value in self.SECRETS.items():
            monkeypatch.setenv(key, value)

        captured: dict = {}

        async def fake_subprocess_shell(command, **kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop here — we only need the env")

        monkeypatch.setattr(
            "asyncio.create_subprocess_shell", fake_subprocess_shell,
        )

        executor = TerminalExecutor()
        session = TerminalSession(workspace_path=tmp_path)
        result = __import__("asyncio").run(
            executor.execute(session, "echo hi"),
        )

        assert result.exit_code != 0          # the fake blew up, as intended
        env = captured.get("env") or {}
        assert not (set(env) & set(self.SECRETS))

    def test_subprocess_sandbox_shares_the_key_lists(self):
        policy = SandboxPolicy(workspace=None)
        sandbox = SubprocessSandbox(policy)
        assert sandbox._STRIP_ALWAYS == shell_safety.SECRET_ENV_KEYS
        assert sandbox._NETWORK_ENV_KEYS == shell_safety.NETWORK_ENV_KEYS
