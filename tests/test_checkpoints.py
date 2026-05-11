"""Tests for the checkpoint store."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from gitpilot.checkpoints import CheckpointStore, ToolCallDescriptor


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hello')\n")
    return tmp_path


@pytest.fixture()
def history_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("history")


def _git_available() -> bool:
    return shutil.which("git") is not None


@pytest.mark.skipif(not _git_available(), reason="git binary required")
def test_snapshot_creates_record(workspace: Path, history_root: Path) -> None:
    store = CheckpointStore(workspace, history_root=history_root)
    record = store.snapshot(
        ToolCallDescriptor(name="write_local_file", target_path="src/app.py"),
        transcript=[{"role": "user", "content": "edit it"}],
    )
    assert record.id
    assert record.tool_name == "write_local_file"
    listed = store.list()
    assert listed and listed[0].id == record.id


@pytest.mark.skipif(not _git_available(), reason="git binary required")
def test_restore_round_trip(workspace: Path, history_root: Path) -> None:
    store = CheckpointStore(workspace, history_root=history_root)
    record = store.snapshot(ToolCallDescriptor(name="write_local_file", target_path="src/app.py"))
    # Simulate a mutation, then restore.
    (workspace / "src" / "app.py").write_text("MUTATED\n")
    restored = store.restore(record.id)
    assert (workspace / "src" / "app.py").read_text() == "print('hello')\n"
    assert restored["record"]["id"] == record.id


@pytest.mark.skipif(not _git_available(), reason="git binary required")
def test_prune_keeps_only_n(workspace: Path, history_root: Path) -> None:
    store = CheckpointStore(workspace, history_root=history_root)
    for i in range(5):
        store.snapshot(ToolCallDescriptor(name=f"write_local_file_{i}"))
    removed = store.prune(keep_last=2)
    assert removed == 3
    assert len(store.list()) == 2
