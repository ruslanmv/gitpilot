"""Topology schema v2 — Batch V4-G1.

The document is executable configuration: it grants capabilities and can set an
approval mode. So the tests that matter here are not "does it parse" but "can a
document obtain something its author was not allowed to grant" — the tighten-only
approval rule and the trust gate on repo-local files.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gitpilot.agent.policy import CapabilityMask
from gitpilot.permissions import PermissionMode
from gitpilot.topology import registry as registry_mod
from gitpilot.topology.legacy import LEGACY_ORDER, LEGACY_TOPOLOGIES
from gitpilot.topology.schema import (
    NAMESPACE_LABELS,
    SUBAGENT_TEMPLATES,
    ApprovalMode,
    ApprovalSpec,
    DialectChoice,
    Engine,
    ExecutionStyle,
    SchemaError,
    Topology,
    TopologyCategory,
    VerificationMode,
    build_flow_graph,
    parse_document,
)

MINIMAL = {"id": "tiny"}

FULL = {
    "id": "sample",
    "name": "Sample",
    "icon": "🧠",
    "category": "system",
    "execution": {"engine": "agentic_loop", "dialect": "auto"},
    "agent": {"role": "software_engineer", "subagents": ["explorer", "reviewer"]},
    "capabilities": {
        "fs.read": True,
        "fs.write": True,
        "terminal.run": {"network": False},
        "git.commit": "ask",
        "git.push": False,
        "todo.write": True,
        "agent.delegate": {"max_depth": 1},
    },
    "approval": {"mode": "session_default"},
    "verification": {"tests": "auto", "max_fix_cycles": 3},
    "limits": {"max_iterations": 40, "max_tool_calls": 120, "max_runtime_seconds": 1800},
    "visualization": "auto",
}


def _doc(**overrides):
    document = json.loads(json.dumps(FULL))
    document.update(overrides)
    return document


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestSchemaValidation:
    def test_the_documented_example_parses(self):
        """§15.1's document, field for field."""
        topology = parse_document(FULL)

        assert isinstance(topology, Topology)
        assert topology.id == "sample"
        assert topology.policy is not None
        assert topology.policy.execution.engine is Engine.agentic_loop
        assert topology.policy.agent.subagents == ("explorer", "reviewer")
        assert topology.policy.verification.tests is VerificationMode.auto
        assert topology.policy.limits.max_runtime_seconds == 1800

    def test_a_minimal_document_gets_defaults(self):
        """A one-line document must still be a complete policy — a half-filled
        policy is how a run ends up with limits nobody chose."""
        topology = parse_document(MINIMAL)

        assert topology.policy is not None
        assert topology.policy.execution.engine is Engine.agentic_loop
        assert topology.policy.execution.dialect is DialectChoice.auto
        assert topology.policy.approval.mode is ApprovalMode.session_default
        assert topology.policy.limits.max_iterations == 40
        assert topology.name and topology.description and topology.icon

    def test_a_document_without_an_id_is_refused(self):
        with pytest.raises(SchemaError, match="needs an 'id'"):
            parse_document({"name": "No id"})

    @pytest.mark.parametrize("bad_id", ["Default", "my topology", "3rd", "a-b"])
    def test_an_id_that_is_not_snake_case_is_refused(self, bad_id):
        """The id appears in URLs and in the saved preference file."""
        with pytest.raises(SchemaError, match="lower_snake_case"):
            parse_document({"id": bad_id})

    @pytest.mark.parametrize(("section", "value", "message"), [
        ("execution", {"engine": "magic"}, "execution.engine"),
        ("execution", {"dialect": "esperanto"}, "execution.dialect"),
        ("approval", {"mode": "whenever"}, "approval.mode"),
        ("verification", {"tests": "maybe"}, "verification.tests"),
    ])
    def test_an_unknown_enum_value_is_refused(self, section, value, message):
        """Not defaulted: a typo'd ``approval.mode`` silently becoming
        ``session_default`` is a policy the author did not write."""
        with pytest.raises(SchemaError, match=message):
            parse_document(_doc(**{section: value}))

    @pytest.mark.parametrize("limits", [
        {"max_iterations": 0},
        {"max_tool_calls": -5},
        {"max_runtime_seconds": "lots"},
        {"max_iterations": True},
    ])
    def test_a_nonsense_limit_is_refused(self, limits):
        with pytest.raises(SchemaError, match="must be a positive integer"):
            parse_document(_doc(limits=limits))

    def test_a_negative_fix_cycle_count_is_refused(self):
        with pytest.raises(SchemaError, match="max_fix_cycles"):
            parse_document(_doc(verification={"tests": "auto", "max_fix_cycles": -1}))

    def test_an_unknown_subagent_is_refused(self):
        """Naming a template that does not exist would produce a delegation that
        fails at the moment the model finally reaches for it."""
        with pytest.raises(SchemaError, match="unknown subagent"):
            parse_document(_doc(agent={"subagents": ["archaeologist"]}))

    def test_the_subagent_vocabulary_matches_the_real_templates(self):
        """``SUBAGENT_TEMPLATES`` is a constant so parsing does not import the
        agent package. This is what stops it drifting from the templates."""
        from gitpilot.agent.delegation import template_names

        assert set(SUBAGENT_TEMPLATES) == set(template_names())

    def test_a_non_mapping_document_is_refused(self):
        with pytest.raises(SchemaError, match="must be a mapping"):
            parse_document(["id: default"])

    @pytest.mark.parametrize("section", ["execution", "agent", "approval", "limits"])
    def test_a_section_that_is_not_a_mapping_is_refused(self, section):
        with pytest.raises(SchemaError, match="must be a mapping"):
            parse_document(_doc(**{section: ["oops"]}))

    def test_an_unknown_top_level_key_warns_but_loads(self, caplog):
        """A document written for a newer GitPilot should still work here."""
        with caplog.at_level("WARNING"):
            topology = parse_document(_doc(quantum_mode=True))

        assert topology.id == "sample"
        assert "unknown key" in caplog.text

    def test_the_category_defaults_from_the_engine(self):
        assert parse_document({"id": "a"}).category is TopologyCategory.system
        assert parse_document(
            {"id": "b", "execution": {"engine": "sequential_pipeline"}},
        ).category is TopologyCategory.pipeline

    def test_an_unknown_category_is_refused(self):
        with pytest.raises(SchemaError, match="unknown category"):
            parse_document(_doc(category="experimental"))

    def test_the_engine_maps_onto_the_style_clients_speak(self):
        assert Engine.agentic_loop.execution_style is ExecutionStyle.agentic_loop
        assert Engine.sequential_pipeline.execution_style is ExecutionStyle.crew_pipeline
        assert Engine.single_task.execution_style is ExecutionStyle.single_task


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestCapabilityParsing:
    def test_the_raw_mapping_is_preserved(self):
        """``RunRequest.topology_capabilities`` wants the mapping; round-tripping
        through a mask would lose the qualifiers the mask does not model."""
        policy = parse_document(FULL).policy

        assert policy.capabilities["terminal.run"] == {"network": False}
        assert policy.capabilities["agent.delegate"] == {"max_depth": 1}

    def test_it_builds_a_mask_that_closes_by_default(self):
        """Naming capabilities is how a topology says "only these"."""
        mask = parse_document(FULL).policy.capability_mask()

        assert isinstance(mask, CapabilityMask)
        assert mask.open_by_default is False
        assert mask.allows("fs.read")
        assert not mask.allows("fs.delete"), "never granted, so withheld"

    def test_an_explicit_false_is_a_denial_not_an_absence(self):
        mask = parse_document(FULL).policy.capability_mask()

        assert not mask.allows("git.push")
        assert "git.push" in mask.denied

    def test_ask_survives_as_a_qualifier(self):
        qualifier = parse_document(FULL).policy.capability_mask().qualifier("git.commit")

        assert qualifier is not None
        assert qualifier.force_ask is True

    def test_a_mapping_qualifier_survives(self):
        mask = parse_document(FULL).policy.capability_mask()

        assert mask.qualifier("terminal.run").network is False
        assert mask.qualifier("agent.delegate").max_depth == 1

    def test_a_wildcard_grants_a_family(self):
        policy = parse_document(_doc(capabilities={"fs.*": True, "fs.delete": False})).policy
        mask = policy.capability_mask()

        assert mask.allows("fs.read")
        assert mask.allows("fs.write")
        assert not mask.allows("fs.delete"), "an explicit denial beats the wildcard"

    @pytest.mark.parametrize("value", [True, False, None, "ask", "true", "forbidden"])
    def test_the_documented_value_shapes_are_accepted(self, value):
        parse_document(_doc(capabilities={"fs.read": value}))

    def test_an_unusable_value_is_refused(self):
        with pytest.raises(SchemaError, match="unusable value"):
            parse_document(_doc(capabilities={"fs.read": ["yes", "please"]}))

    def test_a_word_that_is_not_a_verdict_is_refused(self):
        with pytest.raises(SchemaError, match="expected true, false or 'ask'"):
            parse_document(_doc(capabilities={"fs.read": "sometimes"}))

    @pytest.mark.parametrize("key", ["FS.read", "fs read", "1fs.read", ""])
    def test_a_key_that_is_not_a_capability_is_refused(self, key):
        """A misspelled capability must withhold a tool, not grant one — so it
        has to be caught rather than passed through to the mask."""
        with pytest.raises(SchemaError, match="not a capability id"):
            parse_document(_doc(capabilities={key: True}))

    def test_capabilities_are_optional_and_mean_unrestricted(self):
        """An unmigrated topology keeps Phase C's behaviour."""
        policy = parse_document({"id": "open"}).policy

        assert policy.capabilities == {}
        assert policy.capability_mask().open_by_default is True

    def test_read_only_is_derived_from_the_grants(self):
        assert parse_document(_doc(capabilities={
            "fs.read": True, "fs.grep": True, "git.status": True,
        })).policy.read_only is True
        assert parse_document(FULL).policy.read_only is False

    def test_namespaces_ignore_denied_capabilities(self):
        policy = parse_document(_doc(capabilities={
            "fs.read": True, "web.fetch": False,
        })).policy

        assert policy.namespaces() == ["fs"]


