"""Phase 0 hygiene — Batches V4-0A and V4-0D.

V4-0A removed a duplicated topology-resolution block in ``dispatch_request``
(the first computed ``_active_topology``; the second immediately overwrote it
with the same value after re-reading the preference file) and deleted the
orphaned ``gitpilot._api_core`` snapshot of the API module.

V4-0D gave the legacy session WebSocket a way to carry the caller's GitHub
token.  It previously passed none, so repo-mode chat over that socket ran on
whatever ``GITHUB_TOKEN`` the server process held.
"""
from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# V4-0A — dead code
# ---------------------------------------------------------------------------

class TestTopologyResolutionDedup:
    @pytest.mark.asyncio
    @patch("gitpilot.agentic._dispatch_pipeline", new_callable=AsyncMock)
    @patch("gitpilot.agentic.get_saved_topology_preference", return_value=None)
    async def test_preference_file_read_once(self, mock_pref, mock_pipeline):
        """The duplicate block read the saved preference a second time."""
        from gitpilot.agentic import dispatch_request

        mock_pipeline.return_value = {"topology_id": "feature_builder", "result": "ok"}

        await dispatch_request(
            "Add a feature", "owner/repo", token="fake", topology_id="feature_builder",
        )

        assert mock_pref.call_count == 1

    @pytest.mark.asyncio
    @patch("gitpilot.agentic._dispatch_pipeline", new_callable=AsyncMock)
    @patch("gitpilot.agentic._is_incompatible_model", return_value=False)
    @patch(
        "gitpilot.agentic.get_saved_topology_preference",
        return_value="feature_builder",
    )
    async def test_saved_preference_still_routes_to_its_pipeline(
        self, mock_pref, mock_incompatible, mock_pipeline,
    ):
        """Routing is unchanged when the topology comes from the saved preference.

        This is the path the deleted block re-derived: with no explicit
        ``topology_id`` the resolved id must still reach ``_dispatch_pipeline``.
        """
        from gitpilot.agentic import dispatch_request

        mock_pipeline.return_value = {"topology_id": "feature_builder", "result": "ok"}

        result = await dispatch_request("Add a feature", "owner/repo", token="fake")

        mock_pipeline.assert_awaited_once()
        assert mock_pipeline.call_args[0][0].id == "feature_builder"
        assert result["topology_id"] == "feature_builder"

    @pytest.mark.asyncio
    @patch("gitpilot.agentic._dispatch_pipeline", new_callable=AsyncMock)
    @patch("gitpilot.agentic._is_incompatible_model", return_value=False)
    @patch("gitpilot.agentic.get_saved_topology_preference", return_value="quick_fix")
    async def test_explicit_topology_beats_saved_preference(
        self, mock_pref, mock_incompatible, mock_pipeline,
    ):
        from gitpilot.agentic import dispatch_request

        mock_pipeline.return_value = {"topology_id": "bug_hunter", "result": "ok"}

        await dispatch_request(
            "Fix the crash", "owner/repo", token="fake", topology_id="bug_hunter",
        )

        assert mock_pipeline.call_args[0][0].id == "bug_hunter"


class TestOrphanModuleRemoved:
    def test_api_core_snapshot_is_gone(self):
        """A near-duplicate of the live API module invites editing the wrong file."""
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("gitpilot._api_core")

    def test_api_package_still_exposes_the_app(self):
        from gitpilot.api import app

        assert app is not None


# ---------------------------------------------------------------------------
# V4-0D — auth propagation
# ---------------------------------------------------------------------------

class _FakeWebSocket:
    """Minimal stand-in with real mappings for headers and query params."""

    def __init__(self, headers=None, query=None):
        self.headers = dict(headers or {})
        self.query_params = dict(query or {})


class TestWebSocketTokenResolution:
    def _resolve(self, **kwargs):
        from gitpilot._api_app import _ws_github_token

        return _ws_github_token(_FakeWebSocket(**kwargs))

    @pytest.mark.parametrize("header,expected", [
        ("Bearer ghp_abc", "ghp_abc"),
        ("token ghp_abc", "ghp_abc"),
        ("ghp_abc", "ghp_abc"),
    ])
    def test_authorization_header_forms(self, header, expected):
        assert self._resolve(headers={"authorization": header}) == expected

    def test_query_param_for_browsers(self):
        """The browser WebSocket API cannot set headers."""
        assert self._resolve(query={"token": "ghp_query"}) == "ghp_query"

    def test_header_wins_over_query(self):
        resolved = self._resolve(
            headers={"authorization": "Bearer ghp_header"},
            query={"token": "ghp_query"},
        )
        assert resolved == "ghp_header"

    def test_no_credentials_returns_none(self):
        """None is valid — the env fallback still serves single-user installs."""
        assert self._resolve() is None

    def test_blank_and_non_string_values_rejected(self):
        assert self._resolve(headers={"authorization": "   "}) is None
        assert self._resolve(query={"token": ""}) is None
        assert self._resolve(headers={"authorization": object()}) is None


class TestWebSocketTokenReachesDispatch:
    @pytest.mark.asyncio
    async def test_token_threaded_into_dispatch_and_context(self, tmp_path):
        from fastapi import WebSocketDisconnect

        from gitpilot.api import session_websocket
        from gitpilot.session import SessionManager

        mgr = SessionManager(root=tmp_path)
        session = mgr.create(repo_full_name="owner/repo", branch="main")
        mgr.save(session)

        seen: dict = {}

        async def fake_dispatch(**kwargs):
            # The tools resolve credentials through the context, not the kwarg,
            # so assert both are in place while the call is in flight.
            from gitpilot.github_api import _request_token

            seen["kwarg"] = kwargs.get("token")
            seen["context"] = _request_token.get()
            return {"answer": "ok"}

        ws = _FakeWebSocket(query={"token": "ghp_ws"})
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        ws.receive_json = AsyncMock(side_effect=[
            {"type": "user_message", "content": "Explain the code"},
            WebSocketDisconnect(),
        ])

        with (
            patch("gitpilot._api_app._session_mgr", mgr),
            patch("gitpilot._api_app.dispatch_request", fake_dispatch),
        ):
            await session_websocket(ws, session.id)

        assert seen["kwarg"] == "ghp_ws"
        assert seen["context"] == "ghp_ws"
