"""Loop-owned checkpoints and firing hooks — Batches V4-D4 and V4-D5."""
from __future__ import annotations

import asyncio
import json

import pytest

from gitpilot.agent.checkpointing import SessionCheckpointer, target_of
from gitpilot.agent.hook_bridge import LoopHooks
from gitpilot.agent.journal import LineType, RunJournal
from gitpilot.agent.loop import AgentLoop, new_run_id
from gitpilot.agent.policy import Decision
from gitpilot.hooks import HookDefinition, HookEvent, HookManager
from gitpilot.session import SessionManager
from gitpilot.toolkit import Effect, Risk, ToolCall, ToolResult, ToolSpec

from .test_loop import ScriptedAdapter, _context, _registry  # noqa: F401 — shared doubles
from gitpilot.agent.contracts import AgentTurn, Finality


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "app.py").write_text("x = 1\n")
    return root


@pytest.fixture
def session_mgr(tmp_path):
    return SessionManager(
        root=tmp_path / "state" / "sessions", history_root=tmp_path / "history",
    )


@pytest.fixture
def session(session_mgr):
    s = session_mgr.create(name="run")
    s.add_message("user", "please edit app.py")
    session_mgr.save(s)
    return s


def _call(tool="fs.write", **arguments):
    return ToolCall(id="c1", tool=tool, arguments=arguments or {"path": "app.py"})


class _Ctx:
    def __init__(self, journal=None, run_id="run_1"):
        self.run_id = run_id
        self.journal = journal


# ---------------------------------------------------------------------------
# V4-D4 — checkpoints
# ---------------------------------------------------------------------------


