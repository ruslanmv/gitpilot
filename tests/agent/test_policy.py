"""The PolicyEngine — Batch V4-D1."""
from __future__ import annotations

import asyncio

import pytest

from gitpilot.agent.policy import (
    CapabilityMask,
    Decision,
    PolicyEngine,
    Qualifier,
    ResolvedPolicy,
    mask_from_tool_policy,
    resolve_policy,
    restrict_mode,
)
from gitpilot.permissions import Action, PermissionMode, PermissionPolicy
from gitpilot.tool_groups import ToolPolicy
from gitpilot.toolkit import ToolCall
from gitpilot.toolkit.builtins import build_default_registry


class _Ctx:
    """The two attributes the engine reads off a context."""

    def __init__(self, registry):
        self.tool_context = type("_TC", (), {"extras": {"registry": registry}})()
        self.run_id = "run_test"


@pytest.fixture
def registry():
    return build_default_registry(include_git=True, include_forge=True)


@pytest.fixture
def ctx(registry):
    return _Ctx(registry)


def _authorize(engine, ctx, tool, **arguments):
    call = ToolCall(id="c1", tool=tool, arguments=arguments)
    return asyncio.run(engine.authorize(call, ctx))


def _engine(**kwargs):
    return PolicyEngine(ResolvedPolicy(**kwargs))


# ---------------------------------------------------------------------------
# The default mask
# ---------------------------------------------------------------------------


class TestDefaultVerdicts:
    def test_a_read_is_allowed_without_asking(self, ctx):
        decision = _authorize(_engine(), ctx, "fs.read", path="a.py")
        assert decision.verdict == "allow"
        assert decision.requires_checkpoint is False

    def test_a_write_asks(self, ctx):
        decision = _authorize(_engine(), ctx, "fs.write", path="a.py", content="x")
        assert decision.verdict == "ask"
        assert decision.requires_checkpoint is True

    def test_a_push_asks_and_is_labelled_high_risk(self, ctx):
        decision = _authorize(_engine(), ctx, "git.push")
        assert decision.verdict == "ask"
        assert decision.risk == "high_risk"

    def test_auto_mode_allows_without_prompting_but_still_checkpoints(self, ctx):
        engine = _engine(mode=PermissionMode.AUTO)
        decision = _authorize(engine, ctx, "fs.write", path="a.py", content="x")
        assert decision.verdict == "allow"
        assert decision.requires_checkpoint is True, (
            "auto mode is the one that most needs an undo — nobody saw it coming"
        )


# ---------------------------------------------------------------------------
# Plan mode / read-only
# ---------------------------------------------------------------------------


MUTATING_TOOLS = [
    ("fs.write", {"path": "a.py", "content": "x"}),
    ("fs.edit", {"path": "a.py", "old": "a", "new": "b"}),
    ("fs.delete", {"path": "a.py"}),
    ("terminal.run", {"command": "npm install left-pad"}),
    ("git.commit", {"message": "x"}),
    ("git.push", {}),
    ("github.pr.create", {"title": "x", "body": "y"}),
    ("test.run", {}),
]


