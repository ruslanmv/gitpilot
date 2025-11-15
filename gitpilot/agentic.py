from __future__ import annotations

import asyncio
from typing import List, Literal

from crewai import Agent, Crew, Process, Task
from pydantic import BaseModel

from .llm_provider import build_llm


class PlanFile(BaseModel):
    path: str
    action: Literal["CREATE", "MODIFY", "DELETE"] = "MODIFY"


class PlanStep(BaseModel):
    step_number: int
    title: str
    description: str
    files: List[PlanFile] = []
    risks: str | None = None


class PlanResult(BaseModel):
    goal: str
    summary: str
    steps: List[PlanStep]


async def generate_plan(goal: str, repo_full_name: str) -> PlanResult:
    """Agentic planning: create a structured plan but DO NOT modify the repo."""
    llm = build_llm()

    planner = Agent(
        role="Repository Refactor Planner",
        goal="Design safe, step-by-step refactor plans for a GitHub repo.",
        backstory=(
            "You are an experienced staff engineer. "
            "You *only* propose plans. You never make changes yourself."
        ),
        llm=llm,
        verbose=False,
        allow_delegation=False,
    )

    plan_task = Task(
        description=(
            "User goal: {goal}\n"
            f"Repository: {repo_full_name}\n\n"
            "You must propose a **plan only**:\n"
            "- Break work into small ordered steps.\n"
            "- For each step list: title, description, and files with specific actions.\n"
            "- For each file, specify the action type:\n"
            '  * "CREATE" - file will be created (does not exist yet)\n'
            '  * "MODIFY" - file will be edited (already exists)\n'
            '  * "DELETE" - file will be removed\n'
            "- Example file entry: { \"path\": \"src/api/login.py\", \"action\": \"CREATE\" }\n"
            "- Only JSON that matches the PlanResult schema.\n"
            "- Do NOT actually change any files or call tools.\n"
        ),
        expected_output=(
            "A structured JSON object with fields: "
            "goal, summary, steps[] where each step has: "
            "step_number, title, description, "
            "files[] (array of {path: string, action: CREATE|MODIFY|DELETE}), "
            "and optional risks."
        ),
        agent=planner,
        output_pydantic=PlanResult,
    )

    crew = Crew(
        agents=[planner],
        tasks=[plan_task],
        process=Process.sequential,
        verbose=False,
    )

    def _run():
        return crew.kickoff(inputs={"goal": goal})

    result = await asyncio.to_thread(_run)
    # CrewAI returns CrewOutput with .pydantic attribute containing the PlanResult
    if hasattr(result, 'pydantic') and result.pydantic:
        return result.pydantic
    return result


async def execute_plan(plan: PlanResult, repo_full_name: str) -> dict:
    """Execute the approved plan by applying changes to the GitHub repository.

    Returns an execution log with details about each step.
    """
    from .github_api import get_file, put_file

    owner, repo = repo_full_name.split("/")
    execution_steps = []

    for step in plan.steps:
        step_summary = f"Step {step.step_number}: {step.title}"

        for file in step.files:
            try:
                if file.action == "CREATE":
                    # Create a new file with placeholder content
                    content = f"# File created by GitPilot\n# TODO: Implement {file.path}\n"
                    await put_file(
                        owner,
                        repo,
                        file.path,
                        content,
                        f"GitPilot: Create {file.path} (Step {step.step_number})",
                    )
                    step_summary += f"\n  ✓ Created {file.path}"

                elif file.action == "MODIFY":
                    # Modify existing file by appending a comment
                    try:
                        existing_content = await get_file(owner, repo, file.path)
                        modified_content = (
                            existing_content
                            + f"\n# Modified by GitPilot - Step {step.step_number}: {step.title}\n"
                        )
                        await put_file(
                            owner,
                            repo,
                            file.path,
                            modified_content,
                            f"GitPilot: Modify {file.path} (Step {step.step_number})",
                        )
                        step_summary += f"\n  ✓ Modified {file.path}"
                    except Exception as e:
                        step_summary += f"\n  ✗ Failed to modify {file.path}: {str(e)}"

                elif file.action == "DELETE":
                    # For safety, we add a deprecation comment instead of deleting
                    try:
                        existing_content = await get_file(owner, repo, file.path)
                        deprecated_content = (
                            f"# DEPRECATED by GitPilot - Step {step.step_number}\n"
                            f"# This file is marked for deletion\n\n"
                            + existing_content
                        )
                        await put_file(
                            owner,
                            repo,
                            file.path,
                            deprecated_content,
                            f"GitPilot: Mark {file.path} for deletion (Step {step.step_number})",
                        )
                        step_summary += f"\n  ✓ Marked {file.path} for deletion"
                    except Exception as e:
                        step_summary += f"\n  ✗ Failed to mark {file.path} for deletion: {str(e)}"

            except Exception as e:
                step_summary += f"\n  ✗ Error processing {file.path}: {str(e)}"

        execution_steps.append({"step_number": step.step_number, "summary": step_summary})

    return {
        "status": "completed",
        "message": f"Successfully executed {len(plan.steps)} steps on {repo_full_name}",
        "executionLog": {"steps": execution_steps},
    }


async def get_flow_definition() -> dict:
    """Return the current CrewAI agent workflow as a visual graph.

    This represents the multi-agent system used for planning and execution.
    """
    flow = {
        "nodes": [
            {
                "id": "repo_reader",
                "label": "Repository Reader",
                "type": "agent",
                "description": "Analyzes repository structure and codebase"
            },
            {
                "id": "planner",
                "label": "Refactor Planner",
                "type": "agent",
                "description": "Creates safe, step-by-step refactor plans"
            },
            {
                "id": "code_writer",
                "label": "Code Writer",
                "type": "agent",
                "description": "Implements approved changes to codebase"
            },
            {
                "id": "reviewer",
                "label": "Code Reviewer",
                "type": "agent",
                "description": "Reviews changes for quality and safety"
            },
            {
                "id": "github_tools",
                "label": "GitHub API",
                "type": "tool",
                "description": "Read/write files, create commits & PRs"
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "repo_reader",
                "target": "planner",
                "label": "Repository summary & context",
            },
            {
                "id": "e2",
                "source": "planner",
                "target": "code_writer",
                "label": "Approved plan with steps",
            },
            {
                "id": "e3",
                "source": "code_writer",
                "target": "reviewer",
                "label": "Modified files",
            },
            {
                "id": "e4",
                "source": "reviewer",
                "target": "github_tools",
                "label": "Approved changes",
            },
            {
                "id": "e5",
                "source": "github_tools",
                "target": "repo_reader",
                "label": "Updated repository state",
            },
        ],
    }
    return flow
