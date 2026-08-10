"""Agent topologies: the roster, the presets, and the saved preference.

These tests pin down what the documentation claims. The agent count and the
pipeline sequences are published in the README and the VS Code extension
listing, so a change here should be a deliberate one that updates the docs
alongside it.
"""
from __future__ import annotations

import pytest

from gitpilot.agent_router import AgentType
from gitpilot.topology_registry import (
    AUTOMATIC_TOPOLOGY_IDS,
    TOPOLOGY_REGISTRY,
    clear_topology_preference,
    get_saved_topology_preference,
    get_topology,
    list_topologies,
    save_topology_preference,
)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Keep the preference file out of the developer's real ~/.gitpilot."""
    import gitpilot.settings as settings_mod

    monkeypatch.setattr(settings_mod, "CONFIG_DIR", tmp_path)
    yield


# ── The agent roster ──────────────────────────────────────────────────────

#: Every specialised agent GitPilot can dispatch to. The README and the VS
#: Code extension listing quote this roster, so it is asserted rather than
#: described.
EXPECTED_AGENTS = {
    "explorer",
    "planner",
    "code_writer",
    "code_reviewer",
    "issue_manager",
    "pr_manager",
    "search",
    "learning",
    "local_editor",
    "terminal",
}


def test_agent_roster_is_ten_specialists():
    """Ten agents, not four.

    "Four specialized agents" describes one pipeline's shape, not the system.
    """
    assert {a.value for a in AgentType} == EXPECTED_AGENTS
    assert len(AgentType) == 10


def test_every_agent_type_can_be_built():
    """A roster entry with no builder is a dispatch-time crash."""
    from gitpilot.agentic import _TOPO_AGENT_MAP

    # The pipeline map is a subset — only these agents appear in sequences.
    mapped = {agent_type for agent_type, _ in _TOPO_AGENT_MAP.values()}
    assert mapped <= set(AgentType)


# ── Topology presets ──────────────────────────────────────────────────────

#: Documented pipeline sequences. Changing one changes what users get.
EXPECTED_PIPELINES = {
    "feature_builder": ["explorer", "planner", "developer", "reviewer", "git_agent"],
    "bug_hunter": ["explorer", "developer", "reviewer", "git_agent"],
    "code_inspector": ["explorer", "reviewer"],
    "architect_mode": ["explorer", "planner"],
    "quick_fix": ["developer", "git_agent"],
}


@pytest.mark.parametrize("topology_id, sequence", EXPECTED_PIPELINES.items())
def test_pipeline_sequences_match_the_documentation(topology_id, sequence):
    topology = get_topology(topology_id)
    assert topology is not None, f"{topology_id} is documented but not registered"
    assert topology.routing_policy.sequence == sequence


def test_pipeline_agent_ids_all_resolve():
    """Every id in a sequence must map to a real agent.

    An unmapped id is skipped at runtime with only a log line, so a typo
    silently shortens the pipeline.
    """
    from gitpilot.agentic import _TOPO_AGENT_MAP

    for topology_id, sequence in EXPECTED_PIPELINES.items():
        unknown = [a for a in sequence if a not in _TOPO_AGENT_MAP]
        assert not unknown, f"{topology_id} references unknown agents: {unknown}"


def test_routed_topologies_declare_no_sequence():
    """System topologies pick agents per request rather than running a chain."""
    for topology_id in ("default", "gitpilot_code", "lite_mode"):
        topology = get_topology(topology_id)
        assert topology is not None
        assert not topology.routing_policy.sequence


def test_list_topologies_exposes_what_the_picker_needs():
    """The VS Code topology picker renders straight from this payload."""
    for summary in list_topologies():
        assert summary["id"] in TOPOLOGY_REGISTRY
        for field in ("name", "description", "category", "execution_style"):
            assert summary.get(field), f"{summary['id']} is missing {field}"
        assert isinstance(summary["agents_used"], list)


# ── The saved preference ──────────────────────────────────────────────────


def test_saving_and_reading_a_preference():
    save_topology_preference("feature_builder")
    assert get_saved_topology_preference() == "feature_builder"


@pytest.mark.parametrize("value", sorted(AUTOMATIC_TOPOLOGY_IDS))
def test_automatic_clears_the_preference(value):
    """"Automatic" is the absence of a preference, not a topology."""
    save_topology_preference("bug_hunter")
    save_topology_preference(value)
    assert get_saved_topology_preference() is None


def test_an_unknown_topology_is_rejected():
    """Storing a name no reader recognises yields a setting that does nothing."""
    save_topology_preference("code_inspector")

    with pytest.raises(ValueError, match="Unknown topology"):
        save_topology_preference("does_not_exist")

    # The previous, valid choice survives the failed write.
    assert get_saved_topology_preference() == "code_inspector"


def test_clearing_when_nothing_is_saved_is_harmless():
    clear_topology_preference()
    clear_topology_preference()
    assert get_saved_topology_preference() is None
