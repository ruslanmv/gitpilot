# tests/agent/test_loop_call_ids.py
"""Call ids are unique within a run, whatever the dialect minted.

Found by the first real-model benchmark run (local 8B tier, REACT_TEXT): the
dialect mints ``react_0`` for every turn, and the replay-safe registry keys its
idempotency ledger on the call id — so after one ``fs.edit`` the corrected retry
came back "idempotency_key was already used for different arguments" and the
run could never write again. The loop now re-keys a repeated id before
dispatch; the journal, the events and the ledger all see the unique one.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from gitpilot import idempotency
from gitpilot.agent import runner as runner_mod
from gitpilot.agent.journal import read_journal
from gitpilot.agent.loop import _with_unique_id
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.providers.base import ProviderResponse
from gitpilot.agent.runner import AgentRunner, RunRequest
from gitpilot.idempotency import IdempotencyStore
from gitpilot.toolkit.registry import ToolCall

from .parity_fixture import fixture_repo  # noqa: F401 — pytest fixture


class _ReactProvider:
    """A REACT_TEXT model that answers from a script."""

    name = "ollama"
    supports_native_tools = False
    supports_parallel_tool_calls = False
    supports_streaming = False

    def __init__(self, texts):
        self.model = "llama3:8b"
        self._texts = list(texts)

    async def complete(self, messages, *, tools=None, system=None, **opts):  # noqa: ARG002
        return ProviderResponse(text=self._texts.pop(0))

    def stream(self, *args, **kwargs):  # pragma: no cover - not used here
        raise NotImplementedError


def _react(tool: str, arguments: dict) -> str:
    return f"Thought: next step\nAction: {tool}\nAction Input: {json.dumps(arguments)}"


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """The ledger the runtime registry consults, kept out of the user's home."""
    store = IdempotencyStore(tmp_path / "idem.sqlite3")
    monkeypatch.setattr(idempotency, "_default_store", store)
    return store


@pytest.fixture
def request_settings_win(monkeypatch):
    """Let the request's own ``verification="off"`` stand.

    ``AgentRunner.run`` applies the topology's policy over the request, and every
    shipped topology verifies with tests when files changed — which would keep
    asking the scripted model for a test run after the two writes this test is
    about.
    """
    def unchanged(request, workspace=None):  # noqa: ARG001 - the policy hook's signature
        return request

    monkeypatch.setattr(runner_mod, "apply_topology_policy", unchanged)


class TestUniqueIds:
    def test_a_fresh_id_is_left_alone(self):
        call = ToolCall(id="react_0", tool="fs.read", arguments={"path": "a"})
        assert _with_unique_id(call, set()) is call

    def test_a_repeated_id_gets_the_next_free_suffix(self):
        call = ToolCall(id="react_0", tool="fs.write", arguments={"path": "b"})
        used = {"react_0", "react_0-2"}

        renamed = _with_unique_id(call, used)

        assert renamed.id == "react_0-3"
        assert (renamed.tool, renamed.arguments) == (call.tool, call.arguments)


@pytest.mark.usefixtures("isolated_store", "request_settings_win")
class TestAReactRunCanWriteTwice:
    def test_both_writes_land_and_the_journal_tells_them_apart(self, tmp_path, fixture_repo):  # noqa: F811
        provider = _ReactProvider([
            _react("fs.write", {"path": "first.txt", "content": "one\n"}),
            _react("fs.write", {"path": "second.txt", "content": "two\n"}),
            "Final Answer: wrote both files",
        ])

        result = asyncio.run(AgentRunner().run(
            RunRequest(
                task="write first.txt and second.txt",
                session_id="sess-call-ids",
                workspace_root=fixture_repo.path,
                journal_root=tmp_path / "journals",
                session_mode="auto",
                approvals="none",
                # Two writes and a final answer are the whole script; a
                # verification pass would ask the model for more.
                verification="off",
            ),
            provider=provider,
            profile=resolve_profile("ollama", "llama3:8b", cache_path=tmp_path / "c.json"),
        ))

        assert result.status == "completed", result.answer
        assert (fixture_repo.path / "first.txt").read_text() == "one\n"
        assert (fixture_repo.path / "second.txt").read_text() == "two\n"

        lines = list(read_journal(result.context.journal.path))
        call_ids = [line["call_id"] for line in lines if line.get("type") == "tool_call"]
        assert call_ids == ["react_0", "react_0-2"]

        results = [line for line in lines if line.get("type") == "tool_result"]
        contents = [r.get("content") for r in results]
        assert [r.get("ok") for r in results] == [True, True], contents
        assert not any("idempotency_key" in str(c) for c in contents)
