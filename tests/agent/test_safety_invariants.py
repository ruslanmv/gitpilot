# tests/agent/test_safety_invariants.py
"""The programme's safety ratchet — Batch V4-D7.

Six properties, asserted over **recorded journals from real runs** rather than
over the code that produced them. That distinction is the point: a unit test says
"the policy engine denies plan-mode writes", while these say "no run in this
suite ever executed a mutating tool in plan mode, whatever path it took to get
there". A future batch that adds a code path around the policy engine breaks
these without touching a single test it knows about.

The properties (§19.3):

1. no side-effecting result without a preceding ``allow``/approved decision;
2. no mutating tool in plan mode, a read-only topology, or after a deny — in any
   dialect, including LITE's synthesized calls;
3. no file content in the transcript without a journaled ``fs.read``;
4. no reasoning-tag content in any journal line;
5. DESTRUCTIVE/PRIVILEGED commands never reach a prompt;
6. secret-env stripping holds on every ``terminal.run``.

Each is checked against a matrix of runs: three dialects × three permission
modes, driven through the real loop with the real policy engine.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import pytest

from gitpilot.agent.context import AgentContext
from gitpilot.agent.contracts import AgentTurn, Dialect, Finality
from gitpilot.agent.journal import LineType, RunJournal, assert_no_reasoning
from gitpilot.agent.loop import AgentLoop, DenyingApprovals, new_run_id
from gitpilot.agent.model_profile import resolve_profile
from gitpilot.agent.policy import (
    READ_ONLY_COMMAND_CLASSES,
    CapabilityMask,
    PolicyEngine,
    ResolvedPolicy,
)
from gitpilot.permissions import PermissionMode
from gitpilot.toolkit import (
    Effect,
    LocalWorkspace,
    ToolCall,
    ToolExecutionContext,
    ToolResult,
)
from gitpilot.toolkit.builtins import build_default_registry

from .test_loop import ScriptedAdapter

# ---------------------------------------------------------------------------
# The recorded runs
# ---------------------------------------------------------------------------

#: Calls the model is scripted to attempt.  Deliberately a mix of the harmless,
#: the mutating and the outright refused, so a run that lets anything through has
#: something to let through.
ATTEMPTS = [
    ("fs.read", {"path": "app.py"}),
    ("fs.glob", {"pattern": "**/*.py"}),
    ("fs.write", {"path": "app.py", "content": "x = 2\n"}),
    ("fs.delete", {"path": "app.py"}),
    ("terminal.run", {"command": "ls -la"}),
    ("terminal.run", {"command": "rm -rf /"}),
    ("terminal.run", {"command": "sudo apt-get install curl"}),
    ("git.commit", {"message": "wip"}),
    ("git.push", {}),
    ("fs.read", {"path": ".env"}),
]

#: Effects that mean the tool changed something outside the process.
SIDE_EFFECTING = frozenset({
    Effect.WRITES_FS, Effect.EXECUTES, Effect.GIT_LOCAL,
    Effect.GIT_REMOTE, Effect.FORGE_WRITE,
})


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "app.py").write_text("x = 1\n")
    (root / ".env").write_text("SECRET=hunter2\n")
    return root


class _Recorded:
    """One completed run, plus the journal it produced."""

    def __init__(self, ctx: AgentContext, label: str) -> None:
        self.ctx = ctx
        self.label = label
        self.lines: List[Dict[str, Any]] = ctx.journal.read()

    def of_type(self, line_type: str) -> List[Dict[str, Any]]:
        return [line for line in self.lines if line["type"] == line_type]

    def __repr__(self) -> str:  # pragma: no cover - test output only
        return f"<run {self.label}>"


def _record_run(
    tmp_path,
    workspace,
    *,
    dialect: str,
    mode: PermissionMode,
    read_only: bool = False,
    capabilities: CapabilityMask | None = None,
) -> _Recorded:
    """Drive a real run through the real policy engine and keep its journal."""
    registry = build_default_registry(include_git=True, include_forge=False)
    profile = resolve_profile(
        *{
            "native": ("claude", "claude-sonnet-4-5"),
            "react_text": ("ollama", "llama3:8b"),
            "lite": ("ollama", "qwen2.5:1.5b"),
        }[dialect],
        cache_path=tmp_path / f"probe-{dialect}.json",
    )

    turns = [
        AgentTurn(
            tool_calls=[ToolCall(id=f"c{index}", tool=tool, arguments=arguments)],
            finality=Finality.CONTINUE,
        )
        for index, (tool, arguments) in enumerate(ATTEMPTS)
    ]
    turns.append(AgentTurn(text="that is everything I could do", finality=Finality.FINAL))

    policy = PolicyEngine(ResolvedPolicy(
        mode=mode,
        allow_mutation=not read_only,
        capabilities=capabilities or CapabilityMask.permissive(),
        allow_network=False,
    ))

    journal = RunJournal(
        run_id=new_run_id(), session_id=f"{dialect}-{mode.value}", root=tmp_path / "journals",
    )
    ctx = AgentContext(
        run_id=journal.run_id,
        task="try everything",
        journal=journal,
        model_profile=profile,
        tool_context=ToolExecutionContext(
            workspace=LocalWorkspace(root=workspace), extras={"registry": registry},
        ),
        session_id=journal.session_id,
        capabilities=policy.policy.capabilities,
        messages=[{"role": "user", "content": "try everything"}],
    )
    ctx.budgets.max_iterations = len(turns) + 2
    ctx.budgets.max_tool_calls = len(turns) + 2

    adapter = ScriptedAdapter(turns, profile=profile, registry=registry)
    loop = AgentLoop(
        adapter, registry, policy=policy, approvals=DenyingApprovals(),
    )
    asyncio.run(loop.run(ctx))
    return _Recorded(ctx, f"{dialect}/{mode.value}{'/read-only' if read_only else ''}")


def _record_run_with(tmp_path, workspace, *, attempts, mode) -> "_Recorded":
    """Record a run over an explicit list of attempts."""
    global ATTEMPTS
    original, ATTEMPTS = ATTEMPTS, attempts
    try:
        return _record_run(tmp_path, workspace, dialect="native", mode=mode)
    finally:
        ATTEMPTS = original


@pytest.fixture(scope="function")
def matrix(tmp_path, workspace):
    """Every dialect against every mode — the journals the properties run over."""
    runs = []
    for dialect in ("native", "react_text", "lite"):
        for mode in (PermissionMode.PLAN, PermissionMode.NORMAL, PermissionMode.AUTO):
            runs.append(_record_run(tmp_path, workspace, dialect=dialect, mode=mode))
    runs.append(
        _record_run(
            tmp_path, workspace, dialect="native",
            mode=PermissionMode.AUTO, read_only=True,
        ),
    )
    return runs


def _registry_specs():
    registry = build_default_registry(include_git=True, include_forge=True)
    return {spec.id: spec for spec in registry.specs()}


SPECS = _registry_specs()


def _is_side_effecting(tool: str) -> bool:
    spec = SPECS.get(tool)
    return spec is not None and bool(spec.effects & SIDE_EFFECTING)


# ---------------------------------------------------------------------------
# 1. Nothing executes unauthorized
# ---------------------------------------------------------------------------


class TestNothingExecutesUnauthorized:
    def test_every_side_effecting_result_has_an_allow_before_it(self, matrix):
        """The ordering the journal exists to make checkable: a decision line
        must precede the result, and it must say allow."""
        for run in matrix:
            decisions: Dict[str, Dict[str, Any]] = {}
            for line in run.lines:
                if line["type"] == LineType.POLICY_DECISION:
                    decisions[line["call_id"]] = line
                elif line["type"] == LineType.TOOL_RESULT:
                    tool = line.get("tool", "")
                    if not _is_side_effecting(tool):
                        continue
                    if line.get("denied") or not line.get("ok"):
                        continue

                    decision = decisions.get(line["call_id"])
                    assert decision is not None, (
                        f"{run.label}: {tool} produced a result with no decision"
                    )
                    assert decision["verdict"] in ("allow", "approved"), (
                        f"{run.label}: {tool} ran under verdict "
                        f"{decision['verdict']!r}"
                    )

    def test_every_call_is_decided_before_it_is_executed(self, matrix):
        for run in matrix:
            seen_decision = set()
            for line in run.lines:
                if line["type"] == LineType.POLICY_DECISION:
                    seen_decision.add(line["call_id"])
                elif line["type"] == LineType.TOOL_RESULT:
                    assert line["call_id"] in seen_decision, (
                        f"{run.label}: {line.get('tool')} executed before any decision"
                    )

    def test_a_denied_call_leaves_a_denial_not_a_result(self, matrix):
        for run in matrix:
            denied = {
                line["call_id"] for line in run.of_type(LineType.POLICY_DECISION)
                if line["verdict"] == "deny"
            }
            for line in run.of_type(LineType.TOOL_RESULT):
                if line["call_id"] in denied:
                    assert line.get("denied") or not line.get("ok"), (
                        f"{run.label}: {line.get('tool')} was denied and still succeeded"
                    )


# ---------------------------------------------------------------------------
# 2. Read-only means read-only, in every dialect
# ---------------------------------------------------------------------------


class TestReadOnlyHolds:
    def _mutating_successes(self, run):
        """Successful calls that could have changed something outside the run.

        Batch V4-G3 made this consult the recorded classification rather than the
        declared effects alone. ``terminal.run`` declares ``WRITES_FS`` because it
        *can* write, not because ``ls -la`` did — and since the policy now decides
        a read-only run's shell calls on the classifier's verdict, the invariant has
        to be as precise as the decision was or it would fail on a call that
        provably changed nothing. The journal carries the class, so the evidence is
        the run's own record rather than this test's opinion.
        """
        decisions = {
            line.get("call_id"): line for line in run.of_type(LineType.POLICY_DECISION)
        }
        out = []
        for line in run.of_type(LineType.TOOL_RESULT):
            if not line.get("ok") or line.get("denied"):
                continue
            tool = line.get("tool", "")
            if not _is_side_effecting(tool):
                continue
            decision = decisions.get(line.get("call_id")) or {}
            if decision.get("command_class") in READ_ONLY_COMMAND_CLASSES:
                continue
            out.append(tool)
        return out

    def _shell_successes(self, run):
        return [
            line for line in run.of_type(LineType.TOOL_RESULT)
            if line.get("ok") and not line.get("denied")
            and line.get("tool") in ("terminal.run", "terminal.run_snippet", "test.run")
        ]

    def test_plan_mode_executes_nothing_mutating_in_any_dialect(self, matrix):
        for run in matrix:
            if "/plan" not in run.label:
                continue
            assert self._mutating_successes(run) == [], (
                f"{run.label} executed mutating tools in plan mode"
            )

    def test_a_read_only_run_executes_nothing_mutating(self, matrix):
        for run in matrix:
            if "read-only" not in run.label:
                continue
            assert self._mutating_successes(run) == [], (
                f"{run.label} mutated despite being read-only"
            )

    def test_every_shell_command_a_read_only_run_ran_was_classified_read_only(self, matrix):
        """The other half of the invariant above, stated positively: the exemption
        is not "shell calls are excused", it is "these particular ones were
        recorded as reading nothing but the repository"."""
        for run in matrix:
            if "read-only" not in run.label:
                continue
            decisions = {
                line.get("call_id"): line
                for line in run.of_type(LineType.POLICY_DECISION)
            }
            for line in self._shell_successes(run):
                decision = decisions.get(line.get("call_id")) or {}
                assert decision.get("command_class") in READ_ONLY_COMMAND_CLASSES, (
                    f"{run.label} ran {line.get('tool')} classified "
                    f"{decision.get('command_class')!r}"
                )

    def test_lite_gets_the_same_protection_as_a_frontier_model(self, tmp_path, workspace):
        """The dialects synthesize canonical calls precisely so one rule covers
        all three; a LITE run that bypassed the engine would be the gap."""
        run = _record_run(
            tmp_path, workspace, dialect="lite", mode=PermissionMode.PLAN,
        )
        assert run.of_type(LineType.POLICY_DECISION), "no decisions were recorded at all"
        assert self._mutating_successes(run) == []

    def test_a_blocked_path_is_never_read_in_any_mode(self, matrix):
        """`.env` is in the default blocked list; reading it is exfiltration
        whichever mode the session is in."""
        for run in matrix:
            for line in run.of_type(LineType.TOOL_RESULT):
                if line.get("tool") != "fs.read":
                    continue
                if line.get("ok") and not line.get("denied"):
                    call = next(
                        (c for c in run.of_type(LineType.TOOL_CALL)
                         if c["call_id"] == line["call_id"]),
                        None,
                    )
                    path = (call or {}).get("arguments", {}).get("path", "")
                    assert ".env" not in path, f"{run.label} read {path}"

    def test_the_same_call_never_runs_after_being_denied(self, matrix):
        """A denial is not advice: re-issuing the identical call must be denied
        again rather than succeeding on the second attempt.

        Keyed on (tool, arguments) rather than on the tool — `terminal.run "ls"`
        and `terminal.run "rm -rf /"` are the same tool and must reach opposite
        verdicts, which is the entire reason D2 classifies the argument instead
        of the name.
        """
        for run in matrix:
            calls = {
                line["call_id"]: (line.get("tool"), _key(line.get("arguments")))
                for line in run.of_type(LineType.TOOL_CALL)
            }
            denied = {
                calls[line["call_id"]]
                for line in run.of_type(LineType.POLICY_DECISION)
                if line["verdict"] == "deny" and line["call_id"] in calls
            }
            succeeded = {
                calls[line["call_id"]]
                for line in run.of_type(LineType.TOOL_RESULT)
                if line.get("ok") and not line.get("denied") and line["call_id"] in calls
            }

            assert not (denied & succeeded), (
                f"{run.label}: {sorted(denied & succeeded)} was denied and still ran"
            )

    def test_a_denied_command_is_denied_on_every_attempt(self, tmp_path, workspace):
        """The scripted run attempts `rm -rf /` once; this attempts it twice, so
        "denied, then allowed on retry" would be visible."""
        run = _record_run_with(
            tmp_path, workspace,
            attempts=[("terminal.run", {"command": "rm -rf /"})] * 3,
            mode=PermissionMode.AUTO,
        )
        verdicts = [line["verdict"] for line in run.of_type(LineType.POLICY_DECISION)]
        assert verdicts == ["deny", "deny", "deny"]


