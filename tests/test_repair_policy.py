"""Fail-closed policy tests for the GitPilot repair flow."""

from __future__ import annotations

from gitpilot.repair.policy import (
    check_risk_policy,
    check_sandbox_policy,
    is_path_allowed,
    validate_changed_files,
)
from gitpilot.repair.schema import RepairRequest


def _req(allowed, forbidden=None, sandbox_required=False):
    data = {
        "client_id": "c",
        "workspace_id": "w",
        "task_id": "t",
        "repo_url": "https://github.com/acme/app",
        "allowed_paths": allowed,
        "sandbox": {"provider": "matrixlab", "required": sandbox_required},
    }
    if forbidden is not None:
        data["forbidden_paths"] = forbidden
    return RepairRequest.from_json(data)


def test_empty_allowed_paths_blocks():
    req = _req([])
    res = validate_changed_files(["tests/test_health.py"], req)
    assert res.allowed is False
    assert any("allowed_paths is empty" in v for v in res.violations)


def test_file_outside_allowed_blocks():
    req = _req(["tests/**"])
    res = validate_changed_files(["src/app.py"], req)
    assert res.allowed is False
    assert any("outside allowed_paths" in v for v in res.violations)


def test_forbidden_path_blocks():
    req = _req(["**"], forbidden=["secrets/**"])
    res = validate_changed_files(["secrets/key.pem"], req)
    assert res.allowed is False


def test_env_touch_blocks():
    req = _req(["**"])
    res = validate_changed_files([".env"], req)
    assert res.allowed is False
    assert any("secret" in v or ".env" in v for v in res.violations)


def test_secret_touch_blocks():
    req = _req(["**"])
    res = validate_changed_files(["config/db_secret.yaml"], req)
    assert res.allowed is False


def test_token_touch_blocks():
    req = _req(["**"])
    res = validate_changed_files(["app/auth_token.py"], req)
    assert res.allowed is False


def test_allowed_file_passes():
    req = _req(["tests/**"])
    res = validate_changed_files(["tests/test_health.py"], req)
    assert res.allowed is True
    assert res.violations == []


def test_is_path_allowed_fail_closed_empty():
    assert is_path_allowed("tests/test_health.py", [], []) is False


def test_is_path_allowed_basic():
    assert is_path_allowed("tests/test_health.py", ["tests/**"], []) is True
    assert is_path_allowed(".env", ["**"], []) is False


def test_sandbox_required_unavailable_blocks():
    req = _req(["tests/**"], sandbox_required=True)
    res = check_sandbox_policy(req, sandbox_healthy=False)
    assert res.allowed is False


def test_sandbox_required_available_ok():
    req = _req(["tests/**"], sandbox_required=True)
    res = check_sandbox_policy(req, sandbox_healthy=True)
    assert res.allowed is True


def test_high_risk_without_approval_blocks():
    res = check_risk_policy("high", human_approval=False)
    assert res.allowed is False


def test_high_risk_with_approval_ok():
    res = check_risk_policy("high", human_approval=True)
    assert res.allowed is True
