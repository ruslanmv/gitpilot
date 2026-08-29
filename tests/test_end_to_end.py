"""The hello.py end-to-end verification, as a test — Batch V4-H5.

``scripts/verify_end_to_end.py`` exists as a command because the answer depends on
the model in front of it. These tests pin the parts that do *not* depend on the
model, so the plumbing cannot rot between the occasions someone runs it for real:

* every dialect gets from "create hello.py" to a file that prints the right thing;
* at every real model's schema budget the agent is offered a way to read, a way to
  write and a way to run — the property whose absence made an 8B model read-only;
* a grant may name a tool id or its capability group, because every shipped
  topology document names ids and twelve specs declare a group.

The last two are the defects this verification found, which is the argument for
having written it: three phases of tests passed while the default topology silently
withheld every git-read and GitHub tool, and while `llama3:8b` was offered no way to
write a file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.verify_end_to_end import (  # noqa: E402
    EXPECTED_OUTPUT,
    HELLO_SOURCE,
    start_stub,
    verify,
)

from gitpilot.agent.model_profile import resolve_profile  # noqa: E402
from gitpilot.toolkit.builtins import build_default_registry  # noqa: E402
from gitpilot.toolkit.registry import CapabilitySet, SchemaProfile  # noqa: E402
from gitpilot.topology.registry import TOPOLOGY_REGISTRY, get_policy  # noqa: E402

#: One model per dialect, plus a frontier one — the §19.2 matrix's own rungs.
MODELS = [
    ("ollama", "qwen2.5:1.5b", "lite"),
    ("ollama", "llama3:8b", "react_text"),
    ("openai", "gpt-4o-mini", "react_text"),
    ("claude", "claude-sonnet-4-5", "native"),
]


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("family", "model", "dialect"), MODELS, ids=lambda v: str(v))
def test_a_model_writes_hello_py_and_it_runs(family, model, dialect):
    """The whole stack, checked by executing the result rather than reading it.

    The provider makes a real HTTP request to a real server, the dialect parses a
    real response, the policy engine authorises a real ``fs.write``, and the file
    that lands on disk is run by a real subprocess. Only the model's weights are
    stood in for.
    """
    server, base_url = start_stub(model)
    try:
        report = verify(model=model, base_url=base_url, real=False)
    finally:
        server.shutdown()
        server.server_close()

    failed = [(name, detail) for name, ok, detail in report.steps if not ok]
    assert failed == [], failed


def test_the_expected_output_is_what_the_task_asks_for():
    assert EXPECTED_OUTPUT in HELLO_SOURCE


# ---------------------------------------------------------------------------
# The schema budget must leave a working agent
# ---------------------------------------------------------------------------


class _Mask:
    """The default topology's mask, as a ``CapabilitySet``."""

    def __init__(self) -> None:
        policy = get_policy("default")
        assert policy is not None
        self._mask = policy.capability_mask()

    def allows(self, capability: str) -> bool:
        return self._mask.allows(capability)


def _offered(budget: int, capabilities: CapabilitySet) -> set[str]:
    registry = build_default_registry(include_git=True, include_forge=True)
    render = registry.render(capabilities, SchemaProfile(max_tool_schemas=budget))
    return {schema["name"] for schema in render.schemas}


@pytest.mark.parametrize(("family", "model", "_dialect"), MODELS, ids=lambda v: str(v))
def test_every_real_budget_leaves_a_way_to_read_write_and_run(
    family, model, _dialect, tmp_path,
):
    """The defect this found: ``llama3:8b`` has an 8-schema budget, and every write
    tool ranked behind every read tool — so the model most of this programme is for
    was offered a read-only toolset and could not create a file.

    Asserted as a property rather than as a list, because the right *members* of a
    tight budget are a judgement that will change; "the agent can act" is not.
    """
    profile = resolve_profile(family, model, cache_path=tmp_path / "p.json")
    offered = _offered(profile.max_tool_schemas, _Mask())

    assert offered & {"fs.read"}, f"{model} cannot read"
    assert offered & {"fs.write", "fs.edit"}, f"{model} cannot change anything"
    assert offered & {"terminal.run"}, f"{model} cannot run anything"
    assert len(offered) <= profile.max_tool_schemas


