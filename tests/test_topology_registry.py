"""Tests for the topology registry system.

Covers:
  1. Registry integrity — all 7 topologies load, have valid structure
  2. Graph integrity — every edge references existing nodes
  3. Classifier — keyword matching returns correct topologies
  4. Preference persistence — save/load round-trips
  5. API endpoint wiring — FastAPI routes respond correctly
  6. Pipeline dispatch — _dispatch_pipeline wires agents & tasks correctly
  7. Backward compatibility — legacy get_flow_definition() still works

Note: Tests that require crewai (dispatch, API endpoints) are guarded by
``pytest.importorskip`` so they skip cleanly when crewai is unavailable or
has import issues (e.g. missing _cffi_backend in sandboxed environments).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Registry & pure logic imports (no LLM needed)
# ---------------------------------------------------------------------------
from gitpilot.topology_registry import (
    TOPOLOGY_REGISTRY,
    DEFAULT_TOPOLOGY_ID,
    TopologyCategory,
    ExecutionStyle,
    RoutingStrategy,
    Topology,
    TopologyMeta,
    ClassificationResult,
    list_topologies,
    get_topology,
    get_topology_graph,
    classify_message,
    get_saved_topology_preference,
    save_topology_preference,
)

# ---------------------------------------------------------------------------
# Helpers: detect whether crewai is importable in this environment
# ---------------------------------------------------------------------------
_crewai_available = False
try:
    import crewai  # noqa: F401
    _crewai_available = True
except Exception:
    pass

needs_crewai = pytest.mark.skipif(
    not _crewai_available,
    reason="crewai not importable in this environment",
)


# ===========================================================================
# 1. Registry integrity
# ===========================================================================

class TestRegistryIntegrity:
    """Verify the registry itself is well-formed."""

    def test_registry_has_expected_topologies(self):
        assert len(TOPOLOGY_REGISTRY) == 8

    def test_all_expected_ids_present(self):
        expected = {
            "default", "gitpilot_code",
            "feature_builder", "bug_hunter", "code_inspector",
            "architect_mode", "quick_fix", "lite_mode",
        }
        assert set(TOPOLOGY_REGISTRY.keys()) == expected

    def test_default_topology_exists(self):
        assert DEFAULT_TOPOLOGY_ID in TOPOLOGY_REGISTRY

    @pytest.mark.parametrize("tid", list(TOPOLOGY_REGISTRY.keys()))
    def test_topology_has_required_fields(self, tid):
        t = TOPOLOGY_REGISTRY[tid]
        assert isinstance(t, Topology)
        assert t.id == tid
        assert t.name
        assert t.description
        assert isinstance(t.category, TopologyCategory)
        assert isinstance(t.execution_style, ExecutionStyle)
        assert isinstance(t.icon, str) and len(t.icon) > 0
        assert isinstance(t.agents_used, list) and len(t.agents_used) > 0
        assert t.routing_policy is not None
        assert isinstance(t.routing_policy.strategy, RoutingStrategy)
        assert isinstance(t.flow_graph, dict)

    @pytest.mark.parametrize("tid", list(TOPOLOGY_REGISTRY.keys()))
    def test_flow_graph_has_nodes_and_edges(self, tid):
        g = TOPOLOGY_REGISTRY[tid].flow_graph
        assert "nodes" in g and len(g["nodes"]) > 0
        assert "edges" in g and len(g["edges"]) > 0

    def test_system_topologies(self):
        systems = [t for t in TOPOLOGY_REGISTRY.values() if t.category == TopologyCategory.system]
        assert len(systems) == 3
        ids = {t.id for t in systems}
        assert ids == {"default", "gitpilot_code", "lite_mode"}

    def test_pipeline_topologies(self):
        pipelines = [t for t in TOPOLOGY_REGISTRY.values() if t.category == TopologyCategory.pipeline]
        assert len(pipelines) == 5
        for p in pipelines:
            assert p.execution_style == ExecutionStyle.crew_pipeline
            assert p.routing_policy.strategy == RoutingStrategy.fixed_sequence
            assert p.routing_policy.sequence is not None
            assert len(p.routing_policy.sequence) > 0

    def test_default_uses_classify_and_dispatch(self):
        t = TOPOLOGY_REGISTRY["default"]
        assert t.execution_style == ExecutionStyle.single_task
        assert t.routing_policy.strategy == RoutingStrategy.classify_and_dispatch

    def test_gitpilot_code_uses_react_loop(self):
        t = TOPOLOGY_REGISTRY["gitpilot_code"]
        assert t.execution_style == ExecutionStyle.react_loop
        assert t.routing_policy.strategy == RoutingStrategy.always_main_agent
        assert t.routing_policy.primary_agent == "main_react_agent"


# ===========================================================================
# 2. Graph integrity — edges reference valid nodes
# ===========================================================================

class TestGraphIntegrity:
    @pytest.mark.parametrize("tid", list(TOPOLOGY_REGISTRY.keys()))
    def test_edges_reference_valid_nodes(self, tid):
        g = TOPOLOGY_REGISTRY[tid].flow_graph
        node_ids = {n["id"] for n in g["nodes"]}
        for edge in g["edges"]:
            assert edge["source"] in node_ids, (
                f"Topology {tid}: edge {edge['id']} source "
                f"'{edge['source']}' not in node set"
            )
            assert edge["target"] in node_ids, (
                f"Topology {tid}: edge {edge['id']} target "
                f"'{edge['target']}' not in node set"
            )

    @pytest.mark.parametrize("tid", list(TOPOLOGY_REGISTRY.keys()))
    def test_node_ids_are_unique(self, tid):
        g = TOPOLOGY_REGISTRY[tid].flow_graph
        ids = [n["id"] for n in g["nodes"]]
        assert len(ids) == len(set(ids)), f"Topology {tid}: duplicate node IDs"

    @pytest.mark.parametrize("tid", list(TOPOLOGY_REGISTRY.keys()))
    def test_edge_ids_are_unique(self, tid):
        g = TOPOLOGY_REGISTRY[tid].flow_graph
        ids = [e["id"] for e in g["edges"]]
        assert len(ids) == len(set(ids)), f"Topology {tid}: duplicate edge IDs"

    @pytest.mark.parametrize("tid", list(TOPOLOGY_REGISTRY.keys()))
    def test_nodes_have_position(self, tid):
        g = TOPOLOGY_REGISTRY[tid].flow_graph
        for n in g["nodes"]:
            assert "position" in n, f"Topology {tid}: node {n['id']} missing position"
            assert "x" in n["position"] and "y" in n["position"]

    @pytest.mark.parametrize("tid", list(TOPOLOGY_REGISTRY.keys()))
    def test_nodes_have_data_with_label(self, tid):
        g = TOPOLOGY_REGISTRY[tid].flow_graph
        for n in g["nodes"]:
            assert "data" in n, f"Topology {tid}: node {n['id']} missing data"
            assert "label" in n["data"], f"Topology {tid}: node {n['id']} data missing label"


# ===========================================================================
# 3. Classifier
# ===========================================================================

class TestClassifier:
    def test_returns_classification_result(self):
        r = classify_message("Fix the 403 error")
        assert isinstance(r, ClassificationResult)
        assert r.recommended
        assert isinstance(r.confidence, float)
        assert isinstance(r.alternatives, list)

    def test_bug_keywords_recommend_bug_hunter(self):
        for msg in [
            "Fix the 403 error in auth middleware",
            "Debug the crash in payment module",
            "The tests are failing after the last merge",
            "There's a regression in the login flow",
        ]:
            r = classify_message(msg)
            assert r.recommended == "bug_hunter", f"Expected bug_hunter for: {msg!r}, got {r.recommended}"

    def test_feature_keywords_recommend_feature_builder(self):
        for msg in [
            "Add a new /users endpoint",
            "Create a component for settings",
            "Implement OAuth2 authentication",
            "Build a migration from v1 to v2",
        ]:
            r = classify_message(msg)
            assert r.recommended == "feature_builder", f"Expected feature_builder for: {msg!r}, got {r.recommended}"

    def test_review_keywords_recommend_code_inspector(self):
        for msg in [
            "Review my recent changes for security issues",
            "Audit the code quality of the API",
            "Inspect the diff for vulnerabilities",
        ]:
            r = classify_message(msg)
            assert r.recommended == "code_inspector", f"Expected code_inspector for: {msg!r}, got {r.recommended}"

    def test_architecture_keywords_recommend_architect_mode(self):
        for msg in [
            "How should we approach the database migration?",
            "Design a plan for refactoring the auth module",
            "Evaluate trade-offs between REST and GraphQL",
        ]:
            r = classify_message(msg)
            assert r.recommended == "architect_mode", f"Expected architect_mode for: {msg!r}, got {r.recommended}"

    def test_quick_fix_keywords(self):
        for msg in [
            "Fix the typo in README.md",
            "Update the version bump in package.json",
            "Fix formatting in the config file",
        ]:
            r = classify_message(msg)
            # quick_fix or bug_hunter are both acceptable for "fix" messages
            assert r.recommended in ("quick_fix", "bug_hunter"), (
                f"Expected quick_fix or bug_hunter for: {msg!r}, got {r.recommended}"
            )

    def test_unknown_message_falls_back_to_default(self):
        r = classify_message("tell me a joke about programming")
        assert r.recommended == "default"

    def test_to_dict_format(self):
        r = classify_message("Fix a bug")
        d = r.to_dict()
        assert "recommended_topology" in d
        assert "confidence" in d
        assert "alternatives" in d
        assert isinstance(d["alternatives"], list)

    def test_alternatives_include_system_topologies(self):
        r = classify_message("Add a new endpoint")
        d = r.to_dict()
        alt_ids = {a["id"] for a in d["alternatives"]}
        # System topologies should appear as alternatives
        assert "default" in alt_ids or d["recommended_topology"] == "default"
        assert "gitpilot_code" in alt_ids or d["recommended_topology"] == "gitpilot_code"


# ===========================================================================
# 4. list/get helpers
# ===========================================================================

class TestListAndGet:
    def test_list_topologies_returns_all(self):
        result = list_topologies()
        assert len(result) == 8
        for item in result:
            assert "id" in item
            assert "name" in item
            assert "category" in item
            assert "execution_style" in item
            assert "agents_used" in item

    def test_get_topology_existing(self):
        t = get_topology("feature_builder")
        assert t is not None
        assert t.id == "feature_builder"

    def test_get_topology_missing(self):
        t = get_topology("nonexistent")
        assert t is None

    def test_get_topology_graph_returns_expected_shape(self):
        g = get_topology_graph("gitpilot_code")
        assert "nodes" in g
        assert "edges" in g
        assert g["topology_id"] == "gitpilot_code"
        assert g["topology_name"]
        assert g["topology_icon"]
        assert g["execution_style"]

    def test_get_topology_graph_fallback_to_default(self):
        g = get_topology_graph("nonexistent_id")
        assert g["topology_id"] == "default"

    def test_get_topology_graph_none_returns_default(self):
        g = get_topology_graph(None)
        assert g["topology_id"] == "default"

    def test_legacy_compat_nodes_have_top_level_label(self):
        """FlowViewer legacy code reads node.label (not node.data.label)."""
        g = get_topology_graph("feature_builder")
        for n in g["nodes"]:
            assert "label" in n, f"Node {n['id']} missing top-level label"

    def test_to_meta(self):
        t = TOPOLOGY_REGISTRY["bug_hunter"]
        m = t.to_meta()
        assert isinstance(m, TopologyMeta)
        assert m.id == "bug_hunter"
        assert m.execution_style == ExecutionStyle.crew_pipeline

    def test_to_dict(self):
        t = TOPOLOGY_REGISTRY["quick_fix"]
        d = t.to_dict()
        assert d["id"] == "quick_fix"
        assert d["category"] == "pipeline"
        assert d["execution_style"] == "crew_pipeline"
        assert d["routing_policy"]["strategy"] == "fixed_sequence"
        assert d["routing_policy"]["sequence"] == ["developer", "git_agent"]


# ===========================================================================
# 5. Preference persistence
# ===========================================================================

class TestPreferencePersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        """Save a preference and read it back."""
        with patch("gitpilot.settings.CONFIG_DIR", tmp_path):
            save_topology_preference("bug_hunter")
            pref_file = tmp_path / "topology_pref.json"
            assert pref_file.exists()
            pref = get_saved_topology_preference()
            assert pref == "bug_hunter"

    def test_load_returns_none_when_no_file(self, tmp_path):
        with patch("gitpilot.settings.CONFIG_DIR", tmp_path):
            pref = get_saved_topology_preference()
            assert pref is None

    def test_load_returns_none_on_corrupted_file(self, tmp_path):
        pref_file = tmp_path / "topology_pref.json"
        pref_file.write_text("not json!!!", encoding="utf-8")
        with patch("gitpilot.settings.CONFIG_DIR", tmp_path):
            pref = get_saved_topology_preference()
            assert pref is None


# ===========================================================================
# 6. Pipeline topology agent sequence validation
# ===========================================================================

class TestPipelineSequences:
    """Verify each pipeline topology's sequence maps to known agent IDs."""

    KNOWN_TOPO_AGENTS = {"explorer", "planner", "developer", "reviewer", "git_agent"}

    @pytest.mark.parametrize("tid", [
        "feature_builder", "bug_hunter", "code_inspector",
        "architect_mode", "quick_fix",
    ])
    def test_sequence_agents_are_known(self, tid):
        t = TOPOLOGY_REGISTRY[tid]
        seq = t.routing_policy.sequence
        assert seq is not None
        for agent_id in seq:
            assert agent_id in self.KNOWN_TOPO_AGENTS, (
                f"Topology {tid}: agent '{agent_id}' not in known set"
            )

    def test_feature_builder_has_5_agents(self):
        t = TOPOLOGY_REGISTRY["feature_builder"]
        assert len(t.routing_policy.sequence) == 5
        assert t.routing_policy.sequence == [
            "explorer", "planner", "developer", "reviewer", "git_agent",
        ]

    def test_bug_hunter_has_4_agents(self):
        t = TOPOLOGY_REGISTRY["bug_hunter"]
        assert len(t.routing_policy.sequence) == 4
        assert t.routing_policy.sequence == [
            "explorer", "developer", "reviewer", "git_agent",
        ]

    def test_code_inspector_is_read_only(self):
        t = TOPOLOGY_REGISTRY["code_inspector"]
        assert t.routing_policy.sequence == ["explorer", "reviewer"]

    def test_architect_mode_is_read_only(self):
        t = TOPOLOGY_REGISTRY["architect_mode"]
        assert t.routing_policy.sequence == ["explorer", "planner"]

    def test_quick_fix_is_minimal(self):
        t = TOPOLOGY_REGISTRY["quick_fix"]
        assert t.routing_policy.sequence == ["developer", "git_agent"]


