"""Tests for gitpilot.headless module.

The headless module imports agentic and agent_tools, which pull in the full
CrewAI tool-decoration chain.  To keep tests fast and isolated we mock those
heavy transitive imports via sys.modules before importing headless.
"""
from __future__ import annotations

import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Build lightweight stubs for the heavy transitive imports so that
# ``from gitpilot.headless import ...`` does not trigger the full CrewAI
# tool-decoration pipeline (which can fail in constrained CI environments).
# ---------------------------------------------------------------------------

_fake_agentic = ModuleType("gitpilot.agentic")
_fake_agentic.dispatch_request = AsyncMock(return_value={"result": "stub"})

_fake_agent_tools = ModuleType("gitpilot.agent_tools")
_fake_agent_tools.set_repo_context = MagicMock()

# Ensure the stubs are registered *before* headless is imported
sys.modules.setdefault("gitpilot.agentic", _fake_agentic)
sys.modules.setdefault("gitpilot.agent_tools", _fake_agent_tools)

from gitpilot.headless import HeadlessResult, run_headless  # noqa: E402


# ---------------------------------------------------------------------------
# HeadlessResult
# ---------------------------------------------------------------------------

class TestHeadlessResult:
    def test_success_result(self):
        r = HeadlessResult(success=True, output="all done")
        assert r.success is True
        assert r.error is None

    def test_failure_result(self):
        r = HeadlessResult(success=False, output="", error="kaboom")
        assert r.success is False
        assert r.error == "kaboom"

    def test_to_json(self):
        r = HeadlessResult(
            success=True, output="ok",
            session_id="abc", pr_url="https://github.com/o/r/pull/1",
        )
        parsed = json.loads(r.to_json())
        assert parsed["success"] is True
        assert parsed["output"] == "ok"
        assert parsed["session_id"] == "abc"
        assert parsed["pr_url"] == "https://github.com/o/r/pull/1"

    def test_to_json_includes_error(self):
        r = HeadlessResult(success=False, output="", error="fail")
        parsed = json.loads(r.to_json())
        assert parsed["error"] == "fail"

    def test_optional_fields_default_none(self):
        r = HeadlessResult(success=True, output="x")
        assert r.session_id is None
        assert r.pr_url is None
        assert r.plan is None


# ---------------------------------------------------------------------------
# run_headless - success path
# ---------------------------------------------------------------------------

class TestRunHeadlessSuccess:
    async def test_basic_request(self):
        with (
            patch("gitpilot.headless.set_repo_context") as mock_ctx,
            patch(
                "gitpilot.headless.dispatch_request",
                new_callable=AsyncMock,
                return_value={"result": "Changes applied"},
            ) as mock_dispatch,
        ):
            result = await run_headless(
                repo_full_name="owner/repo",
                message="fix the bug",
                token="tok123",
            )

        mock_ctx.assert_called_once_with("owner", "repo", token="tok123", branch="main")
        mock_dispatch.assert_called_once()
        assert result.success is True
        assert result.output == "Changes applied"

    async def test_with_branch(self):
        with (
            patch("gitpilot.headless.set_repo_context") as mock_ctx,
            patch(
                "gitpilot.headless.dispatch_request",
                new_callable=AsyncMock,
                return_value={"result": "ok"},
            ),
        ):
            await run_headless("o/r", "msg", "t", branch="develop")

        mock_ctx.assert_called_once_with("o", "r", token="t", branch="develop")

    async def test_dispatch_returns_string(self):
        with (
            patch("gitpilot.headless.set_repo_context"),
            patch(
                "gitpilot.headless.dispatch_request",
                new_callable=AsyncMock,
                return_value="plain string result",
            ),
        ):
            result = await run_headless("o/r", "msg", "t")

        assert result.output == "plain string result"


# ---------------------------------------------------------------------------
# run_headless - from_pr
# ---------------------------------------------------------------------------

class TestRunHeadlessFromPR:
    async def test_from_pr_fetches_context(self):
        mock_pr = {"title": "Fix login", "body": "Details here"}
        with (
            patch("gitpilot.headless.set_repo_context"),
            patch(
                "gitpilot.headless.dispatch_request",
                new_callable=AsyncMock,
                return_value={"result": "done"},
            ),
            patch(
                "gitpilot.github_pulls.get_pull_request",
                new_callable=AsyncMock,
                return_value=mock_pr,
            ),
        ):
            result = await run_headless("owner/repo", "check this", "tok", from_pr=42)

        assert result.success is True

    async def test_from_pr_failure_continues(self):
        """If fetching the PR fails, execution should still proceed."""
        with (
            patch("gitpilot.headless.set_repo_context"),
            patch(
                "gitpilot.headless.dispatch_request",
                new_callable=AsyncMock,
                return_value={"result": "continued"},
            ),
            patch(
                "gitpilot.github_pulls.get_pull_request",
                new_callable=AsyncMock,
                side_effect=Exception("API error"),
            ),
        ):
            result = await run_headless("o/r", "msg", "t", from_pr=99)

        assert result.success is True
        assert result.output == "continued"


# ---------------------------------------------------------------------------
# run_headless - error path
# ---------------------------------------------------------------------------

class TestRunHeadlessError:
    async def test_dispatch_exception(self):
        with (
            patch("gitpilot.headless.set_repo_context"),
            patch(
                "gitpilot.headless.dispatch_request",
                new_callable=AsyncMock,
                side_effect=RuntimeError("agent crashed"),
            ),
        ):
            result = await run_headless("o/r", "msg", "t")

        assert result.success is False
        assert result.error == "agent crashed"
        assert result.output == ""

    async def test_invalid_repo_format_raises(self):
        """A repo name without / raises ValueError from unpacking."""
        with (
            patch("gitpilot.headless.set_repo_context"),
            patch(
                "gitpilot.headless.dispatch_request",
                new_callable=AsyncMock,
                return_value={"result": "ok"},
            ),
        ):
            with pytest.raises(ValueError):
                await run_headless("noslash", "msg", "t")


# ---------------------------------------------------------------------------
# run_headless - dispatch result types
# ---------------------------------------------------------------------------

class TestRunHeadlessResultTypes:
    async def test_dict_result_extracts_result_key(self):
        with (
            patch("gitpilot.headless.set_repo_context"),
            patch(
                "gitpilot.headless.dispatch_request",
                new_callable=AsyncMock,
                return_value={"result": "specific value", "extra": "ignored"},
            ),
        ):
            result = await run_headless("o/r", "msg", "t")

        assert result.output == "specific value"

    async def test_dict_without_result_key(self):
        with (
            patch("gitpilot.headless.set_repo_context"),
            patch(
                "gitpilot.headless.dispatch_request",
                new_callable=AsyncMock,
                return_value={"other": "data"},
            ),
        ):
            result = await run_headless("o/r", "msg", "t")

        assert result.output == ""

    async def test_non_dict_result(self):
        with (
            patch("gitpilot.headless.set_repo_context"),
            patch(
                "gitpilot.headless.dispatch_request",
                new_callable=AsyncMock,
                return_value=42,
            ),
        ):
            result = await run_headless("o/r", "msg", "t")

        assert result.output == "42"
