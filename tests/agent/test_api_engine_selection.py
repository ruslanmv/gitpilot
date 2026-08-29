"""The v2 stream endpoint's engine selection — Batch V4-C3.

The claim this file has to earn is the narrow one: turning the ``agent_loop``
flag on changes behaviour for the pilot topology **and for nothing else**.  The
selector is unit-tested in ``test_runner_pilot.py``; what is checked here is that
the endpoint actually branches on it, and that the loop path carries the two
pieces of session housekeeping the legacy path does — persisting the assistant
turn and releasing the bus.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gitpilot import _api_app as api
from gitpilot.agent.providers.base import ProviderResponse, RawToolCall

from .parity_fixture import fixture_repo  # noqa: F401 — pytest fixture


class _Request:
    """The two things ``v2_chat_stream`` asks of a request."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _call(payload):
    return asyncio.run(api.v2_chat_stream(_Request(payload)))


# ---------------------------------------------------------------------------
# Branching
# ---------------------------------------------------------------------------


class TestEndpointSelection:
    def test_the_pilot_topology_reaches_the_loop(self):
        async def fake_loop(**kwargs):
            fake_loop.kwargs = kwargs
            return "loop-response"

        with patch.object(api, "_should_use_loop", return_value=True), \
             patch.object(api, "_stream_agent_loop", fake_loop), \
             patch.object(api, "_StreamingExecutor") as legacy:
            response = _call({"message": "hello", "topology_id": "tool_augmented_react"})

        assert response == "loop-response"
        legacy.assert_not_called()
        assert fake_loop.kwargs["user_message"] == "hello"

    def test_every_other_topology_still_gets_the_legacy_executor(self):
        called = []

        async def fake_loop(**kwargs):  # pragma: no cover - must not run
            called.append(kwargs)

        with patch.object(api, "_stream_agent_loop", fake_loop), \
             patch.object(api, "_StreamingExecutor") as legacy:
            _call({"message": "hello", "topology_id": "feature_builder"})

        assert called == []
        legacy.assert_called_once()

    def test_the_saved_preference_is_what_gets_consulted(self):
        """An unset ``topology_id`` must fall back to the stored default rather
        than silently meaning "no topology"."""
        seen = []

        with patch.object(api, "_get_saved_topology_pref", return_value="tool_augmented_react"), \
             patch.object(api, "_should_use_loop", side_effect=lambda t: seen.append(t) or False), \
             patch.object(api, "_StreamingExecutor"):
            _call({"message": "hello"})

        assert seen == ["tool_augmented_react"]

    def test_an_empty_message_is_still_rejected_first(self):
        with patch.object(api, "_should_use_loop", return_value=True) as selector:
            response = _call({"message": "", "topology_id": "tool_augmented_react"})

        assert response.status_code == 400
        selector.assert_not_called()


# ---------------------------------------------------------------------------
# What the loop path owes the session
# ---------------------------------------------------------------------------


class _Session:
    def __init__(self, path):
        self.repo_full_name = ""
        self.branch = None
        self.repo_root = str(path)
        self.messages = [SimpleNamespace(role="user", content="earlier")]
        self.added = []

    def add_message(self, role, content):
        self.added.append((role, content))


def _drain(response):
    async def consume():
        return [chunk async for chunk in response.body_iterator]

    return asyncio.run(consume())


@pytest.fixture
def _scripted():
    class _Provider:
        name = "fake"
        supports_native_tools = True
        supports_parallel_tool_calls = False
        supports_streaming = False
        model = "gpt-4o-mini"

        def __init__(self):
            self._responses = [
                ProviderResponse(tool_calls=[
                    RawToolCall(id="c1", name="fs.read", arguments={"path": "README.md"}),
                ]),
                ProviderResponse(text="it is a fixture repository"),
            ]

        async def complete(self, messages, *, tools=None, system=None, **opts):
            return self._responses.pop(0) if self._responses else ProviderResponse(text="done")

        def stream(self, *args, **kwargs):  # pragma: no cover
            raise NotImplementedError

    return _Provider()


class TestLoopStreamHousekeeping:
    def _run(self, session, fixture_repo, provider):
        from gitpilot.agent.model_profile import resolve_profile

        bus = api._get_bus("sess-loop")
        profile = resolve_profile(
            "openai", "gpt-4o-mini", cache_path=fixture_repo.path / ".probe.json",
        )
        real_run = api._runner().run

        async def run_with_doubles(request, **kwargs):
            return await real_run(request, provider=provider, profile=profile, **kwargs)

        with patch.object(api, "_session_mgr", MagicMock()), \
             patch.object(api._runner(), "run", run_with_doubles):
            response = asyncio.run(api._stream_agent_loop(
                bus=bus, session=session, session_id="sess-loop",
                user_message="what is this repo?", workspace=None,
                repo_full_name="", branch=None, token=None,
            ))
            return response, _drain(response)

    def test_the_answer_is_persisted_to_the_transcript(self, fixture_repo, _scripted):
        session = _Session(fixture_repo.path)

        self._run(session, fixture_repo, _scripted)

        assert session.added == [("assistant", "it is a fixture repository")]

    def test_the_bus_is_released_when_the_stream_ends(self, fixture_repo, _scripted):
        session = _Session(fixture_repo.path)

        self._run(session, fixture_repo, _scripted)

        from gitpilot.agent_events import _session_buses

        assert "sess-loop" not in _session_buses

    def test_the_run_is_streamed_as_sse(self, fixture_repo, _scripted):
        session = _Session(fixture_repo.path)

        _response, chunks = self._run(session, fixture_repo, _scripted)

        body = "".join(chunks)
        assert body.startswith("data: ")
        types = [
            json.loads(line[len("data: "):])["type"]
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        assert "tool_start" in types
        assert types[-1] in ("done", "error")

    def test_a_session_with_no_github_repo_is_not_turned_away(
        self, fixture_repo, _scripted,
    ):
        """The legacy executor closes the stream immediately for these — the exact
        local-model case this engine exists for."""
        session = _Session(fixture_repo.path)
        session.repo_full_name = ""

        _response, chunks = self._run(session, fixture_repo, _scripted)

        assert any("tool_start" in chunk for chunk in chunks)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestCancel:
    def test_cancel_falls_through_to_the_runner(self):
        """The loop path does not register in ``_active_executors``; the endpoint
        tries that dict first and then asks the runner."""
        runner = MagicMock()
        runner.cancel.return_value = True

        with patch.object(api, "_agent_runner", runner):
            result = asyncio.run(api.v2_agent_cancel(_Request({"session_id": "s"})))

        assert result["engine"] == "agent_loop"
        runner.cancel.assert_called_once_with("s")

    def test_a_legacy_executor_still_wins(self):
        executor = MagicMock()
        runner = MagicMock()

        with patch.dict(api._active_executors, {"s": executor}, clear=True), \
             patch.object(api, "_agent_runner", runner):
            result = asyncio.run(api.v2_agent_cancel(_Request({"session_id": "s"})))

        assert result["status"] == "cancelled"
        assert "engine" not in result
        executor.cancel.assert_called_once()
        runner.cancel.assert_not_called()
