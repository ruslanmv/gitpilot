"""The default and lite policies — Batch V4-G2.

Two documents and one retirement. What these tests are for is the part that is
easy to get wrong: the documents exist, but nothing is rerouted by their
existence. ``default`` still runs the CrewAI dispatch it always has, and it stays
that way until the flip (Batch V4-G5, §15.4) — so "the document is authored" and
"the traffic moved" have to be separately true.
"""
from __future__ import annotations

import pytest

from gitpilot.agent.contracts import Dialect
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.runner import (
    DEFAULT_AGENTIC_TOPOLOGY_ID,
    FLAG_AGENT_LOOP_DEFAULT,
    PILOT_TOPOLOGY_ID,
    RunRequest,
    apply_topology_policy,
    should_use_loop,
)
from gitpilot.topology import registry as registry_mod
from gitpilot.topology.legacy import LEGACY_TOPOLOGIES
from gitpilot.topology.schema import DialectChoice, Engine, ExecutionStyle

ENGINEER = "default"   # Batch V4-G5 moved the document onto `default`


# ---------------------------------------------------------------------------
# The Autonomous Engineer
# ---------------------------------------------------------------------------


class TestAutonomousEngineer:
    def test_it_is_registered_as_a_policy_document(self):
        topology = registry_mod.get_topology(ENGINEER)

        assert topology is not None
        assert topology.policy is not None
        assert topology.policy.source == "builtin"
        assert topology.execution_style is ExecutionStyle.agentic_loop

    def test_it_runs_the_agentic_loop_in_the_dialect_the_model_can_speak(self):
        policy = registry_mod.get_policy(ENGINEER)

        assert policy.execution.engine is Engine.agentic_loop
        assert policy.execution.dialect is DialectChoice.auto

    def test_it_grants_the_full_safe_capability_set(self):
        """Reads outright, writes granted (that is the job), and anything that
        leaves a mark someone else sees asks first."""
        mask = registry_mod.get_policy(ENGINEER).capability_mask()

        for capability in ("fs.read", "fs.glob", "fs.grep", "fs.write", "fs.edit"):
            assert mask.allows(capability), capability
        for capability in ("fs.delete", "git.commit", "github.pr.create"):
            assert mask.qualifier(capability).force_ask is True, capability

    def test_it_never_pushes(self):
        """An autonomous run that can push can publish a mistake before anyone
        reads it. A topology that wants that has to say so."""
        mask = registry_mod.get_policy(ENGINEER).capability_mask()

        assert not mask.allows("git.push")
        assert "git.push" in mask.denied

    def test_it_runs_the_shell_without_network(self):
        qualifier = registry_mod.get_policy(ENGINEER).capability_mask().qualifier("terminal.run")

        assert qualifier is not None
        assert qualifier.network is False

    def test_it_delegates_one_level_deep(self):
        policy = registry_mod.get_policy(ENGINEER)

        assert policy.agent.subagents == ("explorer", "reviewer", "researcher", "test_analyst")
        assert policy.capability_mask().qualifier("agent.delegate").max_depth == 1

    def test_it_verifies_when_the_project_has_tests(self):
        policy = registry_mod.get_policy(ENGINEER)

        assert policy.verification.tests.value == "auto"
        assert policy.verification.max_fix_cycles == 3

    def test_its_graph_is_generated_from_the_document(self):
        graph = registry_mod.get_topology(ENGINEER).flow_graph
        node_ids = {node["id"] for node in graph["nodes"]}

        assert "main_agent" in node_ids
        assert {"subagent_explorer", "subagent_test_analyst"} <= node_ids
        for edge in graph["edges"]:
            assert edge["source"] in node_ids and edge["target"] in node_ids


# ---------------------------------------------------------------------------
# T2's retirement
# ---------------------------------------------------------------------------


