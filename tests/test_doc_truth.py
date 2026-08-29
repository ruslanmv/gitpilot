"""Docs must not describe execution that does not happen — Batches V4-0E, C4, H5.

A ratchet, not a style check. ``docs/agents.md`` once described ``react_loop`` as
"an iterative reason-act loop with subagents" while both topologies declaring it ran
through the ``single_task`` path. Batch V4-0E pinned that caveat in place for exactly
as long as it was true; Batch V4-C2 shipped the loop, the ratchet fired, and the
caveat was replaced with what actually happens. Batch V4-G5 changed the claim again —
every topology but one now *declares* the agentic engine while a flag decides who
runs it — and the ratchet fired again, which is the whole point of it.

Every assertion below derives from code rather than from prose, so the doc cannot go
stale in either direction:

* the topologies the doc says declare ``agentic_loop`` must really declare it;
* the pilot named in prose must be the one the selector accepts;
* the opt-out the doc offers must exist and must really opt out;
* the documented defaults for both engine flags must match the flags.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DOC = REPO_ROOT / "docs" / "agents.md"
LOOP_MODULE = REPO_ROOT / "gitpilot" / "agent" / "loop.py"

CAVEAT_HEADING = "`react_loop` is not wired yet"

#: Topologies the doc says carry a policy document declaring the agentic engine.
DOCUMENTED_AS_AGENTIC = (
    "default", "lite_mode", "feature_builder", "code_inspector",
    "tool_augmented_react",
)

#: The escape hatch the doc offers a user who wants the pre-v4 behaviour.
DOCUMENTED_OPT_OUT = "classic"


def _executor_exists() -> bool:
    """Whether an ``AgentLoop`` executor has shipped (Batch V4-C2)."""
    return LOOP_MODULE.exists()


def _doc() -> str:
    return AGENTS_DOC.read_text(encoding="utf-8")


def test_react_loop_claim_matches_reality():
    text = _doc()
    has_caveat = CAVEAT_HEADING in text

    if _executor_exists():
        assert not has_caveat, (
            "gitpilot/agent/loop.py exists, so the 'react_loop is not wired yet' "
            "caveat in docs/agents.md is now false — update the doc and this test "
            "to assert the loop's actual behaviour."
        )
    else:
        assert has_caveat, (
            "docs/agents.md documents react_loop as an executing loop, but no "
            "AgentLoop executor exists (gitpilot/agent/loop.py is absent)."
        )


def test_the_doc_points_at_the_work_that_is_still_outstanding():
    """The engine is not finished, and a reader deserves to be told where the rest
    of it is described."""
    assert "upgrade-v4-batches.md" in _doc()


def test_the_doc_names_the_topology_that_really_loops():
    """The pilot named in prose has to be the one the selector accepts."""
    if not _executor_exists():
        return
    from gitpilot.agent.runner import PILOT_TOPOLOGY_ID, should_use_loop

    assert should_use_loop(PILOT_TOPOLOGY_ID, flag_enabled=True) is True
    assert PILOT_TOPOLOGY_ID in _doc()


def test_topologies_documented_as_agentic_really_declare_it():
    """Batch V4-G5's claim, checked against the documents rather than the prose."""
    if not _executor_exists():
        return
    from gitpilot.topology.registry import get_policy

    not_agentic = [
        topology_id for topology_id in DOCUMENTED_AS_AGENTIC
        if (get_policy(topology_id) or None) is None
        or get_policy(topology_id).execution.engine.value != "agentic_loop"
    ]
    assert not_agentic == [], (
        f"docs/agents.md says these declare the agentic engine, and they do not: "
        f"{not_agentic}"
    )


def test_the_documented_opt_out_really_opts_out():
    """A user told they can back out has to actually be able to."""
    if not _executor_exists():
        return
    from gitpilot.agent.runner import CLASSIC_TOPOLOGY_ID, should_use_loop
    from gitpilot.topology.registry import get_topology

    assert DOCUMENTED_OPT_OUT in _doc()
    assert CLASSIC_TOPOLOGY_ID == DOCUMENTED_OPT_OUT
    assert get_topology(DOCUMENTED_OPT_OUT) is not None
    assert should_use_loop(
        DOCUMENTED_OPT_OUT,
        flag_enabled=True,
        default_flag_enabled=True,
        legacy_engines_enabled=False,
    ) is False


def test_the_retired_ids_the_doc_promises_still_resolve():
    if not _executor_exists():
        return
    from gitpilot.topology.registry import resolve_id

    text = _doc()
    for retired in ("gitpilot_code", "autonomous_engineer"):
        assert retired in text
        assert resolve_id(retired) == "default"


def test_both_documented_flag_defaults_match_the_flags():
    """"off by default" in the doc has to be true of both flags, so flipping either
    one on is not a silent change."""
    if not _executor_exists():
        return
    from gitpilot.agent.loop import FLAG_AGENT_LOOP
    from gitpilot.agent.runner import AGENT_LOOP_DEFAULT_ON, FLAG_AGENT_LOOP_DEFAULT
    from gitpilot.flags import is_on

    text = _doc()
    assert FLAG_AGENT_LOOP in text
    assert FLAG_AGENT_LOOP_DEFAULT in text
    assert "off by default" in text, (
        "the doc no longer says the engine flags are off by default — if that "
        "changed, say what the new default is here and in the doc together"
    )
    assert is_on(FLAG_AGENT_LOOP, default=False) is False
    assert AGENT_LOOP_DEFAULT_ON is False


def test_the_doc_describes_the_benchmark_gate_that_governs_the_flip():
    """The flip is a measured decision, and the doc should say how it is measured."""
    if not _executor_exists():
        return
    from bench.report import REGRESSION_BUDGET

    text = _doc()
    assert "python -m bench" in text
    assert f"{REGRESSION_BUDGET}\u00d7" in text or f"{REGRESSION_BUDGET}x" in text