# ---------------------------------------------------------------------------
# 3. No file content without a read
# ---------------------------------------------------------------------------


class TestNoUnjournaledContent:
    def test_file_content_in_the_transcript_traces_back_to_a_read(self, matrix):
        """A dialect that read a file itself — rather than through the registry —
        would put content in front of the model with no decision and no record."""
        for run in matrix:
            journaled_reads = {
                self._path_of(run, line["call_id"])
                for line in run.of_type(LineType.TOOL_RESULT)
                if line.get("tool") in ("fs.read", "fs.glob", "fs.grep", "fs.list")
                and line.get("ok")
            }
            assert "hunter2" not in _transcript(run.ctx), (
                f"{run.label}: secret file content reached the transcript"
            )
            if "x = 1" in _transcript(run.ctx):
                assert any(
                    path and "app.py" in path for path in journaled_reads
                ), f"{run.label}: app.py content appeared with no journaled read"

    @staticmethod
    def _path_of(run, call_id):
        for line in run.of_type(LineType.TOOL_CALL):
            if line["call_id"] == call_id:
                return line.get("arguments", {}).get("path")
        return None


def _key(arguments: Any) -> str:
    """A stable identity for a call's arguments."""
    try:
        return json.dumps(arguments or {}, sort_keys=True)
    except TypeError:
        return repr(arguments)


