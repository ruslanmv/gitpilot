"""Tests for the two-command onboarding bootstrap inside ``gitpilot serve``.

The helper is :func:`gitpilot.cli._maybe_bootstrap_workspace`.  It must:

* run the wizard non-interactively when the workspace is fresh;
* leave a configured workspace untouched;
* never raise to the caller — ``gitpilot serve`` must keep booting
  even if the bootstrap blows up.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest

from gitpilot import cli, flags
from gitpilot.init_wizard import FLAG_INIT_WIZARD
from gitpilot.trusted_folders import TrustStatus, TrustStore


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    flags.clear_all_overrides()
    for env in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "WATSONX_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    yield
    flags.clear_all_overrides()


# ----------------------------------------------------------------------
# Fresh workspace → wizard runs
# ----------------------------------------------------------------------

def test_fresh_workspace_writes_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the trust store to live in tmp so we don't pollute $HOME.
    trust_path = tmp_path / "trusted.json"
    monkeypatch.setattr("gitpilot.trusted_folders.DEFAULT_STORE", trust_path)

    cli._maybe_bootstrap_workspace(tmp_path)

    assert (tmp_path / ".env").exists()
    assert (tmp_path / ".gitpilot" / "modes.yaml").exists()
    assert (tmp_path / "AGENTS.md").exists()
    # Defaulted to ollama because no env var was set.
    assert "GITPILOT_LLM_PROVIDER=ollama" in (tmp_path / ".env").read_text()


def test_env_credentials_pick_matching_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("gitpilot.trusted_folders.DEFAULT_STORE", tmp_path / "trusted.json")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")

    cli._maybe_bootstrap_workspace(tmp_path)

    env_text = (tmp_path / ".env").read_text()
    assert "GITPILOT_LLM_PROVIDER=openai" in env_text
    assert "OPENAI_API_KEY=sk-from-env" in env_text


def test_anthropic_key_wins_over_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("gitpilot.trusted_folders.DEFAULT_STORE", tmp_path / "trusted.json")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xyz")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")

    cli._maybe_bootstrap_workspace(tmp_path)

    env_text = (tmp_path / ".env").read_text()
    assert "GITPILOT_LLM_PROVIDER=anthropic" in env_text
    assert "ANTHROPIC_API_KEY=sk-ant-xyz" in env_text
    assert "OPENAI_API_KEY" not in env_text


# ----------------------------------------------------------------------
# Configured workspace → no-op
# ----------------------------------------------------------------------

def test_existing_env_file_short_circuits(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("# do not overwrite\nGITPILOT_LLM_PROVIDER=openai\n")
    cli._maybe_bootstrap_workspace(tmp_path)
    # Was preserved.
    assert "do not overwrite" in (tmp_path / ".env").read_text()
    # Wizard didn't fire — no other artefacts appeared.
    assert not (tmp_path / ".gitpilot").exists()
    assert not (tmp_path / "AGENTS.md").exists()


def test_existing_gitpilot_dir_short_circuits(tmp_path: Path) -> None:
    (tmp_path / ".gitpilot").mkdir()
    cli._maybe_bootstrap_workspace(tmp_path)
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / "AGENTS.md").exists()


def test_existing_agents_md_short_circuits(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Pre-existing\n")
    cli._maybe_bootstrap_workspace(tmp_path)
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / ".gitpilot").exists()


# ----------------------------------------------------------------------
# Flag isolation
# ----------------------------------------------------------------------

def test_flag_value_is_restored_after_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("gitpilot.trusted_folders.DEFAULT_STORE", tmp_path / "trusted.json")
    flags.set_override(FLAG_INIT_WIZARD, False)
    cli._maybe_bootstrap_workspace(tmp_path)
    # The bootstrap must not leak the flag flip.
    assert flags.is_on(FLAG_INIT_WIZARD) is False


# ----------------------------------------------------------------------
# Errors are swallowed
# ----------------------------------------------------------------------

def test_bootstrap_failure_never_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("gitpilot.trusted_folders.DEFAULT_STORE", tmp_path / "trusted.json")

    def explode(*args, **kwargs):
        raise RuntimeError("simulated")

    monkeypatch.setattr("gitpilot.init_wizard.run_wizard", explode)
    # Must not raise.
    cli._maybe_bootstrap_workspace(tmp_path)
