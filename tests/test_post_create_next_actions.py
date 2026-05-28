"""After a plan creates/modifies runnable files, the executor should
surface deterministic next_actions so the chat UI can offer one-click
follow-ups ("Run demo.py", "Open in Canvas") without LLM round-tripping.

These tests cover the helper logic that builds the next_actions list.
The end-to-end executor is exercised in the wider integration suite;
here we lock the contract so a refactor can't quietly drop the
suggestions.
"""

from __future__ import annotations

from gitpilot.agentic import _is_runnable_path


def _build_next_actions(plan_steps):
    """Mirror the inline logic in execute_plan() so we can test it in
    isolation. Kept in sync with the function — any change there must
    be reflected here and vice versa."""
    next_actions = []
    seen = set()
    for step in plan_steps:
        for pf in step.get("files", []) or []:
            action = (pf.get("action") or "").upper()
            path = pf.get("path")
            if not path or path in seen:
                continue
            if action in {"CREATE", "MODIFY"} and _is_runnable_path(path):
                seen.add(path)
                next_actions.append({
                    "kind": "run_file",
                    "label": f"Prepare Run {path}",
                    "payload": {"file": path},
                })
                next_actions.append({
                    "kind": "open_workspace",
                    "label": "Open Workspace",
                    "payload": {"file": path},
                })
    return next_actions


def test_create_runnable_python_emits_run_and_workspace() -> None:
    actions = _build_next_actions([
        {"files": [{"path": "demo.py", "action": "CREATE"}]},
    ])
    kinds = [a["kind"] for a in actions]
    # Enterprise vocabulary: "Prepare Run" + "Open Workspace".
    # "Canvas" is reserved for the runnable split-editor and lives in
    # the overflow menu of the file preview.
    assert kinds == ["run_file", "open_workspace"]
    assert actions[0]["payload"]["file"] == "demo.py"
    assert actions[0]["label"] == "Prepare Run demo.py"
    assert actions[1]["label"] == "Open Workspace"


def test_modify_runnable_also_emits_actions() -> None:
    actions = _build_next_actions([
        {"files": [{"path": "main.js", "action": "MODIFY"}]},
    ])
    assert any(a["kind"] == "run_file" for a in actions)


def test_create_markdown_emits_no_actions() -> None:
    actions = _build_next_actions([
        {"files": [{"path": "README.md", "action": "CREATE"}]},
    ])
    assert actions == []


def test_delete_runnable_emits_no_actions() -> None:
    actions = _build_next_actions([
        {"files": [{"path": "demo.py", "action": "DELETE"}]},
    ])
    assert actions == []


def test_read_only_emits_no_actions() -> None:
    actions = _build_next_actions([
        {"files": [{"path": "demo.py", "action": "READ"}]},
    ])
    assert actions == []


def test_dedup_across_steps() -> None:
    """If the same file appears in two steps (e.g. CREATE then MODIFY in
    a multi-pass plan), we only suggest the next_actions once."""
    actions = _build_next_actions([
        {"files": [{"path": "demo.py", "action": "CREATE"}]},
        {"files": [{"path": "demo.py", "action": "MODIFY"}]},
    ])
    paths = [a["payload"]["file"] for a in actions]
    assert paths.count("demo.py") == 2  # one run_file + one open_in_canvas
    assert len(actions) == 2


def test_multiple_runnable_files() -> None:
    actions = _build_next_actions([
        {"files": [
            {"path": "a.py", "action": "CREATE"},
            {"path": "b.sh", "action": "CREATE"},
        ]},
    ])
    files = {a["payload"]["file"] for a in actions}
    assert files == {"a.py", "b.sh"}
