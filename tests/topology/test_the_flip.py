"""The flip — Batch V4-G5.

``default`` now carries the agentic policy document. What these tests pin is the
distance between *declaring* that and *doing* it, because the two land in different
releases on purpose (§15.4):

* the document is in place, and every id that used to name this behaviour still
  resolves;
* ``classic`` exists, is genuinely the old thing, and opting into it genuinely opts
  out of the loop;
* the flag that executes the document is **off**, and turning it on is one line
  whose evidence is a benchmark report the gate has read.

The last one is the point. A flip nobody can undo, or one that happens because a
document was authored, is not a flip — it is a surprise.
"""
from __future__ import annotations

import json

import pytest

from bench.gate import evaluate, evaluate_file
from bench.report import REGRESSION_BUDGET
from gitpilot.agent.runner import (
    AGENT_LOOP_DEFAULT_ON,
    CLASSIC_TOPOLOGY_ID,
    DEFAULT_AGENTIC_TOPOLOGY_ID,
    FLAG_AGENT_LOOP_DEFAULT,
    RunRequest,
    apply_topology_policy,
    should_use_loop,
)
from gitpilot.topology import registry as registry_mod
from gitpilot.topology.legacy import LEGACY_TOPOLOGIES
from gitpilot.topology.schema import Engine, ExecutionStyle


# ---------------------------------------------------------------------------
# The default is the engineer
# ---------------------------------------------------------------------------


class TestDefaultIsTheEngineer:
    def test_default_carries_the_agentic_document(self):
        policy = registry_mod.get_policy("default")

        assert policy is not None
        assert policy.execution.engine is Engine.agentic_loop
        assert policy.source == "builtin"

    def test_it_is_the_full_safe_policy_and_not_a_new_one(self):
        """The flip changes which id the policy lives under, not the policy."""
        policy = registry_mod.get_policy("default")
        mask = policy.capability_mask()

        assert mask.allows("fs.write")
        assert mask.qualifier("git.commit").force_ask is True
        assert not mask.allows("git.push")
        assert policy.agent.role == "software_engineer"

    def test_the_named_constant_points_at_it(self):
        assert DEFAULT_AGENTIC_TOPOLOGY_ID == "default"
        assert CLASSIC_TOPOLOGY_ID == "classic"

    def test_the_default_id_is_still_a_real_registry_entry(self):
        """``get_topology_graph`` falls back to ``registry[DEFAULT_TOPOLOGY_ID]``, so
        making ``default`` an alias of something else would KeyError."""
        assert registry_mod.DEFAULT_TOPOLOGY_ID in registry_mod.TOPOLOGY_REGISTRY

    @pytest.mark.parametrize("retired", ["gitpilot_code", "autonomous_engineer"])
    def test_every_id_this_behaviour_ever_had_still_resolves(self, retired):
        """A preference saved before either rename must not 404."""
        assert registry_mod.resolve_id(retired) == "default"

    def test_the_picker_shows_one_card_for_it(self):
        listed = [entry["id"] for entry in registry_mod.list_topologies()]

        assert listed.count("default") == 1
        assert "autonomous_engineer" not in listed
        assert "gitpilot_code" not in listed

    def test_its_graph_is_generated_from_the_document(self):
        graph = registry_mod.get_topology("default").flow_graph
        node_ids = {node["id"] for node in graph["nodes"]}

        assert "main_agent" in node_ids
        assert "router" not in node_ids, "still the hand-drawn T1 graph"
        for edge in graph["edges"]:
            assert edge["source"] in node_ids and edge["target"] in node_ids


# ---------------------------------------------------------------------------
# classic
# ---------------------------------------------------------------------------


