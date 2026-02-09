"""Tests for gitpilot.hooks module."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gitpilot.hooks import HookDefinition, HookEvent, HookManager, HookResult


# ---------------------------------------------------------------------------
# Register / Unregister
# ---------------------------------------------------------------------------

class TestRegisterUnregister:
    def test_register_adds_hook(self):
        mgr = HookManager()
        hook = HookDefinition(
            event=HookEvent.POST_EDIT, name="lint", command="ruff check .",
        )
        mgr.register(hook)
        hooks = mgr.list_hooks()
        assert len(hooks) == 1
        assert hooks[0]["name"] == "lint"
        assert hooks[0]["event"] == "post_edit"

    def test_register_multiple_hooks(self):
        mgr = HookManager()
        mgr.register(HookDefinition(event=HookEvent.POST_EDIT, name="lint", command="ruff ."))
        mgr.register(HookDefinition(event=HookEvent.POST_EDIT, name="format", command="black ."))
        hooks = mgr.list_hooks()
        assert len(hooks) == 2

    def test_unregister_removes_hook(self):
        mgr = HookManager()
        mgr.register(HookDefinition(event=HookEvent.PRE_COMMIT, name="test", command="pytest"))
        mgr.unregister(HookEvent.PRE_COMMIT, "test")
        hooks = mgr.list_hooks()
        assert len(hooks) == 0

    def test_unregister_nonexistent_is_noop(self):
        mgr = HookManager()
        mgr.unregister(HookEvent.SESSION_START, "nope")  # should not raise
        assert mgr.list_hooks() == []


# ---------------------------------------------------------------------------
# list_hooks
# ---------------------------------------------------------------------------

class TestListHooks:
    def test_empty_initially(self):
        mgr = HookManager()
        assert mgr.list_hooks() == []

    def test_includes_all_fields(self):
        mgr = HookManager()
        mgr.register(HookDefinition(
            event=HookEvent.PRE_PUSH, name="verify",
            command="make verify", blocking=True, timeout=60,
        ))
        hooks = mgr.list_hooks()
        h = hooks[0]
        assert h["blocking"] is True
        assert h["timeout"] == 60
        assert h["command"] == "make verify"


# ---------------------------------------------------------------------------
# fire (async)
# ---------------------------------------------------------------------------

class TestFire:
    async def test_fire_no_hooks(self):
        mgr = HookManager()
        results = await mgr.fire(HookEvent.SESSION_START)
        assert results == []

    async def test_fire_handler_hook(self):
        mgr = HookManager()
        handler = MagicMock(return_value="done")
        mgr.register(HookDefinition(
            event=HookEvent.USER_MESSAGE, name="log", handler=handler,
        ))
        results = await mgr.fire(HookEvent.USER_MESSAGE, context={"text": "hi"})
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].output == "done"
        handler.assert_called_once_with({"text": "hi"})

    async def test_fire_command_hook(self):
        mgr = HookManager()
        mgr.register(HookDefinition(
            event=HookEvent.POST_COMMIT, name="notify", command="echo ok",
        ))

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"ok\n", None)
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            results = await mgr.fire(HookEvent.POST_COMMIT)

        assert len(results) == 1
        assert results[0].success is True
        assert "ok" in results[0].output

    async def test_fire_command_failure(self):
        mgr = HookManager()
        mgr.register(HookDefinition(
            event=HookEvent.PRE_COMMIT, name="test", command="pytest", blocking=True,
        ))

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"FAILED", None)
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            results = await mgr.fire(HookEvent.PRE_COMMIT)

        assert results[0].success is False
        assert results[0].blocked is True

    async def test_fire_blocking_stops_chain(self):
        mgr = HookManager()
        mgr.register(HookDefinition(
            event=HookEvent.PRE_COMMIT, name="fail-first",
            command="false", blocking=True,
        ))
        mgr.register(HookDefinition(
            event=HookEvent.PRE_COMMIT, name="never-runs",
            command="echo yes",
        ))

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", None)
        mock_proc.returncode = 1

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            results = await mgr.fire(HookEvent.PRE_COMMIT)

        # Only the first hook should have run (blocking failure stops chain)
        assert len(results) == 1
        assert results[0].blocked is True

    async def test_fire_command_timeout(self):
        import asyncio as _asyncio

        mgr = HookManager()
        mgr.register(HookDefinition(
            event=HookEvent.POST_EDIT, name="slow", command="sleep 999", timeout=1,
        ))

        mock_proc = AsyncMock()
        mock_proc.communicate.side_effect = _asyncio.TimeoutError()
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_shell", return_value=mock_proc):
            results = await mgr.fire(HookEvent.POST_EDIT)

        assert results[0].success is False
        assert "timed out" in results[0].output

    async def test_fire_handler_exception(self):
        mgr = HookManager()

        def bad_handler(ctx):
            raise ValueError("boom")

        mgr.register(HookDefinition(
            event=HookEvent.SESSION_END, name="bad", handler=bad_handler,
        ))
        results = await mgr.fire(HookEvent.SESSION_END)
        assert results[0].success is False
        assert "boom" in results[0].output


# ---------------------------------------------------------------------------
# is_blocked
# ---------------------------------------------------------------------------

class TestIsBlocked:
    def test_not_blocked(self):
        mgr = HookManager()
        results = [HookResult(hook_name="a", event=HookEvent.PRE_COMMIT, success=True)]
        assert mgr.is_blocked(results) is False

    def test_blocked(self):
        mgr = HookManager()
        results = [HookResult(
            hook_name="a", event=HookEvent.PRE_COMMIT, success=False, blocked=True,
        )]
        assert mgr.is_blocked(results) is True


# ---------------------------------------------------------------------------
# load_from_file
# ---------------------------------------------------------------------------

class TestLoadFromFile:
    def test_loads_valid_file(self, tmp_path):
        config = [
            {"event": "post_edit", "name": "lint", "command": "ruff check ."},
            {"event": "pre_commit", "name": "test", "command": "pytest", "blocking": True},
        ]
        path = tmp_path / "hooks.json"
        path.write_text(json.dumps(config))

        mgr = HookManager()
        mgr.load_from_file(path)
        hooks = mgr.list_hooks()
        assert len(hooks) == 2
        assert hooks[0]["name"] == "lint"
        assert hooks[1]["blocking"] is True

    def test_missing_file_is_noop(self, tmp_path):
        mgr = HookManager()
        mgr.load_from_file(tmp_path / "nonexistent.json")
        assert mgr.list_hooks() == []

    def test_invalid_json_logs_warning(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json{")
        mgr = HookManager()
        mgr.load_from_file(path)  # should not raise
        assert mgr.list_hooks() == []