class TestGitPilotCodeIsAnAlias:
    RETIRED = ("gitpilot_code", "autonomous_engineer")

    def test_it_is_no_longer_a_registry_entry(self):
        """The picker showed two cards for one idea, and only one of them had an
        executor behind it (§15.3, T2)."""
        for retired in self.RETIRED:
            assert retired not in registry_mod.TOPOLOGY_REGISTRY

    def test_a_saved_preference_still_resolves(self):
        for retired in self.RETIRED:
            assert registry_mod.resolve_id(retired) == ENGINEER
            assert registry_mod.get_topology(retired).id == ENGINEER

    def test_it_can_still_be_saved_as_a_preference(self, tmp_path, monkeypatch):
        """An id a user saved months ago must not start raising."""
        from gitpilot import settings as settings_mod

        monkeypatch.setattr(settings_mod, "CONFIG_DIR", tmp_path)
        registry_mod.save_topology_preference("gitpilot_code")

        assert registry_mod.get_saved_topology_preference() == "gitpilot_code"

    def test_the_picker_offers_one_card_not_three(self):
        listed = [entry["id"] for entry in registry_mod.list_topologies()]

        assert listed.count(ENGINEER) == 1
        for retired in self.RETIRED:
            assert retired not in listed

    def test_only_a_v1_entry_can_be_absorbed(self, tmp_path, monkeypatch, caplog):
        """Two documents fighting over one id would let load order decide which
        policy a user gets."""
        path = tmp_path / "topologies.yaml"
        path.write_text(
            "topologies:\n"
            "  - id: usurper\n"
            f"    aliases: [{ENGINEER}]\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(registry_mod, "USER_TOPOLOGIES_FILE", path)
        with caplog.at_level("WARNING"):
            registry_mod.refresh()
        try:
            assert ENGINEER in registry_mod.TOPOLOGY_REGISTRY, "a document was deleted"
            assert "usurper" in registry_mod.TOPOLOGY_REGISTRY
            assert "keeping both" in caplog.text
        finally:
            monkeypatch.undo()
            registry_mod.refresh()

    def test_the_v1_definition_is_still_in_the_legacy_module(self):
        """Retiring an id is a registry decision, not a deletion — the v1 entry
        stays readable until its whole family goes (Batch V4-H5)."""
        assert LEGACY_TOPOLOGIES["gitpilot_code"].policy is None

    def test_the_previous_default_is_reachable_as_classic(self):
        """A user who wants the old behaviour needs a name to ask for, and "the old
        default" is not a name (Batch V4-G5, §15.4)."""
        classic = registry_mod.get_topology("classic")

        assert classic is not None
        assert classic.policy is None
        assert classic.execution_style is ExecutionStyle.single_task
        assert classic.flow_graph == LEGACY_TOPOLOGIES["default"].flow_graph


# ---------------------------------------------------------------------------
# Lite Mode
# ---------------------------------------------------------------------------


class TestLiteMode:
    def test_it_is_the_same_loop_with_the_dialect_pinned(self):
        """Lite Mode used to be a separate planner universe; now it is a policy
        over the one engine (§6.4)."""
        policy = registry_mod.get_policy("lite_mode")

        assert policy.execution.engine is Engine.agentic_loop
        assert policy.execution.dialect is DialectChoice.lite

    def test_the_pin_reaches_the_profile(self, tmp_path):
        """A frontier model selected into Lite Mode is spoken to in LITE — that is
        what makes this an explicit override rather than a suggestion."""
        from gitpilot.agent.runner import AgentRunner

        request = apply_topology_policy(
            RunRequest(task="t", topology_id="lite_mode", journal_root=tmp_path),
        )
        profile = resolve_profile("claude", "claude-sonnet-4-5", cache_path=tmp_path / "c.json")
        assert profile.dialect is Dialect.NATIVE

        _loop, ctx = AgentRunner().build(request, provider=_FakeProvider(), profile=profile)

        assert ctx.model_profile.dialect is Dialect.LITE

    def test_it_does_not_delegate(self):
        """A 1.5B model spawning subagents multiplies the failure surface rather
        than dividing the work."""
        policy = registry_mod.get_policy("lite_mode")

        assert policy.agent.subagents == ()
        assert not policy.capability_mask().allows("agent.delegate")

    def test_its_budgets_are_tight(self):
        """A small model that has not converged in twelve turns is looping."""
        limits = registry_mod.get_policy("lite_mode").limits

        assert limits.max_iterations == 12
        assert limits.max_tool_calls == 30
        assert limits.max_runtime_seconds == 600

    def test_the_budgets_reach_the_run(self, tmp_path):
        from gitpilot.agent.runner import AgentRunner

        request = apply_topology_policy(
            RunRequest(task="t", topology_id="lite_mode", journal_root=tmp_path),
        )
        _loop, ctx = AgentRunner().build(
            request,
            provider=_FakeProvider(),
            profile=resolve_profile("ollama", "qwen2.5:1.5b", cache_path=tmp_path / "q.json"),
        )

        assert ctx.budgets.max_iterations == 12
        assert ctx.budgets.max_tool_calls == 30

    def test_it_fixes_at_most_once(self):
        assert registry_mod.get_policy("lite_mode").verification.max_fix_cycles == 1

    def test_it_does_not_offer_a_todo_tool(self):
        """The LITE dialect drives its own three-phase plan rather than asking the
        model to maintain one."""
        assert not registry_mod.get_policy("lite_mode").capability_mask().allows("todo.write")

    def test_auto_activation_still_belongs_to_the_profile(self, tmp_path):
        """The topology is the explicit override; a small model gets LITE anyway."""
        profile = resolve_profile("ollama", "qwen2.5:1.5b", cache_path=tmp_path / "q.json")

        assert profile.dialect is Dialect.LITE

    def test_the_lite_preference_still_reads_as_lite_mode(self, tmp_path, monkeypatch):
        """``_is_small_local_model`` keys on this string to pick the legacy lite
        planner; the retirement must not have moved it."""
        from gitpilot import settings as settings_mod

        monkeypatch.setattr(settings_mod, "CONFIG_DIR", tmp_path)
        registry_mod.save_topology_preference("lite_mode")

        assert registry_mod.get_saved_topology_preference() == "lite_mode"


# ---------------------------------------------------------------------------
# Authoring a document reroutes nobody
# ---------------------------------------------------------------------------


class TestNothingIsReroutedYet:
    def test_the_default_now_declares_the_agentic_engine(self):
        """Batch V4-G5. Declaring it is not the same as running it — see below."""
        default = registry_mod.get_topology("default")

        assert default.policy is not None
        assert default.execution_style is ExecutionStyle.agentic_loop

    def test_the_flag_is_off_so_the_declaration_changes_nothing_yet(self):
        """§15.4: the flip lands behind ``agent_loop_default``, after the §18
        benchmark gate. The document says what the engine should do; the flag says
        whether it does it."""
        from gitpilot.agent.runner import AGENT_LOOP_DEFAULT_ON
        import gitpilot.flags as flags

        assert AGENT_LOOP_DEFAULT_ON is False
        flags.clear_all_overrides()
        assert should_use_loop("default") is False

    @pytest.mark.parametrize("topology_id", [ENGINEER, "lite_mode", "classic"])
    def test_with_both_flags_off_nothing_reaches_the_loop(self, topology_id):
        assert should_use_loop(
            topology_id, flag_enabled=False, default_flag_enabled=False,
        ) is False

    @pytest.mark.parametrize("topology_id", [ENGINEER, "lite_mode"])
    def test_the_pilot_flag_alone_does_not_reroute_a_migrated_topology(self, topology_id):
        """``agent_loop`` is the pilot's flag. Turning it on must not change
        behaviour for a user who did not pick T9."""
        assert should_use_loop(
            topology_id, flag_enabled=True, default_flag_enabled=False,
        ) is False

    def test_the_pilot_still_routes_on_its_own_flag(self):
        assert should_use_loop(
            PILOT_TOPOLOGY_ID, flag_enabled=True, default_flag_enabled=False,
        ) is True

    @pytest.mark.parametrize("topology_id", [ENGINEER, "lite_mode", PILOT_TOPOLOGY_ID])
    def test_the_default_flag_routes_every_agentic_document(self, topology_id):
        assert should_use_loop(
            topology_id, flag_enabled=False, default_flag_enabled=True,
        ) is True

    def test_the_default_flag_does_not_reroute_an_unmigrated_topology(self):
        """The flag routes what a *document* declares. ``classic`` has none, which
        is the point of it: opting out has to actually opt out."""
        assert should_use_loop(
            "classic", flag_enabled=True, default_flag_enabled=True,
        ) is False

    def test_an_unknown_topology_never_reaches_the_loop(self):
        assert should_use_loop(
            "no_such_topology", flag_enabled=True, default_flag_enabled=True,
        ) is False

    def test_an_empty_topology_id_never_reaches_the_loop(self):
        assert should_use_loop("", flag_enabled=True, default_flag_enabled=True) is False

    def test_the_flag_names_are_stable(self):
        assert FLAG_AGENT_LOOP_DEFAULT == "agent_loop_default"
        assert DEFAULT_AGENTIC_TOPOLOGY_ID == ENGINEER

    def test_the_flag_is_read_from_the_flags_file_when_not_passed(self, monkeypatch):
        import gitpilot.flags as flags

        flags.clear_all_overrides()
        assert should_use_loop(ENGINEER) is False

        flags.set_override(FLAG_AGENT_LOOP_DEFAULT, True)
        try:
            assert should_use_loop(ENGINEER) is True
        finally:
            flags.clear_all_overrides()


class _FakeProvider:
    name = "fake"
    model = "fake-model"
    supports_native_tools = True
    supports_parallel_tool_calls = False
    supports_streaming = False

    async def complete(self, messages, **kwargs):  # pragma: no cover - never called
        raise NotImplementedError

    def stream(self, *args, **kwargs):  # pragma: no cover - never called
        raise NotImplementedError
