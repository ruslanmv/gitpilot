"""A topology document reaching the engine — Batch V4-G1.

The schema tests prove a document *parses*. These prove it *lands*: that
``RunRequest`` ends up carrying the capabilities, limits, verification and mode
the document declared, and — the part that matters — that every field is filled
restrictively, so a document can tighten a caller's request and never widen it.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from gitpilot.agent.contracts import Dialect
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.runner import RunRequest, apply_topology_policy
from gitpilot.topology import registry as registry_mod
from gitpilot.topology.schema import parse_document

PILOT = "tool_augmented_react"


@pytest.fixture()
def document(tmp_path, monkeypatch):
    """A user topology with known contents, visible to the registry."""
    path = tmp_path / "topologies.yaml"
    path.write_text(
        """
topologies:
  - id: wiring_probe
    name: Wiring Probe
    execution:
      engine: agentic_loop
      dialect: lite
    capabilities:
      fs.read: true
      fs.grep: true
      git.status: true
    approval:
      mode: plan
    verification:
      tests: required
      max_fix_cycles: 1
    limits:
      max_iterations: 7
      max_tool_calls: 9
      max_runtime_seconds: 60
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(registry_mod, "USER_TOPOLOGIES_FILE", path)
    registry_mod.refresh()
    yield "wiring_probe"
    monkeypatch.undo()
    registry_mod.refresh()


# ---------------------------------------------------------------------------
# What the document fills in
# ---------------------------------------------------------------------------