# ===========================================================================
# 7. _dispatch_pipeline wiring (mock-based)
# ===========================================================================

@needs_crewai
class TestDispatchPipeline:
    """Test that _dispatch_pipeline correctly builds a multi-task crew."""

    @pytest.fixture(autouse=True)
    def _mock_crewai(self):
        """Prevent CrewAI from initialising real LLMs."""
        with patch("crewai.agent.core.create_llm", return_value=MagicMock()):
            yield

    @pytest.fixture(autouse=True)
    def _ensure_tools_cache(self):
        """Pre-populate the lazy caches so tests can mock through them."""
        from gitpilot.agentic import _tools, _crewai
        _tools()  # force load
        _crewai()  # force load

    @pytest.mark.asyncio
    @patch("gitpilot.agentic._build_llm", return_value="ollama/qwen2:0.5b")
    async def test_pipeline_builds_correct_agent_count(self, mock_llm):
        """Verify _dispatch_pipeline creates the right number of agents/tasks."""
        from gitpilot.agentic import _dispatch_pipeline, _tools_cache

        _tools_cache["set_repo_context"] = MagicMock()
        topo = TOPOLOGY_REGISTRY["feature_builder"]

        # Mock Crew.kickoff to avoid running any actual LLM
        mock_result = MagicMock()
        mock_result.raw = "Pipeline completed successfully"

        with patch("crewai.Crew") as MockCrew:
            mock_crew_instance = MagicMock()
            mock_crew_instance.kickoff.return_value = mock_result
            MockCrew.return_value = mock_crew_instance

            # Also patch _crewai cache to use our mock Crew
            from gitpilot.agentic import _crewai_cache
            orig_crew = _crewai_cache.get("Crew")
            _crewai_cache["Crew"] = MockCrew

            try:
                result = await _dispatch_pipeline(
                    topo, "Add a new feature", "owner/repo",
                    token="fake", branch_name="main",
                )
            finally:
                if orig_crew:
                    _crewai_cache["Crew"] = orig_crew

        # Verify Crew was called with correct number of agents and tasks
        call_kwargs = MockCrew.call_args.kwargs
        assert len(call_kwargs["agents"]) == 5  # explorer, planner, developer, reviewer, git_agent
        assert len(call_kwargs["tasks"]) == 5
        assert call_kwargs["process"].value == "sequential"

        # Verify result structure
        assert result["topology_id"] == "feature_builder"
        assert result["execution_style"] == "crew_pipeline"
        assert result["agents_used"] == ["explorer", "planner", "developer", "reviewer", "git_agent"]
        assert result["result"] == "Pipeline completed successfully"

    @pytest.mark.asyncio
    @patch("gitpilot.agentic._build_llm", return_value="ollama/qwen2:0.5b")
    async def test_pipeline_tasks_have_context_chaining(self, mock_llm):
        """Verify each task receives all prior tasks as context."""
        from gitpilot.agentic import _dispatch_pipeline, _tools_cache, _crewai_cache

        _tools_cache["set_repo_context"] = MagicMock()
        topo = TOPOLOGY_REGISTRY["bug_hunter"]  # 4 agents

        mock_crew_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.raw = "Bug fixed"
        mock_crew_instance.kickoff.return_value = mock_result
        MockCrew = MagicMock(return_value=mock_crew_instance)

        orig_crew = _crewai_cache.get("Crew")
        _crewai_cache["Crew"] = MockCrew

        try:
            await _dispatch_pipeline(
                topo, "Fix error", "o/r", token="fake",
            )
        finally:
            if orig_crew:
                _crewai_cache["Crew"] = orig_crew

        tasks = MockCrew.call_args.kwargs["tasks"]
        # First task has no context (or empty)
        assert tasks[0].context is None or tasks[0].context == []
        # Second task has task[0] as context
        assert len(tasks[1].context) == 1
        # Third task has tasks[0:2] as context
        assert len(tasks[2].context) == 2
        # Fourth task has tasks[0:3] as context
        assert len(tasks[3].context) == 3

    @pytest.mark.asyncio
    @patch("gitpilot.agentic._build_llm", return_value="ollama/qwen2:0.5b")
    async def test_quick_fix_pipeline_has_2_agents(self, mock_llm):
        from gitpilot.agentic import _dispatch_pipeline, _tools_cache, _crewai_cache

        _tools_cache["set_repo_context"] = MagicMock()
        topo = TOPOLOGY_REGISTRY["quick_fix"]

        mock_crew_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.raw = "Done"
        mock_crew_instance.kickoff.return_value = mock_result
        MockCrew = MagicMock(return_value=mock_crew_instance)

        orig_crew = _crewai_cache.get("Crew")
        _crewai_cache["Crew"] = MockCrew

        try:
            result = await _dispatch_pipeline(
                topo, "Fix typo", "o/r",
            )
        finally:
            if orig_crew:
                _crewai_cache["Crew"] = orig_crew

        assert len(MockCrew.call_args.kwargs["agents"]) == 2
        assert result["agents_used"] == ["developer", "git_agent"]


