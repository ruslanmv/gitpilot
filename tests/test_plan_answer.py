"""A question on the plan path gets an answer, not the plan's intent.

Logged case: "what we have in the current repo" on a repository with one
README.md. The planner returned a single READ step and the summary "Inspect
the single existing file in the repository to report its contents.", which the
chat showed as the reply; README.md was never read.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from gitpilot import _api_app as api_module
from gitpilot import plan_answer, scm
from gitpilot.agentic import PlanFile, PlanResult, PlanStep

README = "# Course notes\n\nLecture notes and exercises for a course.\n"
INTENT = "Inspect the single existing file in the repository to report its contents."


def _read_plan(*paths: str) -> PlanResult:
    return PlanResult(goal="what we have in the current repo", summary=INTENT, steps=[
        PlanStep(step_number=1, title="Read", description="Read the files",
                 files=[PlanFile(path=p, action="READ") for p in paths]),
    ])


async def _fetch(owner, repo, path, token=None, ref=None):
    if path == "README.md":
        return README
    raise FileNotFoundError(path)


@pytest.mark.parametrize("action", ["CREATE", "MODIFY", "DELETE", "EXECUTE", "INDEX"])
def test_plans_that_do_something_are_left_alone(action):
    plan = PlanResult(goal="g", summary="s", steps=[PlanStep(
        step_number=1, title="t", description="d",
        files=[PlanFile(path="README.md", action="READ"), PlanFile(path="a.py", action=action)])])
    assert plan_answer.read_only_paths(plan) is None
    assert plan_answer.read_only_paths({"steps": [], "summary": "x"}) is None
    assert plan_answer.read_only_paths({"steps": [{"files": []}], "execution_plan": {"x": 1}}) is None


async def test_a_read_only_plan_is_answered_from_the_files():
    seen = {}

    async def ask(messages):
        seen["messages"] = messages
        return "This repository holds **README.md**: lecture notes for a course."

    plan = _read_plan("README.md")
    done = await plan_answer.answer_read_only_plan(
        plan, goal="what we have in the current repo", owner="acme", repo="notes", token=None,
        ref="master", repo_paths=["README.md"], fetch=_fetch, ask=ask)
    assert done and plan.summary.startswith("This repository holds")
    prompt = seen["messages"][1]["content"]
    assert "what we have in the current repo" in prompt
    assert "----- README.md -----" in prompt and "Lecture notes" in prompt
    assert "Files in the repository (1):" in prompt


async def test_without_a_model_the_answer_comes_from_the_files():
    async def down(_messages):
        raise RuntimeError("provider unreachable")

    plan = {"goal": "g", "summary": INTENT, "steps": [{"files": [{"path": "README.md", "action": "READ"}]}]}
    await plan_answer.answer_read_only_plan(plan, goal="g", owner="acme", repo="notes", token=None,
                                            ref="master", repo_paths=["README.md"], fetch=_fetch, ask=down)
    assert plan["summary"] != INTENT
    assert "acme/notes** contains 1 file" in plan["summary"]
    assert "> Lecture notes" in plan["summary"]


async def test_paths_the_repository_does_not_have_are_not_read():
    fetched = []

    async def fetch(owner, repo, path, token=None, ref=None):
        fetched.append(path)
        return README

    async def ask(_messages):
        return "answer"

    plan = _read_plan("does/not/exist.py", "../secrets.env")
    await plan_answer.answer_read_only_plan(plan, goal="g", owner="o", repo="r", token=None, ref="main",
                                            repo_paths=["README.md", "src/a.py"], fetch=fetch, ask=ask)
    assert fetched == ["README.md"]                       # falls back to the README, never the invented paths


async def test_reading_is_capped():
    big = "x" * 50_000
    sizes = {}

    async def fetch(owner, repo, path, token=None, ref=None):
        return big

    async def ask(messages):
        sizes["prompt"] = len(messages[1]["content"])
        return "ok"

    paths = [f"f{i}.md" for i in range(10)]
    await plan_answer.answer_read_only_plan(_read_plan(*paths), goal="g", owner="o", repo="r", token=None,
                                            ref="main", repo_paths=paths, fetch=fetch, ask=ask)
    assert sizes["prompt"] < plan_answer.MAX_TOTAL_CHARS + 2_000


# --- the /api/chat/plan route -------------------------------------------------------

@pytest.fixture
def route(monkeypatch):
    @contextmanager
    def _noop_ctx(*_a, **_kw):
        yield

    async def tree(owner, repo, token=None, ref="HEAD"):
        return [{"path": "README.md", "type": "blob"}]

    monkeypatch.setattr(api_module, "execution_context", _noop_ctx)
    monkeypatch.setattr(api_module, "_is_lite_mode_active", lambda: False)
    monkeypatch.setattr(scm, "get_repo_tree", tree)
    monkeypatch.setattr(scm, "get_file", _fetch)
    return TestClient(api_module.app)


def test_the_route_returns_the_answer_as_the_summary(route, monkeypatch):
    async def planner(goal, repo_full_name, token=None, branch_name=None, **_kw):
        return _read_plan("README.md")

    async def ask(messages):
        assert "Lecture notes" in messages[1]["content"]
        return "The repository has one file, README.md, with lecture notes for a course."

    monkeypatch.setattr(api_module, "generate_plan", planner)
    monkeypatch.setattr(plan_answer, "_ask_configured_model", ask)
    resp = route.post("/api/chat/plan", json={"goal": "what we have in the current repo",
                                              "repo_owner": "acme", "repo_name": "notes", "branch_name": "master"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"].startswith("The repository has one file")
    assert body["steps"][0]["files"][0] == {"path": "README.md", "action": "READ"}


def test_the_route_leaves_change_plans_alone(route, monkeypatch):
    async def planner(goal, repo_full_name, token=None, branch_name=None, **_kw):
        return PlanResult(goal=goal, summary="Add a CONTRIBUTING guide.", steps=[PlanStep(
            step_number=1, title="Create", description="d",
            files=[PlanFile(path="CONTRIBUTING.md", action="CREATE")])])

    async def ask(_messages):  # pragma: no cover - must not be called
        raise AssertionError("a change plan must not be answered")

    monkeypatch.setattr(api_module, "generate_plan", planner)
    monkeypatch.setattr(plan_answer, "_ask_configured_model", ask)
    resp = route.post("/api/chat/plan", json={"goal": "add a contributing guide",
                                              "repo_owner": "acme", "repo_name": "notes", "branch_name": "master"})
    assert resp.status_code == 200 and resp.json()["summary"] == "Add a CONTRIBUTING guide."
