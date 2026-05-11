"""Tests for the context budget manager."""
from __future__ import annotations

from gitpilot.context_budget import (
    BudgetPolicy,
    ContextBudgetManager,
    Message,
    estimate_tokens,
)


def test_estimate_tokens_is_nonzero_for_text() -> None:
    assert estimate_tokens("hello world") > 0
    assert estimate_tokens("") == 0


def test_total_tokens_sums_messages() -> None:
    mgr = ContextBudgetManager()
    mgr.add(Message(role="user", content="hello"))
    mgr.add(Message(role="assistant", content="hi there"))
    assert mgr.total_tokens() > 0


def test_needs_condense_triggers_above_threshold() -> None:
    policy = BudgetPolicy(max_tokens=100, condense_at_ratio=0.5, keep_recent_turns=1)
    mgr = ContextBudgetManager(policy=policy)
    mgr.add(Message(role="system", content="sys"))
    mgr.add(Message(role="user", content="x" * 600))
    assert mgr.needs_condense() is True


def test_condense_drops_oversize_tool_outputs_first() -> None:
    policy = BudgetPolicy(
        max_tokens=100,
        condense_at_ratio=0.4,
        keep_recent_turns=2,
        large_tool_output_tokens=10,
    )
    mgr = ContextBudgetManager(policy=policy)
    mgr.add(Message(role="system", content="sys", importance="pinned"))
    mgr.add(Message(role="tool", content="x" * 800))
    mgr.add(Message(role="user", content="recent"))
    saved = mgr.maybe_condense()
    assert saved > 0
    # The bulky tool output must have been replaced.
    assert all("dropped" in m.content or m.tokens < 50 for m in mgr.messages() if m.role == "tool")


def test_condense_summarises_when_truncation_alone_insufficient() -> None:
    policy = BudgetPolicy(
        max_tokens=80,
        condense_at_ratio=0.5,
        keep_recent_turns=1,
        large_tool_output_tokens=10_000,
    )
    mgr = ContextBudgetManager(policy=policy)
    for i in range(8):
        mgr.add(Message(role="user", content=f"step {i}: " + "x" * 80))
    mgr.add(Message(role="user", content="final question"))
    mgr.maybe_condense()
    messages = mgr.messages()
    summaries = [m for m in messages if m.meta.get("summary") == "1"]
    assert summaries, "expected a summary message after condensation"
    assert messages[-1].content == "final question"  # most-recent preserved


def test_stats_reports_ratio() -> None:
    policy = BudgetPolicy(max_tokens=1000)
    mgr = ContextBudgetManager(policy=policy)
    mgr.add(Message(role="user", content="x" * 400))
    stats = mgr.stats()
    assert stats.max_tokens == 1000
    assert 0 < stats.ratio <= 1
    assert stats.to_dict()["max_tokens"] == 1000