# ===========================================================================
# 8. dispatch_request topology routing
# ===========================================================================

@needs_crewai
class TestDispatchRequestTopologyRouting:
    """Verify dispatch_request routes to _dispatch_pipeline for pipeline topologies."""

    @pytest.fixture(autouse=True)
    def _mock_crewai(self):
        with patch("crewai.agent.core.create_llm", return_value=MagicMock()):
            yield

    @pytest.mark.asyncio
    @patch("gitpilot.agentic._dispatch_pipeline", new_callable=AsyncMock)
    @patch("gitpilot.agentic.get_saved_topology_preference", return_value=None)
    async def test_pipeline_topology_id_routes_to_pipeline(self, mock_pref, mock_pipeline):
        """When topology_id is a pipeline topology, dispatch_request should call _dispatch_pipeline."""
        from gitpilot.agentic import dispatch_request

        mock_pipeline.return_value = {
            "category": "topology_pipeline",
            "topology_id": "feature_builder",
            "result": "ok",
        }

        result = await dispatch_request(
            "Add a new feature",
            "owner/repo",
            token="fake",
            topology_id="feature_builder",
        )

        mock_pipeline.assert_called_once()
        call_args = mock_pipeline.call_args
        assert call_args[0][0].id == "feature_builder"  # first arg is the topology
        assert result["topology_id"] == "feature_builder"

    @pytest.mark.asyncio
    @patch("gitpilot.agentic._is_incompatible_model", return_value=False)
    @patch("gitpilot.agentic._build_llm", return_value="ollama/qwen2:0.5b")
    @patch("gitpilot.agentic.route_request")
    @patch("gitpilot.agentic.get_saved_topology_preference", return_value=None)
    async def test_no_topology_falls_through_to_legacy(
        self, mock_pref, mock_route, mock_llm, mock_incompatible
    ):
        """When topology_id is None and no saved pref, dispatch_request uses legacy routing.

        Note: _is_incompatible_model is mocked to False so we explicitly test the
        legacy multi-agent path. In production, this auto-detection routes small
        local models (qwen2.5:1.5b, deepseek-r1, etc.) to Lite Mode for reliability.
        """
        from gitpilot.agentic import dispatch_request
        from gitpilot.agent_router import AgentType, RequestCategory, WorkflowPlan

        mock_route.return_value = WorkflowPlan(
            category=RequestCategory.PLAN_EXECUTE,
            agents=[AgentType.EXPLORER, AgentType.PLANNER, AgentType.CODE_WRITER],
            description="Default plan+execute",
        )

        with patch("gitpilot.agentic.generate_plan", new_callable=AsyncMock) as mock_plan:
            mock_plan.return_value = MagicMock(
                model_dump=lambda: {"goal": "test", "summary": "test", "steps": []}
            )
            result = await dispatch_request(
                "Refactor the auth module",
                "owner/repo",
                token="fake",
            )

        mock_route.assert_called_once()
        assert result["category"] == "plan_execute"


