"""The planner's JSON vocabulary collides with the ReAct `Action:` keyword.

The plan schema teaches the model to write CREATE / MODIFY / DELETE / READ /
INDEX / EXECUTE. Mid-plan, a small model reliably emits those same words as tool
calls:

    Action: READ
    Action Input: {"file_path": "new_file.py"}

The arguments are right; only the name is wrong. CrewAI answers "Action 'READ'
don't exist", and the model burns two or three turns rediscovering the canonical
name — sometimes giving up and returning a plan whose only step is the read it
never managed to perform.

Rejecting a semantically correct call over a naming detail is the defect. These
tests pin the two layers that fix it: the collision resolves, and the prompt says
plainly which vocabulary is which.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gitpilot import agent_tools

ROOT = Path(__file__).resolve().parents[1]

#: Every word the planner is taught to write as an "action" value.
PLAN_ACTIONS = ("CREATE", "MODIFY", "DELETE", "READ", "INDEX", "EXECUTE")


@pytest.fixture()
def fake_repo(monkeypatch):
    """Capture what the tools ask for, without touching a network."""
    seen: list[str] = []

    def fetch(path):
        seen.append(path)
        return "print('hi')\n"

    monkeypatch.setattr(agent_tools, "_fetch_file_content", fetch)
    return seen


def _tool_names() -> set[str]:
    return {t.name for t in agent_tools.PLANNER_TOOLS}


# ── The collision resolves ──────────────────────────────────────────────────

def test_the_read_action_is_a_usable_tool_name(fake_repo):
    """`Action: READ` is what the model actually emits, so it has to work."""
    assert "READ" in _tool_names()

    alias = next(t for t in agent_tools.PLANNER_TOOLS if t.name == "READ")
    output = alias.run(file_path="new_file.py")
    assert "new_file.py" in output
    assert fake_repo == ["new_file.py"]


def test_the_alias_is_the_same_read_not_a_second_implementation(fake_repo):
    """Two code paths would drift; the alias must delegate, not duplicate."""
    canonical = agent_tools.read_file.run(file_path="README.md")
    alias = agent_tools.read_file_alias.run(file_path="README.md")
    assert canonical == alias
    assert fake_repo == ["README.md", "README.md"]


def test_the_canonical_tools_still_come_first():
    """Instructions name the canonical tools; aliases only catch the mistake."""
    names = [t.name for t in agent_tools.PLANNER_TOOLS]
    assert names.index("Read file content") < names.index("READ")
    assert names.index("List all files in repository") < names.index("LIST")


def test_only_the_planner_gets_the_aliases():
    """Every other agent keeps the deliberately small explorer surface.

    The planner is the only agent taught the JSON action vocabulary, so it is
    the only one that confuses those words for tool names. Widening the shared
    set would add prompt noise for agents that never make the mistake.
    """
    shared = {t.name for t in agent_tools.REPOSITORY_TOOLS}
    assert "READ" not in shared and "LIST" not in shared
    assert shared < _tool_names()


def test_the_planner_agent_is_wired_to_the_alias_surface():
    source = (ROOT / "gitpilot" / "agentic.py").read_text(encoding="utf-8")
    planner = source[source.index('role="Repository Refactor Planner"'):]
    assert 'tools=_tools()["PLANNER_TOOLS"]' in planner[:600]


def test_the_alias_reports_a_read_failure_like_the_canonical_tool(monkeypatch):
    """A missing file must not look different depending on which name was used."""
    def boom(_path):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(agent_tools, "_fetch_file_content", boom)
    canonical = agent_tools.read_file.run(file_path="gone.py")
    alias = agent_tools.read_file_alias.run(file_path="gone.py")
    assert canonical == alias
    assert "Error reading file gone.py" in alias


# ── The prompt says which vocabulary is which ───────────────────────────────

def test_the_planner_prompt_separates_json_values_from_tools():
    """Belt and braces: the alias makes the mistake harmless, this makes it rarer.

    The plan prompt has a hard character budget (small models degrade on long
    prompts), so this line has to earn its space — one sentence that both says
    where the action words belong and names the tool to use instead.
    """
    prompts = (ROOT / "gitpilot" / "agent_prompts.py").read_text(encoding="utf-8")
    assert "action words go INSIDE the JSON" in prompts
    assert '"Read file content"' in prompts


def test_every_plan_action_is_still_documented():
    """The disambiguation must not quietly drop an action from the schema."""
    prompts = (ROOT / "gitpilot" / "agent_prompts.py").read_text(encoding="utf-8")
    for action in PLAN_ACTIONS:
        assert action in prompts, f"{action} vanished from the plan vocabulary"
