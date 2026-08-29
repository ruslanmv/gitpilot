"""Pipelines as policies — Batch V4-G3.

Five CrewAI pipelines become five documents over the one engine. Two things have
to be true at once and they pull in opposite directions:

* the documents must describe real policy — capabilities, verification, limits —
  and the strongest way to check that is to *run* one and see the mask hold;
* nothing may be rerouted yet, so with ``agent_loop_default`` off the legacy
  dispatcher must still find a sequence to run for every one of the five.

The §19.1 trace test lives here, on ``code_inspector``, and the difference from
Batch V4-E6's version is where the mask comes from: E6 built a read-only
``ResolvedPolicy`` in the test, so it proved the *engine* enforces read-only. This
one loads ``defaults/code_inspector.yaml`` and proves the *shipped topology* does.
A document that says read-only and grants ``fs.write`` would pass E6 and fail here.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from gitpilot.agent.contracts import Dialect
from gitpilot.agent.journal import LineType, read_journal
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.policy import PolicyEngine, resolve_policy
from gitpilot.agent.providers.base import ProviderResponse, RawToolCall
from gitpilot.agent.prompts import ROLE_PROMPTS
from gitpilot.agent.runner import (
    FLAG_LEGACY_CREW_ENGINES,
    AgentRunner,
    RunRequest,
    apply_topology_policy,
    should_use_loop,
)
from gitpilot.topology import registry as registry_mod
from gitpilot.topology.schema import ROLE_PROFILES, Engine, RoutingStrategy

PIPELINES = ("feature_builder", "bug_hunter", "code_inspector", "architect_mode", "quick_fix")
READ_ONLY_PIPELINES = ("code_inspector", "architect_mode")
WRITING_PIPELINES = ("feature_builder", "bug_hunter", "quick_fix")

MUTATING_TOOLS = frozenset({
    "fs.write", "fs.edit", "fs.delete",
    "git.commit", "git.push", "git.branch", "git.stash",
    "github.issue.create", "github.issue.comment", "github.pr.create",
})


# ---------------------------------------------------------------------------
# All five, as documents
# ---------------------------------------------------------------------------


class TestAllFiveMigrated:
    @pytest.mark.parametrize("topology_id", PIPELINES)
    def test_it_has_a_policy_document(self, topology_id):
        policy = registry_mod.get_policy(topology_id)

        assert policy is not None
        assert policy.source == "builtin"
        assert policy.execution.engine is Engine.agentic_loop

    @pytest.mark.parametrize("topology_id", PIPELINES)
    def test_it_stays_a_pipeline_in_the_picker(self, topology_id):
        assert registry_mod.get_topology(topology_id).category.value == "pipeline"

    @pytest.mark.parametrize("topology_id", PIPELINES)
    def test_it_keeps_its_classifier_hints(self, topology_id):
        """§15.2: routing is unchanged, so the hints have to survive the move."""
        hints = registry_mod.get_topology(topology_id).routing_policy.classifier_hints

        assert len(hints) >= 3

    @pytest.mark.parametrize("topology_id", PIPELINES)
    def test_its_role_has_a_prompt_profile(self, topology_id):
        role = registry_mod.get_policy(topology_id).agent.role

        assert role in ROLE_PROMPTS, f"{topology_id} names a role with no persona"

    def test_the_role_vocabulary_matches_the_prompt_table(self):
        """``ROLE_PROFILES`` is a constant in the schema so parsing does not import
        the prompts module. This is what stops the two drifting."""
        assert set(ROLE_PROFILES) == set(ROLE_PROMPTS)

    @pytest.mark.parametrize("topology_id", PIPELINES)
    def test_its_graph_is_generated_and_consistent(self, topology_id):
        graph = registry_mod.get_topology(topology_id).flow_graph
        node_ids = {node["id"] for node in graph["nodes"]}

        assert "main_agent" in node_ids
        for edge in graph["edges"]:
            assert edge["source"] in node_ids and edge["target"] in node_ids

    @pytest.mark.parametrize("topology_id", READ_ONLY_PIPELINES)
    def test_a_read_only_pipeline_grants_nothing_that_writes(self, topology_id):
        policy = registry_mod.get_policy(topology_id)
        mask = policy.capability_mask()

        assert policy.read_only is True
        for capability in ("fs.write", "fs.edit", "fs.delete", "git.commit", "git.push"):
            assert not mask.allows(capability), capability

    @pytest.mark.parametrize("topology_id", WRITING_PIPELINES)
    def test_a_writing_pipeline_can_write_but_asks_before_committing(self, topology_id):
        mask = registry_mod.get_policy(topology_id).capability_mask()

        assert mask.allows("fs.write")
        assert mask.qualifier("git.commit").force_ask is True
        assert not mask.allows("git.push")

    def test_feature_builder_and_bug_hunter_require_verification(self):
        """A feature or a fix that has not been run is not finished (§15.3)."""
        for topology_id in ("feature_builder", "bug_hunter"):
            assert registry_mod.get_policy(topology_id).verification.tests.value == "required"

    def test_a_read_only_pipeline_verifies_nothing(self):
        """There is nothing to verify in a run that changes nothing — and asking
        for it would make every inspection run the suite for no reason."""
        for topology_id in READ_ONLY_PIPELINES:
            assert registry_mod.get_policy(topology_id).verification.tests.value == "off"

    def test_quick_fix_is_narrow(self):
        """The value of T7 was the narrowness, and narrowness is a limit."""
        policy = registry_mod.get_policy("quick_fix")

        assert policy.limits.max_iterations == 12
        assert policy.agent.subagents == ()
        assert not policy.capability_mask().allows("agent.delegate")

    def test_architect_mode_can_research_outside_the_repo(self):
        """A plan that names a library needs to be able to check it exists."""
        mask = registry_mod.get_policy("architect_mode").capability_mask()

        assert mask.allows("web.search")
        assert mask.allows("web.fetch")

    def test_the_yaml_off_boolean_trap_is_handled(self):
        """YAML 1.1 reads a bare ``off`` as False, and §15.1 spells the value
        ``off``. One file must mean one thing whichever parser read it."""
        from gitpilot.topology.schema import parse_document

        bare = parse_document({"id": "bare_off", "verification": {"tests": False}})
        quoted = parse_document({"id": "quoted_off", "verification": {"tests": "off"}})

        assert bare.policy.verification.tests is quoted.policy.verification.tests

    def test_a_yaml_on_boolean_is_refused_rather_than_guessed(self):
        from gitpilot.topology.schema import SchemaError, parse_document

        with pytest.raises(SchemaError, match="read 'on'/'yes' as a boolean"):
            parse_document({"id": "bare_on", "verification": {"tests": True}})


# ---------------------------------------------------------------------------
# The legacy dispatcher still has something to run
# ---------------------------------------------------------------------------


class TestTheLegacyPathIsIntact:
    @pytest.mark.parametrize("topology_id", PIPELINES)
    def test_it_still_selects_as_a_fixed_sequence(self, topology_id):
        """``agentic.py`` picks a CrewAI pipeline on this and nothing else. A
        migrated pipeline that dropped its sequence would stop running the moment
        its document landed — with the flag still off."""
        routing = registry_mod.get_topology(topology_id).routing_policy

        assert routing.strategy is RoutingStrategy.fixed_sequence
        assert routing.sequence

    @pytest.mark.parametrize("topology_id", PIPELINES)
    def test_its_sequence_is_the_one_the_v1_entry_had(self, topology_id):
        from gitpilot.topology.legacy import LEGACY_TOPOLOGIES

        document = registry_mod.get_topology(topology_id).routing_policy.sequence
        v1 = LEGACY_TOPOLOGIES[topology_id].routing_policy.sequence

        assert document == v1, "the CrewAI dispatcher builds agents from this list"

    @pytest.mark.parametrize("topology_id", PIPELINES)
    def test_with_the_flags_off_it_does_not_reach_the_loop(self, topology_id):
        assert should_use_loop(
            topology_id,
            flag_enabled=False,
            default_flag_enabled=False,
            legacy_engines_enabled=True,
        ) is False

    @pytest.mark.parametrize("topology_id", PIPELINES)
    def test_opting_out_of_crewai_routes_it_to_the_loop(self, topology_id):
        """Otherwise "no legacy engines" would leave a migrated pipeline with
        nothing at all to run."""
        assert should_use_loop(
            topology_id,
            flag_enabled=False,
            default_flag_enabled=False,
            legacy_engines_enabled=False,
        ) is True

    def test_opting_out_does_not_invent_an_engine_for_an_unmigrated_topology(self):
        assert should_use_loop(
            "classic",
            flag_enabled=False,
            default_flag_enabled=False,
            legacy_engines_enabled=False,
        ) is False

    def test_the_opt_out_flag_defaults_to_keeping_the_legacy_engines(self):
        import gitpilot.flags as flags

        flags.clear_all_overrides()
        assert flags.is_on(FLAG_LEGACY_CREW_ENGINES, default=True) is True
        assert should_use_loop("code_inspector") is False


# ---------------------------------------------------------------------------
# §19.1 on the shipped read-only topology
# ---------------------------------------------------------------------------

TASK = (
    "Analyze how this repository's testing infrastructure works. "
    "Do not modify files."
)


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8",
    )
    (root / "Makefile").write_text("test:\n\tpython -m pytest -q\n", encoding="utf-8")
    (root / "src" / "app.py").write_text("def greet(n):\n    return n\n", encoding="utf-8")
    (root / "tests" / "test_app.py").write_text(
        "from src.app import greet\n\n\ndef test_greet():\n    assert greet('x') == 'x'\n",
        encoding="utf-8",
    )
    (root / "tests" / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    return root


class _Inspector:
    """A reviewer working the task, including four things it may not do.

    The refused attempts are the point: a read-only topology whose run never tries
    to write proves nothing about the document.
    """

    name = "recorded"
    model = "gpt-4o-mini"
    supports_native_tools = True
    supports_parallel_tool_calls = False
    supports_streaming = False

    ANSWER = (
        "This project uses pytest. pyproject.toml sets testpaths to tests/, the "
        "suite is tests/test_app.py with a conftest.py, and `make test` runs "
        "python -m pytest -q."
    )

    def __init__(self):
        self.parent_steps = [
            RawToolCall(id="p1", name="fs.read", arguments={"path": "pyproject.toml"}),
            RawToolCall(id="p2", name="fs.glob", arguments={"pattern": "tests/**/*.py"}),
            RawToolCall(id="p3", name="agent.delegate", arguments={
                "agent": "explorer",
                "task": "Describe the layout of the tests/ directory.",
                "expected": "the files and what each covers",
            }),
            RawToolCall(id="p4", name="test.detect", arguments={}),
            # A permitted shell command: reading is in READ_ONLY.
            RawToolCall(id="p5", name="terminal.run", arguments={"command": "cat Makefile"}),
            # Four refusals, each for a different reason.
            RawToolCall(id="p6", name="fs.write", arguments={
                "path": "tests/test_new.py", "content": "def test_x(): pass\n",
            }),
            RawToolCall(id="p7", name="git.commit", arguments={"message": "review notes"}),
            # Not destructive — a plain build. The document's `classes` list is
            # what refuses it, and it must be a denial rather than a prompt.
            RawToolCall(id="p8", name="terminal.run", arguments={"command": "npm install"}),
            RawToolCall(id="p9", name="terminal.run", arguments={"command": "rm -rf tests"}),
        ]
        self.child_steps = [RawToolCall(id="s1", name="fs.list", arguments={"path": "tests"})]

    async def complete(self, messages, *, tools=None, system=None, **opts):
        offered = {getattr(tool, "name", "") for tool in (tools or [])}
        if "agent.delegate" in offered:
            if self.parent_steps:
                return ProviderResponse(
                    text="Let me look.", tool_calls=[self.parent_steps.pop(0)],
                )
            return ProviderResponse(text=self.ANSWER)
        if self.child_steps:
            return ProviderResponse(tool_calls=[self.child_steps.pop(0)])
        return ProviderResponse(text=json.dumps({
            "summary": "tests/ holds test_app.py and a conftest.py",
            "files": ["tests/test_app.py", "tests/conftest.py"],
            "status": "completed",
        }))

    def stream(self, *args, **kwargs):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture()
def inspection(repo, tmp_path):
    """One run of ``code_inspector``, with its policy taken from its own document.

    Nothing about the mask is written in this test. ``apply_topology_policy`` reads
    ``defaults/code_inspector.yaml`` and ``resolve_policy`` turns it into the
    engine's policy, exactly as a real request would.
    """
    from gitpilot.agent.loop import DenyingApprovals, LoopEvents

    profile = resolve_profile("openai", "gpt-4o-mini", cache_path=tmp_path / "c.json")
    assert profile.dialect is Dialect.NATIVE

    request = apply_topology_policy(RunRequest(
        task=TASK,
        session_id="trace-inspector",
        topology_id="code_inspector",
        workspace_root=repo,
        journal_root=tmp_path / "journals",
        session_mode="normal",
    ))
    resolved = resolve_policy(
        session_mode="normal",
        requested_mode=request.requested_mode,
        topology_capabilities=request.topology_capabilities,
        read_only=request.read_only,
        allow_network=False,
    )

    events: list[dict] = []

    async def emit(event_type: str, payload: dict) -> None:
        events.append({"type": event_type, **payload})

    loop, ctx = AgentRunner().build(
        request, provider=_Inspector(), profile=profile,
    )
    loop.policy = PolicyEngine(resolved)
    loop.approvals = DenyingApprovals()
    loop.events = LoopEvents(emit)
    ctx.capabilities = resolved.capabilities

    result = asyncio.run(loop.run(ctx))
    return result, events, ctx, resolved


def _lines(ctx):
    return list(read_journal(ctx.journal.path))


def _of(ctx, line_type):
    return [line for line in _lines(ctx) if line["type"] == line_type]


def _names(events):
    return {e.get("name") for e in events if e["type"] == "tool_start"}


class TestInspectorTrace:
    def test_the_document_produced_a_read_only_run(self, inspection):
        _result, _events, _ctx, resolved = inspection

        assert resolved.read_only is True
        assert request_mode_is_plan(resolved)

    def test_the_run_completes(self, inspection):
        result, _events, _ctx, _resolved = inspection

        assert result.status in ("completed", "degraded"), result.answer

    def test_it_read_the_configuration_and_searched_the_suite(self, inspection):
        _result, events, _ctx, _resolved = inspection
        names = _names(events)

        assert "fs.read" in names
        assert "fs.glob" in names

    def test_the_answer_names_the_real_framework(self, inspection):
        result, _events, _ctx, _resolved = inspection

        assert "pytest" in (result.answer or "").lower()

    def test_nothing_was_modified(self, inspection):
        _result, _events, ctx, _resolved = inspection

        applied = [
            line for line in _of(ctx, LineType.TOOL_RESULT)
            if line.get("tool") in MUTATING_TOOLS and line.get("ok")
        ]
        assert applied == []

    def test_the_file_it_tried_to_write_does_not_exist(self, inspection, repo):
        assert not (repo / "tests" / "test_new.py").exists()

    def test_the_write_was_refused_by_the_document_not_by_luck(self, inspection):
        """``fs.write`` is absent from the document, so the mask denies it — and
        the denial names the capability rather than the mode."""
        _result, _events, ctx, _resolved = inspection

        denials = [
            line for line in _of(ctx, LineType.POLICY_DECISION)
            if line.get("tool") == "fs.write"
        ]
        assert denials, "no decision was recorded for the write"
        assert denials[0]["verdict"] == "deny"
        assert "not granted" in denials[0]["reason"]

    def _shell_decisions(self, ctx):
        return {
            line.get("command_class"): line["verdict"]
            for line in _of(ctx, LineType.POLICY_DECISION)
            if line.get("tool") == "terminal.run"
        }

    def test_a_build_command_was_denied_not_queued_for_approval(self, inspection):
        """`terminal.run: {classes: [READ_ONLY, TEST]}`. Asking a human to approve
        `npm install` during a code review is asking the wrong question — the
        document already answered it."""
        _result, _events, ctx, _resolved = inspection

        assert self._shell_decisions(ctx)["MUTATING"] == "deny"

    def test_a_destructive_command_was_denied(self, inspection):
        _result, _events, ctx, _resolved = inspection

        assert self._shell_decisions(ctx)["DESTRUCTIVE"] == "deny"

    def test_a_read_only_command_reached_a_human_rather_than_a_refusal(self, inspection):
        """The classes list is a restriction, not a blanket refusal: `cat Makefile`
        changes nothing, so the read-only gate lets it through and the *risk*
        ratchet asks — ``terminal.run`` is declared HIGH_RISK and no stage may
        lower a verdict below a tool's own risk.

        The distinction being pinned is refusal versus prompt. A build is refused
        because the document says which classes it accepts; a read is offered to
        the user, who can approve it."""
        _result, _events, ctx, _resolved = inspection

        assert self._shell_decisions(ctx)["READ_ONLY"] == "ask"

    def test_nothing_but_the_shell_ever_asked(self, inspection):
        """§19.1's "zero ask decisions", as far as it holds honestly: reads,
        searches, delegation and detection all resolve without a human. Only the
        shell — a HIGH_RISK tool by declaration — reaches one."""
        _result, _events, ctx, _resolved = inspection

        asked = {
            line.get("tool") for line in _of(ctx, LineType.POLICY_DECISION)
            if line.get("verdict") == "ask"
        }
        assert asked <= {"terminal.run"}, sorted(asked)

    def test_every_call_was_decided_before_it_ran(self, inspection):
        _result, _events, ctx, _resolved = inspection
        decided: set[str] = set()

        for line in _lines(ctx):
            if line["type"] == LineType.POLICY_DECISION:
                decided.add(line.get("call_id", ""))
            elif line["type"] == LineType.TOOL_RESULT:
                assert line.get("call_id") in decided, line

    def test_a_subagent_ran_under_the_documents_grants(self, inspection):
        """The child's mask is parent ∩ template, and the parent's came from the
        document — so a read-only topology cannot spawn a writing child."""
        _result, _events, ctx, _resolved = inspection

        delegations = [
            line for line in _of(ctx, LineType.TOOL_RESULT)
            if line.get("tool") == "agent.delegate"
        ]
        assert delegations
        assert delegations[0].get("ok")

        applied = [
            line for line in _of(ctx, LineType.TOOL_RESULT)
            if line.get("tool") in MUTATING_TOOLS and line.get("ok")
        ]
        assert applied == []

    def test_the_role_prompt_reached_the_system_payload(self, inspection):
        _result, _events, ctx, _resolved = inspection

        assert "reviewing code" in ctx.system_payload.lower()

    def test_the_journal_is_complete_and_ordered(self, inspection):
        _result, _events, ctx, _resolved = inspection
        lines = _lines(ctx)

        assert lines[0]["type"] == LineType.RUN_STARTED
        assert lines[-1]["type"] == LineType.RUN_FINISHED
        assert [line["seq"] for line in lines] == sorted(line["seq"] for line in lines)

    def test_the_limits_came_from_the_document(self, inspection):
        _result, _events, ctx, _resolved = inspection

        assert ctx.budgets.max_iterations == 30
        assert ctx.budgets.max_tool_calls == 90


def request_mode_is_plan(resolved) -> bool:
    """``approval.mode: plan`` in the document became plan mode in the run."""
    return resolved.mode.value == "plan"
