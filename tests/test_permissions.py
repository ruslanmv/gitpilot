"""Tests for gitpilot.permissions module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gitpilot.permissions import (
    Action,
    PermissionManager,
    PermissionMode,
    PermissionPolicy,
    READ_ONLY_ACTIONS,
    RISKY_ACTIONS,
)


# ---------------------------------------------------------------------------
# PermissionMode basics
# ---------------------------------------------------------------------------

class TestPermissionMode:
    def test_default_is_normal(self):
        policy = PermissionPolicy()
        assert policy.mode == PermissionMode.NORMAL

    def test_modes_are_strings(self):
        assert PermissionMode.NORMAL.value == "normal"
        assert PermissionMode.PLAN.value == "plan"
        assert PermissionMode.AUTO.value == "auto"


# ---------------------------------------------------------------------------
# PLAN mode — read-only
# ---------------------------------------------------------------------------

class TestPlanMode:
    def test_read_allowed(self):
        pm = PermissionManager(PermissionPolicy(mode=PermissionMode.PLAN))
        assert pm.check(Action.READ_FILE) is True

    def test_write_blocked(self):
        pm = PermissionManager(PermissionPolicy(mode=PermissionMode.PLAN))
        with pytest.raises(PermissionError, match="plan mode"):
            pm.check(Action.WRITE_FILE)

    def test_delete_blocked(self):
        pm = PermissionManager(PermissionPolicy(mode=PermissionMode.PLAN))
        with pytest.raises(PermissionError, match="plan mode"):
            pm.check(Action.DELETE_FILE)

    def test_command_blocked(self):
        pm = PermissionManager(PermissionPolicy(mode=PermissionMode.PLAN))
        with pytest.raises(PermissionError, match="plan mode"):
            pm.check(Action.RUN_COMMAND)

    def test_git_push_blocked(self):
        pm = PermissionManager(PermissionPolicy(mode=PermissionMode.PLAN))
        with pytest.raises(PermissionError, match="plan mode"):
            pm.check(Action.GIT_PUSH)

    def test_needs_confirmation_false(self):
        pm = PermissionManager(PermissionPolicy(mode=PermissionMode.PLAN))
        assert pm.needs_confirmation(Action.DELETE_FILE) is False


# ---------------------------------------------------------------------------
# AUTO mode — everything allowed, no confirmation
# ---------------------------------------------------------------------------

class TestAutoMode:
    def test_all_actions_allowed(self):
        pm = PermissionManager(PermissionPolicy(mode=PermissionMode.AUTO))
        for action in Action:
            assert pm.check(action) is True

    def test_no_confirmation_needed(self):
        pm = PermissionManager(PermissionPolicy(mode=PermissionMode.AUTO))
        for action in Action:
            assert pm.needs_confirmation(action) is False


# ---------------------------------------------------------------------------
# NORMAL mode — default behaviour
# ---------------------------------------------------------------------------

class TestNormalMode:
    def test_basic_actions_allowed(self):
        pm = PermissionManager()
        assert pm.check(Action.READ_FILE) is True
        assert pm.check(Action.WRITE_FILE) is True

    def test_risky_actions_need_confirmation(self):
        pm = PermissionManager()
        for action in RISKY_ACTIONS:
            assert pm.needs_confirmation(action) is True

    def test_safe_actions_no_confirmation(self):
        pm = PermissionManager()
        assert pm.needs_confirmation(Action.READ_FILE) is False
        assert pm.needs_confirmation(Action.WRITE_FILE) is False


# ---------------------------------------------------------------------------
# Path blocking
# ---------------------------------------------------------------------------

class TestPathBlocking:
    def test_env_file_blocked(self):
        pm = PermissionManager()
        with pytest.raises(PermissionError, match="blocked by policy"):
            pm.check(Action.READ_FILE, context={"path": ".env"})

    def test_pem_file_blocked(self):
        pm = PermissionManager()
        with pytest.raises(PermissionError, match="blocked by policy"):
            pm.check(Action.READ_FILE, context={"path": "server.pem"})

    def test_key_file_blocked(self):
        pm = PermissionManager()
        with pytest.raises(PermissionError, match="blocked by policy"):
            pm.check(Action.READ_FILE, context={"path": "private.key"})

    def test_credentials_file_blocked(self):
        pm = PermissionManager()
        with pytest.raises(PermissionError, match="blocked by policy"):
            pm.check(Action.READ_FILE, context={"path": "credentials.json"})

    def test_nested_env_file_blocked(self):
        pm = PermissionManager()
        with pytest.raises(PermissionError, match="blocked by policy"):
            pm.check(Action.READ_FILE, context={"path": "config/.env"})

    def test_safe_path_allowed(self):
        pm = PermissionManager()
        assert pm.check(Action.READ_FILE, context={"path": "src/main.py"}) is True

    def test_custom_blocked_paths(self):
        policy = PermissionPolicy(blocked_paths=["*.secret"])
        pm = PermissionManager(policy)
        with pytest.raises(PermissionError):
            pm.check(Action.READ_FILE, context={"path": "config.secret"})


# ---------------------------------------------------------------------------
# Allowed actions whitelist
# ---------------------------------------------------------------------------

class TestAllowedActions:
    def test_whitelist_blocks_unlisted(self):
        policy = PermissionPolicy(allowed_actions={Action.READ_FILE})
        pm = PermissionManager(policy)
        with pytest.raises(PermissionError, match="not in allowed actions"):
            pm.check(Action.WRITE_FILE)

    def test_whitelist_allows_listed(self):
        policy = PermissionPolicy(allowed_actions={Action.READ_FILE, Action.WRITE_FILE})
        pm = PermissionManager(policy)
        assert pm.check(Action.READ_FILE) is True
        assert pm.check(Action.WRITE_FILE) is True


# ---------------------------------------------------------------------------
# to_dict / load_from_file
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_to_dict(self):
        pm = PermissionManager()
        d = pm.to_dict()
        assert d["mode"] == "normal"
        assert isinstance(d["blocked_paths"], list)

    def test_load_from_file(self, tmp_path):
        config = {
            "mode": "auto",
            "blocked_paths": ["*.secret"],
            "allowed_commands": ["ls", "cat"],
        }
        path = tmp_path / "permissions.json"
        path.write_text(json.dumps(config))

        pm = PermissionManager()
        pm.load_from_file(path)
        assert pm.policy.mode == PermissionMode.AUTO
        assert pm.policy.blocked_paths == ["*.secret"]
        assert pm.policy.allowed_commands == ["ls", "cat"]

    def test_load_missing_file_is_noop(self, tmp_path):
        pm = PermissionManager()
        pm.load_from_file(tmp_path / "nope.json")
        assert pm.policy.mode == PermissionMode.NORMAL  # unchanged

    def test_load_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{bad json")
        pm = PermissionManager()
        pm.load_from_file(path)  # should not raise
        assert pm.policy.mode == PermissionMode.NORMAL