# ---------------------------------------------------------------------------
# The generated flow graph
# ---------------------------------------------------------------------------


class TestAutoFlowGraph:
    def test_it_has_the_documented_shape(self):
        """Main agent + one node per capability namespace + subagent nodes, so the
        picker and the Agent Workflow view work without a hand-drawn graph."""
        graph = parse_document(FULL).flow_graph
        node_ids = [node["id"] for node in graph["nodes"]]

        assert node_ids[0] == "user_request"
        assert "main_agent" in node_ids
        assert node_ids[-1] == "output"
        assert {"tools_fs", "tools_terminal", "tools_git", "tools_todo"} <= set(node_ids)
        assert {"subagent_explorer", "subagent_reviewer"} <= set(node_ids)

    def test_delegation_is_drawn_as_subagents_not_as_a_namespace(self):
        """A box labelled "Agent" next to the subagents it spawns says nothing
        twice."""
        node_ids = {node["id"] for node in parse_document(FULL).flow_graph["nodes"]}

        assert "tools_agent" not in node_ids

    def test_delegation_without_named_templates_still_draws_a_box(self):
        graph = parse_document(_doc(
            agent={"role": "software_engineer"},
            capabilities={"fs.read": True, "agent.delegate": True},
        )).flow_graph
        node_ids = {node["id"] for node in graph["nodes"]}

        assert "subagent_task" in node_ids

    def test_every_edge_references_a_real_node(self):
        graph = parse_document(FULL).flow_graph
        node_ids = {node["id"] for node in graph["nodes"]}

        for edge in graph["edges"]:
            assert edge["source"] in node_ids, edge
            assert edge["target"] in node_ids, edge

    def test_edge_ids_are_unique(self):
        edges = parse_document(FULL).flow_graph["edges"]

        assert len({edge["id"] for edge in edges}) == len(edges)

    def test_a_denied_capability_gets_no_node(self):
        """The picture is generated so it cannot claim what the policy withholds."""
        graph = parse_document(_doc(capabilities={
            "fs.read": True, "github.pr.create": False,
        })).flow_graph
        node_ids = {node["id"] for node in graph["nodes"]}

        assert "tools_fs" in node_ids
        assert "tools_github" not in node_ids

    def test_a_read_only_topology_says_so_on_the_agent_node(self):
        graph = parse_document(_doc(capabilities={"fs.read": True})).flow_graph
        main = next(node for node in graph["nodes"] if node["id"] == "main_agent")

        assert main["data"]["mode"] == "read-only"

    def test_an_unknown_namespace_gets_a_node_titled_after_itself(self):
        """An unfamiliar namespace should look unfamiliar, not vanish."""
        graph = parse_document(_doc(capabilities={"quantum.entangle": True})).flow_graph
        node = next(n for n in graph["nodes"] if n["id"] == "tools_quantum")

        assert node["data"]["label"] == "Quantum"
        assert "quantum" not in NAMESPACE_LABELS

    def test_every_node_carries_a_label(self):
        """``get_topology_graph`` hoists ``data.label``; a node without one shows
        up in the picker as its own id."""
        for node in parse_document(FULL).flow_graph["nodes"]:
            assert node["data"]["label"]
            assert isinstance(node["position"]["x"], int)

    def test_nodes_are_laid_out_without_collisions(self):
        graph = parse_document(FULL).flow_graph
        positions = [(n["position"]["x"], n["position"]["y"]) for n in graph["nodes"]]

        assert len(set(positions)) == len(positions)

    def test_an_embedded_graph_wins_over_generation(self):
        drawn = {"nodes": [{"id": "only", "data": {"label": "Only"}}], "edges": []}
        topology = parse_document(_doc(flow_graph=drawn))

        assert [n["id"] for n in topology.flow_graph["nodes"]] == ["only"]
        assert topology.policy.visualization == "embedded"

    def test_build_flow_graph_is_deterministic(self):
        policy = parse_document(FULL).policy

        first = build_flow_graph(policy, name="Sample")
        second = build_flow_graph(policy, name="Sample")

        assert first == second