class TestCheckpointer:
    def test_a_mutating_call_is_snapshotted(self, session, session_mgr, workspace):
        checkpointer = SessionCheckpointer(
            session_id=session.id, session_manager=session_mgr, workspace=workspace,
        )

        assert asyncio.run(checkpointer.create(_call(), _Ctx())) is not None

        checkpoints = session_mgr.load(session.id).checkpoints
        assert len(checkpoints) == 1
        assert checkpoints[0].tool_name == "fs.write"
        assert checkpoints[0].target_path == "app.py"
        assert "app.py" in checkpoints[0].description

    def test_the_arguments_are_recorded(self, session, session_mgr, workspace):
        """The field has existed since checkpoints landed and every caller left
        it empty, so "what was this snapshot for?" had only a name and a path."""
        checkpointer = SessionCheckpointer(
            session_id=session.id, session_manager=session_mgr, workspace=workspace,
        )
        asyncio.run(checkpointer.create(
            _call("fs.edit", path="app.py", old="x = 1", new="x = 2"), _Ctx(),
        ))

        store_id = session_mgr.load(session.id).checkpoints[0].store_id
        record = json.loads(
            (session_mgr.history_root / "meta" / store_id / "tool_call.json").read_text()
        ) if (session_mgr.history_root / "meta").exists() else None

        # The descriptor is stored by CheckpointStore; find it wherever it lives.
        if record is None:
            from gitpilot.checkpoints import CheckpointStore

            store = CheckpointStore(workspace, history_root=session_mgr.history_root)
            record = store.restore(store_id).get("tool_call") or {}
        assert record.get("arguments", {}).get("new") == "x = 2"

    def test_the_run_and_journal_position_are_recorded(
        self, session, session_mgr, workspace, tmp_path,
    ):
        """A checkpoint that cannot say where in the run it sits can only rewind
        files, not the run."""
        journal = RunJournal(run_id="run_abc", session_id=session.id, root=tmp_path / "j")
        journal.append(LineType.STATE_CHANGE, {"state": "running"})

        checkpointer = SessionCheckpointer(
            session_id=session.id, session_manager=session_mgr, workspace=workspace,
        )
        asyncio.run(checkpointer.create(_call(), _Ctx(journal=journal, run_id="run_abc")))

        checkpoint = session_mgr.load(session.id).checkpoints[0]
        assert checkpoint.run_id == "run_abc"
        assert checkpoint.journal_seq == 1

    def test_the_transcript_is_current_not_stale(self, session, session_mgr, workspace):
        """A checkpoint's value is the conversation it carries, and the run has
        moved on since it started."""
        checkpointer = SessionCheckpointer(
            session_id=session.id, session_manager=session_mgr, workspace=workspace,
        )
        live = session_mgr.load(session.id)
        live.add_message("assistant", "I'll edit app.py")
        session_mgr.save(live)

        asyncio.run(checkpointer.create(_call(), _Ctx()))

        assert session_mgr.load(session.id).checkpoints[0].message_index == 2

    def test_no_workspace_means_no_snapshots(self, session, session_mgr):
        """Recording checkpoints that could never be restored helps nobody."""
        checkpointer = SessionCheckpointer(
            session_id=session.id, session_manager=session_mgr, workspace=None,
        )
        assert checkpointer.enabled is False
        assert asyncio.run(checkpointer.create(_call(), _Ctx())) is None

    def test_a_vanished_session_does_not_break_the_run(self, session_mgr, workspace):
        checkpointer = SessionCheckpointer(
            session_id="no-such-session", session_manager=session_mgr, workspace=workspace,
        )
        assert asyncio.run(checkpointer.create(_call(), _Ctx())) is None

    def test_old_snapshots_are_pruned(self, session, session_mgr, workspace):
        """`CheckpointStore.prune` has never had a production caller, and a loop
        that snapshots before every write would grow the shadow repo forever."""
        checkpointer = SessionCheckpointer(
            session_id=session.id, session_manager=session_mgr,
            workspace=workspace, keep_last=3,
        )
        for index in range(6):
            (workspace / "app.py").write_text(f"x = {index}\n")
            asyncio.run(checkpointer.create(_call(path="app.py"), _Ctx()))

        from gitpilot.checkpoints import CheckpointStore

        store = CheckpointStore(workspace, history_root=session_mgr.history_root)
        assert len(store.list()) <= 3

    def test_a_session_store_that_errors_does_not_break_the_run(
        self, session_mgr, workspace, caplog,
    ):
        """A snapshot is a safety net; a net that stops the show is worse than
        one with a hole in it."""
        import logging

        class _Broken:
            history_root = None

            def load(self, session_id):
                raise RuntimeError("disk on fire")

        checkpointer = SessionCheckpointer(
            session_id="s", session_manager=_Broken(), workspace=workspace,
        )

        with caplog.at_level(logging.WARNING, logger="gitpilot.agent.checkpointing"):
            assert asyncio.run(checkpointer.create(_call(), _Ctx())) is None
        assert "could not load session" in caplog.text

    def test_a_prune_failure_is_not_reported_as_a_snapshot_failure(
        self, session, session_mgr, workspace, monkeypatch,
    ):
        """Pruning is housekeeping; the snapshot it follows already succeeded."""
        import gitpilot.checkpoints as checkpoints_module

        def explode(*args, **kwargs):
            raise RuntimeError("cannot list the shadow repo")

        checkpointer = SessionCheckpointer(
            session_id=session.id, session_manager=session_mgr, workspace=workspace,
        )
        monkeypatch.setattr(checkpoints_module.CheckpointStore, "prune", explode)

        assert asyncio.run(checkpointer.create(_call(), _Ctx())) is not None
        assert len(session_mgr.load(session.id).checkpoints) == 1

    @pytest.mark.parametrize(("arguments", "expected"), [
        ({"path": "a.py"}, "a.py"),
        ({"file_path": "b.py"}, "b.py"),
        ({"filename": "c.py"}, "c.py"),
        ({"message": "no path here"}, None),
    ])
    def test_the_target_is_found_however_it_is_named(self, arguments, expected):
        assert target_of(arguments) == expected