# ===========================================================================
# 9. get_flow_definition backward compatibility
# ===========================================================================

@needs_crewai
class TestGetFlowDefinitionCompat:

    @pytest.mark.asyncio
    async def test_legacy_call_returns_hardcoded_graph(self):
        """With no topology preference, the legacy hardcoded graph is returned."""
        from gitpilot.agentic import get_flow_definition

        with patch("gitpilot.agentic.get_saved_topology_preference", return_value=None):
            flow = await get_flow_definition()

        # Legacy graph has the "router" node at the top
        node_ids = {n["id"] for n in flow["nodes"]}
        assert "router" in node_ids
        assert "repo_explorer" in node_ids
        # Legacy graph does NOT have "topology_id" key
        assert "topology_id" not in flow

    @pytest.mark.asyncio
    async def test_topology_id_returns_registry_graph(self):
        from gitpilot.agentic import get_flow_definition

        flow = await get_flow_definition(topology_id="gitpilot_code")

        assert flow["topology_id"] == "gitpilot_code"
        node_ids = {n["id"] for n in flow["nodes"]}
        assert "main_react_agent" in node_ids

    @pytest.mark.asyncio
    async def test_saved_pref_is_used_when_no_id_provided(self):
        from gitpilot.agentic import get_flow_definition

        with patch("gitpilot.agentic.get_saved_topology_preference", return_value="bug_hunter"):
            flow = await get_flow_definition()

        assert flow["topology_id"] == "bug_hunter"


