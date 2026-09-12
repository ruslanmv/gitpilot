"""Production invariants for approved mutating tool retries."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from gitpilot.idempotency import (
    IdempotencyConflict,
    IdempotencyError,
    IdempotencyIndeterminate,
    IdempotencyInProgress,
    IdempotencyStore,
)


def test_completed_mutation_replays_without_executing_twice(tmp_path):
    store = IdempotencyStore(tmp_path / "idem.sqlite3")
    calls: list[str] = []

    def operation():
        calls.append("executed")
        return {"number": 42, "url": "https://example.invalid/42"}

    first = store.run_once(
        scope="github.issue.create:o/r",
        idempotency_key="approval-123",
        arguments={"title": "Bug"},
        operation=operation,
    )
    second = store.run_once(
        scope="github.issue.create:o/r",
        idempotency_key="approval-123",
        arguments={"title": "Bug"},
        operation=operation,
    )

    assert first == second == {"number": 42, "url": "https://example.invalid/42"}
    assert calls == ["executed"]


def test_result_survives_a_new_store_instance(tmp_path):
    path = tmp_path / "idem.sqlite3"
    first_store = IdempotencyStore(path)
    first_store.run_once(
        scope="github.pr.create:o/r",
        idempotency_key="approval-1",
        arguments={"head": "fix", "base": "main"},
        operation=lambda: {"number": 9},
    )

    restarted_store = IdempotencyStore(path)
    called = False

    def must_not_run():
        nonlocal called
        called = True
        return {"number": 10}

    result = restarted_store.run_once(
        scope="github.pr.create:o/r",
        idempotency_key="approval-1",
        arguments={"head": "fix", "base": "main"},
        operation=must_not_run,
    )
    assert result == {"number": 9}
    assert called is False


def test_same_key_cannot_authorize_changed_arguments(tmp_path):
    store = IdempotencyStore(tmp_path / "idem.sqlite3")
    store.run_once(
        scope="github.issue.create:o/r",
        idempotency_key="approval-1",
        arguments={"title": "A"},
        operation=lambda: {"number": 1},
    )

    with pytest.raises(IdempotencyConflict, match="different arguments"):
        store.run_once(
            scope="github.issue.create:o/r",
            idempotency_key="approval-1",
            arguments={"title": "B"},
            operation=lambda: {"number": 2},
        )


def test_inflight_duplicate_is_not_executed(tmp_path):
    store = IdempotencyStore(tmp_path / "idem.sqlite3")
    store.reserve(
        scope="github.pr.review:o/r:7",
        idempotency_key="approval-7",
        arguments={"event": "APPROVE"},
    )

    with pytest.raises(IdempotencyInProgress, match="already executing"):
        store.reserve(
            scope="github.pr.review:o/r:7",
            idempotency_key="approval-7",
            arguments={"event": "APPROVE"},
        )


def test_ambiguous_failure_fails_closed_on_retry(tmp_path):
    store = IdempotencyStore(tmp_path / "idem.sqlite3")
    calls = 0

    def timeout_after_possible_commit():
        nonlocal calls
        calls += 1
        raise TimeoutError("response lost")

    with pytest.raises(TimeoutError, match="response lost"):
        store.run_once(
            scope="github.issue.create:o/r",
            idempotency_key="approval-timeout",
            arguments={"title": "Maybe created"},
            operation=timeout_after_possible_commit,
        )

    with pytest.raises(IdempotencyIndeterminate, match="may have committed"):
        store.run_once(
            scope="github.issue.create:o/r",
            idempotency_key="approval-timeout",
            arguments={"title": "Maybe created"},
            operation=timeout_after_possible_commit,
        )

    assert calls == 1, "an ambiguous remote outcome must never be retried automatically"


def test_empty_key_is_rejected_before_side_effect(tmp_path):
    store = IdempotencyStore(tmp_path / "idem.sqlite3")
    called = False

    def operation():
        nonlocal called
        called = True

    with pytest.raises(IdempotencyError, match="idempotency_key is required"):
        store.run_once(
            scope="github.issue.create:o/r",
            idempotency_key="",
            arguments={},
            operation=operation,
        )
    assert called is False


def _function_args(path: Path, function_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return {arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
    raise AssertionError(f"{function_name} not found in {path}")


@pytest.mark.parametrize(
    ("relative_path", "function_name"),
    [
        ("gitpilot/agent_tools.py", "edit_file"),
        ("gitpilot/agent_tools.py", "apply_patch_to_file"),
        ("gitpilot/agent_tools.py", "write_file"),
        ("gitpilot/agent_tools.py", "delete_repo_file"),
        ("gitpilot/agent_tools.py", "create_repo_branch"),
        ("gitpilot/local_tools.py", "write_local_file"),
        ("gitpilot/local_tools.py", "delete_local_file"),
        ("gitpilot/issue_tools.py", "create_issue"),
        ("gitpilot/issue_tools.py", "update_issue"),
        ("gitpilot/issue_tools.py", "add_issue_comment"),
        ("gitpilot/pr_tools.py", "create_pull_request"),
        ("gitpilot/pr_tools.py", "merge_pull_request"),
        ("gitpilot/pr_tools.py", "create_pr_review"),
        ("gitpilot/pr_tools.py", "add_pr_comment"),
    ],
)
def test_legacy_mutating_tools_require_runtime_idempotency_key(relative_path, function_name):
    root = Path(__file__).resolve().parents[1]
    assert "idempotency_key" in _function_args(root / relative_path, function_name)
