"""MCP correctness fixes — Batch V4-0B.

Two long-standing defects, both of which produced *plausible* output rather
than an error, which is why neither was noticed:

* ``MCPClient.to_crewai_tools`` generated one wrapper per discovered tool,
  each carrying the right name and calling whichever tool the loop visited
  last (late-binding closure).
* GitPilot's own MCP server probed for methods that have never existed
  (``SkillManager.list``, ``agentic.build_plan``), so ``gitpilot.list_skills``
  silently returned an empty catalogue and ``gitpilot.plan`` always reported
  "no plan() entrypoint".
"""
from __future__ import annotations

import asyncio

import pytest

from gitpilot.mcp_client import (
    MCPClient,
    MCPConnection,
    MCPServerConfig,
    MCPTool,
    TransportType,
)
from gitpilot.mcp_server import CallContext
from gitpilot.mcp_server_tools import _execute, _list_skills, _plan


def _identity_tool_decorator(name):
    """Stand-in for ``crewai.tools.tool`` so these tests need no CrewAI."""
    def _decorate(fn):
        fn.gitpilot_tool_name = name
        return fn
    return _decorate


def _conn_with_two_tools() -> MCPConnection:
    cfg = MCPServerConfig(name="srv", transport=TransportType.HTTP, url="http://x")
    return MCPConnection(
        config=cfg,
        tools=[
            MCPTool(name="alpha", description="first tool", server_name="srv"),
            MCPTool(name="beta", description="second tool", server_name="srv"),
        ],
    )


class TestCrewAIWrapperBinding:
    def test_each_wrapper_calls_its_own_tool(self, monkeypatch):
        """The regression: both wrappers used to invoke 'beta'."""
        conn = _conn_with_two_tools()
        client = MCPClient()
        called: list[str] = []

        async def fake_call_tool(_conn, tool_name, params=None):
            called.append(tool_name)
            return f"result:{tool_name}"

        monkeypatch.setattr(client, "call_tool", fake_call_tool)

        wrappers = [
            client._make_crewai_wrapper(
                _identity_tool_decorator, conn, tool.name, tool.description,
            )
            for tool in conn.tools
        ]

        assert wrappers[0]() == "result:alpha"
        assert wrappers[1]() == "result:beta"
        assert called == ["alpha", "beta"]

    def test_description_is_readable_before_decoration(self):
        """CrewAI reads the docstring when it decorates, so it must be set first."""
        conn = _conn_with_two_tools()
        client = MCPClient()

        seen: list[str | None] = []

        def capturing_decorator(name):
            def _decorate(fn):
                seen.append(fn.__doc__)
                return fn
            return _decorate

        client._make_crewai_wrapper(capturing_decorator, conn, "alpha", "first tool")
        assert seen == ["first tool"]

    def test_wrapper_signature_stays_single_param(self):
        """Binding via a factory, not default args — the args schema must not grow."""
        import inspect

        conn = _conn_with_two_tools()
        client = MCPClient()
        wrapper = client._make_crewai_wrapper(
            _identity_tool_decorator, conn, "alpha", "first tool",
        )
        assert list(inspect.signature(wrapper).parameters) == ["params"]


class TestServerToolContracts:
    def test_list_skills_finds_registered_skills(self, tmp_path, monkeypatch):
        from gitpilot.skills import Skill, SkillManager

        class _Populated(SkillManager):
            def __init__(self):
                super().__init__(workspace_path=tmp_path)
                self._skills = {
                    "review": Skill(name="review", description="review a diff"),
                }

        monkeypatch.setattr("gitpilot.skills.SkillManager", _Populated)

        result = asyncio.run(_list_skills({}, CallContext()))

        assert result["available"] is True
        assert [s["name"] for s in result["skills"]] == ["review"]

    def test_list_skills_reports_a_manager_without_a_lister(self, monkeypatch):
        """An unusable manager must say so, not report an empty catalogue."""
        class _Useless:
            pass

        monkeypatch.setattr("gitpilot.skills.SkillManager", _Useless)

        result = asyncio.run(_list_skills({}, CallContext()))

        assert result["available"] is False
        assert "listing method" in result["reason"]

    def test_plan_calls_generate_plan(self, monkeypatch):
        captured: dict = {}

        async def fake_generate_plan(goal, repo_full_name, **kwargs):
            captured.update(goal=goal, repo=repo_full_name, kwargs=kwargs)
            return {"summary": "did the thing"}

        monkeypatch.setattr("gitpilot.agentic.generate_plan", fake_generate_plan)

        result = asyncio.run(_plan(
            {"prompt": "add a test", "owner": "o", "repo": "r", "branch": "dev"},
            CallContext(),
        ))

        assert result["available"] is True
        assert result["plan"] == {"summary": "did the thing"}
        assert captured["goal"] == "add a test"
        assert captured["repo"] == "o/r"
        assert captured["kwargs"]["branch_name"] == "dev"

    def test_plan_requires_a_repository(self):
        result = asyncio.run(_plan({"prompt": "add a test"}, CallContext()))
        assert result["available"] is False
        assert "owner and repo" in result["reason"]

    def test_execute_reports_why_it_is_unavailable(self):
        """Previously surfaced a TypeError string that read like a caller bug."""
        result = asyncio.run(_execute({"plan_id": "abc"}, CallContext()))
        assert result["available"] is False
        assert "not persisted" in result["reason"]
        assert "/api/chat/execute" in result["reason"]