class TestPlanMode:
    @pytest.mark.parametrize(("tool", "arguments"), MUTATING_TOOLS)
    def test_every_mutating_tool_is_denied(self, ctx, tool, arguments):
        decision = _authorize(_engine(mode=PermissionMode.PLAN), ctx, tool, **arguments)
        assert decision.verdict == "deny"
        assert "read-only" in decision.reason

    @pytest.mark.parametrize("tool", ["fs.read", "fs.list", "fs.glob", "fs.grep"])
    def test_reads_still_work(self, ctx, tool):
        decision = _authorize(_engine(mode=PermissionMode.PLAN), ctx, tool, path="a.py", pattern="*")
        assert decision.verdict == "allow"

    def test_a_read_only_run_is_the_same_as_plan_mode_for_writes(self, ctx):
        engine = _engine(mode=PermissionMode.NORMAL, allow_mutation=False)
        decision = _authorize(engine, ctx, "fs.write", path="a.py", content="x")
        assert decision.verdict == "deny"

    def test_a_read_only_command_is_asked_rather_than_refused(self, ctx):
        """Changed in Batch V4-G3, deliberately.

        This used to deny, on the reasoning that "a command that only reads today
        can write tomorrow". But by the time the read-only gate runs, the
        classifier has already matched the command against an allowlist of
        binaries — the same evidence that makes ``fs.read`` safe. There is no
        coherent position where ``fs.read`` is permitted in plan mode and ``cat``
        is not, and the blanket refusal meant a read-only reviewer could not run
        ``git status``.

        It still *asks*, because ``terminal.run`` is declared HIGH_RISK and the
        ratchet cannot lower a verdict below the tool's own risk. Two independent
        gates, both doing their job: "could this change the project?" says no,
        "should a human see a shell command?" says yes.
        """
        decision = _authorize(_engine(mode=PermissionMode.PLAN), ctx, "terminal.run", command="ls")

        assert decision.verdict == "ask"
        assert decision.command_class == "READ_ONLY"

    def test_a_safe_read_only_command_tool_is_allowed_outright(self, ctx):
        """``terminal.which`` declares EXECUTES and is SAFE, so nothing is left to
        ask about once the read-only gate is satisfied."""
        decision = _authorize(_engine(mode=PermissionMode.PLAN), ctx, "terminal.which", name="git")

        assert decision.verdict == "allow"

    def test_reading_git_state_is_allowed_in_plan_mode(self, ctx):
        """``git.status``/``diff``/``log`` declared GIT_LOCAL until Batch V4-G3,
        which made every read-only run unable to look at the repository it was
        reviewing."""
        for tool in ("git.status", "git.diff", "git.log"):
            decision = _authorize(_engine(mode=PermissionMode.PLAN), ctx, tool)
            assert decision.verdict == "allow", tool

    def test_running_the_suite_needs_the_topology_to_ask_for_it(self, ctx):
        """A suite run executes arbitrary project code, so a read-only run refuses
        it unless the capability names TEST (§15.3, T5)."""
        from gitpilot.agent.policy import CapabilityMask, Qualifier

        refused = _authorize(_engine(mode=PermissionMode.PLAN), ctx, "test.run")
        assert refused.verdict == "deny"

        opted_in = _engine(
            mode=PermissionMode.PLAN,
            capabilities=CapabilityMask(
                granted={"test.run": Qualifier(classes=("TEST",))},
                open_by_default=True,
            ),
        )
        assert _authorize(opted_in, ctx, "test.run").verdict == "ask"


# ---------------------------------------------------------------------------
# Mode resolution (F11)
# ---------------------------------------------------------------------------


class TestModeResolution:
    def test_a_request_cannot_escalate_normal_to_auto(self):
        """The body of a chat request is not an authenticated channel."""
        assert restrict_mode("normal", "auto") is PermissionMode.NORMAL

    def test_a_request_can_restrict_auto_to_plan(self):
        assert restrict_mode("auto", "plan") is PermissionMode.PLAN

    def test_a_request_can_restrict_normal_to_plan(self):
        assert restrict_mode("normal", "plan") is PermissionMode.PLAN

    def test_no_request_mode_keeps_the_session_mode(self):
        assert restrict_mode("auto", None) is PermissionMode.AUTO
        assert restrict_mode("auto", "") is PermissionMode.AUTO

    def test_nonsense_falls_back_rather_than_raising(self):
        assert restrict_mode("wibble", None) is PermissionMode.NORMAL
        assert restrict_mode("plan", "wibble") is PermissionMode.PLAN

    def test_resolution_runs_through_one_function(self, ctx):
        policy = resolve_policy(session_mode="auto", requested_mode="plan")
        assert policy.mode is PermissionMode.PLAN
        assert policy.read_only is True


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_an_ungranted_capability_is_denied(self, ctx):
        mask = CapabilityMask.from_mapping({"fs.read": True})
        decision = _authorize(_engine(capabilities=mask), ctx, "fs.write", path="a.py")
        assert decision.verdict == "deny"
        assert "not granted" in decision.reason

    def test_an_ungranted_tool_is_not_even_offered(self, registry):
        """Offering a tool we would only deny wastes a turn and teaches the model
        that its tools are unreliable."""
        mask = CapabilityMask.from_mapping({"fs.read": True, "fs.glob": True})
        names = {schema["name"] for schema in registry.schemas(mask)}
        assert names == {"fs.read", "fs.glob"}

    def test_a_family_grant_covers_its_members(self, ctx):
        mask = CapabilityMask.from_mapping({"fs.*": True})
        assert _authorize(_engine(capabilities=mask), ctx, "fs.read", path="a.py").verdict == "allow"

    def test_an_explicit_denial_beats_its_family_grant(self, ctx):
        mask = CapabilityMask.from_mapping({"fs.*": True, "fs.delete": False})
        assert mask.allows("fs.read") is True
        assert mask.allows("fs.delete") is False

    def test_the_ask_literal_forces_approval_regardless_of_risk(self, ctx):
        mask = CapabilityMask.from_mapping({"fs.read": "ask"})
        decision = _authorize(_engine(capabilities=mask), ctx, "fs.read", path="a.py")
        assert decision.verdict == "ask", "a safe tool marked `ask` must still ask"

    def test_a_path_qualifier_is_honoured(self, ctx):
        mask = CapabilityMask.from_mapping({"fs.*": {"paths": "src/**"}})
        engine = _engine(capabilities=mask, mode=PermissionMode.AUTO)

        assert _authorize(engine, ctx, "fs.write", path="src/a.py").verdict == "allow"
        outside = _authorize(engine, ctx, "fs.write", path="docs/a.md")
        assert outside.verdict == "deny"
        assert "outside the paths" in outside.reason

    def test_an_exclude_qualifier_wins_over_paths(self, ctx):
        mask = CapabilityMask.from_mapping(
            {"fs.*": {"paths": "src/**", "exclude": ["**/*.lock"]}},
        )
        engine = _engine(capabilities=mask, mode=PermissionMode.AUTO)
        assert _authorize(engine, ctx, "fs.write", path="src/uv.lock").verdict == "deny"

    def test_a_network_qualifier_denies_a_network_tool(self, ctx):
        mask = CapabilityMask.from_mapping({"git.*": {"network": False}})
        decision = _authorize(_engine(capabilities=mask), ctx, "git.push")
        assert decision.verdict == "deny"
        assert "network" in decision.reason

    def test_intersecting_masks_never_widens_a_grant(self):
        topology = CapabilityMask.from_mapping({"fs.read": True, "fs.write": True})
        mode = CapabilityMask.from_mapping({"fs.read": True})

        merged = topology.intersect(mode)

        assert merged.allows("fs.read") is True
        assert merged.allows("fs.write") is False

    def test_intersecting_keeps_the_stricter_qualifier(self):
        wide = CapabilityMask.from_mapping({"fs.write": {"paths": "src/**"}})
        narrow = CapabilityMask.from_mapping({"fs.write": {"exclude": ["**/*.lock"]}})

        qualifier = wide.intersect(narrow).qualifier("fs.write")

        assert qualifier is not None
        assert "**/*.lock" in qualifier.exclude