class TestClassic:
    def test_it_is_the_previous_default_unchanged(self):
        """Pinning the previous behaviour must not mean pinning a *slightly
        different* previous behaviour."""
        classic = registry_mod.get_topology("classic")
        v1 = LEGACY_TOPOLOGIES["default"]

        assert classic.policy is None
        assert classic.execution_style is ExecutionStyle.single_task
        assert classic.routing_policy.strategy == v1.routing_policy.strategy
        assert classic.agents_used == v1.agents_used
        assert classic.flow_graph == v1.flow_graph

    def test_it_has_its_own_name_rather_than_the_old_one(self):
        """A picker showing two cards called "Default" helps nobody."""
        assert registry_mod.get_topology("classic").name != LEGACY_TOPOLOGIES["default"].name
        assert "Classic" in registry_mod.get_topology("classic").name

    def test_it_is_offered_in_the_picker(self):
        """The escape hatch has to be findable, not just documented."""
        assert "classic" in {entry["id"] for entry in registry_mod.list_topologies()}

    def test_it_can_be_saved_as_a_preference(self, tmp_path, monkeypatch):
        from gitpilot import settings as settings_mod

        monkeypatch.setattr(settings_mod, "CONFIG_DIR", tmp_path)
        registry_mod.save_topology_preference("classic")

        assert registry_mod.get_saved_topology_preference() == "classic"

    def test_choosing_it_opts_out_of_the_loop_even_with_every_flag_on(self):
        """Opting out has to actually opt out — including when the flip has landed
        and the legacy engines have been opted out of globally."""
        assert should_use_loop(
            "classic",
            flag_enabled=True,
            default_flag_enabled=True,
            legacy_engines_enabled=False,
        ) is False

    def test_it_leaves_a_request_unrestricted(self):
        request = RunRequest(task="t", topology_id="classic")

        assert apply_topology_policy(request) == request

    def test_it_still_dispatches_through_the_legacy_router(self):
        from gitpilot.topology.schema import RoutingStrategy

        routing = registry_mod.get_topology("classic").routing_policy

        assert routing.strategy is RoutingStrategy.classify_and_dispatch


# ---------------------------------------------------------------------------
# Declaring is not doing
# ---------------------------------------------------------------------------


class TestTheFlagGovernsExecution:
    def test_the_flip_has_not_been_taken(self):
        """§15.4: behind ``agent_loop_default``, after the §18 gate. The gate has
        not been demonstrated, so this is False and the default still runs the
        legacy engine."""
        assert AGENT_LOOP_DEFAULT_ON is False

    def test_with_no_flags_set_the_default_takes_the_legacy_path(self):
        import gitpilot.flags as flags

        flags.clear_all_overrides()
        assert should_use_loop("default") is False

    def test_turning_the_flag_on_routes_the_default_to_the_loop(self):
        """The whole of the flip, exercised: this is what changes when the constant
        changes."""
        import gitpilot.flags as flags

        flags.clear_all_overrides()
        flags.set_override(FLAG_AGENT_LOOP_DEFAULT, True)
        try:
            assert should_use_loop("default") is True
        finally:
            flags.clear_all_overrides()

    def test_the_flag_can_be_opted_into_per_machine(self):
        """Until the gate passes, a user who wants it can have it."""
        import gitpilot.flags as flags

        flags.clear_all_overrides()
        assert should_use_loop("default", default_flag_enabled=True) is True

    def test_the_constant_is_what_is_consulted(self, monkeypatch):
        """A test that only checked the constant's value would not notice it being
        ignored."""
        import gitpilot.agent.runner as runner_mod
        import gitpilot.flags as flags

        flags.clear_all_overrides()
        monkeypatch.setattr(runner_mod, "AGENT_LOOP_DEFAULT_ON", True)

        assert should_use_loop("default") is True

    def test_the_document_reaches_a_default_run_once_it_is_on(self, tmp_path):
        """The reason routing the *resolved* id matters: a `default` request must
        run under `default`'s policy, not the pilot's."""
        applied = apply_topology_policy(
            RunRequest(task="t", topology_id="default", journal_root=tmp_path),
        )

        assert applied.topology_capabilities
        assert applied.role == "software_engineer"
        assert applied.max_iterations == 40


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def _tier(name: str, verdict: str, reasons=()) -> dict:
    return {"tier": name, "verdict": verdict, "reasons": list(reasons), "engines": {}}


def _report(tiers, *, missing=(), budget: float = REGRESSION_BUDGET) -> dict:
    return {
        "verdict": "pass",
        "budget": budget,
        "missing_tiers": list(missing),
        "tiers": tiers,
        "cells": [],
    }