def _transcript(ctx: AgentContext) -> str:
    return "\n".join(str(message.get("content", "")) for message in ctx.messages)


# ---------------------------------------------------------------------------
# 4. No private reasoning is ever persisted
# ---------------------------------------------------------------------------


class TestNoReasoningPersisted:
    def test_no_journal_line_carries_reasoning_tags(self, matrix):
        for run in matrix:
            for line in run.lines:
                assert_no_reasoning(line, where=f"{run.label} line {line.get('seq')}")

    def test_the_guard_actually_catches_it(self):
        """A property test over data nothing produces would pass vacuously."""
        from gitpilot.agent.journal import JournalError

        with pytest.raises(JournalError):
            assert_no_reasoning({"text": "<think>the user cannot see this</think>"})

    def test_the_journal_refuses_to_write_it(self, tmp_path):
        from gitpilot.agent.journal import JournalError

        journal = RunJournal(run_id="r", session_id="s", root=tmp_path)
        with pytest.raises(JournalError):
            journal.append(LineType.TURN, {"text": "<thought>hidden</thought>"})


# ---------------------------------------------------------------------------
# 5. The worst commands never reach a prompt
# ---------------------------------------------------------------------------


class TestNoPromptForTheWorst:
    def test_destructive_and_privileged_commands_are_denied_outright(self, matrix):
        """Asking a user to approve `rm -rf /` is a prompt whose safe answer we
        already know, and whose unsafe answer is one misclick away."""
        for run in matrix:
            for line in run.of_type(LineType.POLICY_DECISION):
                if line.get("command_class") in ("DESTRUCTIVE", "PRIVILEGED"):
                    assert line["verdict"] == "deny", (
                        f"{run.label}: a {line['command_class']} command got "
                        f"verdict {line['verdict']!r}"
                    )

    def test_no_approval_was_ever_requested_for_one(self, matrix):
        for run in matrix:
            asked = {
                line["call_id"] for line in run.of_type(LineType.POLICY_DECISION)
                if line["verdict"] == "ask"
            }
            for line in run.of_type(LineType.TOOL_CALL):
                if line["call_id"] not in asked:
                    continue
                command = line.get("arguments", {}).get("command", "")
                if not command:
                    continue
                from gitpilot.agent.command_class import classify

                assert classify(command) not in ("DESTRUCTIVE", "PRIVILEGED"), (
                    f"{run.label}: prompted for {command!r}"
                )

    def test_they_are_denied_in_auto_mode_too(self, matrix):
        """Auto mode means "do not ask me", not "do anything"."""
        for run in matrix:
            if "/auto" not in run.label:
                continue
            for line in run.of_type(LineType.POLICY_DECISION):
                if line.get("command_class") in ("DESTRUCTIVE", "PRIVILEGED"):
                    assert line["verdict"] == "deny", run.label