class TestCheckpointOwnership:
    def _loop_with(self, tmp_path, checkpointer, policy=None, approvals=None):
        registry = _registry(tool_id="fs.write", effects=frozenset({Effect.WRITES_FS}))
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call("fs.write", path="a.py")], finality=Finality.CONTINUE),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)
        loop = AgentLoop(
            adapter, registry, checkpointer=checkpointer,
            policy=policy, approvals=approvals,
        )
        return loop, ctx

    def test_the_snapshot_precedes_the_call(self, tmp_path):
        order = []

        class _Checkpointer:
            async def create(self, call, ctx):
                order.append("checkpoint")
                return "ck-1"

        registry = _registry(tool_id="fs.write", effects=frozenset({Effect.WRITES_FS}))

        async def writer(call, ctx):
            order.append("execute")
            return ToolResult.success(call, "written")

        registry._handlers["fs.write"] = writer   # noqa: SLF001
        adapter = ScriptedAdapter([
            AgentTurn(tool_calls=[_call("fs.write", path="a.py")], finality=Finality.CONTINUE),
            AgentTurn(text="done", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)

        asyncio.run(AgentLoop(adapter, registry, checkpointer=_Checkpointer()).run(ctx))

        assert order == ["checkpoint", "execute"]

    def test_exactly_one_snapshot_on_the_ask_path(self, tmp_path):
        """The gate used to snapshot on approval; if it still did, this path
        would snapshot twice."""
        created = []

        class _Checkpointer:
            async def create(self, call, ctx):
                created.append(call.tool)
                return f"ck-{len(created)}"

        class _AskingPolicy:
            async def authorize(self, call, ctx):
                return Decision(verdict="ask", risk="approval", requires_checkpoint=True)

        class _Approver:
            async def request(self, call, decision, ctx):
                return True

        loop, ctx = self._loop_with(
            tmp_path, _Checkpointer(), policy=_AskingPolicy(), approvals=_Approver(),
        )
        asyncio.run(loop.run(ctx))

        assert created == ["fs.write"]

    def test_a_denied_call_is_not_snapshotted(self, tmp_path):
        created = []

        class _Checkpointer:
            async def create(self, call, ctx):
                created.append(call.tool)
                return "ck"

        class _DenyingPolicy:
            async def authorize(self, call, ctx):
                return Decision(verdict="deny", reason="no", requires_checkpoint=True)

        loop, ctx = self._loop_with(tmp_path, _Checkpointer(), policy=_DenyingPolicy())
        asyncio.run(loop.run(ctx))

        assert created == []


class TestRewind:
    def test_a_rewind_can_truncate_the_run_journal(
        self, session, session_mgr, workspace, tmp_path,
    ):
        """Files back as they were, under a journal that still describes the
        edits that followed, is two histories that disagree."""
        journal = RunJournal(run_id="run_r", session_id=session.id, root=tmp_path / "j")
        journal.append(LineType.STATE_CHANGE, {"state": "running"})
        journal.append(LineType.TOOL_CALL, {"call_id": "c1", "tool": "fs.write", "arguments": {}})
        at = journal.seq
        journal.append(LineType.TOOL_RESULT, {"call_id": "c1", "ok": True})
        journal.append(LineType.TURN, {"text": "later work"})

        removed = journal.truncate_after(at)

        assert removed == 2
        assert [line["seq"] for line in journal.read()] == [1, 2]
        assert journal.path.with_suffix(f".rewound-{at}.jsonl").exists(), (
            "the discarded tail should be archived, not deleted"
        )

    def test_truncation_is_off_by_default(self, session, session_mgr, workspace):
        """The journal is also the audit record; a caller inspecting history
        afterwards must not have it rewritten under them."""
        checkpointer = SessionCheckpointer(
            session_id=session.id, session_manager=session_mgr, workspace=workspace,
        )
        asyncio.run(checkpointer.create(_call(), _Ctx()))
        live = session_mgr.load(session.id)

        session_mgr.rewind_to_checkpoint(
            live, live.checkpoints[0].id, workspace_path=workspace,
        )   # no truncate_journal=True — must not raise, must not truncate

    def test_the_files_come_back(self, session, session_mgr, workspace):
        checkpointer = SessionCheckpointer(
            session_id=session.id, session_manager=session_mgr, workspace=workspace,
        )
        asyncio.run(checkpointer.create(_call(), _Ctx()))
        (workspace / "app.py").write_text("x = 999\n")

        live = session_mgr.load(session.id)
        session_mgr.rewind_to_checkpoint(
            live, live.checkpoints[0].id, workspace_path=workspace,
        )

        assert (workspace / "app.py").read_text() == "x = 1\n"


# ---------------------------------------------------------------------------
# V4-D5 — hooks
# ---------------------------------------------------------------------------


class TestHooksFire:
    def _manager(self, event, **kwargs):
        manager = HookManager()
        seen = []

        def handler(context):
            seen.append(dict(context))
            return kwargs.get("output", "ok")

        manager.register(HookDefinition(
            event=event, name=kwargs.get("name", "spy"), handler=handler,
            blocking=kwargs.get("blocking", False),
        ))
        return manager, seen

    def test_pre_and_post_tool_use_fire(self, workspace):
        manager, seen = self._manager(HookEvent.PRE_TOOL_USE)
        hooks = LoopHooks(manager, workspace=workspace, session_id="s")

        asyncio.run(hooks.fire("pre_tool_use", call=_call("fs.read", path="a.py"), ctx=_Ctx()))

        assert len(seen) == 1
        assert seen[0]["tool"] == "fs.read"
        assert seen[0]["run_id"] == "run_1"

    @pytest.mark.parametrize(("tool", "event"), [
        ("fs.write", HookEvent.PRE_EDIT),
        ("fs.edit", HookEvent.PRE_EDIT),
        ("git.commit", HookEvent.PRE_COMMIT),
        ("git.push", HookEvent.PRE_PUSH),
    ])
    def test_each_tool_fires_its_own_lifecycle_event(self, workspace, tool, event):
        manager, seen = self._manager(event)
        hooks = LoopHooks(manager, workspace=workspace, session_id="s")

        asyncio.run(hooks.fire("pre_tool_use", call=_call(tool, path="a.py"), ctx=_Ctx()))

        assert len(seen) == 1, f"{event.value} did not fire for {tool}"

    @pytest.mark.parametrize(("tool", "event"), [
        ("fs.write", HookEvent.POST_EDIT),
        ("git.commit", HookEvent.POST_COMMIT),
    ])
    def test_the_after_events_fire_too(self, workspace, tool, event):
        manager, seen = self._manager(event)
        hooks = LoopHooks(manager, workspace=workspace, session_id="s")
        result = ToolResult.success(_call(tool), "done")

        asyncio.run(hooks.fire("post_tool_use", call=_call(tool), result=result, ctx=_Ctx()))

        assert len(seen) == 1
        assert seen[0]["ok"] == "1"

    def test_session_start_also_fires_user_message(self, workspace):
        manager, started = self._manager(HookEvent.SESSION_START)
        _m2, messaged = self._manager(HookEvent.USER_MESSAGE)
        manager._hooks[HookEvent.USER_MESSAGE] = _m2._hooks[HookEvent.USER_MESSAGE]  # noqa: SLF001
        hooks = LoopHooks(manager, workspace=workspace, session_id="s")

        asyncio.run(hooks.session_start("do the thing"))

        assert len(started) == 1
        assert messaged[0]["message"] == "do the thing"

    def test_session_end_reports_the_status(self, workspace):
        manager, seen = self._manager(HookEvent.SESSION_END)
        hooks = LoopHooks(manager, workspace=workspace, session_id="s")

        asyncio.run(hooks.session_end("completed"))

        assert seen[0]["status"] == "completed"

    def test_a_blocking_hook_prevents_execution_and_the_model_sees_why(self, tmp_path):
        manager = HookManager()
        manager.register(HookDefinition(
            event=HookEvent.PRE_COMMIT, name="needs-ticket",
            command="echo 'commits need a ticket number'; exit 1", blocking=True,
        ))
        hooks = LoopHooks(manager, workspace=tmp_path, session_id="s")

        registry = _registry(tool_id="git.commit", effects=frozenset({Effect.GIT_LOCAL}))
        adapter = ScriptedAdapter([
            AgentTurn(
                tool_calls=[ToolCall(id="c1", tool="git.commit", arguments={"path": "x"})],
                finality=Finality.CONTINUE,
            ),
            AgentTurn(text="understood", finality=Finality.FINAL),
        ])
        ctx = _context(tmp_path, registry)

        asyncio.run(AgentLoop(adapter, registry, hooks=hooks).run(ctx))

        assert len(ctx.ledger) == 1
        entry = ctx.ledger.entries[0]
        assert entry.result.ok is False
        assert "needs-ticket" in entry.result.content
        assert "ticket number" in entry.result.content

    def test_a_hook_that_raises_is_logged_and_non_fatal(self, workspace, caplog):
        import logging

        class _Exploding(HookManager):
            async def fire(self, event, context=None, cwd=None):
                raise RuntimeError("hook infrastructure is down")

        manager = _Exploding()
        manager.register(HookDefinition(
            event=HookEvent.PRE_TOOL_USE, name="x", command="true",
        ))
        hooks = LoopHooks(manager, workspace=workspace, session_id="s")

        with caplog.at_level(logging.WARNING, logger="gitpilot.agent.hook_bridge"):
            blocked = asyncio.run(hooks.fire("pre_tool_use", call=_call(), ctx=_Ctx()))

        assert blocked is None
        assert "raised" in caplog.text

    def test_a_slow_hook_is_reported(self, workspace, caplog):
        import logging

        manager = HookManager()
        manager.register(HookDefinition(
            event=HookEvent.PRE_TOOL_USE, name="slow", handler=lambda ctx: "done",
        ))
        hooks = LoopHooks(manager, workspace=workspace, session_id="s", budget_s=0.0)

        with caplog.at_level(logging.WARNING, logger="gitpilot.agent.hook_bridge"):
            asyncio.run(hooks.fire("pre_tool_use", call=_call(), ctx=_Ctx()))

        assert "the run was waiting" in caplog.text

    def test_a_project_config_is_loaded(self, workspace):
        config = workspace / ".gitpilot"
        config.mkdir()
        (config / "hooks.json").write_text(json.dumps([
            {"event": "post_edit", "name": "lint", "command": "true"},
        ]))

        hooks = LoopHooks.for_workspace(workspace, session_id="s")

        assert [h["name"] for h in hooks.manager.list_hooks()] == ["lint"]

    def test_a_broken_config_does_not_stop_the_run(self, workspace):
        config = workspace / ".gitpilot"
        config.mkdir()
        (config / "hooks.json").write_text("{not json")

        assert LoopHooks.for_workspace(workspace).manager.list_hooks() == []


class TestHookEnvironment:
    def test_a_hook_command_does_not_receive_credentials(self, tmp_path, monkeypatch):
        """The manager built its child env from `os.environ` unfiltered. Nothing
        fired hooks, so it never mattered; giving them callers without fixing it
        would hand every user hook every API key."""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
        monkeypatch.setenv("HOME_SAFE_VALUE", "keep-me")

        out = tmp_path / "env.txt"
        manager = HookManager()
        manager.register(HookDefinition(
            event=HookEvent.POST_EDIT, name="dump",
            command=f"env > {out}",
        ))

        asyncio.run(manager.fire(HookEvent.POST_EDIT, {"path": "a.py"}, cwd=tmp_path))

        dumped = out.read_text()
        assert "ghp_secret" not in dumped
        assert "sk-secret" not in dumped
        assert "keep-me" in dumped, "only credentials should be stripped"
        assert "GITPILOT_HOOK_PATH=a.py" in dumped
