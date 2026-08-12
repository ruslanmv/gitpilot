"""Forge tools, the absent web provider, and registry assembly — Batch V4-A6."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from gitpilot.toolkit import (
    Effect,
    LocalWorkspace,
    RepoBinding,
    Risk,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
)
from gitpilot.toolkit import forge as forge_tools
from gitpilot.toolkit import web as web_tools
from gitpilot.toolkit.builtins import build_default_registry, describe_default_registry
from gitpilot.toolkit.registry import capabilities_from


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    forge_tools.register(reg)
    return reg


@pytest.fixture()
def repo_ctx() -> ToolExecutionContext:
    return ToolExecutionContext(repo=RepoBinding(owner="o", repo="r", token="tok", branch="main"))


def _run(registry, tool, arguments, ctx):
    return asyncio.run(registry.execute(ToolCall(id="t", tool=tool, arguments=arguments), ctx))


class TestIssues:
    def test_list_summarises_each_issue(self, registry, repo_ctx):
        with patch("gitpilot.github_issues.list_issues", new_callable=AsyncMock) as list_issues:
            list_issues.return_value = [
                {"number": 7, "state": "open", "title": "Broken build", "user": {"login": "alice"}},
            ]
            result = _run(registry, "github.issue.list", {"state": "open"}, repo_ctx)

        assert result.content == "#7 [open] Broken build — alice"
        assert result.data["numbers"] == [7]
        assert list_issues.await_args.kwargs["token"] == "tok"

    def test_empty_list_is_stated(self, registry, repo_ctx):
        with patch("gitpilot.github_issues.list_issues", new_callable=AsyncMock) as list_issues:
            list_issues.return_value = []
            result = _run(registry, "github.issue.list", {}, repo_ctx)

        assert result.content == "No issues matched."

    def test_get_includes_the_body(self, registry, repo_ctx):
        with patch("gitpilot.github_issues.get_issue", new_callable=AsyncMock) as get_issue:
            get_issue.return_value = {
                "number": 7, "state": "open", "title": "T", "user": {"login": "a"}, "body": "details",
            }
            result = _run(registry, "github.issue.get", {"number": 7}, repo_ctx)

        assert result.content.endswith("details")

    def test_get_handles_a_missing_body(self, registry, repo_ctx):
        with patch("gitpilot.github_issues.get_issue", new_callable=AsyncMock) as get_issue:
            get_issue.return_value = {"number": 7, "state": "open", "title": "T", "body": None}
            result = _run(registry, "github.issue.get", {"number": 7}, repo_ctx)

        assert "(no description)" in result.content

    def test_create_returns_the_url(self, registry, repo_ctx):
        with patch("gitpilot.github_issues.create_issue", new_callable=AsyncMock) as create:
            create.return_value = {"number": 12, "html_url": "https://github.com/o/r/issues/12"}
            result = _run(registry, "github.issue.create", {"title": "Bug", "labels": ["bug"]}, repo_ctx)

        assert result.content == "Created issue #12: https://github.com/o/r/issues/12"
        assert create.await_args.kwargs["labels"] == ["bug"]

    def test_number_must_be_a_positive_integer(self, registry, repo_ctx):
        assert _run(registry, "github.issue.get", {"number": 0}, repo_ctx).error == "invalid_arguments"


class TestPullRequests:
    def test_list_shows_the_branch_pair(self, registry, repo_ctx):
        with patch("gitpilot.github_pulls.list_pull_requests", new_callable=AsyncMock) as list_prs:
            list_prs.return_value = [{
                "number": 3, "state": "open", "title": "Add feature", "draft": True,
                "head": {"ref": "feat"}, "base": {"ref": "main"},
            }]
            result = _run(registry, "github.pr.list", {}, repo_ctx)

        assert result.content == "#3 [open] (draft) Add feature — feat → main"

    def test_create_requires_head_and_base(self, registry, repo_ctx):
        result = _run(registry, "github.pr.create", {"title": "T"}, repo_ctx)

        assert result.error == "invalid_arguments"
        assert "head" in result.content and "base" in result.content

    def test_create_passes_draft_through(self, registry, repo_ctx):
        with patch("gitpilot.github_pulls.create_pull_request", new_callable=AsyncMock) as create:
            create.return_value = {"number": 9, "html_url": "u"}
            _run(registry, "github.pr.create", {
                "title": "T", "head": "feat", "base": "main", "draft": True,
            }, repo_ctx)

        assert create.await_args.kwargs["draft"] is True


class TestSearch:
    def test_code_search_is_scoped_to_the_bound_repo_by_default(self, registry, repo_ctx):
        with patch("gitpilot.github_search.search_code", new_callable=AsyncMock) as search:
            search.return_value = {"total_count": 1, "items": [
                {"path": "a.py", "repository": {"full_name": "o/r"}},
            ]}
            _run(registry, "github.search.code", {"query": "needle"}, repo_ctx)

        assert search.await_args.kwargs["owner"] == "o"
        assert search.await_args.kwargs["repo"] == "r"

    def test_code_search_can_widen_beyond_the_repo(self, registry, repo_ctx):
        with patch("gitpilot.github_search.search_code", new_callable=AsyncMock) as search:
            search.return_value = {"total_count": 0, "items": []}
            _run(registry, "github.search.code", {"query": "x", "this_repo_only": False}, repo_ctx)

        assert search.await_args.kwargs["owner"] is None


class TestRiskAndCapabilities:
    def test_reads_are_safe_and_share_the_read_capability(self, registry):
        for tool in ("github.issue.list", "github.issue.get", "github.pr.list",
                     "github.pr.get", "github.search.code", "github.search.issues"):
            spec = registry.spec(tool)
            assert spec.risk is Risk.SAFE, tool
            assert spec.capability == "github.read", tool

    def test_writes_need_approval_and_declare_forge_write(self, registry):
        for tool in ("github.issue.create", "github.issue.comment", "github.pr.create"):
            spec = registry.spec(tool)
            assert spec.risk is Risk.APPROVAL, tool
            assert Effect.FORGE_WRITE in spec.effects, tool

    def test_pr_write_is_a_separate_capability_from_issue_write(self, registry):
        """A topology can allow issue comments without allowing PRs."""
        assert registry.spec("github.pr.create").capability == "github.pr.write"
        assert registry.spec("github.issue.create").capability == "github.issue.write"

    def test_requires_a_repository_binding(self, registry):
        result = _run(registry, "github.issue.list", {}, ToolExecutionContext())
        assert not result.ok
        assert "no repository bound" in result.content


class TestGitLabIsNotHalfWired:
    def test_no_gitlab_tools_are_registered(self, registry):
        """Tools that would fail on the first call are worse than absent ones."""
        assert forge_tools.gitlab_available() is False
        assert not [tool for tool in registry.ids() if tool.startswith("gitlab.")]

    def test_the_stub_list_is_empty_rather_than_partial(self):
        assert forge_tools.GITLAB_SPECS == []


class TestWebProviderAbsent:
    def test_registers_nothing_while_no_provider_exists(self):
        reg = ToolRegistry()
        web_tools.register(reg)

        assert len(reg) == 0, (
            "a web tool that always errors invites retries; absent is the honest state"
        )

    def test_provider_availability_is_asked_not_assumed(self):
        assert web_tools.provider_available() is False

    def test_flow_graphs_still_advertise_web_tools(self):
        """The reason this module exists: the topologies name tools with no provider."""
        from gitpilot.topology_registry import TOPOLOGY_REGISTRY

        advertised = [
            topology.id
            for topology in TOPOLOGY_REGISTRY.values()
            if "WebSearch" in str(topology.flow_graph)
        ]
        assert advertised, "expected topologies naming WebSearch"


class TestDefaultRegistry:
    def test_assembles_every_namespace(self):
        reg = build_default_registry()
        namespaces = {tool.split(".", 1)[0] for tool in reg.ids()}

        assert {"fs", "terminal", "git", "github", "test"} <= namespaces
        assert "web" not in namespaces

    def test_each_build_is_independent(self):
        """Per-run registries: one session's tools cannot leak into another's."""
        first, second = build_default_registry(), build_default_registry()
        assert first is not second
        assert first.ids() == second.ids()

    def test_namespaces_can_be_withheld(self):
        reg = build_default_registry(include_git=False, include_forge=False)
        assert not [tool for tool in reg.ids() if tool.startswith(("git.", "github."))]

    def test_no_duplicate_ids_across_modules(self):
        reg = build_default_registry()
        assert len(reg.ids()) == len(set(reg.ids()))

    def test_capability_mask_can_withhold_push(self):
        """Asserted again with the real policy engine in Batch V4-D1."""
        reg = build_default_registry()
        render = reg.render(capabilities=capabilities_from({"fs.*", "git.read"}))
        offered = {schema["name"] for schema in render.schemas}

        assert "git.push" not in offered
        assert "git.status" in offered
        assert any(d.id == "git.push" and d.reason == "capability-not-granted" for d in render.dropped)

    def test_description_summary_is_serialisable(self):
        rows = describe_default_registry()
        assert {"id", "title", "capability", "risk", "effects", "signature"} <= set(rows[0])
        assert all(isinstance(row["effects"], list) for row in rows)

    def test_exported_from_public_api(self):
        """DoD for Batch V4-A1."""
        from gitpilot import public_api

        assert public_api.build_default_registry is build_default_registry
        for name in ("ToolSpec", "ToolRegistry", "ToolCall", "ToolResult",
                     "ToolExecutionContext", "ToolRisk", "ToolEffect",
                     "SchemaProfile", "LEGACY_ALIASES", "FLAG_TOOL_REGISTRY"):
            assert name in public_api.__all__, name
            assert hasattr(public_api, name), name

    def test_no_production_caller_yet(self):
        """Phase A ships the surface; Batch V4-C2 is what consumes it."""
        import subprocess

        out = subprocess.run(
            ["git", "grep", "-l", "-E", r"from \.toolkit|from gitpilot\.toolkit|import toolkit",
             "--", "gitpilot/"],
            capture_output=True, text=True,
        )
        callers = {line for line in out.stdout.split() if line}
        assert callers <= {"gitpilot/public_api/__init__.py"}, (
            f"unexpected production consumer of the registry: {callers}"
        )