# ---------------------------------------------------------------------------
# Approvals may only tighten
# ---------------------------------------------------------------------------


class TestApprovalTightenOnly:
    @pytest.mark.parametrize(("session", "document", "expected"), [
        ("normal", "plan", PermissionMode.PLAN),      # tightening: honoured
        ("auto", "normal", PermissionMode.NORMAL),    # tightening: honoured
        ("auto", "plan", PermissionMode.PLAN),        # tightening: honoured
        ("normal", "auto", PermissionMode.NORMAL),    # loosening: refused
        ("plan", "auto", PermissionMode.PLAN),        # loosening: refused
        ("plan", "normal", PermissionMode.PLAN),      # loosening: refused
    ])
    def test_a_document_can_only_tighten_the_session_mode(self, session, document, expected):
        """A topology is configuration, and configuration is not an authenticated
        escalation channel any more than a chat body is (§8.1, F11)."""
        spec = ApprovalSpec(mode=ApprovalMode(document))

        assert spec.effective_mode(session) is expected

    @pytest.mark.parametrize("session", ["plan", "normal", "auto"])
    def test_session_default_leaves_the_session_alone(self, session):
        spec = ApprovalSpec(mode=ApprovalMode.session_default)

        assert spec.effective_mode(session) is PermissionMode(session)
        assert spec.requested_mode() is None

    @pytest.mark.parametrize("session", ["plan", "normal", "auto"])
    def test_headless_is_about_who_answers_not_what_is_permitted(self, session):
        """``none`` must not silently become a permission mode: it means nobody is
        watching, so a call that would ask is denied."""
        spec = ApprovalSpec(mode=ApprovalMode.none)

        assert spec.headless is True
        assert spec.requested_mode() is None
        assert spec.effective_mode(session) is PermissionMode(session)
        assert spec.approval_timeout_s() == 0.0

    def test_a_non_headless_topology_keeps_the_default_timeout(self):
        assert ApprovalSpec().approval_timeout_s(90.0) == 90.0

    def test_a_document_asking_for_auto_never_widens_a_normal_run(self):
        """The end-to-end version of the rule, through the parser."""
        topology = parse_document(_doc(approval={"mode": "auto"}))

        assert topology.policy.approval.effective_mode("normal") is PermissionMode.NORMAL