# ---------------------------------------------------------------------------
# 6. Secrets never reach a subprocess
# ---------------------------------------------------------------------------


class TestSecretsStripped:
    def test_the_child_environment_has_no_credentials(self, monkeypatch):
        from gitpilot.shell_safety import SECRET_ENV_KEYS, strip_secret_env

        for key in SECRET_ENV_KEYS:
            monkeypatch.setenv(key, f"secret-{key}")
        monkeypatch.setenv("PATH_LIKE_THING", "keep")

        env = strip_secret_env()

        for key in SECRET_ENV_KEYS:
            assert key not in env, key
        assert env["PATH_LIKE_THING"] == "keep"

    def test_the_sandbox_strips_them(self, tmp_path, monkeypatch):
        """`terminal.run` and `test.run` route through here, so this is the one
        place the property has to hold."""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")

        from gitpilot.sandbox import SandboxPolicy, SubprocessSandbox

        sandbox = SubprocessSandbox(SandboxPolicy(workspace=tmp_path, allow_network=False))
        result = asyncio.run(sandbox.run("env", timeout=30))

        assert "ghp_secret" not in result.stdout
        assert "sk-secret" not in result.stdout

    def test_the_proxy_is_removed_when_the_network_is_off(self, monkeypatch):
        from gitpilot.shell_safety import strip_secret_env

        monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")

        assert "HTTPS_PROXY" not in strip_secret_env(allow_network=False)
        assert "HTTPS_PROXY" in strip_secret_env(allow_network=True)

    def test_a_hook_command_gets_the_same_treatment(self, tmp_path, monkeypatch):
        """Hooks run user-authored shell too, and they got their first callers in
        Batch V4-D5 — so they join the property rather than being an exception."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")

        from gitpilot.hooks import HookDefinition, HookEvent, HookManager

        out = tmp_path / "env.txt"
        manager = HookManager()
        manager.register(HookDefinition(
            event=HookEvent.POST_EDIT, name="dump", command=f"env > {out}",
        ))
        asyncio.run(manager.fire(HookEvent.POST_EDIT, {}, cwd=tmp_path))

        assert "sk-ant-secret" not in out.read_text()


# ---------------------------------------------------------------------------
# The ratchet itself
# ---------------------------------------------------------------------------


class TestTheRatchetIsReal:
    def test_the_matrix_actually_attempted_dangerous_things(self, matrix):
        """These properties are only worth anything if the runs they check
        genuinely tried to break them."""
        for run in matrix:
            attempted = {line.get("tool") for line in run.of_type(LineType.TOOL_CALL)}
            assert "fs.write" in attempted, f"{run.label} never tried to write"
            assert "terminal.run" in attempted, f"{run.label} never tried a command"
            assert "git.push" in attempted, f"{run.label} never tried to push"

    def test_something_was_denied_in_every_run(self, matrix):
        for run in matrix:
            verdicts = {
                line["verdict"] for line in run.of_type(LineType.POLICY_DECISION)
            }
            assert "deny" in verdicts, (
                f"{run.label} denied nothing at all — the engine may not be wired in"
            )

    def test_the_permissive_stub_would_fail_these(self, tmp_path, workspace):
        """Proof the properties are load-bearing: swap the engine for Phase C's
        permissive stub and invariant 2 breaks."""
        from gitpilot.agent.loop import PermissivePolicy

        registry = build_default_registry(include_git=True, include_forge=False)
        profile = resolve_profile("claude", "claude-sonnet-4-5", cache_path=tmp_path / "p.json")
        journal = RunJournal(run_id=new_run_id(), session_id="stub", root=tmp_path / "j")
        ctx = AgentContext(
            run_id=journal.run_id, task="x", journal=journal, model_profile=profile,
            tool_context=ToolExecutionContext(
                workspace=LocalWorkspace(root=workspace), extras={"registry": registry},
            ),
            session_id="stub", messages=[{"role": "user", "content": "x"}],
        )
        adapter = ScriptedAdapter(
            [
                AgentTurn(
                    tool_calls=[ToolCall(
                        id="c1", tool="fs.write",
                        arguments={"path": "app.py", "content": "overwritten\n"},
                    )],
                    finality=Finality.CONTINUE,
                ),
                AgentTurn(text="done", finality=Finality.FINAL),
            ],
            profile=profile, registry=registry,
        )

        asyncio.run(AgentLoop(adapter, registry, policy=PermissivePolicy()).run(ctx))

        wrote = [
            line for line in journal.read()
            if line["type"] == LineType.TOOL_RESULT and line.get("ok")
            and line.get("tool") == "fs.write"
        ]
        assert wrote, "the stub should have let the write through — that is the point"
        assert (workspace / "app.py").read_text() == "overwritten\n"