class TestGate:
    def test_a_complete_green_matrix_permits_the_flip(self):
        report = _report([
            _tier("frontier", "pass"), _tier("local_8b", "pass"), _tier("local_small", "pass"),
        ])

        passed, reasons = evaluate(report)

        assert passed is True
        assert reasons == []

    def test_one_failing_tier_blocks_it(self):
        report = _report([
            _tier("frontier", "pass"),
            _tier("local_small", "fail", ["success rate 40% < legacy 70%"]),
        ])

        passed, reasons = evaluate(report)

        assert passed is False
        assert any("local_small" in reason and "success rate" in reason for reason in reasons)

    def test_a_tier_that_was_never_run_blocks_it(self):
        """The failure mode: run two tiers, see green, flip for the third."""
        report = _report([_tier("frontier", "pass")], missing=["local_small"])

        passed, reasons = evaluate(report)

        assert passed is False
        assert any("local_small" in reason for reason in reasons)

    def test_an_incomplete_tier_blocks_it(self):
        report = _report([
            _tier("frontier", "pass"),
            _tier("local_8b", "incomplete", ["legacy produced no cells for this tier"]),
        ])

        assert evaluate(report)[0] is False

    def test_an_empty_matrix_blocks_it(self):
        assert evaluate(_report([]))[0] is False

    def test_a_looser_budget_than_the_gate_requires_is_rejected(self):
        """Otherwise the gate could be cleared by scoring the same run twice with a
        kinder ruler."""
        report = _report([_tier("frontier", "pass")], budget=3.0)

        passed, reasons = evaluate(report)

        assert passed is False
        assert any("looser" in reason for reason in reasons)

    def test_a_stricter_budget_in_the_report_is_fine(self):
        report = _report([_tier("frontier", "pass")], budget=1.2)

        assert evaluate(report)[0] is True

    def test_something_that_is_not_a_report_is_rejected(self):
        assert evaluate({"hello": "world"})[0] is False
        assert evaluate([])[0] is False

    def test_a_missing_file_is_not_a_pass(self, tmp_path):
        passed, reasons = evaluate_file(tmp_path / "never-written.json")

        assert passed is False
        assert "no benchmark report" in reasons[0]

    def test_an_unreadable_file_is_not_a_pass(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")

        assert evaluate_file(path)[0] is False

    def test_a_real_report_round_trips_through_the_gate(self, tmp_path):
        """End to end: summarise cells, write the file, read it back, decide."""
        from bench.report import summarise
        from bench.runner import CellMetrics, CellRun
        from bench.tasks import Outcome

        cells = []
        for tier in ("frontier", "local_8b", "local_small"):
            for engine in ("legacy", "loop"):
                cells.append(CellRun(
                    task_id="t", tier=tier, engine=engine,
                    outcome=Outcome.passed(),
                    metrics=CellMetrics(wall_time_s=1.0, prompt_tokens=100),
                    category="edit",
                ))
        path = summarise(cells).write(tmp_path / "results.json")

        assert evaluate_file(path)[0] is True

    def test_the_cli_reports_a_failure_on_stderr(self, tmp_path, capsys):
        from bench.gate import main

        path = tmp_path / "results.json"
        path.write_text(json.dumps(_report([_tier("frontier", "fail", ["too slow"])])), "utf-8")

        assert main([str(path)]) == 1
        captured = capsys.readouterr()
        assert "gate: not met" in captured.err
        assert "too slow" in captured.err

    def test_the_cli_succeeds_on_a_green_report(self, tmp_path, capsys):
        from bench.gate import main

        path = tmp_path / "results.json"
        path.write_text(json.dumps(_report([
            _tier("frontier", "pass"), _tier("local_8b", "pass"), _tier("local_small", "pass"),
        ])), "utf-8")

        assert main([str(path)]) == 0
        assert "gate: pass" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The endpoint routes the resolved id
# ---------------------------------------------------------------------------


class TestTheEndpointUsesTheResolvedTopology:
    def test_the_default_id_is_the_endpoints_fallback(self):
        """Before Batch V4-G5 an unnamed request resolved to ``""``, which
        ``should_use_loop`` could only answer "no" to. Now it resolves to the
        topology whose document decides."""
        from gitpilot import _api_app

        assert _api_app._DEFAULT_TOPOLOGY_ID == "default"

    def test_the_loop_stream_takes_a_topology_rather_than_assuming_one(self):
        """Running a ``default`` request under the pilot's policy would apply a
        policy nobody selected."""
        import inspect

        from gitpilot import _api_app

        signature = inspect.signature(_api_app._stream_agent_loop)

        assert "topology_id" in signature.parameters
