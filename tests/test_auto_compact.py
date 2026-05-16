"""Tests for the auto-compaction hook (Batch B3).

Pin three things:

* below threshold → no-op (we don't fold prematurely)
* above threshold → fold older non-essential turns into a single
  summary system message, keep the last N recent turns
* idempotency — running compaction twice on already-compacted history
  doesn't fold the summary into another summary
"""
from __future__ import annotations

import pytest

from gitpilot import api as api_module
from gitpilot import flags
from gitpilot.auto_compact import (
    COMPACTED_FLAG,
    DEFAULT_KEEP_RECENT_TURNS,
    FLAG_AUTO_COMPACT,
    SUMMARY_LABEL,
    maybe_compact_session,
)
from gitpilot.context_budget import estimate_tokens
from gitpilot.session import Message


def _make_session_with_history(messages: list[tuple[str, str]]):
    """Build and save a session with the supplied (role, content) pairs."""
    session = api_module._session_mgr.create(
        repo_full_name="o/r", branch="main", name="compact-test"
    )
    for role, content in messages:
        session.messages.append(Message(role=role, content=content))
    api_module._session_mgr.save(session)
    return session


def test_no_op_below_threshold() -> None:
    """A handful of short messages must not trigger compaction even on
    an 8 k window — that would be ridiculous."""
    session = _make_session_with_history([
        ("user", "do thing"),
        ("assistant", "ok"),
        ("user", "thanks"),
    ])
    report = maybe_compact_session(
        api_module._session_mgr, session.id, context_window=8_192
    )
    assert report.compacted is False
    reloaded = api_module._session_mgr.load(session.id)
    assert len(reloaded.messages) == 3   # untouched


def test_folds_above_threshold_and_preserves_recent_turns() -> None:
    """A long history must fold older non-essential turns into a
    single summary, keeping the last N recent messages verbatim."""
    chunk = "lorem ipsum " * 200   # ~400 tokens per message under heuristic
    messages = [("user" if i % 2 == 0 else "assistant", chunk) for i in range(20)]
    session = _make_session_with_history(messages)

    report = maybe_compact_session(
        api_module._session_mgr, session.id, context_window=8_192
    )
    assert report.compacted is True
    assert report.before_tokens > report.after_tokens
    assert report.messages_folded >= 1

    reloaded = api_module._session_mgr.load(session.id)
    # Recent turns preserved exactly.
    assert len(reloaded.messages) == 1 + DEFAULT_KEEP_RECENT_TURNS
    summary = reloaded.messages[0]
    assert summary.role == "system"
    assert SUMMARY_LABEL in summary.content
    assert summary.metadata.get(COMPACTED_FLAG) == "1"
    # Last N recent turns are still verbatim.
    for m in reloaded.messages[1:]:
        assert m.content == chunk


def test_idempotent_no_recompact_of_summary() -> None:
    """Running compaction twice must not fold the summary itself."""
    chunk = "lorem ipsum " * 200
    messages = [("user" if i % 2 == 0 else "assistant", chunk) for i in range(20)]
    session = _make_session_with_history(messages)

    first = maybe_compact_session(
        api_module._session_mgr, session.id, context_window=8_192
    )
    assert first.compacted is True

    second = maybe_compact_session(
        api_module._session_mgr, session.id, context_window=8_192
    )
    # Either: stayed below threshold post-fold (compacted=False), OR
    # found nothing new to fold (compacted=False with that reason).
    # Either way: no further folding of an already-summary entry.
    assert second.compacted is False
    reloaded = api_module._session_mgr.load(session.id)
    assert reloaded.messages[0].metadata.get(COMPACTED_FLAG) == "1"


def test_flag_off_is_a_noop() -> None:
    session = _make_session_with_history([
        ("user", "x" * 8000) for _ in range(20)
    ])
    flags.set_override(FLAG_AUTO_COMPACT, False)
    try:
        report = maybe_compact_session(
            api_module._session_mgr, session.id, context_window=8_192
        )
    finally:
        flags.clear_override(FLAG_AUTO_COMPACT)
    assert report.compacted is False
    assert report.reason == "flag off"


def test_missing_session_returns_clean_report() -> None:
    report = maybe_compact_session(
        api_module._session_mgr, "does-not-exist", context_window=8_192
    )
    assert report.compacted is False


def test_no_session_id_is_noop() -> None:
    report = maybe_compact_session(
        api_module._session_mgr, None, context_window=8_192
    )
    assert report.compacted is False


def test_reserved_response_lowers_effective_budget() -> None:
    """A bigger reserved_response should make compaction fire SOONER —
    less effective budget means the threshold is hit earlier."""
    chunk = "lorem ipsum " * 100   # ~200 tok each
    msgs = [("user" if i % 2 == 0 else "assistant", chunk) for i in range(20)]

    s1 = _make_session_with_history(msgs)
    s2 = _make_session_with_history(msgs)

    # Low reservation: budget is bigger → less likely to fire.
    r_low = maybe_compact_session(
        api_module._session_mgr, s1.id, context_window=8_192,
        reserved_response=0,
    )
    # High reservation: budget is tight → much more likely to fire.
    r_high = maybe_compact_session(
        api_module._session_mgr, s2.id, context_window=8_192,
        reserved_response=6_000,
    )
    # The high-reservation run must compact at least as aggressively
    # as the low-reservation one.
    if r_high.compacted:
        assert True  # at-least-as-aggressive
    else:
        # If neither fires, the low one must also not have fired.
        assert r_low.compacted is False


def test_summary_actually_shrinks_token_total() -> None:
    """The whole point of compaction is to free budget."""
    chunk = "lorem ipsum " * 400  # ~800 tok each
    session = _make_session_with_history([
        ("user" if i % 2 == 0 else "assistant", chunk) for i in range(20)
    ])
    before = sum(estimate_tokens(m.content) for m in session.messages)
    report = maybe_compact_session(
        api_module._session_mgr, session.id, context_window=8_192
    )
    assert report.compacted is True
    assert report.after_tokens < report.before_tokens
    assert report.before_tokens == before
