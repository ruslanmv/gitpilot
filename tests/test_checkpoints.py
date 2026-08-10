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


# ── Restore is a mirror, not an overlay ──────────────────────────────────


@pytest.mark.skipif(not _git_available(), reason="git binary required")
def test_restore_undoes_a_file_creation(workspace: Path, history_root: Path) -> None:
    """The thing users most want undone is a file that should never have existed.

    Restore used to be additive, so a checkpoint could put an edited file back
    but never remove one the agent invented.
    """
    store = CheckpointStore(workspace, history_root=history_root)
    record = store.snapshot(ToolCallDescriptor(name="write_local_file"))

    (workspace / "src" / "app.py").write_text("edited\n")
    (workspace / "invented.py").write_text("should not exist\n")
    (workspace / "src" / "nested").mkdir()
    (workspace / "src" / "nested" / "deep.py").write_text("nor this\n")

    result = store.restore(record.id)

    assert (workspace / "src" / "app.py").read_text() == "print('hello')\n"
    assert not (workspace / "invented.py").exists()
    assert not (workspace / "src" / "nested" / "deep.py").exists()
    assert "invented.py" in result["removed"]


@pytest.mark.skipif(not _git_available(), reason="git binary required")
def test_restore_leaves_ignored_directories_alone(workspace: Path, history_root: Path) -> None:
    """A rewind must never take out a virtualenv or someone's .git."""
    store = CheckpointStore(workspace, history_root=history_root)
    record = store.snapshot(ToolCallDescriptor(name="write_local_file"))

    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "big.js").write_text("x" * 100)
    (workspace / ".git").mkdir()
    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

    store.restore(record.id)

    assert (workspace / "node_modules" / "big.js").exists()
    assert (workspace / ".git" / "HEAD").exists()


@pytest.mark.skipif(not _git_available(), reason="git binary required")
def test_two_checkpoints_rewind_to_the_right_one(workspace: Path, history_root: Path) -> None:
    store = CheckpointStore(workspace, history_root=history_root)
    first = store.snapshot(ToolCallDescriptor(name="write_local_file", note="one"))

    (workspace / "src" / "app.py").write_text("second state\n")
    second = store.snapshot(ToolCallDescriptor(name="write_local_file", note="two"))

    (workspace / "src" / "app.py").write_text("third state\n")

    store.restore(second.id)
    assert (workspace / "src" / "app.py").read_text() == "second state\n"

    store.restore(first.id)
    assert (workspace / "src" / "app.py").read_text() == "print('hello')\n"


# ── A workspace too large to copy says so ────────────────────────────────


@pytest.mark.skipif(not _git_available(), reason="git binary required")
def test_oversized_workspace_records_the_transcript_only(
    workspace: Path, history_root: Path
) -> None:
    """Half a checkpoint that is honest beats a rewind that cannot happen."""
    (workspace / "big.bin").write_bytes(b"x" * 4096)
    store = CheckpointStore(workspace, history_root=history_root, max_bytes=1024)

    record = store.snapshot(
        ToolCallDescriptor(name="write_local_file"),
        transcript=[{"role": "user", "content": "go"}],
    )

    assert record.has_files is False
    assert record.files_changed == 0

    # And restoring it must not wipe the workspace it never captured.
    (workspace / "src" / "app.py").write_text("still mine\n")
    result = store.restore(record.id)

    assert result["files_restored"] is False
    assert (workspace / "src" / "app.py").read_text() == "still mine\n"
    assert result["transcript"] == [{"role": "user", "content": "go"}]


@pytest.mark.skipif(not _git_available(), reason="git binary required")
def test_records_written_before_has_files_still_load(
    workspace: Path, history_root: Path
) -> None:
    """An existing checkpoint on disk must survive the field being added."""
    import json

    store = CheckpointStore(workspace, history_root=history_root)
    record = store.snapshot(ToolCallDescriptor(name="write_local_file"))

    record_file = store.history_dir / "meta" / record.id / "record.json"
    data = json.loads(record_file.read_text())
    del data["has_files"]
    data["some_future_field"] = "ignored"
    record_file.write_text(json.dumps(data))

    listed = store.list()
    assert listed and listed[0].id == record.id
    assert listed[0].has_files is True


@pytest.mark.skipif(not _git_available(), reason="git binary required")
def test_rapid_checkpoints_do_not_overwrite_each_other(
    workspace: Path, history_root: Path
) -> None:
    """An agent makes several edits per second; each needs its own checkpoint.

    With a second-resolution id the later snapshot silently replaced the
    earlier one, so the rewind the user reached for no longer existed.
    """
    store = CheckpointStore(workspace, history_root=history_root)

    ids = []
    for i in range(5):
        (workspace / "src" / "app.py").write_text(f"state {i}\n")
        ids.append(store.snapshot(ToolCallDescriptor(name="write_local_file")).id)

    assert len(set(ids)) == 5, "checkpoints taken in the same second collided"
    assert len(store.list()) == 5