class TestApplyTopologyPolicy:
    def test_a_topology_without_a_document_is_left_alone(self):
        """The additive guarantee for Phase G: migrating one topology cannot
        change one that has not been migrated. ``classic`` is the one left
        unmigrated on purpose (Batch V4-G5)."""
        request = RunRequest(task="t", topology_id="classic")

        assert apply_topology_policy(request) == request

    def test_an_unknown_topology_is_left_alone(self):
        request = RunRequest(task="t", topology_id="no_such_topology")

        assert apply_topology_policy(request) == request

    def test_the_capabilities_reach_the_request(self, document):
        applied = apply_topology_policy(RunRequest(task="t", topology_id=document))

        assert applied.topology_capabilities == {
            "fs.read": True, "fs.grep": True, "git.status": True,
        }

    def test_the_limits_reach_the_request(self, document):
        applied = apply_topology_policy(RunRequest(task="t", topology_id=document))

        assert applied.max_iterations == 7
        assert applied.max_tool_calls == 9
        assert applied.max_runtime_seconds == 60

    def test_the_verification_policy_reaches_the_request(self, document):
        applied = apply_topology_policy(RunRequest(task="t", topology_id=document))

        assert applied.verification == "required"
        assert applied.max_fix_cycles == 1

    def test_a_read_only_document_makes_the_run_read_only(self, document):
        applied = apply_topology_policy(RunRequest(task="t", topology_id=document))

        assert applied.read_only is True

    def test_the_pilot_document_is_not_read_only(self):
        applied = apply_topology_policy(RunRequest(task="t", topology_id=PILOT))

        assert applied.read_only is False
        assert "fs.write" in (applied.topology_capabilities or {})

    def test_a_pinned_dialect_reaches_the_request(self, document):
        applied = apply_topology_policy(RunRequest(task="t", topology_id=document))

        assert applied.dialect == "lite"

    def test_dialect_auto_pins_nothing(self):
        applied = apply_topology_policy(RunRequest(task="t", topology_id=PILOT))

        assert applied.dialect is None

    def test_a_headless_document_denies_instead_of_prompting(self, tmp_path, monkeypatch):
        path = tmp_path / "topologies.yaml"
        path.write_text(
            "topologies:\n  - id: unattended\n    approval:\n      mode: none\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(registry_mod, "USER_TOPOLOGIES_FILE", path)
        registry_mod.refresh()
        try:
            applied = apply_topology_policy(RunRequest(task="t", topology_id="unattended"))
            assert applied.approvals == "none"
        finally:
            monkeypatch.undo()
            registry_mod.refresh()


# ---------------------------------------------------------------------------
# Restrictively, in every direction
# ---------------------------------------------------------------------------


class TestItOnlyTightens:
    def test_a_callers_tighter_iteration_cap_survives(self, document):
        applied = apply_topology_policy(
            RunRequest(task="t", topology_id=document, max_iterations=3),
        )

        assert applied.max_iterations == 3

    def test_a_callers_looser_iteration_cap_is_capped(self, document):
        applied = apply_topology_policy(
            RunRequest(task="t", topology_id=document, max_iterations=500),
        )

        assert applied.max_iterations == 7

    def test_a_read_only_caller_stays_read_only_under_a_writing_document(self):
        applied = apply_topology_policy(
            RunRequest(task="t", topology_id=PILOT, read_only=True),
        )

        assert applied.read_only is True

    def test_the_stricter_of_the_two_modes_wins(self, document):
        """The document says ``plan``; a caller asking for ``auto`` does not undo
        it, and a caller already at ``plan`` is not loosened either."""
        assert apply_topology_policy(
            RunRequest(task="t", topology_id=document, requested_mode="auto"),
        ).requested_mode == "plan"
        assert apply_topology_policy(
            RunRequest(task="t", topology_id=document, requested_mode="plan"),
        ).requested_mode == "plan"

    def test_a_documents_mode_applies_when_the_caller_asked_for_none(self, document):
        applied = apply_topology_policy(RunRequest(task="t", topology_id=document))

        assert applied.requested_mode == "plan"

    def test_a_callers_mode_survives_a_session_default_document(self):
        applied = apply_topology_policy(
            RunRequest(task="t", topology_id=PILOT, requested_mode="plan"),
        )

        assert applied.requested_mode == "plan"

    def test_capabilities_the_caller_set_are_not_overwritten(self):
        """A subagent spawn passes its own intersected mask; the registry must not
        hand the child the parent topology's grants back."""
        narrow = {"fs.read": True}
        applied = apply_topology_policy(
            RunRequest(task="t", topology_id=PILOT, topology_capabilities=narrow),
        )

        assert applied.topology_capabilities == narrow

    def test_a_callers_approver_is_not_replaced(self, tmp_path, monkeypatch):
        path = tmp_path / "topologies.yaml"
        path.write_text(
            "topologies:\n  - id: unattended2\n    approval:\n      mode: none\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(registry_mod, "USER_TOPOLOGIES_FILE", path)
        registry_mod.refresh()
        try:
            sentinel = object()
            applied = apply_topology_policy(
                RunRequest(task="t", topology_id="unattended2", approvals=sentinel),
            )
            assert applied.approvals is sentinel
        finally:
            monkeypatch.undo()
            registry_mod.refresh()


# ---------------------------------------------------------------------------
# Through the runner
# ---------------------------------------------------------------------------


class TestThroughTheRunner:
    def _profile(self, tmp_path, family="openai", model="gpt-4o-mini"):
        return resolve_profile(family, model, cache_path=tmp_path / "probe.json")

    def test_the_limits_become_the_run_budget(self, tmp_path, document):
        from gitpilot.agent.runner import AgentRunner

        request = apply_topology_policy(
            RunRequest(task="t", topology_id=document, journal_root=tmp_path),
        )
        _loop, ctx = AgentRunner().build(
            request, provider=_FakeProvider(), profile=self._profile(tmp_path),
        )

        assert ctx.budgets.max_iterations == 7
        assert ctx.budgets.max_tool_calls == 9
        assert ctx.budgets.max_runtime_seconds == 60

    def test_a_pinned_lite_dialect_is_honoured(self, tmp_path, document):
        """T8 stops being a separate planner universe by being this (§6.4)."""
        from gitpilot.agent.runner import AgentRunner

        request = apply_topology_policy(
            RunRequest(task="t", topology_id=document, journal_root=tmp_path),
        )
        _loop, ctx = AgentRunner().build(
            request, provider=_FakeProvider(), profile=self._profile(tmp_path),
        )

        assert ctx.model_profile.dialect is Dialect.LITE

    def test_an_unknown_pinned_dialect_is_ignored(self, tmp_path, caplog):
        from gitpilot.agent.runner import AgentRunner

        request = RunRequest(
            task="t", topology_id="default", journal_root=tmp_path, dialect="klingon",
        )
        with caplog.at_level("WARNING"):
            _loop, ctx = AgentRunner().build(
                request, provider=_FakeProvider(), profile=self._profile(tmp_path),
            )

        assert ctx.model_profile.dialect is Dialect.NATIVE
        assert "unknown pinned dialect" in caplog.text


class TestForcedDialect:
    def _profile(self, tmp_path, family, model):
        return resolve_profile(family, model, cache_path=tmp_path / f"{model}.json")

    def test_pinning_lite_on_a_frontier_model_is_honoured(self, tmp_path):
        profile = self._profile(tmp_path, "claude", "claude-sonnet-4-5")
        assert profile.dialect is Dialect.NATIVE

        pinned = profile.forced(Dialect.LITE)

        assert pinned.dialect is Dialect.LITE
        assert pinned.max_tool_schemas <= 6
        assert pinned.schema_verbosity == "names_only"
        assert pinned.supports_parallel_tool_calls is False
        assert "pinned" in pinned.source

    def test_pinning_native_on_a_model_that_cannot_is_refused(self, tmp_path, caplog):
        """A topology is configuration; the profile is measurement. Obeying the
        pin would produce a run where every tool call comes back malformed."""
        profile = self._profile(tmp_path, "ollama", "qwen2.5:1.5b")
        assert profile.dialect is Dialect.LITE

        with caplog.at_level("WARNING"):
            pinned = profile.forced(Dialect.NATIVE)

        assert pinned.dialect is Dialect.LITE
        assert "keeping lite" in caplog.text

    def test_pinning_the_dialect_it_already_speaks_changes_nothing(self, tmp_path):
        profile = self._profile(tmp_path, "openai", "gpt-4o-mini")

        assert profile.forced(profile.dialect) is profile

    def test_the_context_window_is_not_touched(self, tmp_path):
        """Pinning the dialect says how to talk to the model, not what it is."""
        profile = self._profile(tmp_path, "claude", "claude-sonnet-4-5")

        assert profile.forced(Dialect.LITE).context_window == profile.context_window


# ---------------------------------------------------------------------------
# The mask the document produces
# ---------------------------------------------------------------------------


class TestResolvedPolicy:
    def test_resolve_policy_honours_the_documents_capabilities(self, document):
        from gitpilot.agent.policy import resolve_policy

        applied = apply_topology_policy(RunRequest(task="t", topology_id=document))
        resolved = resolve_policy(
            session_mode="normal",
            requested_mode=applied.requested_mode,
            topology_capabilities=applied.topology_capabilities,
            read_only=applied.read_only,
        )

        assert resolved.capabilities.allows("fs.read")
        assert not resolved.capabilities.allows("fs.write")
        assert resolved.read_only is True

    def test_the_pilot_document_still_asks_before_committing(self):
        from gitpilot.agent.policy import resolve_policy

        applied = apply_topology_policy(RunRequest(task="t", topology_id=PILOT))
        resolved = resolve_policy(
            session_mode="normal", topology_capabilities=applied.topology_capabilities,
        )

        assert resolved.capabilities.qualifier("git.commit").force_ask is True
        assert not resolved.capabilities.allows("git.push")

    def test_every_capability_the_pilot_names_is_a_real_tool(self):
        """A document naming a tool that does not exist grants nothing and reads
        as though it grants something."""
        from gitpilot.toolkit.builtins import build_default_registry

        policy = registry_mod.get_policy(PILOT)
        known = {spec.id for spec in build_default_registry(
            include_git=True, include_forge=True,
        ).specs()} | {"agent.delegate"}

        unknown = sorted(set(policy.capabilities) - known)
        assert unknown == [], f"the pilot grants tools that do not exist: {unknown}"

    def test_the_document_round_trips_through_to_dict(self):
        policy = registry_mod.get_policy(PILOT)
        rebuilt = parse_document({"id": PILOT, **policy.to_dict()})

        assert rebuilt.policy.capabilities == policy.capabilities
        assert rebuilt.policy.limits == policy.limits


class _FakeProvider:
    name = "fake"
    model = "gpt-4o-mini"
    supports_native_tools = True
    supports_parallel_tool_calls = False
    supports_streaming = False

    async def complete(self, messages, **kwargs):  # pragma: no cover - never called
        raise NotImplementedError

    def stream(self, *args, **kwargs):  # pragma: no cover - never called
        raise NotImplementedError


def test_replace_keeps_the_request_a_dataclass():
    """``apply_topology_policy`` returns a new request rather than mutating one."""
    request = RunRequest(task="t", topology_id=PILOT)
    applied = apply_topology_policy(request)

    assert applied is not request
    assert request.topology_capabilities is None
    assert replace(request, task="u").task == "u"
