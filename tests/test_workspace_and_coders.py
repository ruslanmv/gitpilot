"""The checkout a run works in, and which coders a deployment can offer.

Two things a real project build depends on and a demo does not: the clone has to
actually happen (with credentials, for private repositories), and a control plane
offering a coder picker has to be told the truth about this host.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from gitpilot.inference.coder_registry import available_coders
from gitpilot.repair.schema import RepairRequest
from gitpilot.repair.service import run_repair
from gitpilot.repair.workspace import (
    WorkspaceError,
    clone,
    create_branch,
    redact,
    token_for,
)


def _origin(tmp_path, branch: str = "main"):
    """A real git repository to clone from."""
    origin = tmp_path / "origin"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "-b", branch, str(origin)], check=True)
    subprocess.run(["git", "-C", str(origin), "config", "user.email", "t@e.st"], check=True)
    subprocess.run(["git", "-C", str(origin), "config", "user.name", "T"], check=True)
    (origin / "app.py").write_text("print('hi')\n")
    subprocess.run(["git", "-C", str(origin), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(origin), "commit", "-qm", "init"], check=True)
    return origin


# ── The clone is real ───────────────────────────────────────────────────────

def test_clone_produces_a_working_checkout(tmp_path):
    workspace = clone(str(_origin(tmp_path)), branch="main")
    assert os.path.isfile(os.path.join(workspace.path, "app.py"))
    assert not workspace.fell_back_to_default
    create_branch(workspace.path, "gitpilot/task-1")
    head = subprocess.run(["git", "-C", workspace.path, "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert head == "gitpilot/task-1"


def test_a_missing_base_branch_falls_back_and_says_so(tmp_path):
    """Repos on 'master' (or brand-new ones) must not fail the whole run silently."""
    workspace = clone(str(_origin(tmp_path, branch="master")), branch="main")
    assert workspace.fell_back_to_default
    assert os.path.isfile(os.path.join(workspace.path, "app.py"))


def test_an_unreachable_repository_raises_rather_than_returning_an_empty_workspace(tmp_path):
    with pytest.raises(WorkspaceError):
        clone(str(tmp_path / "nothing-here"), branch="main")
    with pytest.raises(WorkspaceError):
        clone("")


def test_a_failed_clone_mentions_the_missing_credential(tmp_path, monkeypatch):
    for name in ("GITPILOT_GIT_TOKEN", "GITHUB_TOKEN", "GH_TOKEN", "GIT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(WorkspaceError) as exc:
        clone("https://127.0.0.1:1/private/repo.git", timeout=30)
    assert "GITPILOT_GIT_TOKEN" in str(exc.value)


# ── Credentials never leak ──────────────────────────────────────────────────

def test_a_token_is_only_used_for_http_remotes(monkeypatch):
    monkeypatch.setenv("GITPILOT_GIT_TOKEN", "ghp_secret")
    assert token_for("https://github.com/acme/app") == "ghp_secret"
    assert token_for("git@github.com:acme/app.git") == ""
    assert token_for("/srv/repos/app") == ""


def test_credentials_are_redacted_from_anything_reported(monkeypatch):
    monkeypatch.setenv("GITPILOT_GIT_TOKEN", "ghp_secret")
    assert "ghp_secret" not in redact("fatal: auth failed for ghp_secret", "ghp_secret")
    assert redact("https://user:pw@host/x") == "https://***@host/x"


def test_the_token_never_reaches_the_command_line(tmp_path, monkeypatch):
    """A token in argv is readable from the process table — it must go via env."""
    monkeypatch.setenv("GITPILOT_GIT_TOKEN", "ghp_secret")
    seen: dict[str, object] = {}
    import gitpilot.repair.workspace as ws

    real_run = ws._run

    def spy(argv, env, timeout):
        seen["argv"] = list(argv)
        seen["env"] = dict(env)
        return real_run(argv, env, timeout)

    monkeypatch.setattr(ws, "_run", spy)
    clone(str(_origin(tmp_path)), branch="main")
    assert not any("ghp_secret" in part for part in seen["argv"])
    # The askpass helper carries it instead, and is cleaned up afterwards.
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0"


# ── A run with no code fails loudly ─────────────────────────────────────────

def _plan(repo_url: str) -> dict:
    return {
        "client_id": "daypilot", "workspace_id": "ws-1", "task_id": "t-1",
        "repo_url": repo_url, "branch": "main", "mode": "dry_run",
        "allowed_paths": ["**"],
        "sandbox": {"provider": "matrixlab", "profile": "default", "required": False},
    }


def test_a_run_whose_clone_fails_is_an_error_not_an_invented_patch(tmp_path):
    response = run_repair(RepairRequest.from_json(_plan(str(tmp_path / "absent"))), demo_mode=False)
    assert response.status == "error"
    assert response.patch_preview == ""
    assert any("could not clone" in w for w in response.warnings)


def test_demo_mode_still_runs_offline(tmp_path):
    response = run_repair(RepairRequest.from_json(_plan("https://example.invalid/x")), demo_mode=True)
    assert response.status == "ok"


# ── The coder catalog tells the truth ───────────────────────────────────────

def test_the_builtin_coder_is_always_offered(monkeypatch):
    monkeypatch.delenv("GITPILOT_CODER_PROVIDER", raising=False)
    monkeypatch.delenv("GITPILOT_AGENT_CMD", raising=False)
    coders = {c["id"]: c for c in available_coders()}
    assert coders["ollabridge"]["available"] is True
    assert coders["ollabridge"]["default"] is True
    assert {"claude_code", "codex"} <= set(coders)


def test_an_agent_that_cannot_run_here_says_why(monkeypatch):
    monkeypatch.delenv("GITPILOT_AGENT_CMD", raising=False)
    monkeypatch.setattr("gitpilot.inference.coder_registry.shutil.which", lambda _: None)
    claude = next(c for c in available_coders() if c["id"] == "claude_code")
    assert claude["available"] is False
    assert "not installed" in claude["reason"]


def test_an_installed_agent_without_its_credential_is_not_offered(monkeypatch):
    monkeypatch.delenv("GITPILOT_AGENT_CMD", raising=False)
    monkeypatch.setattr("gitpilot.inference.coder_registry.shutil.which", lambda _: "/usr/bin/x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    claude = next(c for c in available_coders() if c["id"] == "claude_code")
    assert claude["available"] is False and "ANTHROPIC_API_KEY" in claude["reason"]

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    claude = next(c for c in available_coders() if c["id"] == "claude_code")
    assert claude["available"] is True and claude["reason"] == ""


def test_a_custom_agent_appears_when_configured(monkeypatch):
    monkeypatch.setenv("GITPILOT_AGENT_CMD", "my-agent run {prompt}")
    assert any(c["id"] == "agent" for c in available_coders())


def test_the_coders_endpoint_is_served_and_authenticated(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gitpilot.matrix_runs_router import build_matrix_runs_router

    monkeypatch.setenv("GITPILOT_A2A_REQUIRE_AUTH", "true")
    monkeypatch.setenv("GITPILOT_A2A_SHARED_SECRET", "s3cret")
    app = FastAPI()
    app.include_router(build_matrix_runs_router())
    client = TestClient(app)

    # The picker's data sits behind the same A2A secret as the runs it feeds.
    assert client.get("/api/v1/gitpilot/coders").status_code in (401, 403)
    resp = client.get("/api/v1/gitpilot/coders", headers={"X-A2A-Secret": "s3cret"})
    assert resp.status_code == 200
    body = resp.json()
    assert any(c["id"] == "ollabridge" for c in body["coders"])
    assert body["default"]
