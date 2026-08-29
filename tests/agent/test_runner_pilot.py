"""Runner and the T9 pilot wiring — Batch V4-C3."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from gitpilot.agent.journal import read_seed
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.providers.base import ProviderResponse, RawToolCall
from gitpilot.agent.runner import (
    PILOT_TOPOLOGY_ID,
    AgentRunner,
    RunRequest,
    should_use_loop,
)

from .parity_fixture import fixture_repo  # noqa: F401 — pytest fixture


class _ScriptedProvider:
    """Answers with a queued script of responses."""

    name = "fake"
    supports_native_tools = True
    supports_parallel_tool_calls = False
    supports_streaming = False

    def __init__(self, responses):
        self.model = "gpt-4o-mini"
        self._responses = list(responses)
        self.calls = 0

    async def complete(self, messages, *, tools=None, system=None, **opts):
        self.calls += 1
        if not self._responses:
            return ProviderResponse(text="done")
        return self._responses.pop(0)

    def stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


def _profile(tmp_path):
    return resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "c.json")


# ---------------------------------------------------------------------------
# Engine selection
# ---------------------------------------------------------------------------


class TestEngineSelection:
    def test_the_pilot_topology_with_the_flag_on(self):
        assert should_use_loop(PILOT_TOPOLOGY_ID, flag_enabled=True) is True

    @pytest.mark.parametrize("topology", [
        "default", "gitpilot_code", "feature_builder", "lite_mode", "",
    ])
    def test_every_other_topology_keeps_the_legacy_path(self, topology):
        """Enabling the flag must not change behaviour for a user who did not
        pick the pilot topology."""
        assert should_use_loop(topology, flag_enabled=True) is False

    def test_the_flag_off_means_legacy_even_for_the_pilot(self):
        assert should_use_loop(PILOT_TOPOLOGY_ID, flag_enabled=False) is False

    def test_the_flag_defaults_to_off(self):
        from gitpilot.agent.loop import FLAG_AGENT_LOOP
        from gitpilot.flags import is_on

        assert is_on(FLAG_AGENT_LOOP, default=False) is False


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


class TestRunner:
    def test_a_read_only_run_answers_from_what_it_finds(self, tmp_path, fixture_repo):
        provider = _ScriptedProvider([
            ProviderResponse(tool_calls=[
                RawToolCall(id="c1", name="fs.read", arguments={"path": "README.md"}),
            ]),
            ProviderResponse(text="It is a fixture repository."),
        ])

        result = asyncio.run(AgentRunner().run(
            RunRequest(
                task="what is this repo?",
                session_id="sess-1",
                workspace_root=fixture_repo.path,
                journal_root=tmp_path / "journals",
                read_only=True,
            ),
            provider=provider, profile=_profile(tmp_path),
        ))

        assert result.status == "completed"
        assert result.answer == "It is a fixture repository."
        assert result.context.files_read == {"README.md"}

    def test_the_journal_lands_under_the_session(self, tmp_path, fixture_repo):
        result = asyncio.run(AgentRunner().run(
            RunRequest(
                task="explain", session_id="sess-2",
                workspace_root=fixture_repo.path, journal_root=tmp_path / "journals",
                transcript_index=3,
            ),
            provider=_ScriptedProvider([ProviderResponse(text="answered")]),
            profile=_profile(tmp_path),
        ))

        journal_path = result.context.journal.path
        assert journal_path.parent == tmp_path / "journals" / "sess-2" / "runs"

        seed = read_seed(journal_path)
        assert seed.task == "explain"
        assert seed.transcript_index == 3
        assert seed.topology_id == PILOT_TOPOLOGY_ID

    def test_prior_conversation_is_seeded_before_the_task(self, tmp_path, fixture_repo):
        _loop, ctx = AgentRunner().build(
            RunRequest(
                task="and now this", session_id="s",
                workspace_root=fixture_repo.path, journal_root=tmp_path / "j",
                prior_messages=[{"role": "user", "content": "earlier question"}],
            ),
            provider=_ScriptedProvider([]), profile=_profile(tmp_path),
        )

        assert [m["content"] for m in ctx.messages] == ["earlier question", "and now this"]

    def test_git_tools_are_withheld_without_a_checkout(self, tmp_path):
        """Offering them would spend a turn discovering they cannot work."""
        _loop, ctx = AgentRunner().build(
            RunRequest(task="x", session_id="s", journal_root=tmp_path / "j"),
            provider=_ScriptedProvider([]), profile=_profile(tmp_path),
        )

        registry = ctx.tool_context.extras["registry"]
        assert not [tool for tool in registry.ids() if tool.startswith("git.")]

    def test_max_iterations_is_honoured(self, tmp_path, fixture_repo):
        provider = _ScriptedProvider([
            ProviderResponse(tool_calls=[
                RawToolCall(id=f"c{i}", name="fs.read", arguments={"path": "README.md"}),
            ])
            for i in range(10)
        ])

        result = asyncio.run(AgentRunner().run(
            RunRequest(
                task="loop forever", session_id="s",
                workspace_root=fixture_repo.path, journal_root=tmp_path / "j",
                max_iterations=2,
            ),
            provider=provider, profile=_profile(tmp_path),
        ))

        assert result.status == "budget_exceeded"
        assert result.context.iteration == 2

    def test_cancel_reaches_a_live_run(self, tmp_path, fixture_repo):
        runner = AgentRunner()
        runner.build(
            RunRequest(
                task="x", session_id="cancel-me",
                workspace_root=fixture_repo.path, journal_root=tmp_path / "j",
            ),
            provider=_ScriptedProvider([]), profile=_profile(tmp_path),
        )

        assert runner.active("cancel-me") is True
        assert runner.cancel("cancel-me") is True
        assert runner.cancel("no-such-session") is False


# ---------------------------------------------------------------------------
# Event bridging
# ---------------------------------------------------------------------------


def _drain(bus, queue):
    seen = []
    while not queue.empty():
        seen.append(queue.get_nowait())
    return seen


class TestEventBridging:
    def test_loop_events_reach_the_session_bus(self, tmp_path, fixture_repo):
        from gitpilot.agent_events import AgentEventBus

        bus = AgentEventBus()
        seen = []

        async def drive():
            _sub_id, queue = bus.subscribe()
            await AgentRunner().run(
                RunRequest(
                    task="explain", session_id="sess-3",
                    workspace_root=fixture_repo.path, journal_root=tmp_path / "j",
                ),
                bus=bus,
                provider=_ScriptedProvider([
                    ProviderResponse(tool_calls=[
                        RawToolCall(id="c1", name="fs.read", arguments={"path": "README.md"}),
                    ]),
                    ProviderResponse(text="the answer"),
                ]),
                profile=_profile(tmp_path),
            )
            seen.extend(_drain(bus, queue))

        asyncio.run(drive())

        types = [event.type.value for event in seen]
        assert "tool_start" in types
        assert "tool_result" in types
        assert "text_delta" in types

    def test_events_without_a_renderer_surface_as_status_changes(self, tmp_path, fixture_repo):
        """Better a visible status line than silently dropping an event."""
        from gitpilot.agent_events import AgentEventBus

        bus = AgentEventBus()
        seen = []

        async def drive():
            _sub_id, queue = bus.subscribe()
            await AgentRunner().run(
                RunRequest(
                    task="explain", session_id="sess-4",
                    workspace_root=fixture_repo.path, journal_root=tmp_path / "j",
                ),
                bus=bus, provider=_ScriptedProvider([ProviderResponse(text="hi")]),
                profile=_profile(tmp_path),
            )
            seen.extend(_drain(bus, queue))

        asyncio.run(drive())

        statuses = [
            event.data.get("status") for event in seen if event.type.value == "status_change"
        ]
        assert "run_started" in statuses
        assert "iteration" in statuses

    def test_a_dead_bus_does_not_kill_the_run(self, tmp_path, fixture_repo):
        class _Dead:
            async def emit(self, event):
                raise RuntimeError("client gone")

        result = asyncio.run(AgentRunner().run(
            RunRequest(
                task="x", session_id="s",
                workspace_root=fixture_repo.path, journal_root=tmp_path / "j",
            ),
            bus=_Dead(), provider=_ScriptedProvider([ProviderResponse(text="fine")]),
            profile=_profile(tmp_path),
        ))

        assert result.status == "completed"


# ---------------------------------------------------------------------------
# Folder sessions
# ---------------------------------------------------------------------------


class TestFolderSessions:
    def test_a_session_with_no_github_repo_still_runs(self, tmp_path, fixture_repo):
        """The legacy executor refuses these outright and closes the stream —
        exactly the local-model case this engine exists for."""
        provider = _ScriptedProvider([
            ProviderResponse(tool_calls=[
                RawToolCall(id="c1", name="fs.glob", arguments={"pattern": "**/*.py"}),
            ]),
            ProviderResponse(text="three python files"),
        ])

        result = asyncio.run(AgentRunner().run(
            RunRequest(
                task="what python files are here?", session_id="folder-1",
                workspace_root=fixture_repo.path, repo=None,
                journal_root=tmp_path / "j",
            ),
            provider=provider, profile=_profile(tmp_path),
        ))

        assert result.status == "completed"
        assert result.answer == "three python files"

    def test_forge_tools_are_withheld_without_a_repo_binding(self, tmp_path, fixture_repo):
        _loop, ctx = AgentRunner().build(
            RunRequest(
                task="x", session_id="s", workspace_root=fixture_repo.path,
                journal_root=tmp_path / "j",
            ),
            provider=_ScriptedProvider([]), profile=_profile(tmp_path),
        )

        registry = ctx.tool_context.extras["registry"]
        assert not [tool for tool in registry.ids() if tool.startswith("github.")]


# ---------------------------------------------------------------------------
# The API surface
# ---------------------------------------------------------------------------


class TestAssembly:
    def test_the_flag_is_consulted_when_no_answer_is_passed_in(self):
        with patch("gitpilot.flags.is_on", return_value=True) as is_on:
            assert should_use_loop(PILOT_TOPOLOGY_ID) is True
        assert is_on.call_args.args[0] == "agent_loop"

    def test_a_provider_is_built_from_settings_when_none_is_given(self, tmp_path):
        with patch("gitpilot.agent.providers.build_provider") as build:
            build.return_value = _ScriptedProvider([])
            AgentRunner().build(
                RunRequest(task="x", session_id="s", journal_root=tmp_path / "j", model="gpt-4o"),
                profile=_profile(tmp_path),
            )
        build.assert_called_once_with(model="gpt-4o")

    def test_a_lite_run_is_given_the_real_file_list(self, tmp_path, fixture_repo):
        """A LITE model names paths in prose; the loop validates each against this
        list, which is what stops a hallucinated path from being applied."""
        profile = resolve_profile("ollama", "qwen2.5:1.5b", cache_path=tmp_path / "c.json")
        loop, _ctx = AgentRunner().build(
            RunRequest(
                task="x", session_id="s", workspace_root=fixture_repo.path,
                journal_root=tmp_path / "j",
            ),
            provider=_ScriptedProvider([]), profile=profile,
        )

        known = getattr(loop.adapter, "state", None)
        assert known is not None, "the lite adapter should have been prepared"
        assert "README.md" in set(known.known_files)


class TestPolicyFlag:
    """Rule 2 of the programme: flag off = byte-identical behaviour.

    With ``agent_policy`` off the runner must assemble exactly what Phase C
    assembled — the permissive stub and the permissive mask — so turning the flag
    off is a real revert rather than a third behaviour.
    """

    def _built(self, tmp_path, fixture_repo, **overrides):
        request = RunRequest(
            task="x", session_id="s", workspace_root=fixture_repo.path,
            journal_root=tmp_path / "j", **overrides,
        )
        return AgentRunner().build(
            request, provider=_ScriptedProvider([]), profile=_profile(tmp_path),
        )

    def test_the_flag_defaults_to_off(self):
        from gitpilot.agent.policy import FLAG_AGENT_POLICY
        from gitpilot.flags import is_on

        assert is_on(FLAG_AGENT_POLICY, default=False) is False

    def test_off_gives_the_phase_c_stub_and_an_open_mask(self, tmp_path, fixture_repo):
        from gitpilot.agent.loop import PermissivePolicy
        from gitpilot.toolkit import ALL_CAPABILITIES

        loop, ctx = self._built(tmp_path, fixture_repo)

        assert isinstance(loop.policy, PermissivePolicy)
        assert ctx.capabilities is ALL_CAPABILITIES

    def test_on_gives_the_real_engine_and_a_resolved_mask(self, tmp_path, fixture_repo):
        from gitpilot.agent.policy import CapabilityMask, PolicyEngine

        with patch("gitpilot.agent.runner._policy_enabled", return_value=True):
            loop, ctx = self._built(tmp_path, fixture_repo)

        assert isinstance(loop.policy, PolicyEngine)
        assert isinstance(ctx.capabilities, CapabilityMask)

    def test_on_asks_through_the_registry(self, tmp_path, fixture_repo):
        from gitpilot.agent.approvals import LoopApprovals

        with patch("gitpilot.agent.runner._policy_enabled", return_value=True):
            loop, _ctx = self._built(tmp_path, fixture_repo)

        assert isinstance(loop.approvals, LoopApprovals)

    def test_a_headless_run_denies_rather_than_hanging(self, tmp_path, fixture_repo):
        from gitpilot.agent.loop import DenyingApprovals

        loop, _ctx = self._built(tmp_path, fixture_repo, approvals="none")

        assert isinstance(loop.approvals, DenyingApprovals)

    def test_the_session_mode_is_what_resolves(self, tmp_path, fixture_repo):
        """A request may restrict it and may never raise it (§8.1, F11)."""
        from gitpilot.permissions import PermissionMode

        with patch("gitpilot.agent.runner._policy_enabled", return_value=True):
            loop, _ctx = self._built(
                tmp_path, fixture_repo, session_mode="normal", requested_mode="auto",
            )
        assert loop.policy.policy.mode is PermissionMode.NORMAL

        with patch("gitpilot.agent.runner._policy_enabled", return_value=True):
            loop, _ctx = self._built(
                tmp_path, fixture_repo, session_mode="auto", requested_mode="plan",
            )
        assert loop.policy.policy.mode is PermissionMode.PLAN

    def test_a_read_only_request_says_so_in_the_prompt(self, tmp_path, fixture_repo):
        with patch("gitpilot.agent.runner._policy_enabled", return_value=True):
            _loop, ctx = self._built(tmp_path, fixture_repo, session_mode="plan")

        assert "read-only" in ctx.system_payload

    def test_a_project_with_hooks_gets_them(self, tmp_path, fixture_repo):
        from gitpilot.agent.hook_bridge import LoopHooks

        with patch("gitpilot.agent.runner._policy_enabled", return_value=True):
            loop, _ctx = self._built(tmp_path, fixture_repo)

        assert isinstance(loop.hooks, LoopHooks)

    def test_a_session_manager_supplies_the_checkpointer(self, tmp_path, fixture_repo):
        from gitpilot.agent.checkpointing import SessionCheckpointer
        from gitpilot.session import SessionManager

        manager = SessionManager(root=tmp_path / "sessions")
        loop, _ctx = self._built(tmp_path, fixture_repo, session_manager=manager)

        assert isinstance(loop.checkpointer, SessionCheckpointer)

    def test_no_session_manager_means_no_checkpointer(self, tmp_path, fixture_repo):
        loop, _ctx = self._built(tmp_path, fixture_repo)
        assert loop.checkpointer is None


class TestFileListing:
    def test_a_checkout_is_listed_through_git(self, tmp_path, fixture_repo):
        from gitpilot.agent.runner import _known_files

        listed = _known_files(RunRequest(task="x", workspace_root=fixture_repo.path))

        assert "README.md" in listed

    def test_a_plain_folder_falls_back_to_walking_the_tree(self, tmp_path):
        from gitpilot.agent.runner import _known_files

        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "a.py").write_text("x = 1\n")

        listed = _known_files(RunRequest(task="x", workspace_root=tmp_path))

        assert listed == ["sub/a.py"]

    def test_a_missing_git_binary_does_not_stop_the_run(self, tmp_path):
        from gitpilot.agent.runner import _known_files

        (tmp_path / "a.py").write_text("x = 1\n")
        with patch("subprocess.run", side_effect=FileNotFoundError("no git")):
            assert _known_files(RunRequest(task="x", workspace_root=tmp_path)) == ["a.py"]

    def test_no_workspace_means_no_list(self):
        from gitpilot.agent.runner import _known_files

        assert _known_files(RunRequest(task="x")) == []


class TestEventDescriptions:
    """Events with no first-class renderer still have to say something."""

    @pytest.mark.parametrize(("event_type", "payload", "expected"), [
        ("iteration", {"n": 3}, "step 3"),
        ("dialect_downgraded", {"to": "lite"}, "switched to the lite dialect"),
        ("tools_pruned", {"count": 4}, "4 tool(s) withheld"),
        ("run_started", {"model": "qwen2.5:7b"}, "running on qwen2.5:7b"),
        ("something_new", {}, ""),
    ])
    def test_each_known_event_reads_as_a_sentence(self, event_type, payload, expected):
        from gitpilot.agent.runner import _describe

        assert _describe(event_type, payload) == expected

    def test_an_unrenderable_event_is_downgraded_rather_than_dropped(self, caplog):
        """A factory that does not match its payload is our bug — and the loop's
        emitter swallows exceptions, so it has to be logged loudly here or it
        would vanish entirely."""
        import logging

        from gitpilot.agent.runner import _bus_bridge

        seen = []

        class _Bus:
            async def emit(self, event):
                seen.append(event)

        with patch.dict(
            "gitpilot.agent.runner._EVENT_FACTORIES",
            {"tool_start": lambda p: (_ for _ in ()).throw(KeyError("args"))},
        ), caplog.at_level(logging.WARNING, logger="gitpilot.agent.runner"):
            asyncio.run(_bus_bridge(_Bus())("tool_start", {}))

        assert [event.type.value for event in seen] == ["status_change"]
        assert "could not render tool_start" in caplog.text


class TestApiWiring:
    def test_the_endpoint_exposes_the_loop_helpers(self):
        from gitpilot import _api_app

        assert callable(_api_app._should_use_loop)
        assert callable(_api_app._stream_agent_loop)
        assert "_agent_runner" in vars(_api_app)

    def test_selection_is_delegated_rather_than_duplicated(self):
        """One call, so the endpoint cannot drift from should_use_loop."""
        from gitpilot import _api_app

        with patch("gitpilot.agent.runner.should_use_loop", return_value=True) as selector:
            assert _api_app._should_use_loop("tool_augmented_react") is True
        selector.assert_called_once_with("tool_augmented_react")

    def test_a_missing_agent_package_falls_back_to_legacy(self):
        """A stripped runtime must still serve the existing path."""
        from gitpilot import _api_app

        with patch("gitpilot.agent.runner.should_use_loop", side_effect=ImportError):
            assert _api_app._should_use_loop("tool_augmented_react") is False
