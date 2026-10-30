# tests/test_legacy_ws_deprecation.py
"""Batch V4-H4: the legacy ``/ws/sessions`` protocol is frozen and deprecated.

The web app no longer speaks it; it stays served for one release cycle for
clients that have not moved, and announces that once per process the way
``docs/API_STABILITY.md`` §3 prescribes — a ``DeprecationWarning`` in the fixed
template, plus one log line, because a server's warnings filter usually hides
the former.
"""
from __future__ import annotations

import logging
import warnings
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import WebSocketDisconnect

from gitpilot import _api_app, _deprecation
from gitpilot.session import SessionManager

_KEY = _api_app.LEGACY_WS_DEPRECATION_KEY


@pytest.fixture
def fresh_process_state(monkeypatch):
    """As if nothing had connected yet in this process."""
    with _deprecation._LOCK:
        _deprecation._WARNED.discard(_KEY)
    monkeypatch.setattr(_api_app, "_legacy_ws_logged", False)
    yield
    with _deprecation._LOCK:
        _deprecation._WARNED.discard(_KEY)


def _session(tmp_path):
    mgr = SessionManager(root=tmp_path)
    session = mgr.create(repo_full_name="o/r", branch="main")
    mgr.save(session)
    return mgr, session


async def _connect(mgr, session_id):
    ws = AsyncMock()
    ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())
    with patch("gitpilot._api_app._session_mgr", mgr):
        await _api_app.session_websocket(ws, session_id)
    return ws


@pytest.mark.usefixtures("fresh_process_state")
class TestTheLegacySocketIsDeprecated:
    async def test_the_first_connection_warns_once_and_the_protocol_still_works(self, tmp_path):
        mgr, session = _session(tmp_path)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            first = await _connect(mgr, session.id)
            second = await _connect(mgr, session.id)

        ours = [
            w for w in caught
            if issubclass(w.category, DeprecationWarning) and "/ws/sessions" in str(w.message)
        ]
        assert len(ours) == 1, "once per process, not once per connection"
        assert "/api/v2/chat/stream" in str(ours[0].message)

        # Frozen, not broken: the handshake is what it always was.
        for ws in (first, second):
            ws.accept.assert_awaited_once()
            handshake = ws.send_json.call_args_list[0][0][0]
            assert handshake["type"] == "session_restored"
            assert handshake["session_id"] == session.id

    async def test_the_log_says_it_once_too(self, tmp_path, caplog):
        mgr, session = _session(tmp_path)

        with (
            caplog.at_level(logging.WARNING, logger=_api_app.logger.name),
            warnings.catch_warnings(),
        ):
            warnings.simplefilter("ignore", DeprecationWarning)
            await _connect(mgr, session.id)
            await _connect(mgr, session.id)

        lines = [
            record for record in caplog.records
            if "/ws/sessions" in record.getMessage() and "deprecated" in record.getMessage()
        ]
        assert len(lines) == 1

    def test_the_message_follows_the_api_stability_template(self):
        """``<old> is deprecated; use <new> instead (will be removed in vX.Y)``."""
        message = _api_app.LEGACY_WS_DEPRECATION

        assert message.startswith("/ws/sessions/{session_id} is deprecated; use ")
        assert " instead (will be removed in v" in message
        assert message.endswith(")")