# ---------------------------------------------------------------------------
# ToolPolicy absorption (F5)
# ---------------------------------------------------------------------------


class TestToolPolicyAbsorption:
    def test_a_mode_group_list_becomes_a_capability_mask(self):
        policy = ToolPolicy.from_mode_groups(["read"])
        mask = mask_from_tool_policy(policy)

        assert mask.allows("fs.read") is True
        assert mask.allows("fs.write") is False

    def test_the_edit_guard_regex_becomes_a_path_qualifier(self):
        """F5: the guard could never match a canonical tool id before, because
        the names it was written against were a different scheme entirely."""
        policy = ToolPolicy.from_mode_groups([{"edit": {"fileRegex": "^src/.+"}}])
        qualifier = mask_from_tool_policy(policy).qualifier("fs.write")

        assert qualifier is not None
        assert qualifier.permits_path("src/app.py") is True
        assert qualifier.permits_path("docs/readme.md") is False

    def test_an_empty_policy_stays_permissive(self):
        assert mask_from_tool_policy(ToolPolicy.permissive()).allows("anything.at.all") is True

    def test_the_regex_is_still_enforced_when_it_cannot_be_globbed(self, ctx):
        """A regex we cannot translate is applied through EditGuard rather than
        quietly dropped."""
        policy = ToolPolicy.from_mode_groups(
            ["edit", {"edit": {"fileRegex": r".*\.py$"}}],
        )
        engine = _engine(
            tool_policy=policy, mode=PermissionMode.AUTO,
            capabilities=CapabilityMask.permissive(),
        )

        assert _authorize(engine, ctx, "fs.write", path="a.py").verdict == "allow"
        assert _authorize(engine, ctx, "fs.write", path="a.md").verdict == "deny"


# ---------------------------------------------------------------------------
# Paths (PermissionManager's first caller)
# ---------------------------------------------------------------------------


