"""Per-run AI coder selection: built-in model, Claude Code, Codex, or generic.

Selecting a coder changes only *who writes the patch*. The workspace, the
allowed/forbidden path policy, the sandbox, risk scoring and approval are
unchanged — an agent produces a diff and nothing else: it never commits,
pushes, or opens a PR.
"""

from __future__ import annotations

import subprocess

import pytest

from gitpilot.inference.agent_coder import AGENT_PRESETS, AgentCoder, AgentSpec, resolve_agent_spec
from gitpilot.inference.coder_registry import build_coder
from gitpilot.inference.ollabridge_client import OllaBridgeClient
from gitpilot.matrix_runs_router import MatrixRunRequest, to_repair_request


# ── Registry: the right coder for the right run ─────────────────────────────

def test_default_and_unknown_providers_use_the_builtin_coder(monkeypatch):
    monkeypatch.delenv("GITPILOT_CODER_PROVIDER", raising=False)
    monkeypatch.delenv("GITPILOT_AGENT_CMD", raising=False)
    assert isinstance(build_coder(None, demo_mode=True), OllaBridgeClient)

    class Spec:
        provider = "totally-unknown"
        model = ""

    # A typo must degrade to the safe default, never drop the run.
    assert isinstance(build_coder(Spec(), demo_mode=True), OllaBridgeClient)


@pytest.mark.parametrize("provider", ["claude_code", "codex", "claude-code"])
def test_agent_providers_resolve_to_an_agent_coder(provider, monkeypatch):
    monkeypatch.delenv("GITPILOT_AGENT_CMD", raising=False)

    class Spec:
        pass

    Spec.provider = provider
    coder = build_coder(Spec(), demo_mode=True)
    assert isinstance(coder, AgentCoder)


def test_deployment_default_can_select_an_agent(monkeypatch):
    monkeypatch.setenv("GITPILOT_CODER_PROVIDER", "codex")
    monkeypatch.delenv("GITPILOT_AGENT_CMD", raising=False)
    assert isinstance(build_coder(None, demo_mode=True), AgentCoder)


def test_a_generic_agent_is_defined_by_env(monkeypatch):
    """Any other coding agent plugs in without a new class."""
    monkeypatch.setenv("GITPILOT_AGENT_CMD", "my-agent run {prompt}")
    monkeypatch.setenv("GITPILOT_AGENT_READONLY_FLAGS", "--dry-run")
    spec = resolve_agent_spec("agent")
    assert spec is not None
    assert spec.command == ["my-agent", "run", "{prompt}"]
    assert spec.read_only_flags == ["--dry-run"]


# ── Read-only mapping: dry_run must reach the agent's own sandbox ───────────

def test_dry_run_maps_to_each_agent_read_only_mode():
    claude = AgentCoder(AGENT_PRESETS["claude_code"])
    codex = AgentCoder(AGENT_PRESETS["codex"])
    assert claude._argv("fix it", dry_run=True)[-2:] == ["--permission-mode", "plan"]
    assert claude._argv("fix it", dry_run=False)[-2:] == ["--permission-mode", "acceptEdits"]
    assert codex._argv("fix it", dry_run=True)[-2:] == ["--sandbox", "read-only"]
    assert codex._argv("fix it", dry_run=False)[-2:] == ["--sandbox", "workspace-write"]
    # The task text is substituted, never left as a placeholder.
    assert "fix it" in claude._argv("fix it", dry_run=True)
    assert "{prompt}" not in claude._argv("fix it", dry_run=True)


# ── The agent produces a diff from the real workspace ───────────────────────

def _git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@e.st"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "T"], check=True)
    (tmp_path / "app.py").write_text("print('hi')\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    return tmp_path


def test_agent_edits_become_a_unified_diff(tmp_path):
    """A real agent invocation is simulated with a shell command that edits a
    file; the coder must return the workspace diff — and commit nothing."""
    repo = _git_repo(tmp_path)
    spec = AgentSpec(
        name="fake_agent",
        command=["sh", "-c", "echo 'print(\"patched\")' >> app.py"],
    )
    diff = AgentCoder(spec).code(user="patch the app", workspace=str(repo), dry_run=False)

    assert "app.py" in diff and "patched" in diff
    assert diff.lstrip().startswith("diff --git")
    # Nothing was committed: HEAD still holds only the initial commit.
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert log.count("\n") == 1


def test_new_files_appear_in_the_diff(tmp_path):
    repo = _git_repo(tmp_path)
    spec = AgentSpec(name="fake", command=["sh", "-c", "echo 'x=1' > added.py"])
    diff = AgentCoder(spec).code(user="add a file", workspace=str(repo), dry_run=False)
    assert "added.py" in diff


def test_no_workspace_yields_empty_patch_not_a_guess(tmp_path):
    """Without a checkout there is nothing to edit — return no patch rather than
    inventing one."""
    spec = AgentSpec(name="fake", command=["sh", "-c", "true"])
    assert AgentCoder(spec).code(user="x", workspace=None, dry_run=True) == ""
    assert AgentCoder(spec).code(user="x", workspace=str(tmp_path / "nope")) == ""


def test_missing_agent_executable_is_survivable(tmp_path):
    repo = _git_repo(tmp_path)
    spec = AgentSpec(name="absent", command=["definitely-not-installed-xyz", "{prompt}"])
    assert AgentCoder(spec).code(user="x", workspace=str(repo), dry_run=True) == ""


def test_failing_agent_still_returns_partial_edits(tmp_path):
    """A non-zero exit does not discard work the agent already did."""
    repo = _git_repo(tmp_path)
    spec = AgentSpec(name="flaky", command=["sh", "-c", "echo 'y=2' >> app.py; exit 3"])
    diff = AgentCoder(spec).code(user="x", workspace=str(repo), dry_run=False)
    assert "y=2" in diff


# ── The facade carries the selection end to end ─────────────────────────────

def test_run_request_passes_the_coder_through_to_the_pipeline():
    run = MatrixRunRequest.model_validate({
        "task": "Add a health endpoint",
        "repo": "https://github.com/acme/widget",
        "coder": {"provider": "claude_code", "model": "sonnet"},
    })
    repair = to_repair_request(run)
    assert repair.coder.provider == "claude_code"
    assert repair.coder.model == "sonnet"
    # Guardrails are untouched by the coder choice.
    assert "MATRIX_STANDARDS.lock" in repair.forbidden_paths


def test_omitting_the_coder_keeps_the_pipeline_default():
    run = MatrixRunRequest.model_validate({
        "task": "t", "repo": "https://github.com/acme/widget",
    })
    assert to_repair_request(run).coder.provider == "ollabridge"
