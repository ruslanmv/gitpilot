"""Tests for gitpilot.local_tools module."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gitpilot.workspace import WorkspaceInfo

import gitpilot.local_tools as lt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ws(tmp_path: Path) -> WorkspaceInfo:
    ws_path = tmp_path / "workspace"
    ws_path.mkdir(parents=True, exist_ok=True)
    return WorkspaceInfo(
        owner="test", repo="repo", path=ws_path,
        branch="main", remote_url="https://example.com",
    )


# ---------------------------------------------------------------------------
# set / get active workspace
# ---------------------------------------------------------------------------

class TestActiveWorkspace:
    def test_set_and_get(self, tmp_path):
        ws = _make_ws(tmp_path)
        lt.set_active_workspace(ws)
        assert lt.get_active_workspace() is ws

    def test_get_returns_none_initially(self):
        lt._current_workspace = None
        assert lt.get_active_workspace() is None


# ---------------------------------------------------------------------------
# _require_workspace
# ---------------------------------------------------------------------------

class TestRequireWorkspace:
    def test_raises_when_no_workspace(self):
        lt._current_workspace = None
        with pytest.raises(RuntimeError, match="No active workspace"):
            lt._require_workspace()

    def test_returns_workspace_when_set(self, tmp_path):
        ws = _make_ws(tmp_path)
        lt._current_workspace = ws
        assert lt._require_workspace() is ws


# ---------------------------------------------------------------------------
# read_local_file tool
# ---------------------------------------------------------------------------

class TestReadLocalFile:
    def test_success(self, tmp_path):
        ws = _make_ws(tmp_path)
        lt._current_workspace = ws

        with patch.object(lt._ws_manager, "read_file", new_callable=AsyncMock) as mock_read:
            mock_read.return_value = "file content"
            result = lt.read_local_file.func("test.py")

        assert "file content" in result
        assert "test.py" in result

    def test_error_handling(self, tmp_path):
        ws = _make_ws(tmp_path)
        lt._current_workspace = ws

        with patch.object(lt._ws_manager, "read_file", new_callable=AsyncMock) as mock_read:
            mock_read.side_effect = FileNotFoundError("not found")
            result = lt.read_local_file.func("missing.py")

        assert "Error" in result


# ---------------------------------------------------------------------------
# write_local_file tool
# ---------------------------------------------------------------------------

class TestWriteLocalFile:
    def test_success(self, tmp_path):
        ws = _make_ws(tmp_path)
        lt._current_workspace = ws

        with patch.object(lt._ws_manager, "write_file", new_callable=AsyncMock) as mock_write:
            mock_write.return_value = {"path": "out.py", "size": 42}
            result = lt.write_local_file.func("out.py", "print('hi')")

        assert "42" in result
        assert "out.py" in result

    def test_error_handling(self, tmp_path):
        ws = _make_ws(tmp_path)
        lt._current_workspace = ws

        with patch.object(lt._ws_manager, "write_file", new_callable=AsyncMock) as mock_write:
            mock_write.side_effect = PermissionError("blocked")
            result = lt.write_local_file.func("secret.pem", "data")

        assert "Error" in result


# ---------------------------------------------------------------------------
# delete_local_file tool
# ---------------------------------------------------------------------------

class TestDeleteLocalFile:
    def test_success(self, tmp_path):
        ws = _make_ws(tmp_path)
        lt._current_workspace = ws

        with patch.object(lt._ws_manager, "delete_file", new_callable=AsyncMock) as mock_del:
            mock_del.return_value = True
            result = lt.delete_local_file.func("old.py")

        assert "True" in result


# ---------------------------------------------------------------------------
# list_local_files tool
# ---------------------------------------------------------------------------

class TestListLocalFiles:
    def test_returns_files(self, tmp_path):
        ws = _make_ws(tmp_path)
        lt._current_workspace = ws

        with patch.object(lt._ws_manager, "list_files", new_callable=AsyncMock) as mock_ls:
            mock_ls.return_value = ["a.py", "b.py"]
            result = lt.list_local_files.func(".")

        assert "a.py" in result
        assert "b.py" in result

    def test_empty_directory(self, tmp_path):
        ws = _make_ws(tmp_path)
        lt._current_workspace = ws

        with patch.object(lt._ws_manager, "list_files", new_callable=AsyncMock) as mock_ls:
            mock_ls.return_value = []
            result = lt.list_local_files.func(".")

        assert "No files found" in result


# ---------------------------------------------------------------------------
# search_in_files tool
# ---------------------------------------------------------------------------

class TestSearchInFiles:
    def test_returns_matches(self, tmp_path):
        ws = _make_ws(tmp_path)
        lt._current_workspace = ws

        with patch.object(lt._ws_manager, "search_files", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [
                {"file": "foo.py", "line": 10, "content": "match here"},
            ]
            result = lt.search_in_files.func("match")

        assert "foo.py:10" in result

    def test_no_matches(self, tmp_path):
        ws = _make_ws(tmp_path)
        lt._current_workspace = ws

        with patch.object(lt._ws_manager, "search_files", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = []
            result = lt.search_in_files.func("nothing")

        assert "No matches found" in result


# ---------------------------------------------------------------------------
# git_diff tool
# ---------------------------------------------------------------------------

class TestGitDiff:
    def test_returns_diff(self, tmp_path):
        ws = _make_ws(tmp_path)
        lt._current_workspace = ws

        with patch.object(lt._ws_manager, "diff", new_callable=AsyncMock) as mock_diff:
            mock_diff.return_value = "+new line"
            result = lt.git_diff.func("false")

        assert "+new line" in result

    def test_no_changes(self, tmp_path):
        ws = _make_ws(tmp_path)
        lt._current_workspace = ws

        with patch.object(lt._ws_manager, "diff", new_callable=AsyncMock) as mock_diff:
            mock_diff.return_value = ""
            result = lt.git_diff.func("false")

        assert "No changes" in result


# ---------------------------------------------------------------------------
# run_command tool
# ---------------------------------------------------------------------------

class TestRunCommand:
    def test_blocked_command(self, tmp_path):
        ws = _make_ws(tmp_path)
        lt._current_workspace = ws

        result = lt.run_command.func("rm -rf /")
        assert "Permission denied" in result

    def test_successful_command(self, tmp_path):
        """``run_command`` now prefers ``_run_via_sandbox`` (which talks
        to the configured backend through ``/api/sandbox/run``); the
        legacy ``_executor.execute`` path is only the import-error
        fallback.  Force the sandbox path to raise ``_SandboxFallback``
        so the test exercises the same TerminalExecutor mock contract
        it always did, then mock the executor as before."""
        ws = _make_ws(tmp_path)
        lt._current_workspace = ws

        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = "OK"
        mock_result.stderr = ""
        mock_result.timed_out = False
        mock_result.truncated = False

        with patch.object(lt, "_run_via_sandbox", side_effect=lt._SandboxFallback("forced")), \
             patch.object(lt._executor, "execute", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_result
            result = lt.run_command.func("echo hello")

        assert "Exit code: 0" in result
        assert "OK" in result
