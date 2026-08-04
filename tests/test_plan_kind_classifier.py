"""A sandbox run must not be reported as an empty plan.

"run hello_world.py" is answered by the deterministic short-circuit in
:mod:`gitpilot.agentic`: no LLM, a single step whose file action is EXECUTE,
and an ``execution_plan`` built in pure Python for the green approval card.

The chat UI classified that response as *empty* and printed "The model
returned an empty plan. Try rephrasing more concretely, or pick a stronger
model" — advice that could not possibly help, because no model was involved.
Its actionable-action list was CREATE / MODIFY / DELETE, and EXECUTE was not
on it.

These tests run the shipped classifier (``frontend/utils/planKind.js``) under
node, and feed it the real backend payload, so the two sides of the boundary
are checked against each other rather than against a fixture someone typed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gitpilot.agentic import try_execute_short_circuit

ROOT = Path(__file__).resolve().parents[1]
CLASSIFIER = ROOT / "frontend" / "utils" / "planKind.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to run the shipped classifier"
)


def classify(payload: dict) -> str:
    """Classify one /api/chat/plan response with the real frontend module."""
    script = (
        f"import {{ classifyPlan }} from {json.dumps(CLASSIFIER.as_uri())};\n"
        f"process.stdout.write(classifyPlan(JSON.parse(process.argv[1])));\n"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, "--", json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


# ── The bug, against the real backend payload ───────────────────────────────

def test_a_real_run_file_plan_is_executable():
    """The exact request that failed: "run hello_world.py"."""
    plan = try_execute_short_circuit(
        goal="run hello_world.py",
        intent="execute",
        target_files=["hello_world.py"],
        repo_files=["hello_world.py", "README.md"],
    )
    assert plan is not None
    payload = plan.model_dump()

    # The payload really is the shape the old classifier choked on: nothing
    # it recognised as actionable anywhere in `steps`.
    actions = [f["action"] for s in payload["steps"] for f in s["files"]]
    assert actions == ["EXECUTE"]

    assert classify(payload) == "executable"


def test_an_execute_plan_without_a_sandbox_card_is_still_executable():
    """The ExecutionPlan builder can decline a file; the step is still real."""
    payload = {
        "goal": "run tool.sh",
        "summary": "Run tool.sh in the configured sandbox.",
        "steps": [{
            "step_number": 1,
            "title": "Run tool.sh",
            "description": "Execute through the active sandbox backend.",
            "files": [{"path": "tool.sh", "action": "EXECUTE"}],
        }],
        "execution_plan": None,
    }
    assert classify(payload) == "executable"


def test_an_execution_plan_alone_is_enough():
    """It is built in Python from a resolved path — it cannot be a hallucination."""
    payload = {
        "goal": "run demo.py",
        "summary": "Run demo.py.",
        "steps": [],
        "execution_plan": {"file": "demo.py", "command": ["python", "demo.py"]},
    }
    assert classify(payload) == "executable"


def test_a_reloaded_message_keeps_its_sandbox_card():
    """After a session reload the plan arrives nested under `plan`."""
    payload = {
        "plan": {
            "goal": "run demo.py",
            "summary": "Run demo.py.",
            "steps": [{"step_number": 1, "title": "Run demo.py", "description": "",
                       "files": [{"path": "demo.py", "action": "EXECUTE"}]}],
            "execution_plan": {"file": "demo.py", "command": ["python", "demo.py"]},
        },
    }
    assert classify(payload) == "executable"


# ── The other two kinds still behave ────────────────────────────────────────

def test_a_write_plan_is_still_executable():
    payload = {
        "goal": "add a greeter",
        "summary": "Create hello_world.py.",
        "steps": [{"step_number": 1, "title": "Create it", "description": "",
                   "files": [{"path": "hello_world.py", "action": "CREATE"}]}],
    }
    assert classify(payload) == "executable"


def test_a_read_only_plan_with_an_answer_is_informational():
    """The READs were the research; the summary is the reply."""
    payload = {
        "goal": "what does this project do?",
        "summary": "It is a CLI that syncs calendars.",
        "steps": [{"step_number": 1, "title": "Read README", "description": "",
                   "files": [{"path": "README.md", "action": "READ"}]}],
    }
    assert classify(payload) == "informational"


def test_a_read_only_plan_with_no_answer_is_empty():
    """No answer and nothing to run really is a failure — say so."""
    payload = {
        "goal": "what does this project do?",
        "summary": "Here is the proposed plan for your request.",
        "steps": [{"step_number": 1, "title": "Read README", "description": "",
                   "files": [{"path": "README.md", "action": "READ"}]}],
    }
    assert classify(payload) == "empty"


def test_a_plan_with_no_steps_at_all_is_empty():
    assert classify({"goal": "x", "summary": "", "steps": []}) == "empty"


# ── The component uses the module, rather than a second copy of the rule ────

def test_the_chat_panel_delegates_to_the_shared_classifier():
    panel = (ROOT / "frontend" / "components" / "ChatPanel.jsx").read_text(encoding="utf-8")
    assert 'from "../utils/planKind.js"' in panel
    assert "classifyPlan(data)" in panel
    # No second, drifting copy of the action list.
    assert '["CREATE", "MODIFY", "DELETE"]' not in panel
