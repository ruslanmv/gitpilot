"""Tests for the deterministic ExecutionPlan builder.

The contract: build_execution_plan_* must be pure (no I/O), return
``None`` rather than raise on invalid input, and surface safety
warnings that are stable across calls.
"""

from __future__ import annotations

import pytest

from gitpilot.sandbox_plan import (
    ExecutionPlan,
    SafetyWarning,
    analyze_safety,
    build_execution_plan_for_file,
    build_execution_plan_for_inline,
)


# ---------------------------------------------------------------------------
# Safety analyzer
# ---------------------------------------------------------------------------


def test_analyze_safety_flags_os_system() -> None:
    warns = analyze_safety("import os\nos.system('rm -rf /')\n", "python")
    labels = [w.label for w in warns]
    assert any("os.system" in label or "exec" in label for label in labels)


def test_analyze_safety_flags_network() -> None:
    warns = analyze_safety("import requests\nrequests.get('https://x')\n", "python")
    assert any("network" in w.label.lower() for w in warns)


def test_analyze_safety_flags_plt_show() -> None:
    warns = analyze_safety("import matplotlib.pyplot as plt\nplt.show()\n", "python")
    assert any("plt.show" in w.label for w in warns)


def test_analyze_safety_clean_code_returns_empty() -> None:
    assert analyze_safety("print(2 + 2)", "python") == []


def test_analyze_safety_orders_high_severity_first() -> None:
    code = (
        "import os\n"
        "import requests\n"            # medium
        "os.system('echo hi')\n"       # high
        "open('/tmp/x','w').write('hi')\n"  # low
    )
    warns = analyze_safety(code, "python")
    severities = [w.severity for w in warns]
    assert severities[0] == "high"
    # No "high" can appear after a "low"/"medium".
    rank = {"high": 0, "medium": 1, "low": 2}
    assert severities == sorted(severities, key=lambda s: rank[s])


def test_analyze_safety_empty_input() -> None:
    assert analyze_safety("", "python") == []
    assert analyze_safety(None, "python") == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# File-run plan
# ---------------------------------------------------------------------------


def test_file_plan_happy_path() -> None:
    plan = build_execution_plan_for_file(
        file="demo.py",
        repo_files=["demo.py", "README.md"],
    )
    assert plan is not None
    assert plan.file == "demo.py"
    assert plan.language == "python"
    assert plan.command == ["python", "demo.py"]
    assert plan.requires_approval is True
    assert any("Network" in c.label for c in plan.safety_checks)
    assert plan.inline_code is None


def test_file_plan_rejects_missing_file() -> None:
    assert build_execution_plan_for_file(
        file="ghost.py", repo_files=["demo.py"]
    ) is None


def test_file_plan_rejects_non_runnable_extension() -> None:
    assert build_execution_plan_for_file(
        file="README.md", repo_files=["README.md"]
    ) is None


def test_file_plan_with_reader_attaches_warnings() -> None:
    plan = build_execution_plan_for_file(
        file="risky.py",
        repo_files=["risky.py"],
        file_reader=lambda _: "import requests\nrequests.get('https://x')\n",
    )
    assert plan is not None
    assert any("network" in w.label.lower() for w in plan.safety_warnings)


def test_file_plan_handles_reader_exception_gracefully() -> None:
    def boom(_: str) -> str:
        raise OSError("disk on fire")

    plan = build_execution_plan_for_file(
        file="demo.py",
        repo_files=["demo.py"],
        file_reader=boom,
    )
    assert plan is not None
    assert plan.safety_warnings == []  # graceful degradation


def test_file_plan_resolves_bash_and_node() -> None:
    py = build_execution_plan_for_file(file="a.py", repo_files=["a.py"])
    js = build_execution_plan_for_file(file="a.js", repo_files=["a.js"])
    sh = build_execution_plan_for_file(file="a.sh", repo_files=["a.sh"])
    assert py and py.command[0] == "python"
    assert js and js.command[0] == "node"
    assert sh and sh.command[0] == "bash"


def test_file_plan_unique_plan_ids() -> None:
    a = build_execution_plan_for_file(file="x.py", repo_files=["x.py"])
    b = build_execution_plan_for_file(file="x.py", repo_files=["x.py"])
    assert a is not None and b is not None
    assert a.plan_id != b.plan_id


# ---------------------------------------------------------------------------
# Inline plan
# ---------------------------------------------------------------------------


