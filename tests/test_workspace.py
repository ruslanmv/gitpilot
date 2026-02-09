"""Tests for gitpilot.workspace module."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gitpilot.workspace import WorkspaceInfo, WorkspaceManager


# ---------------------------------------------------------------------------
# Helper to build a fake _run_git result
# ---------------------------------------------------------------------------

def _git_result(stdout="", stderr="", returncode=0):
    """Create a mock result matching the _Result interface."""
    r = MagicMock()
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


# ---------------------------------------------------------------------------
# WorkspaceManager initialisation
# ---------------------------------------------------------------------------

class TestWorkspaceManagerInit:
    def test_default_root_created(self, tmp_path):
        mgr = WorkspaceManager(root=tmp_path / "ws")
        assert mgr.root.exists()

    def test_workspace_path(self, tmp_path):
        mgr = WorkspaceManager(root=tmp_path)
        assert mgr.workspace_path("owner", "repo") == tmp_path / "owner" / "repo"


# ---------------------------------------------------------------------------
# ensure_workspace
# ---------------------------------------------------------------------------

class TestEnsureWorkspace:
    async def test_clones_when_no_git_dir(self, tmp_path):
        mgr = WorkspaceManager(root=tmp_path)
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _git_result("main")
            ws = await mgr.ensure_workspace("owner", "repo", token="tok")

        assert ws.owner == "owner"
        assert ws.repo == "repo"
        assert ws.branch == "main"
        # First call should be clone
        first_call_cmd = mock_git.call_args_list[0][0][0]
        assert "clone" in first_call_cmd

    async def test_fetches_when_git_dir_exists(self, tmp_path):
        mgr = WorkspaceManager(root=tmp_path)
        ws_path = tmp_path / "owner" / "repo" / ".git"
        ws_path.mkdir(parents=True)

        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _git_result("main")
            ws = await mgr.ensure_workspace("owner", "repo", token="tok")

        first_call_cmd = mock_git.call_args_list[0][0][0]
        assert "fetch" in first_call_cmd

    async def test_explicit_branch(self, tmp_path):
        mgr = WorkspaceManager(root=tmp_path)
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _git_result()
            ws = await mgr.ensure_workspace("o", "r", token="t", branch="dev")

        assert ws.branch == "dev"

    async def test_workspace_stored_in_active(self, tmp_path):
        mgr = WorkspaceManager(root=tmp_path)
        with patch.object(mgr, "_run_git", new_callable=AsyncMock) as mock_git:
            mock_git.return_value = _git_result("main")
            await mgr.ensure_workspace("o", "r", token="t")

        assert "o/r" in mgr._active


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    async def test_cleanup_removes_directory(self, tmp_path):
        mgr = WorkspaceManager(root=tmp_path)
        ws_path = tmp_path / "owner" / "repo"
        ws_path.mkdir(parents=True)
        mgr._active["owner/repo"] = MagicMock()

        assert await mgr.cleanup("owner", "repo") is True
        assert not ws_path.exists()
        assert "owner/repo" not in mgr._active

    async def test_cleanup_nonexistent_returns_false(self, tmp_path):
        mgr = WorkspaceManager(root=tmp_path)
        assert await mgr.cleanup("no", "thing") is False


# ---------------------------------------------------------------------------
# File operations
# ---------------------------------------------------------------------------

class TestFileOperations:
    def _make_ws(self, tmp_path):
        ws_path = tmp_path / "workspace"
        ws_path.mkdir()
        return WorkspaceInfo(
            owner="o", repo="r", path=ws_path,
            branch="main", remote_url="https://example.com",
        )

    async def test_read_file(self, tmp_path):
        ws = self._make_ws(tmp_path)
        (ws.path / "hello.txt").write_text("hello world")
        mgr = WorkspaceManager(root=tmp_path)
        content = await mgr.read_file(ws, "hello.txt")
        assert content == "hello world"

    async def test_write_file(self, tmp_path):
        ws = self._make_ws(tmp_path)
        mgr = WorkspaceManager(root=tmp_path)
        result = await mgr.write_file(ws, "sub/new.txt", "data")
        assert result["path"] == "sub/new.txt"
        assert result["size"] == 4
        assert (ws.path / "sub" / "new.txt").read_text() == "data"

    async def test_delete_file(self, tmp_path):
        ws = self._make_ws(tmp_path)
        (ws.path / "gone.txt").write_text("bye")
        mgr = WorkspaceManager(root=tmp_path)
        assert await mgr.delete_file(ws, "gone.txt") is True
        assert not (ws.path / "gone.txt").exists()

    async def test_delete_missing_file(self, tmp_path):
        ws = self._make_ws(tmp_path)
        mgr = WorkspaceManager(root=tmp_path)
        assert await mgr.delete_file(ws, "nope.txt") is False

    async def test_path_traversal_blocked(self, tmp_path):
        ws = self._make_ws(tmp_path)
        mgr = WorkspaceManager(root=tmp_path)
        with pytest.raises(PermissionError, match="Path traversal"):
            await mgr.read_file(ws, "../../etc/passwd")


# ---------------------------------------------------------------------------
# _parse_status
# ---------------------------------------------------------------------------

class TestParseStatus:
    def test_parses_branch_and_changes(self):
        raw = (
            "# branch.head main\n"
            "1 .M N... 100644 100644 abc def file.py\n"
            "1 A. N... 000000 100644 000 abc new.py\n"
            "? untracked.txt\n"
        )
        result = WorkspaceManager._parse_status(raw)
        assert result["branch"] == "main"
        assert "file.py" in result["modified"]
        assert "new.py" in result["added"]
        assert "untracked.txt" in result["untracked"]
        assert result["clean"] is False

    def test_clean_repo(self):
        raw = "# branch.head main\n"
        result = WorkspaceManager._parse_status(raw)
        assert result["clean"] is True


# ---------------------------------------------------------------------------
# _run_git error handling
# ---------------------------------------------------------------------------

class TestRunGit:
    async def test_run_git_raises_on_failure(self, tmp_path):
        mgr = WorkspaceManager(root=tmp_path)
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"fatal: not a repo")
        mock_proc.returncode = 128

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="Git command failed"):
                await mgr._run_git(["git", "status"], cwd=tmp_path)

    async def test_run_git_no_check(self, tmp_path):
        mgr = WorkspaceManager(root=tmp_path)
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"out", b"err")
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await mgr._run_git(["git", "status"], cwd=tmp_path, check=False)
        assert result.returncode == 1
        assert result.stdout == "out"
