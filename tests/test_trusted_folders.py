"""Tests for the trusted-folder store."""
from __future__ import annotations

from pathlib import Path

import pytest

from gitpilot.trusted_folders import (
    TrustStatus,
    TrustStore,
    fingerprint,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    return tmp_path


@pytest.fixture()
def store(tmp_path: Path) -> TrustStore:
    return TrustStore.load(tmp_path / "trusted.json")


def test_unknown_workspace_returns_unknown(workspace: Path, store: TrustStore) -> None:
    assert store.status(workspace) is TrustStatus.UNKNOWN


def test_trusting_workspace_persists(workspace: Path, store: TrustStore) -> None:
    store.trust(workspace, note="initial setup")
    reloaded = TrustStore.load(store.path)
    assert reloaded.status(workspace) is TrustStatus.TRUSTED


def test_fingerprint_change_invalidates_trust(workspace: Path, store: TrustStore) -> None:
    store.trust(workspace)
    (workspace / "pyproject.toml").write_text("[project]\nname='changed'\n")
    assert store.status(workspace) is TrustStatus.FINGERPRINT_MISMATCH


def test_revoke_removes_entry(workspace: Path, store: TrustStore) -> None:
    store.trust(workspace)
    assert store.revoke(workspace) is True
    assert store.status(workspace) is TrustStatus.UNKNOWN


def test_fingerprint_includes_path(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert fingerprint(a) != fingerprint(b)