def test_inline_plan_happy_path() -> None:
    plan = build_execution_plan_for_inline(
        code="print('hi')", language="python",
    )
    assert plan is not None
    assert plan.file is None
    assert plan.inline_code == "print('hi')"
    assert plan.language == "python"
    assert plan.command == ["python", "inline.py"]


def test_inline_plan_normalizes_language_aliases() -> None:
    for alias in ("py", "Python", "PYTHON3"):
        plan = build_execution_plan_for_inline(code="x=1", language=alias)
        assert plan is not None
        assert plan.language == "python"


def test_inline_plan_rejects_empty_code() -> None:
    assert build_execution_plan_for_inline(code="", language="python") is None
    assert build_execution_plan_for_inline(code="   \n\t  ", language="python") is None


def test_inline_plan_rejects_unknown_language() -> None:
    assert build_execution_plan_for_inline(code="puts 1", language="ruby") is None


def test_inline_plan_carries_parent_run_id() -> None:
    plan = build_execution_plan_for_inline(
        code="print(1)", language="py", parent_run_id="run_abc",
    )
    assert plan is not None
    assert plan.parent_run_id == "run_abc"


def test_inline_plan_display_filename_overrides_inline_name() -> None:
    """File-aware Canvas: when a real repo file is opened for editing,
    the plan's displayed command should read ``python demo.py`` instead
    of ``python inline.py``.  Pure cosmetic: execution itself still
    runs the buffer bytes via the inline path."""
    plan = build_execution_plan_for_inline(
        code="print('hi')",
        language="python",
        display_filename="src/demo.py",
    )
    assert plan is not None
    assert plan.command == ["python", "src/demo.py"]
    # Safety check copy should also reflect the file context.
    labels = " ".join(c.label for c in plan.safety_checks)
    assert "src/demo.py" in labels


def test_inline_plan_no_display_filename_falls_back_to_inline() -> None:
    plan = build_execution_plan_for_inline(code="print(1)", language="python")
    assert plan is not None
    assert plan.command == ["python", "inline.py"]


def test_inline_plan_empty_display_filename_falls_back_to_inline() -> None:
    plan = build_execution_plan_for_inline(
        code="print(1)", language="python", display_filename="  ",
    )
    assert plan is not None
    assert plan.command == ["python", "inline.py"]


def test_endpoint_inline_plan_uses_display_filename(client) -> None:
    res = client.post(
        "/api/sandbox/plan",
        json={
            "code": "print(1)", "language": "python",
            "source": "canvas",
            "display_filename": "demo.py",
        },
    )
    assert res.status_code == 200, res.text
    plan = res.json()["plan"]
    assert plan["command"] == ["python", "demo.py"]


def test_inline_plan_serializes_cleanly() -> None:
    plan = build_execution_plan_for_inline(
        code="import requests\nrequests.get('x')", language="python",
    )
    assert plan is not None
    d = plan.to_dict()
    assert isinstance(d["safety"]["warnings"], list)
    assert d["requires_approval"] is True
    assert d["command"] == ["python", "inline.py"]
    assert d["inline_code"].startswith("import requests")


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from gitpilot.sandbox_api import router as sandbox_router

    app = FastAPI()
    app.include_router(sandbox_router)
    return TestClient(app)


def test_endpoint_file_plan(client) -> None:
    res = client.post(
        "/api/sandbox/plan",
        json={"file": "demo.py", "repo_files": ["demo.py"], "source": "chat"},
    )
    assert res.status_code == 200, res.text
    plan = res.json()["plan"]
    assert plan["file"] == "demo.py"
    assert plan["command"] == ["python", "demo.py"]
    assert plan["requires_approval"] is True


def test_endpoint_inline_plan(client) -> None:
    res = client.post(
        "/api/sandbox/plan",
        json={"code": "print(1)", "language": "python", "source": "code_block"},
    )
    assert res.status_code == 200, res.text
    plan = res.json()["plan"]
    assert plan["inline_code"] == "print(1)"
    assert plan["source"] == "code_block"


def test_endpoint_rejects_missing_file(client) -> None:
    res = client.post(
        "/api/sandbox/plan",
        json={"file": "ghost.py", "repo_files": ["demo.py"]},
    )
    assert res.status_code == 400


def test_endpoint_rejects_empty_request(client) -> None:
    res = client.post("/api/sandbox/plan", json={})
    assert res.status_code == 400