def test_the_smallest_budget_still_leaves_the_essentials():
    """Six schemas is the LITE default, and the floor worth defending."""
    offered = _offered(6, _Mask())

    assert {"fs.read", "fs.write", "fs.edit", "terminal.run"} <= offered


# ---------------------------------------------------------------------------
# A grant may name a tool id or its capability group
# ---------------------------------------------------------------------------


class TestGrantsResolveByIdOrGroup:
    def test_twelve_specs_declare_a_group_that_is_not_their_id(self):
        """The premise. If this ever becomes zero, the fallback is dead code and can
        go — but while it holds, a document naming ids needs it."""
        registry = build_default_registry(include_git=True, include_forge=True)
        grouped = [
            spec.id for spec in registry.specs() if spec.capability != spec.id
        ]

        assert len(grouped) >= 10
        assert "git.status" in grouped and "github.pr.create" in grouped

    def test_the_default_topology_can_read_git_state(self):
        """It grants ``git.status``/``git.diff``/``git.log`` by *id*; the specs
        declare ``git.read``. Checking only the group withheld all three."""
        offered = _offered(64, _Mask())

        assert {"git.status", "git.diff", "git.log"} <= offered

    def test_the_default_topology_can_reach_the_forge(self):
        offered = _offered(64, _Mask())

        assert {"github.pr.get", "github.pr.create", "github.issue.list"} <= offered

    def test_an_explicit_denial_still_wins(self):
        """``git.push: false`` has to keep meaning no, whichever name matched."""
        offered = _offered(64, _Mask())

        assert "git.push" not in offered

    @pytest.mark.parametrize("topology_id", sorted(TOPOLOGY_REGISTRY))
    def test_every_shipped_topology_grants_what_it_names(self, topology_id):
        """A document that grants nothing it names is a document that does nothing,
        and eight of them were in that state."""
        policy = get_policy(topology_id)
        if policy is None or not policy.capabilities:
            return   # `classic` has no document

        registry = build_default_registry(include_git=True, include_forge=True)
        mask = policy.capability_mask()
        offered = {spec.id for spec in registry.specs(mask)}

        named = {
            capability for capability, value in policy.capabilities.items()
            if value is not False and value is not None
        }
        real = {spec.id for spec in registry.specs()}
        # Every *tool* the document named by id must be offered.
        missing = sorted((named & real) - offered)
        assert missing == [], f"{topology_id} names but does not grant: {missing}"

    def test_the_policy_engine_agrees_with_the_registry(self):
        """A tool offered because its id was granted must not then be refused at
        call time because its group was not — the two checks have to match."""
        import asyncio

        from gitpilot.agent.policy import PolicyEngine, ResolvedPolicy
        from gitpilot.toolkit import ToolCall, ToolExecutionContext

        registry = build_default_registry(include_git=True, include_forge=True)
        policy = get_policy("default")
        engine = PolicyEngine(ResolvedPolicy(
            capabilities=policy.capability_mask(), allow_network=False,
        ))

        context = ToolExecutionContext(
            session_id="s", run_id="r", extras={"registry": registry},
        )
        context.tool_context = context   # type: ignore[attr-defined]

        for tool in ("git.status", "github.pr.get", "github.pr.create"):
            decision = asyncio.run(engine.authorize(
                ToolCall(id="1", tool=tool, arguments={}), context,
            ))
            assert decision.verdict != "deny", f"{tool}: {decision.reason}"

    def test_an_ask_on_a_tool_id_still_asks(self):
        """``github.pr.create: ask`` has to apply even though the spec's capability
        is the broader ``github.pr.write``."""
        qualifier = _Mask()._mask.qualifier("github.pr.create")   # noqa: SLF001

        assert qualifier is not None
        assert qualifier.force_ask is True
