"""MatrixLab sandbox client tests (offline)."""

from __future__ import annotations

from gitpilot.sandbox_providers.base import SandboxResult
from gitpilot.sandbox_providers.matrixlab_client import MatrixLabClient


def test_health_false_when_unreachable():
    # Point at a closed port so the request fails fast.
    client = MatrixLabClient(base_url="http://127.0.0.1:9", token="t")
    assert client.health() is False


def test_default_url():
    client = MatrixLabClient(base_url=None, token=None)
    assert client.base_url == "http://localhost:8765"


def test_parse_run_response_shape():
    raw = {
        "run_id": "r-1",
        "status": "passed",
        "exit_code": 0,
        "stdout": "ok",
        "stderr": "",
        "duration_ms": 42,
        "artifacts": [{"name": "coverage.xml", "url": "https://x/c.xml"}],
    }
    result = SandboxResult.from_response(raw)
    assert result.run_id == "r-1"
    assert result.passed is True
    assert result.exit_code == 0
    assert result.duration_ms == 42
    assert result.artifacts[0]["name"] == "coverage.xml"


def test_validate_patch_error_when_unreachable():
    client = MatrixLabClient(base_url="http://127.0.0.1:9", token="t")
    result = client.validate_patch(
        client_id="c",
        workspace_id="w",
        repo_url="https://github.com/acme/app",
        branch="gitpilot/x",
        profile="default",
        patch="--- a\n+++ b\n",
    )
    assert result.status == "error"
    assert result.passed is False


def test_payload_filtering():
    payload = MatrixLabClient._build_payload(
        {"client_id": "c", "bogus": "x", "branch": "b", "patch": "p", "empty": None}
    )
    assert "client_id" in payload
    assert "bogus" not in payload
    assert "empty" not in payload
    assert payload["branch"] == "b"