# ===========================================================================
# 10. API endpoint wiring (FastAPI TestClient)
# ===========================================================================

@needs_crewai
class TestAPIEndpoints:
    """Test the topology-related API endpoints are correctly wired."""

    @pytest.fixture()
    def client(self):
        from fastapi.testclient import TestClient
        from gitpilot.api import app
        return TestClient(app)

    def test_list_topologies_endpoint(self, client):
        resp = client.get("/api/flow/topologies")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 8
        ids = {t["id"] for t in data}
        assert "default" in ids
        assert "gitpilot_code" in ids
        assert "feature_builder" in ids

    def test_get_topology_by_id(self, client):
        resp = client.get("/api/flow/topology/bug_hunter")
        assert resp.status_code == 200
        data = resp.json()
        assert data["topology_id"] == "bug_hunter"
        assert "nodes" in data
        assert "edges" in data

    def test_get_topology_unknown_falls_back(self, client):
        resp = client.get("/api/flow/topology/nonexistent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["topology_id"] == "default"

    def test_flow_current_with_topology_param(self, client):
        resp = client.get("/api/flow/current?topology=code_inspector")
        assert resp.status_code == 200
        data = resp.json()
        assert data["topology_id"] == "code_inspector"

    def test_flow_current_without_param_backward_compat(self, client):
        """Without topology param, returns legacy graph or saved pref."""
        with patch("gitpilot.api.get_saved_topology_preference", return_value=None):
            resp = client.get("/api/flow/current")
        assert resp.status_code == 200
        data = resp.json()
        # Legacy graph has "nodes" and "edges"
        assert "nodes" in data
        assert "edges" in data

    def test_classify_endpoint(self, client):
        resp = client.post(
            "/api/flow/classify",
            json={"message": "Fix the 403 error in auth"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "recommended_topology" in data
        assert data["recommended_topology"] == "bug_hunter"
        assert "confidence" in data
        assert "alternatives" in data

    def test_classify_feature_request(self, client):
        resp = client.post(
            "/api/flow/classify",
            json={"message": "Create a new user registration endpoint"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["recommended_topology"] == "feature_builder"

    def test_topology_preference_save_and_get(self, client):
        with patch("gitpilot.api.save_topology_preference") as mock_save:
            mock_save.return_value = None
            resp = client.post(
                "/api/settings/topology",
                json={"topology": "architect_mode"},
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"
            assert resp.json()["topology"] == "architect_mode"
            mock_save.assert_called_once_with("architect_mode")

        with patch("gitpilot.api.get_saved_topology_preference", return_value="architect_mode"):
            resp = client.get("/api/settings/topology")
            assert resp.status_code == 200
            assert resp.json()["topology"] == "architect_mode"

    @pytest.mark.parametrize("tid", list(TOPOLOGY_REGISTRY.keys()))
    def test_each_topology_graph_is_fetchable(self, client, tid):
        """Every registered topology should be fetchable via its endpoint."""
        resp = client.get(f"/api/flow/topology/{tid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["topology_id"] == tid
        assert len(data["nodes"]) > 0
        assert len(data["edges"]) > 0


# ===========================================================================
# 11. Node & edge counts per topology
# ===========================================================================

class TestTopologyGraphSizes:
    """Verify expected node/edge counts as a regression guard."""

    EXPECTED_SIZES = {
        "default":         (15, 25),   # 15 nodes (user, router, 10 agents, 2 tools, output), 25 edges
        "gitpilot_code":     (13, 17),   # 13 nodes, 17 edges
        "feature_builder": (7, 6),     # 7 nodes, 6 edges
        "bug_hunter":      (6, 5),     # 6 nodes, 5 edges
        "code_inspector":  (4, 3),     # 4 nodes, 3 edges
        "architect_mode":  (4, 3),     # 4 nodes, 3 edges
        "quick_fix":       (4, 3),     # 4 nodes, 3 edges
    }

    @pytest.mark.parametrize("tid,expected", list(EXPECTED_SIZES.items()))
    def test_node_edge_count(self, tid, expected):
        g = TOPOLOGY_REGISTRY[tid].flow_graph
        exp_nodes, exp_edges = expected
        assert len(g["nodes"]) == exp_nodes, (
            f"Topology {tid}: expected {exp_nodes} nodes, got {len(g['nodes'])}"
        )
        assert len(g["edges"]) == exp_edges, (
            f"Topology {tid}: expected {exp_edges} edges, got {len(g['edges'])}"
        )


# ===========================================================================
# 12. Classifier hint coverage
# ===========================================================================

class TestClassifierHintCoverage:
    """Ensure pipeline topologies have non-empty classifier hints."""

    @pytest.mark.parametrize("tid", [
        "feature_builder", "bug_hunter", "code_inspector",
        "architect_mode", "quick_fix",
    ])
    def test_pipeline_has_hints(self, tid):
        t = TOPOLOGY_REGISTRY[tid]
        hints = t.routing_policy.classifier_hints
        assert len(hints) >= 3, f"Topology {tid}: needs at least 3 classifier hints"

    def test_system_topologies_have_empty_hints(self):
        """System topologies don't use hints (they're fallbacks)."""
        assert len(TOPOLOGY_REGISTRY["default"].routing_policy.classifier_hints) == 0
        assert len(TOPOLOGY_REGISTRY["gitpilot_code"].routing_policy.classifier_hints) == 0

    def test_no_duplicate_hints_across_topologies(self):
        """Warn about overlapping hints that could cause ambiguity."""
        all_hints = {}
        for tid, t in TOPOLOGY_REGISTRY.items():
            for hint in t.routing_policy.classifier_hints:
                if hint in all_hints:
                    # Some overlap is acceptable (e.g., "fix" in both bug_hunter and quick_fix)
                    # but track it for awareness
                    pass
                all_hints.setdefault(hint, []).append(tid)
        # The actual assertion: no single hint should map to > 2 topologies
        for hint, tids in all_hints.items():
            assert len(tids) <= 2, (
                f"Hint '{hint}' appears in {len(tids)} topologies: {tids}"
            )