# ---------------------------------------------------------------------------
# Loading files
# ---------------------------------------------------------------------------


class TestLoading:
    def _write(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_file_of_documents_loads(self, tmp_path):
        path = self._write(tmp_path / "topologies.yaml", """
topologies:
  - id: one
    name: One
  - id: two
    name: Two
""".lstrip())

        loaded = registry_mod.load_documents(path, source="user")

        assert [t.id for t in loaded] == ["one", "two"]
        assert all(t.policy.source == "user" for t in loaded)

    def test_a_single_document_file_loads(self, tmp_path):
        path = self._write(tmp_path / "one.yaml", "id: solo\nname: Solo\n")

        assert [t.id for t in registry_mod.load_documents(path, source="builtin")] == ["solo"]

    def test_one_invalid_document_does_not_take_out_the_file(self, tmp_path, caplog):
        """One typo in a user's fifth topology should not lose the four above it."""
        path = self._write(tmp_path / "topologies.yaml", """
topologies:
  - id: good
  - id: BAD ID
  - id: also_good
""".lstrip())

        with caplog.at_level("WARNING"):
            loaded = registry_mod.load_documents(path, source="user")

        assert [t.id for t in loaded] == ["good", "also_good"]
        assert "ignoring invalid topology" in caplog.text

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        assert registry_mod.load_documents(tmp_path / "nope.yaml", source="user") == []

    def test_a_file_with_no_documents_warns(self, tmp_path, caplog):
        path = self._write(tmp_path / "topologies.yaml", "settings:\n  theme: dark\n")

        with caplog.at_level("WARNING"):
            assert registry_mod.load_documents(path, source="user") == []
        assert "no 'topologies' list" in caplog.text

    def test_the_tiny_yaml_fallback_reads_a_document(self, monkeypatch, tmp_path):
        """PyYAML is optional, and a missing parser must not mean "no policy" —
        that fails in the permissive direction."""
        import builtins

        real_import = builtins.__import__

        def no_yaml(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("no yaml here")
            return real_import(name, *args, **kwargs)

        document = """
id: lite_ish
name: Lite Ish
execution:
  engine: agentic_loop
  dialect: lite
capabilities:
  fs.read: true
  terminal.run: {network: false}
limits:
  max_iterations: 12
""".lstrip()
        path = self._write(tmp_path / "t.yaml", document)

        # Pin that the fallback is what ran: without this the test passes just as
        # well with PyYAML installed, which is the parser it exists to bypass.
        from gitpilot.yaml_lite import load_yaml_or_json, tiny_yaml

        monkeypatch.setattr(builtins, "__import__", no_yaml)
        assert load_yaml_or_json(document) == tiny_yaml(document)

        loaded = registry_mod.load_documents(path, source="user")

        assert len(loaded) == 1
        policy = loaded[0].policy
        assert policy.execution.dialect is DialectChoice.lite
        assert policy.capabilities["terminal.run"] == {"network": False}
        assert policy.limits.max_iterations == 12

    def test_the_shipped_builtin_documents_all_parse(self):
        """The files in ``defaults/`` are as loadable as the ones a user writes."""
        documents = registry_mod.builtin_documents()

        assert documents, "no built-in topology documents found"
        assert all(t.policy is not None for t in documents)


# ---------------------------------------------------------------------------
# The trust gate
# ---------------------------------------------------------------------------


class TestRepoLocalTopologies:
    def _project(self, tmp_path: Path, *, approval: str = "auto") -> Path:
        workspace = tmp_path / "clone"
        (workspace / ".gitpilot").mkdir(parents=True)
        (workspace / ".gitpilot" / "topologies.yaml").write_text(
            "topologies:\n"
            "  - id: repo_special\n"
            "    name: Repo Special\n"
            "    approval:\n"
            f"      mode: {approval}\n"
            "    capabilities:\n"
            "      fs.write: true\n"
            "      terminal.run: true\n",
            encoding="utf-8",
        )
        return workspace

    def test_an_untrusted_workspace_topology_is_ignored(self, tmp_path, monkeypatch):
        """Cloning a stranger's project must not hand that project a say in what
        GitPilot may do without asking."""
        workspace = self._project(tmp_path)
        monkeypatch.setattr(registry_mod, "workspace_is_trusted", lambda _p: False)

        assert registry_mod.project_documents(workspace) == []
        assert registry_mod.get_topology("repo_special", workspace=workspace) is None

    def test_a_trusted_workspace_topology_loads(self, tmp_path, monkeypatch):
        workspace = self._project(tmp_path)
        monkeypatch.setattr(registry_mod, "workspace_is_trusted", lambda _p: True)

        topology = registry_mod.get_topology("repo_special", workspace=workspace)

        assert topology is not None
        assert topology.policy.source == "project"

    def test_even_a_trusted_repo_topology_cannot_widen_approvals(self, tmp_path, monkeypatch):
        workspace = self._project(tmp_path, approval="auto")
        monkeypatch.setattr(registry_mod, "workspace_is_trusted", lambda _p: True)

        topology = registry_mod.get_topology("repo_special", workspace=workspace)

        assert topology.policy.approval.effective_mode("normal") is PermissionMode.NORMAL

    def test_the_gate_reads_the_real_trust_store(self, tmp_path, monkeypatch):
        from gitpilot import trusted_folders

        store_path = tmp_path / "trusted.json"
        monkeypatch.setattr(trusted_folders, "DEFAULT_STORE", store_path)
        workspace = self._project(tmp_path)

        assert registry_mod.workspace_is_trusted(workspace) is False

        trusted_folders.TrustStore.load(store_path).trust(workspace)
        assert registry_mod.workspace_is_trusted(workspace) is True

    def test_no_workspace_means_no_project_topologies(self):
        assert registry_mod.project_documents(None) == []

    def test_a_workspace_without_the_file_is_quiet(self, tmp_path):
        assert registry_mod.project_documents(tmp_path) == []


# ---------------------------------------------------------------------------
# The registry stays compatible
# ---------------------------------------------------------------------------


class TestRegistryCompatibility:
    @pytest.mark.parametrize("topology_id", LEGACY_ORDER)
    def test_all_nine_legacy_ids_still_resolve(self, topology_id):
        """A saved preference must never 404, whatever has happened to the id
        since it was saved (§15.3 retires several)."""
        from gitpilot.topology_registry import get_topology

        assert get_topology(topology_id) is not None

    @pytest.mark.parametrize("topology_id", [
        tid for tid in LEGACY_ORDER if tid != "gitpilot_code"
    ])
    def test_a_legacy_id_that_was_not_retired_resolves_to_itself(self, topology_id):
        from gitpilot.topology_registry import get_topology

        assert get_topology(topology_id).id == topology_id

    def test_the_retired_id_resolves_to_its_successor(self):
        """T2 was a flow graph promising a ReAct loop with no executor; it retires
        into the topology that has one — which as of Batch V4-G5 is ``default``."""
        from gitpilot.topology_registry import get_topology

        assert get_topology("gitpilot_code").id == "default"

    def test_the_shim_re_exports_the_same_objects(self):
        """``from gitpilot.topology_registry import TOPOLOGY_REGISTRY`` binds the
        object, so the shim has to hand out the registry itself."""
        from gitpilot import topology_registry as shim

        assert shim.TOPOLOGY_REGISTRY is registry_mod.TOPOLOGY_REGISTRY
        assert shim.get_topology is registry_mod.get_topology
        assert shim.classify_message is registry_mod.classify_message

    def test_a_v1_entry_has_no_policy_document(self):
        """``policy is None`` is how a caller tells a hand-written topology from a
        document, and it is what keeps an unmigrated topology unrestricted."""
        from gitpilot.topology_registry import get_policy

        assert get_policy("classic") is None
        assert LEGACY_TOPOLOGIES["default"].policy is None

    def test_the_pilot_topology_now_has_one(self):
        from gitpilot.topology_registry import get_policy

        policy = get_policy("tool_augmented_react")

        assert policy is not None
        assert policy.execution.engine is Engine.agentic_loop
        assert policy.source == "builtin"

    def test_an_unknown_id_still_saves_as_a_400(self, tmp_path, monkeypatch):
        """The endpoint turns this ValueError into a 400 — unchanged behaviour."""
        from gitpilot import settings as settings_mod
        from gitpilot.topology_registry import save_topology_preference

        monkeypatch.setattr(settings_mod, "CONFIG_DIR", tmp_path)
        with pytest.raises(ValueError, match="Unknown topology"):
            save_topology_preference("no_such_topology")

    def test_an_unknown_id_resolves_to_none(self):
        assert registry_mod.resolve_id("no_such_topology") is None
        assert registry_mod.get_topology("no_such_topology") is None

    def test_an_unknown_graph_request_falls_back_to_default(self):
        graph = registry_mod.get_topology_graph("no_such_topology")

        assert graph["topology_id"] == "default"

    def test_an_alias_resolves_to_its_topology(self, tmp_path, monkeypatch):
        """A saved preference outlives the id it names (§15.3 retires ids)."""
        path = tmp_path / "topologies.yaml"
        path.write_text(
            "topologies:\n  - id: successor\n    aliases: [predecessor]\n", encoding="utf-8",
        )
        monkeypatch.setattr(registry_mod, "USER_TOPOLOGIES_FILE", path)
        registry_mod.refresh()
        try:
            assert registry_mod.resolve_id("predecessor") == "successor"
            assert registry_mod.get_topology("predecessor").id == "successor"
        finally:
            monkeypatch.undo()
            registry_mod.refresh()

    def test_a_hidden_topology_resolves_but_is_not_offered(self, tmp_path, monkeypatch):
        path = tmp_path / "topologies.yaml"
        path.write_text(
            "topologies:\n  - id: retired\n    hidden: true\n", encoding="utf-8",
        )
        monkeypatch.setattr(registry_mod, "USER_TOPOLOGIES_FILE", path)
        registry_mod.refresh()
        try:
            listed = {entry["id"] for entry in registry_mod.list_topologies()}
            assert "retired" not in listed
            assert registry_mod.get_topology("retired") is not None
            assert "retired" in {
                entry["id"] for entry in registry_mod.list_topologies(include_hidden=True)
            }
        finally:
            monkeypatch.undo()
            registry_mod.refresh()

    def test_a_document_overlays_the_legacy_entry_with_the_same_id(self):
        """One topology migrates at a time, and the picker never shows both."""
        from gitpilot.topology_registry import TOPOLOGY_REGISTRY

        assert TOPOLOGY_REGISTRY["tool_augmented_react"].policy is not None
        assert LEGACY_TOPOLOGIES["tool_augmented_react"].policy is None
        assert len([
            entry for entry in registry_mod.list_topologies()
            if entry["id"] == "tool_augmented_react"
        ]) == 1

    def test_refresh_mutates_the_registry_in_place(self):
        from gitpilot.topology_registry import TOPOLOGY_REGISTRY

        assert registry_mod.refresh() is TOPOLOGY_REGISTRY
