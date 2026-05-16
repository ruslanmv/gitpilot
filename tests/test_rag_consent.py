"""Tests for per-repo RAG consent (Batch B9).

Pin every contract the router and executor rely on:

* Initial state: no consent.
* Grant writes a readable, well-formed JSON file with a UTC
  timestamp.  Idempotent.
* Revoke deletes the consent file *and* wipes every per-branch
  index directory under the same repo.  Returns ``True`` only when
  something was actually removed; subsequent revokes return False.
* Malformed consent files count as "no consent" (fail closed).
* Paths with unsafe characters are sanitised — owner/repo of the
  form ``"my org/weird repo"`` cannot escape the consent root.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gitpilot.rag_consent import (
    CONSENT_FILE,
    ConsentRecord,
    grant_consent,
    has_consent,
    load_record,
    revoke_consent,
)


@pytest.fixture(autouse=True)
def _isolated_rag_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GITPILOT_RAG_ROOT", str(tmp_path))
    return tmp_path


# ----------------------------------------------------------------------
# Round-trip
# ----------------------------------------------------------------------

def test_initial_state_is_no_consent() -> None:
    assert has_consent("o", "r") is False
    assert load_record("o", "r") is None


def test_grant_creates_well_formed_file(tmp_path: Path) -> None:
    rec = grant_consent("o", "r", granted_by="alice")
    assert isinstance(rec, ConsentRecord)
    assert rec.granted_by == "alice"
    assert has_consent("o", "r") is True
    # Verify the on-disk shape so the router can read it from a
    # different process.
    cpath = tmp_path / "o" / "r" / CONSENT_FILE
    raw = json.loads(cpath.read_text(encoding="utf-8"))
    assert "granted_at" in raw
    assert raw["granted_by"] == "alice"


def test_grant_is_idempotent() -> None:
    grant_consent("o", "r")
    grant_consent("o", "r", granted_by="other")
    # Second call must not error; the record reflects the latest grant.
    rec = load_record("o", "r")
    assert rec is not None
    assert rec.granted_by == "other"


def test_grant_without_granted_by_works() -> None:
    rec = grant_consent("o", "r")
    assert rec.granted_by is None


def test_revoke_returns_true_when_something_existed() -> None:
    grant_consent("o", "r")
    assert revoke_consent("o", "r") is True
    assert has_consent("o", "r") is False


def test_revoke_returns_false_when_nothing_to_revoke() -> None:
    assert revoke_consent("o", "r") is False


def test_revoke_wipes_per_branch_index_directories(tmp_path: Path) -> None:
    """Revoke must remove every <branch>/ directory under the consent
    root — that's the index data we shouldn't keep once consent is
    withdrawn."""
    grant_consent("o", "r")
    # Simulate a built index on two branches.
    (tmp_path / "o" / "r" / "main").mkdir(parents=True)
    (tmp_path / "o" / "r" / "feature_x").mkdir(parents=True)
    (tmp_path / "o" / "r" / "main" / "data.bin").write_text("x")
    (tmp_path / "o" / "r" / "feature_x" / "data.bin").write_text("y")

    assert revoke_consent("o", "r") is True
    assert not (tmp_path / "o" / "r" / "main").exists()
    assert not (tmp_path / "o" / "r" / "feature_x").exists()


# ----------------------------------------------------------------------
# Failure modes — fail closed
# ----------------------------------------------------------------------

def test_malformed_consent_file_counts_as_no_consent(tmp_path: Path) -> None:
    cdir = tmp_path / "o" / "r"
    cdir.mkdir(parents=True)
    (cdir / CONSENT_FILE).write_text("not json at all", encoding="utf-8")
    assert has_consent("o", "r") is False
    assert load_record("o", "r") is None


def test_consent_file_with_wrong_shape_counts_as_no_consent(
    tmp_path: Path,
) -> None:
    cdir = tmp_path / "o" / "r"
    cdir.mkdir(parents=True)
    (cdir / CONSENT_FILE).write_text(json.dumps(["wrong", "shape"]),
                                     encoding="utf-8")
    assert has_consent("o", "r") is False


def test_missing_owner_or_repo_is_no_consent() -> None:
    assert has_consent("", "r") is False
    assert has_consent("o", "") is False
    assert revoke_consent("", "r") is False


def test_grant_rejects_empty_args() -> None:
    with pytest.raises(ValueError):
        grant_consent("", "r")


# ----------------------------------------------------------------------
# Path sanitisation — owner/repo with unsafe characters
# ----------------------------------------------------------------------

def test_unsafe_owner_chars_sanitised(tmp_path: Path) -> None:
    """A malicious-looking owner string must not escape the
    consent root via ``..`` or absolute paths."""
    grant_consent("../etc", "r")
    # The consent file is somewhere under tmp_path, not outside it.
    matches = list(tmp_path.rglob(CONSENT_FILE))
    assert matches, "consent file not created under the sanitised root"
    for m in matches:
        # str(m) starts with tmp_path string — never above it.
        assert str(m).startswith(str(tmp_path))
