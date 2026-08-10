"""Tests for Claude-Code-on-Web parity endpoints.

Covers branch listing, environment CRUD, session message/diff/status,
and WebSocket streaming — all added in the Claude-Code-on-Web parity upgrade.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gitpilot.session import Session, SessionManager


# ============================================================================
# Branch Listing Endpoint
# ============================================================================

class TestBranchListingEndpoint:
    """Tests for GET /api/repos/{owner}/{repo}/branches."""

    @pytest.mark.asyncio
    async def test_list_branches_returns_sorted_list(self):
        """Should return branches sorted with default first."""
        from gitpilot.api import api_list_branches

        # Mock httpx.AsyncClient
        mock_repo_resp = MagicMock()
        mock_repo_resp.status_code = 200
        mock_repo_resp.json.return_value = {"default_branch": "main"}

        mock_branch_resp = MagicMock()
        mock_branch_resp.status_code = 200
        mock_branch_resp.json.return_value = [
            {"name": "feature-b", "protected": False, "commit": {"sha": "bbb"}},
            {"name": "main", "protected": True, "commit": {"sha": "aaa"}},
            {"name": "feature-a", "protected": False, "commit": {"sha": "ccc"}},
        ]
        mock_branch_resp.headers = {"Link": ""}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_repo_resp, mock_branch_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("gitpilot._api_app.get_github_token", return_value="ghp_test"),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await api_list_branches(
                owner="test-owner",
                repo="test-repo",
                page=1,
                per_page=100,
                query=None,
                authorization="Bearer ghp_test",
            )

        assert result.repository == "test-owner/test-repo"
        assert result.default_branch == "main"
        assert len(result.branches) == 3
        # Default branch should be first
        assert result.branches[0].name == "main"
        assert result.branches[0].is_default is True
        assert result.branches[0].protected is True
        # Rest sorted alphabetically
        assert result.branches[1].name == "feature-a"
        assert result.branches[2].name == "feature-b"

    @pytest.mark.asyncio
    async def test_list_branches_query_filter(self):
        """Should filter branches by query substring."""
        from gitpilot.api import api_list_branches

        mock_repo_resp = MagicMock()
        mock_repo_resp.status_code = 200
        mock_repo_resp.json.return_value = {"default_branch": "main"}

        mock_branch_resp = MagicMock()
        mock_branch_resp.status_code = 200
        mock_branch_resp.json.return_value = [
            {"name": "main", "protected": True, "commit": {"sha": "aaa"}},
            {"name": "feature-auth", "protected": False, "commit": {"sha": "bbb"}},
            {"name": "fix-login", "protected": False, "commit": {"sha": "ccc"}},
            {"name": "feature-auth-v2", "protected": False, "commit": {"sha": "ddd"}},
        ]
        mock_branch_resp.headers = {"Link": ""}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_repo_resp, mock_branch_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("gitpilot._api_app.get_github_token", return_value="ghp_test"),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await api_list_branches(
                owner="o",
                repo="r",
                page=1,
                per_page=100,
                query="auth",
                authorization="Bearer ghp_test",
            )

        assert len(result.branches) == 2
        assert result.branches[0].name == "feature-auth"
        assert result.branches[1].name == "feature-auth-v2"

    @pytest.mark.asyncio
    async def test_list_branches_no_token_raises_401(self):
        """Should raise 401 when no GitHub token is provided."""
        from fastapi import HTTPException

        from gitpilot.api import api_list_branches

        with patch("gitpilot._api_app.get_github_token", return_value=None):
            with pytest.raises(HTTPException) as exc_info:
                await api_list_branches(
                    owner="o", repo="r", page=1, per_page=100,
                    query=None, authorization=None,
                )
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_list_branches_has_more_pagination(self):
        """Should detect pagination via Link header."""
        from gitpilot.api import api_list_branches

        mock_repo_resp = MagicMock()
        mock_repo_resp.status_code = 200
        mock_repo_resp.json.return_value = {"default_branch": "main"}

        mock_branch_resp = MagicMock()
        mock_branch_resp.status_code = 200
        mock_branch_resp.json.return_value = [
            {"name": "main", "protected": False, "commit": {"sha": "abc"}},
        ]
        mock_branch_resp.headers = {
            "Link": '<https://api.github.com/repos/o/r/branches?page=2>; rel="next"'
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_repo_resp, mock_branch_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("gitpilot._api_app.get_github_token", return_value="ghp_test"),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await api_list_branches(
                owner="o", repo="r", page=1, per_page=100,
                query=None, authorization="Bearer ghp_test",
            )

        # The implementation now fetches ALL pages internally and returns
        # has_more=False. The loop breaks when len(page_data) < per_page (1 < 100).
        assert result.has_more is False

    @pytest.mark.asyncio
    async def test_list_branches_repo_error_raises(self):
        """Should raise HTTPException when repo is inaccessible."""
        from fastapi import HTTPException

        from gitpilot.api import api_list_branches

        mock_repo_resp = MagicMock()
        mock_repo_resp.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_repo_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("gitpilot._api_app.get_github_token", return_value="ghp_test"),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await api_list_branches(
                    owner="o", repo="r", page=1, per_page=100,
                    query=None, authorization="Bearer ghp_test",
                )
            assert exc_info.value.status_code == 404


# ============================================================================
# Environment CRUD Endpoints
# ============================================================================

class TestEnvironmentCRUD:
    """Tests for /api/environments CRUD operations."""

    @pytest.mark.asyncio
    async def test_list_environments_empty_returns_default(self, tmp_path):
        """Should return a default environment when none exist."""
        from gitpilot.api import api_list_environments

        with patch("gitpilot._api_app._ENV_ROOT", tmp_path):
            result = await api_list_environments()

        assert len(result.environments) == 1
        assert result.environments[0].name == "Default"
        assert result.environments[0].network_access == "limited"

    @pytest.mark.asyncio
    async def test_list_environments_reads_files(self, tmp_path):
        """Should read environment configs from JSON files."""
        from gitpilot.api import api_list_environments

        env1 = {"id": "env1", "name": "Dev", "network_access": "full", "env_vars": {"DEBUG": "1"}}
        env2 = {"id": "env2", "name": "Prod", "network_access": "none", "env_vars": {}}
        (tmp_path / "env1.json").write_text(json.dumps(env1))
        (tmp_path / "env2.json").write_text(json.dumps(env2))

        with patch("gitpilot._api_app._ENV_ROOT", tmp_path):
            result = await api_list_environments()

        assert len(result.environments) == 2
        names = {e.name for e in result.environments}
        assert names == {"Dev", "Prod"}

    @pytest.mark.asyncio
    async def test_list_environments_skips_corrupt_files(self, tmp_path):
        """Should skip malformed JSON files without crashing."""
        from gitpilot.api import api_list_environments

        env1 = {"id": "good", "name": "Good", "network_access": "limited", "env_vars": {}}
        (tmp_path / "good.json").write_text(json.dumps(env1))
        (tmp_path / "bad.json").write_text("NOT VALID JSON{{{")

        with patch("gitpilot._api_app._ENV_ROOT", tmp_path):
            result = await api_list_environments()

        assert len(result.environments) == 1
        assert result.environments[0].name == "Good"

    @pytest.mark.asyncio
    async def test_create_environment(self, tmp_path):
        """Should create a new environment config and persist it."""
        from gitpilot.api import EnvironmentConfig, api_create_environment

        config = EnvironmentConfig(name="Staging", network_access="full", env_vars={"NODE_ENV": "staging"})

        with patch("gitpilot._api_app._ENV_ROOT", tmp_path):
            result = await api_create_environment(config)

        assert result["name"] == "Staging"
        assert result["network_access"] == "full"
        assert result["id"] is not None
        # Verify file was written
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        saved = json.loads(files[0].read_text())
        assert saved["name"] == "Staging"

    @pytest.mark.asyncio
    async def test_create_environment_with_custom_id(self, tmp_path):
        """Should use the provided id if given."""
        from gitpilot.api import EnvironmentConfig, api_create_environment

        config = EnvironmentConfig(id="my-env", name="Custom", network_access="none")

        with patch("gitpilot._api_app._ENV_ROOT", tmp_path):
            result = await api_create_environment(config)

        assert result["id"] == "my-env"
        assert (tmp_path / "my-env.json").exists()

    @pytest.mark.asyncio
    async def test_update_environment(self, tmp_path):
        """Should update an existing environment config."""
        from gitpilot.api import EnvironmentConfig, api_update_environment

        # Pre-create an env file
        original = {"id": "env1", "name": "Old", "network_access": "limited", "env_vars": {}}
        (tmp_path / "env1.json").write_text(json.dumps(original))

        new_config = EnvironmentConfig(name="Updated", network_access="full", env_vars={"KEY": "val"})

        with patch("gitpilot._api_app._ENV_ROOT", tmp_path):
            result = await api_update_environment("env1", new_config)

        assert result["id"] == "env1"
        assert result["name"] == "Updated"
        assert result["network_access"] == "full"
        # Verify file was updated
        saved = json.loads((tmp_path / "env1.json").read_text())
        assert saved["name"] == "Updated"

    @pytest.mark.asyncio
    async def test_delete_environment_existing(self, tmp_path):
        """Should delete an existing environment config."""
        from gitpilot.api import api_delete_environment

        env = {"id": "env1", "name": "ToDelete", "network_access": "limited", "env_vars": {}}
        (tmp_path / "env1.json").write_text(json.dumps(env))

        with patch("gitpilot._api_app._ENV_ROOT", tmp_path):
            result = await api_delete_environment("env1")

        assert result["deleted"] is True
        assert not (tmp_path / "env1.json").exists()

    @pytest.mark.asyncio
    async def test_delete_environment_not_found(self, tmp_path):
        """Should raise 404 for non-existent environment."""
        from fastapi import HTTPException

        from gitpilot.api import api_delete_environment

        with patch("gitpilot._api_app._ENV_ROOT", tmp_path):
            with pytest.raises(HTTPException) as exc_info:
                await api_delete_environment("nonexistent")
            assert exc_info.value.status_code == 404


# ============================================================================
# Session Message / Diff / Status Endpoints
# ============================================================================

class TestSessionMessageEndpoints:
    """Tests for session message, diff, and status endpoints."""

    def _create_session(self, tmp_path: Path, **kwargs) -> tuple[SessionManager, Session]:
        """Helper to create a session with a temp-backed manager."""
        mgr = SessionManager(root=tmp_path)
        session = mgr.create(**kwargs)
        mgr.save(session)
        return mgr, session

    @pytest.mark.asyncio
    async def test_add_message_to_session(self, tmp_path):
        """Should add a message to the session and persist it."""
        from gitpilot.api import api_add_session_message

        mgr, session = self._create_session(tmp_path, repo_full_name="o/r", branch="main")

        with patch("gitpilot._api_app._session_mgr", mgr):
            result = await api_add_session_message(
                session.id,
                {"role": "user", "content": "Hello agent"},
            )

        assert result["message_count"] == 1
        # Verify persistence
        loaded = mgr.load(session.id)
        assert len(loaded.messages) == 1
        assert loaded.messages[0].role == "user"
        assert loaded.messages[0].content == "Hello agent"

    @pytest.mark.asyncio
    async def test_add_message_session_not_found(self, tmp_path):
        """Should raise 404 for non-existent session."""
        from fastapi import HTTPException

        from gitpilot.api import api_add_session_message

        mgr = SessionManager(root=tmp_path)

        with patch("gitpilot._api_app._session_mgr", mgr):
            with pytest.raises(HTTPException) as exc_info:
                await api_add_session_message(
                    "nonexistent-id",
                    {"role": "user", "content": "test"},
                )
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_session_messages(self, tmp_path):
        """Should return all messages for a session."""
        from gitpilot.api import api_get_session_messages

        mgr, session = self._create_session(tmp_path, repo_full_name="o/r")
        session.add_message("user", "hello")
        session.add_message("assistant", "hi there")
        mgr.save(session)

        with patch("gitpilot._api_app._session_mgr", mgr):
            result = await api_get_session_messages(session.id)

        assert result["session_id"] == session.id
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "user"
        assert result["messages"][0]["content"] == "hello"
        assert result["messages"][1]["role"] == "assistant"
        assert result["messages"][1]["content"] == "hi there"

    @pytest.mark.asyncio
    async def test_get_session_messages_not_found(self, tmp_path):
        """Should raise 404 for non-existent session."""
        from fastapi import HTTPException

        from gitpilot.api import api_get_session_messages

        mgr = SessionManager(root=tmp_path)

        with patch("gitpilot._api_app._session_mgr", mgr):
            with pytest.raises(HTTPException) as exc_info:
                await api_get_session_messages("nonexistent-id")
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_session_diff_default(self, tmp_path):
        """Should return empty diff when no diff metadata exists."""
        from gitpilot.api import api_get_session_diff

        mgr, session = self._create_session(tmp_path)

        with patch("gitpilot._api_app._session_mgr", mgr):
            result = await api_get_session_diff(session.id)

        assert result["session_id"] == session.id
        assert result["diff"]["files_changed"] == 0
        assert result["diff"]["additions"] == 0
        assert result["diff"]["deletions"] == 0
        assert result["diff"]["files"] == []

    @pytest.mark.asyncio
    async def test_get_session_diff_with_metadata(self, tmp_path):
        """Should return diff from session metadata when present."""
        from gitpilot.api import api_get_session_diff

        mgr, session = self._create_session(tmp_path)
        session.metadata["diff"] = {
            "files_changed": 3,
            "additions": 42,
            "deletions": 7,
            "files": ["src/a.py", "src/b.py", "tests/test_c.py"],
        }
        mgr.save(session)

        with patch("gitpilot._api_app._session_mgr", mgr):
            result = await api_get_session_diff(session.id)

        assert result["diff"]["files_changed"] == 3
        assert result["diff"]["additions"] == 42
        assert result["diff"]["deletions"] == 7
        assert len(result["diff"]["files"]) == 3

    @pytest.mark.asyncio
    async def test_get_session_diff_not_found(self, tmp_path):
        """Should raise 404 for non-existent session."""
        from fastapi import HTTPException

        from gitpilot.api import api_get_session_diff

        mgr = SessionManager(root=tmp_path)

        with patch("gitpilot._api_app._session_mgr", mgr):
            with pytest.raises(HTTPException) as exc_info:
                await api_get_session_diff("nonexistent-id")
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_session_status_valid(self, tmp_path):
        """Should update session status for all valid values."""
        from gitpilot.api import api_update_session_status

        for status in ("active", "paused", "completed", "failed", "waiting"):
            mgr, session = self._create_session(tmp_path)

            with patch("gitpilot._api_app._session_mgr", mgr):
                result = await api_update_session_status(
                    session.id,
                    {"status": status},
                )

            assert result["status"] == status
            # Verify persistence
            loaded = mgr.load(session.id)
            assert loaded.status == status

    @pytest.mark.asyncio
    async def test_update_session_status_invalid(self, tmp_path):
        """Should raise 400 for an invalid status value."""
        from fastapi import HTTPException

        from gitpilot.api import api_update_session_status

        mgr, session = self._create_session(tmp_path)

        with patch("gitpilot._api_app._session_mgr", mgr):
            with pytest.raises(HTTPException) as exc_info:
                await api_update_session_status(
                    session.id,
                    {"status": "invalid_status"},
                )
            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_update_session_status_not_found(self, tmp_path):
        """Should raise 404 for non-existent session."""
        from fastapi import HTTPException

        from gitpilot.api import api_update_session_status

        mgr = SessionManager(root=tmp_path)

        with patch("gitpilot._api_app._session_mgr", mgr):
            with pytest.raises(HTTPException) as exc_info:
                await api_update_session_status(
                    "nonexistent-id",
                    {"status": "active"},
                )
            assert exc_info.value.status_code == 404


# ============================================================================
# WebSocket Streaming Endpoint
# ============================================================================

class TestWebSocketEndpoint:
    """Tests for WS /ws/sessions/{session_id}."""

    def _create_session(self, tmp_path: Path, **kwargs) -> tuple[SessionManager, Session]:
        mgr = SessionManager(root=tmp_path)
        session = mgr.create(**kwargs)
        mgr.save(session)
        return mgr, session

    @pytest.mark.asyncio
    async def test_ws_session_not_found(self, tmp_path):
        """Should send error and close when session does not exist."""
        from gitpilot.api import session_websocket

        mgr = SessionManager(root=tmp_path)
        ws = AsyncMock()

        with patch("gitpilot._api_app._session_mgr", mgr):
            await session_websocket(ws, "nonexistent-id")

        ws.accept.assert_awaited_once()
        ws.send_json.assert_awaited_once()
        error_msg = ws.send_json.call_args[0][0]
        assert error_msg["type"] == "error"
        assert "not found" in error_msg["message"].lower()
        ws.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ws_sends_session_restored_on_connect(self, tmp_path):
        """Should send session_restored event after accepting connection."""
        from fastapi import WebSocketDisconnect

        from gitpilot.api import session_websocket

        mgr, session = self._create_session(tmp_path, repo_full_name="o/r", branch="main")
        session.add_message("user", "prior message")
        mgr.save(session)

        ws = AsyncMock()
        # Simulate disconnect after initial connection
        ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())

        with patch("gitpilot._api_app._session_mgr", mgr):
            await session_websocket(ws, session.id)

        ws.accept.assert_awaited_once()
        # First send_json call should be session_restored
        first_call = ws.send_json.call_args_list[0][0][0]
        assert first_call["type"] == "session_restored"
        assert first_call["session_id"] == session.id
        assert first_call["message_count"] == 1

    @pytest.mark.asyncio
    async def test_ws_ping_pong(self, tmp_path):
        """Should respond to ping with pong."""
        from fastapi import WebSocketDisconnect

        from gitpilot.api import session_websocket

        mgr, session = self._create_session(tmp_path, repo_full_name="o/r")

        ws = AsyncMock()
        ws.receive_json = AsyncMock(
            side_effect=[{"type": "ping"}, WebSocketDisconnect()]
        )

        with patch("gitpilot._api_app._session_mgr", mgr):
            await session_websocket(ws, session.id)

        # Should have sent: session_restored, pong
        sent_events = [call[0][0] for call in ws.send_json.call_args_list]
        assert sent_events[0]["type"] == "session_restored"
        assert sent_events[1]["type"] == "pong"

    @pytest.mark.asyncio
    async def test_ws_cancel_sets_waiting(self, tmp_path):
        """Should respond to cancel with status_change to waiting."""
        from fastapi import WebSocketDisconnect

        from gitpilot.api import session_websocket

        mgr, session = self._create_session(tmp_path, repo_full_name="o/r")

        ws = AsyncMock()
        ws.receive_json = AsyncMock(
            side_effect=[{"type": "cancel"}, WebSocketDisconnect()]
        )

        with patch("gitpilot._api_app._session_mgr", mgr):
            await session_websocket(ws, session.id)

        sent_events = [call[0][0] for call in ws.send_json.call_args_list]
        assert sent_events[0]["type"] == "session_restored"
        assert sent_events[1]["type"] == "status_change"
        assert sent_events[1]["status"] == "waiting"

    @pytest.mark.asyncio
    async def test_ws_user_message_triggers_agent_flow(self, tmp_path):
        """Should process user_message through the agent dispatch flow."""
        from fastapi import WebSocketDisconnect

        from gitpilot.api import session_websocket

        mgr, session = self._create_session(
            tmp_path, repo_full_name="owner/repo", branch="main"
        )

        ws = AsyncMock()
        ws.receive_json = AsyncMock(
            side_effect=[
                {"type": "user_message", "content": "Explain the code"},
                WebSocketDisconnect(),
            ]
        )

        mock_dispatch = AsyncMock(return_value={"answer": "Here is the explanation."})

        with (
            patch("gitpilot._api_app._session_mgr", mgr),
            patch("gitpilot._api_app.dispatch_request", mock_dispatch),
        ):
            await session_websocket(ws, session.id)

        sent_events = [call[0][0] for call in ws.send_json.call_args_list]
        types = [e["type"] for e in sent_events]

        # Expected flow: session_restored -> message_received -> status_change(active)
        #                -> agent_message -> status_change(waiting)
        assert "session_restored" in types
        assert "message_received" in types
        assert "agent_message" in types

        # Verify agent message content
        agent_msgs = [e for e in sent_events if e["type"] == "agent_message"]
        assert len(agent_msgs) == 1
        assert agent_msgs[0]["content"] == "Here is the explanation."

        # Verify dispatch was called with canonical signature
        mock_dispatch.assert_awaited_once_with(
            user_request="Explain the code",
            repo_full_name="owner/repo",
            branch_name="main",
        )

        # Verify user message was persisted
        loaded = mgr.load(session.id)
        assert any(m.role == "user" and m.content == "Explain the code" for m in loaded.messages)
        assert any(m.role == "assistant" and m.content == "Here is the explanation." for m in loaded.messages)

    @pytest.mark.asyncio
    async def test_ws_user_message_no_repo_sends_error(self, tmp_path):
        """Should notify client when session has no repo connected."""
        from fastapi import WebSocketDisconnect

        from gitpilot.api import session_websocket

        # Session with no repo_full_name
        mgr, session = self._create_session(tmp_path)

        ws = AsyncMock()
        ws.receive_json = AsyncMock(
            side_effect=[
                {"type": "user_message", "content": "do something"},
                WebSocketDisconnect(),
            ]
        )

        with patch("gitpilot._api_app._session_mgr", mgr):
            await session_websocket(ws, session.id)

        sent_events = [call[0][0] for call in ws.send_json.call_args_list]
        agent_msgs = [e for e in sent_events if e["type"] == "agent_message"]
        assert len(agent_msgs) == 1
        assert "not connected" in agent_msgs[0]["content"].lower()

    @pytest.mark.asyncio
    async def test_ws_agent_error_sends_error_event(self, tmp_path):
        """Should send error event when agent dispatch fails."""
        from fastapi import WebSocketDisconnect

        from gitpilot.api import session_websocket

        mgr, session = self._create_session(
            tmp_path, repo_full_name="owner/repo", branch="main"
        )

        ws = AsyncMock()
        ws.receive_json = AsyncMock(
            side_effect=[
                {"type": "user_message", "content": "test"},
                WebSocketDisconnect(),
            ]
        )

        mock_dispatch = AsyncMock(side_effect=RuntimeError("Agent crashed"))

        with (
            patch("gitpilot._api_app._session_mgr", mgr),
            patch("gitpilot._api_app.dispatch_request", mock_dispatch),
        ):
            await session_websocket(ws, session.id)

        sent_events = [call[0][0] for call in ws.send_json.call_args_list]
        error_msgs = [e for e in sent_events if e["type"] == "error"]
        assert len(error_msgs) == 1
        assert "Agent crashed" in error_msgs[0]["message"]


# ============================================================================
# Pydantic Models
# ============================================================================

class TestPydanticModels:
    """Tests for new Pydantic models used by the web parity endpoints."""

    def test_branch_info_defaults(self):
        from gitpilot.api import BranchInfo

        b = BranchInfo(name="main")
        assert b.name == "main"
        assert b.is_default is False
        assert b.protected is False
        assert b.commit_sha is None

    def test_branch_info_full(self):
        from gitpilot.api import BranchInfo

        b = BranchInfo(name="dev", is_default=True, protected=True, commit_sha="abc123")
        assert b.is_default is True
        assert b.protected is True
        assert b.commit_sha == "abc123"

    def test_branch_list_response(self):
        from gitpilot.api import BranchInfo, BranchListResponse

        resp = BranchListResponse(
            repository="o/r",
            default_branch="main",
            page=1,
            per_page=100,
            has_more=False,
            branches=[BranchInfo(name="main", is_default=True)],
        )
        assert resp.repository == "o/r"
        assert len(resp.branches) == 1

    def test_environment_config_defaults(self):
        from gitpilot.api import EnvironmentConfig

        cfg = EnvironmentConfig()
        assert cfg.id is None
        assert cfg.name == "Default"
        assert cfg.network_access == "limited"
        assert cfg.env_vars == {}

    def test_environment_config_full(self):
        from gitpilot.api import EnvironmentConfig

        cfg = EnvironmentConfig(
            id="test-id",
            name="Production",
            network_access="none",
            env_vars={"KEY": "value"},
        )
        assert cfg.id == "test-id"
        assert cfg.name == "Production"
        assert cfg.network_access == "none"
        assert cfg.env_vars == {"KEY": "value"}

    def test_environment_config_model_dump(self):
        from gitpilot.api import EnvironmentConfig

        cfg = EnvironmentConfig(id="e1", name="Dev", env_vars={"A": "1"})
        d = cfg.model_dump()
        assert d["id"] == "e1"
        assert d["name"] == "Dev"
        assert d["env_vars"] == {"A": "1"}