class TestBlockedPaths:
    @pytest.mark.parametrize("path", [".env", ".env.local", "id_rsa.pem", "secrets.json"])
    def test_a_blocked_path_is_refused_for_writes(self, ctx, path):
        decision = _authorize(_engine(), ctx, "fs.write", path=path, content="x")
        assert decision.verdict == "deny"

    @pytest.mark.parametrize("path", [".env", "credentials.yml"])
    def test_a_blocked_path_is_refused_for_reads_too(self, ctx, path):
        """The point of routing every call through the engine: exfiltration by
        reading is not better than by writing."""
        decision = _authorize(_engine(), ctx, "fs.read", path=path)
        assert decision.verdict == "deny"

    def test_a_lite_synthesized_read_goes_through_the_same_check(self, ctx):
        """A 1.5B model's prefetch is a `fs.read` like any other, so the same
        rule protects it — that is the whole reason the dialects synthesize
        canonical calls instead of reading files themselves."""
        call = ToolCall(id="lite-1", tool="fs.read", arguments={"path": ".env"})
        decision = asyncio.run(_engine().authorize(call, ctx))
        assert decision.verdict == "deny"

    def test_a_custom_blocked_list_is_honoured(self, ctx):
        engine = _engine(permissions=PermissionPolicy(blocked_paths=["private/*"]))
        assert _authorize(engine, ctx, "fs.read", path="private/notes.md").verdict == "deny"
        assert _authorize(engine, ctx, "fs.read", path="public/notes.md").verdict == "allow"


class TestAllowedActions:
    def test_an_action_outside_the_allowlist_is_denied(self, ctx):
        engine = _engine(
            permissions=PermissionPolicy(allowed_actions={Action.READ_FILE}),
        )
        assert _authorize(engine, ctx, "fs.read", path="a.py").verdict == "allow"
        assert _authorize(engine, ctx, "git.push").verdict == "deny"


# ---------------------------------------------------------------------------
# Session scope
# ---------------------------------------------------------------------------


class TestSessionScope:
    def test_allow_for_session_stops_asking(self, ctx):
        engine = _engine()
        assert _authorize(engine, ctx, "fs.write", path="a.py").verdict == "ask"

        engine.allow_for_session("fs.write")

        assert _authorize(engine, ctx, "fs.write", path="a.py").verdict == "allow"

    def test_a_session_grant_does_not_override_a_denial(self, ctx):
        """"Allow for session" answers an approval; it is not a capability."""
        engine = _engine(mode=PermissionMode.PLAN)
        engine.allow_for_session("fs.write")
        assert _authorize(engine, ctx, "fs.write", path="a.py").verdict == "deny"


# ---------------------------------------------------------------------------
# The escalate-only property
# ---------------------------------------------------------------------------


class TestEscalateOnly:
    def test_a_decision_can_be_raised(self):
        decision = Decision(verdict="allow", risk="safe")
        raised = decision.escalated_to("deny", "because", risk="high_risk")
        assert raised.verdict == "deny"
        assert raised.risk == "high_risk"

    def test_a_decision_cannot_be_lowered(self):
        decision = Decision(verdict="deny", reason="destructive", risk="high_risk")
        lowered = decision.escalated_to("allow", "looks fine to me", risk="safe")
        assert lowered.verdict == "deny"
        assert lowered.reason == "destructive"
        assert lowered.risk == "high_risk"

    def test_a_harmless_looking_command_cannot_argue_a_tool_down(self, ctx):
        """`terminal.run` is high-risk whatever it is asked to run; classifying
        `ls` as READ_ONLY must not turn that into an allow."""
        decision = _authorize(_engine(), ctx, "terminal.run", command="ls -la")
        assert decision.verdict == "ask"
        assert decision.risk == "high_risk"


# ---------------------------------------------------------------------------
# Unknown tools
# ---------------------------------------------------------------------------


class TestUnknownTools:
    def test_an_unknown_tool_is_treated_as_mutating(self, ctx):
        decision = _authorize(_engine(), ctx, "wibble.frobnicate")
        assert decision.verdict in ("ask", "deny")
        assert decision.requires_checkpoint is True

    def test_an_unknown_tool_is_denied_in_plan_mode(self, ctx):
        decision = _authorize(_engine(mode=PermissionMode.PLAN), ctx, "wibble.frobnicate")
        assert decision.verdict == "deny"

    def test_a_context_without_a_registry_still_produces_a_verdict(self):
        class _Bare:
            tool_context = None
            run_id = "r"

        call = ToolCall(id="c", tool="fs.write", arguments={"path": "a.py"})
        decision = asyncio.run(_engine().authorize(call, _Bare()))
        assert decision.verdict in ("ask", "deny")


class TestQualifierParsing:
    @pytest.mark.parametrize(("value", "granted"), [
        (True, True), (False, False), (None, False),
        ({"paths": "src/**"}, True), ("ask", True), ("yes", True),
    ])
    def test_each_yaml_shape(self, value, granted):
        assert (Qualifier.parse(value) is not None) is granted

    def test_max_depth_is_read_for_delegation(self):
        qualifier = Qualifier.parse({"max_depth": 1})
        assert qualifier is not None
        assert qualifier.max_depth == 1
