"""Filesystem tools and their parity with the CrewAI originals — Batch V4-A3."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

import gitpilot.local_tools as lt
from gitpilot.toolkit import (
    LocalWorkspace,
    RepoBinding,
    Risk,
    ToolCall,
    ToolExecutionContext,
    ToolRegistry,
)
from gitpilot.toolkit import fs as fs_tools

from .parity.fixture_repo import build_fixture_repo
from .parity.harness import ParityCase, assert_parity, run_case


@pytest.fixture()
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    fs_tools.register(reg)
    return reg


@pytest.fixture()
def local_repo(tmp_path):
    """A real git checkout plus the two context objects that address it."""
    ws = build_fixture_repo(tmp_path / "repo")
    ctx = ToolExecutionContext(workspace=LocalWorkspace(root=ws.path))
    return ws, ctx


def _run(registry, tool, arguments, ctx):
    return asyncio.run(
        registry.execute(ToolCall(id="t", tool=tool, arguments=arguments), ctx)
    )


# ---------------------------------------------------------------------------
# Local backend behaviour
# ---------------------------------------------------------------------------


class TestLocalBackend:
    def test_read_returns_framed_content(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "fs.read", {"path": "src/util.py"}, ctx)

        assert result.ok
        assert result.content == "Content of src/util.py:\n---\ndef helper():\n    return 1\n\n---"
        assert result.data["lines"] == 2

    def test_read_missing_file_is_a_correctable_failure(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "fs.read", {"path": "nope.py"}, ctx)

        assert not result.ok and result.error == "read_failed"
        assert result.content.startswith("Error reading nope.py:")

    def test_path_traversal_is_refused(self, registry, local_repo):
        """The workspace jail belongs to WorkspaceManager; the tool must not lose it."""
        _ws, ctx = local_repo
        result = _run(registry, "fs.read", {"path": "../../etc/passwd"}, ctx)

        assert not result.ok
        assert "Path traversal blocked" in result.content

    def test_list_uses_git_tracked_files(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "fs.list", {}, ctx)

        assert "src/app.py" in result.data["paths"]
        assert ".git/config" not in result.content

    def test_glob_spans_directories_with_double_star(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "fs.glob", {"pattern": "**/*.py"}, ctx)

        assert set(result.data["paths"]) == {"src/app.py", "src/util.py", "tests/test_app.py"}

    def test_glob_single_star_does_not_cross_a_slash(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "fs.glob", {"pattern": "*.py"}, ctx)

        assert result.data["paths"] == []

    def test_glob_truncation_is_reported(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "fs.glob", {"pattern": "**/*.py", "max_results": 1}, ctx)

        assert result.data["truncated"] is True
        assert "truncated" in result.content

    def test_grep_finds_a_symbol_with_line_numbers(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "fs.grep", {"pattern": "def greet"}, ctx)

        assert "src/app.py:4: def greet(name):" in result.content

    def test_grep_no_matches(self, registry, local_repo):
        _ws, ctx = local_repo
        result = _run(registry, "fs.grep", {"pattern": "zzz_nothing"}, ctx)

        assert result.content == "No matches found."

    def test_write_creates_parent_directories(self, registry, local_repo):
        ws, ctx = local_repo
        result = _run(registry, "fs.write", {"path": "new/deep/file.txt", "content": "hi\n"}, ctx)

        assert result.ok
        assert (ws.path / "new/deep/file.txt").read_text() == "hi\n"
        assert result.data["paths_written"] == ["new/deep/file.txt"]

    def test_edit_replaces_exact_string(self, registry, local_repo):
        ws, ctx = local_repo
        result = _run(registry, "fs.edit", {
            "path": "src/util.py",
            "old_string": "return 1",
            "new_string": "return 2",
        }, ctx)

        assert result.ok, result.content
        assert (ws.path / "src/util.py").read_text() == "def helper():\n    return 2\n"
        assert result.data["replacements"] == 1

    def test_edit_refuses_on_occurrence_mismatch_and_says_why(self, registry, local_repo):
        """The strict contract is the feature: it teaches the model to widen the match."""
        ws, ctx = local_repo
        result = _run(registry, "fs.edit", {
            "path": "docs/guide.md",
            "old_string": "greet",
            "new_string": "hello",
        }, ctx)

        assert not result.ok and result.error == "edit_conflict"
        assert result.content.startswith("Error:")
        assert "greet" in (ws.path / "docs/guide.md").read_text(), "file must be untouched"

    def test_edit_allows_an_expected_multiple(self, registry, local_repo):
        ws, ctx = local_repo
        result = _run(registry, "fs.edit", {
            "path": "docs/guide.md",
            "old_string": "greet",
            "new_string": "hello",
            "expected_occurrences": 2,
        }, ctx)

        assert result.ok and result.data["replacements"] == 2
        assert "greet" not in (ws.path / "docs/guide.md").read_text()

    def test_delete_reports_whether_anything_was_removed(self, registry, local_repo):
        ws, ctx = local_repo
        assert _run(registry, "fs.delete", {"path": "src/util.py"}, ctx).content == "Deleted: True"
        assert not (ws.path / "src/util.py").exists()
        assert _run(registry, "fs.delete", {"path": "src/util.py"}, ctx).content == "Deleted: False"


# ---------------------------------------------------------------------------
# GitHub backend behaviour
# ---------------------------------------------------------------------------


class TestGitHubBackend:
    @pytest.fixture()
    def repo_ctx(self):
        return ToolExecutionContext(
            repo=RepoBinding(owner="o", repo="r", token="tok", branch="main"),
        )

    def test_read_goes_through_the_api_with_the_bound_ref(self, registry, repo_ctx):
        with patch("gitpilot.github_api.get_file", new_callable=AsyncMock) as get_file:
            get_file.return_value = "print('x')\n"
            result = _run(registry, "fs.read", {"path": "a.py"}, repo_ctx)

        get_file.assert_awaited_once_with("o", "r", "a.py", token="tok", ref="main")
        assert result.content == "Content of a.py:\n---\nprint('x')\n\n---"

    def test_read_error_keeps_the_github_wording(self, registry, repo_ctx):
        """Two legacy surfaces worded this differently; both wordings are kept."""
        with patch("gitpilot.github_api.get_file", new_callable=AsyncMock) as get_file:
            get_file.side_effect = RuntimeError("404")
            result = _run(registry, "fs.read", {"path": "a.py"}, repo_ctx)

        assert result.content == "Error reading file a.py: 404"

    def test_list_renders_the_repository_header(self, registry, repo_ctx):
        with patch("gitpilot.github_api.get_repo_tree", new_callable=AsyncMock) as tree:
            tree.return_value = [{"path": "b.py"}, {"path": "a.py"}]
            result = _run(registry, "fs.list", {}, repo_ctx)

        assert result.content == "Repository: o/r (Branch: main)\nFiles:\n  - a.py\n  - b.py\n"

    def test_empty_repository_is_stated_not_errored(self, registry, repo_ctx):
        with patch("gitpilot.github_api.get_repo_tree", new_callable=AsyncMock) as tree:
            tree.return_value = []
            result = _run(registry, "fs.list", {}, repo_ctx)

        assert result.ok
        assert result.content == "Repository is empty - no files found. (Branch: main)"

    def test_write_commits_and_reports_the_sha(self, registry, repo_ctx):
        with patch("gitpilot.github_api.put_file", new_callable=AsyncMock) as put_file:
            put_file.return_value = {"commit_sha": "abcdef1234567890"}
            result = _run(registry, "fs.write", {
                "path": "a.py", "content": "x", "commit_message": "Add a",
            }, repo_ctx)

        put_file.assert_awaited_once_with("o", "r", "a.py", "x", "Add a", token="tok", branch="main")
        assert result.content == "File 'a.py' written successfully. Commit: abcdef12"
        assert result.data["commit_sha"] == "abcdef1234567890"

    def test_write_supplies_a_commit_message_when_the_model_omits_it(self, registry, repo_ctx):
        with patch("gitpilot.github_api.put_file", new_callable=AsyncMock) as put_file:
            put_file.return_value = {"commit_sha": "aa"}
            _run(registry, "fs.write", {"path": "a.py", "content": "x"}, repo_ctx)

        assert put_file.await_args[0][4] == "Update a.py"

    def test_edit_reads_modifies_and_commits(self, registry, repo_ctx):
        with (
            patch("gitpilot.github_api.get_file", new_callable=AsyncMock) as get_file,
            patch("gitpilot.github_api.put_file", new_callable=AsyncMock) as put_file,
        ):
            get_file.return_value = "value = 1\n"
            put_file.return_value = {"commit_sha": "deadbeefcafe"}
            result = _run(registry, "fs.edit", {
                "path": "a.py", "old_string": "1", "new_string": "2", "commit_message": "Bump",
            }, repo_ctx)

        assert put_file.await_args[0][3] == "value = 2\n"
        assert result.content.endswith("Commit: deadbeef")

    def test_grep_prefilters_paths_before_fetching_contents(self, registry, repo_ctx):
        """The pre-filter is the biggest cost saving on a GitHub-backed repo."""
        with (
            patch("gitpilot.github_api.get_repo_tree", new_callable=AsyncMock) as tree,
            patch("gitpilot.github_api.get_file", new_callable=AsyncMock) as get_file,
        ):
            tree.return_value = [{"path": "a.py"}, {"path": "b.md"}, {"path": "c.py"}]
            get_file.return_value = "needle here\n"
            result = _run(registry, "fs.grep", {"pattern": "needle", "path_pattern": "**/*.py"}, repo_ctx)

        fetched = sorted(c.args[2] for c in get_file.await_args_list)
        assert fetched == ["a.py", "c.py"], "b.md must never be downloaded"
        assert "Found 2 match(es)" in result.content

    def test_grep_reports_when_the_path_filter_matches_nothing(self, registry, repo_ctx):
        with patch("gitpilot.github_api.get_repo_tree", new_callable=AsyncMock) as tree:
            tree.return_value = [{"path": "a.md"}]
            result = _run(registry, "fs.grep", {"pattern": "x", "path_pattern": "**/*.py"}, repo_ctx)

        assert "No files matched path_pattern: **/*.py" in result.content

    def test_delete_reports_the_commit(self, registry, repo_ctx):
        with patch("gitpilot.github_api.delete_file", new_callable=AsyncMock) as delete:
            delete.return_value = {"commit_sha": "1234567890"}
            result = _run(registry, "fs.delete", {"path": "a.py", "commit_message": "Drop a"}, repo_ctx)

        assert result.content == "File 'a.py' deleted. Commit: 12345678"


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


class TestBackendSelection:
    def test_local_checkout_wins_when_both_bindings_exist(self, registry, tmp_path):
        """It is cheaper, more capable, and what the user is looking at."""
        ws = build_fixture_repo(tmp_path / "repo")
        ctx = ToolExecutionContext(
            workspace=LocalWorkspace(root=ws.path),
            repo=RepoBinding(owner="o", repo="r", token="tok", branch="main"),
        )

        with patch("gitpilot.github_api.get_file", new_callable=AsyncMock) as get_file:
            result = _run(registry, "fs.read", {"path": "src/util.py"}, ctx)

        get_file.assert_not_awaited()
        assert "def helper" in result.content

    def test_no_binding_at_all_is_a_clean_failure(self, registry):
        result = _run(registry, "fs.read", {"path": "a.py"}, ToolExecutionContext())
        assert not result.ok
        assert "no repository bound" in result.content


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------


class TestRiskClassification:
    def test_reads_are_safe_and_writes_need_approval(self, registry):
        assert registry.spec("fs.read").risk is Risk.SAFE
        assert registry.spec("fs.grep").risk is Risk.SAFE
        for tool in ("fs.write", "fs.edit", "fs.delete"):
            assert registry.spec(tool).risk is Risk.APPROVAL, tool

    def test_write_tools_declare_a_write_effect(self, registry):
        from gitpilot.toolkit import Effect

        assert Effect.WRITES_FS in registry.spec("fs.edit").effects
        assert Effect.WRITES_FS not in registry.spec("fs.read").effects


# ---------------------------------------------------------------------------
# Parity with the CrewAI tools
# ---------------------------------------------------------------------------

PARITY_CASES = [
    ParityCase(
        name="fs.read/local",
        tool="fs.read",
        arguments={"path": "src/app.py"},
        legacy=lambda ws: lt.read_local_file.func("src/app.py"),
    ),
    ParityCase(
        name="fs.read/local-missing",
        tool="fs.read",
        arguments={"path": "absent.py"},
        legacy=lambda ws: lt.read_local_file.func("absent.py"),
    ),
    ParityCase(
        name="fs.list/local",
        tool="fs.list",
        arguments={},
        legacy=lambda ws: lt.list_local_files.func("."),
    ),
    ParityCase(
        name="fs.list/local-subdir",
        tool="fs.list",
        arguments={"directory": "src"},
        legacy=lambda ws: lt.list_local_files.func("src"),
    ),
    ParityCase(
        name="fs.grep/local",
        tool="fs.grep",
        arguments={"pattern": "def greet"},
        legacy=lambda ws: lt.search_in_files.func("def greet", "."),
    ),
    ParityCase(
        name="fs.grep/local-no-match",
        tool="fs.grep",
        arguments={"pattern": "zzz_nothing"},
        legacy=lambda ws: lt.search_in_files.func("zzz_nothing", "."),
    ),
    ParityCase(
        name="fs.write/local",
        tool="fs.write",
        arguments={"path": "written.txt", "content": "payload\n"},
        legacy=lambda ws: lt.write_local_file.func("written.txt", "payload\n"),
    ),
    ParityCase(
        name="fs.delete/local-absent",
        tool="fs.delete",
        arguments={"path": "never-existed.txt"},
        legacy=lambda ws: lt.delete_local_file.func("never-existed.txt"),
    ),
]


@pytest.mark.parametrize("case", PARITY_CASES, ids=lambda c: c.name)
def test_registry_output_matches_the_crewai_tool(case, registry, tmp_path):
    """Byte-identical to what the model reads today — Phase A's exit criterion.

    Each case gets its own checkout so a mutating case cannot influence the next.
    """
    ws = build_fixture_repo(tmp_path / "repo")
    lt.set_active_workspace(ws)
    try:
        outcome = run_case(
            case, ws, registry, ToolExecutionContext(workspace=LocalWorkspace(root=ws.path)),
        )
    finally:
        lt._current_workspace = None

    assert_parity(outcome)
