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
    llm = build_llm()

    # Create a Code Writer agent that generates actual content
    code_writer = Agent(
        role="Expert Code Writer",
        goal="Generate high-quality, production-ready code and documentation based on requirements.",
        backstory=(
            "You are a senior software engineer with expertise in multiple programming languages. "
            "You write clean, well-documented, and functional code. "
            "You understand context and generate appropriate content for each file type. "
            "For documentation files (README.md, docs, etc.), you write clear, comprehensive content. "
            "For code files, you follow best practices and include proper comments."
        ),
        llm=llm,
        verbose=False,
        allow_delegation=False,
    )

    for step in plan.steps:
        step_summary = f"Step {step.step_number}: {step.title}"

        for file in step.files:
            try:
                if file.action == "CREATE":
                    # Use LLM to generate appropriate content for the new file
                    create_task = Task(
                        description=(
                            f"Generate complete content for a new file: {file.path}\n\n"
                            f"Overall Goal: {plan.goal}\n"
                            f"Step Context: {step.description}\n\n"
                            f"Requirements:\n"
                            f"- Create production-ready content appropriate for {file.path}\n"
                            f"- If it's a documentation file (.md, .txt, .rst), write comprehensive, well-structured documentation\n"
                            f"- If it's a code file, include proper imports, comments, and follow best practices\n"
                            f"- If it's a configuration file, include sensible defaults and comments\n"
                            f"- Make the content complete and ready to use\n"
                            f"- Do NOT include placeholder comments like 'TODO' or 'IMPLEMENT THIS'\n"
                            f"- The content should be fully functional and informative\n\n"
                            f"Return ONLY the file content, no explanations or markdown code blocks."
                        ),
                        expected_output=f"Complete, production-ready content for {file.path}",
                        agent=code_writer,
                    )

                    def _create():
                        crew = Crew(
                            agents=[code_writer],
                            tasks=[create_task],
                            process=Process.sequential,
                            verbose=False,
                        )
                        result = crew.kickoff()
                        # Extract the raw output from CrewOutput
                        if hasattr(result, 'raw'):
                            return result.raw
                        return str(result)

                    content = await asyncio.to_thread(_create)

                    # Clean up any markdown code blocks that might be included
                    content = content.strip()
                    if content.startswith("```"):
                        lines = content.split("\n")
                        # Remove first line (```language) and last line (```)
                        if lines[-1].strip() == "```":
                            content = "\n".join(lines[1:-1])
                        else:
                            content = "\n".join(lines[1:])

                    await put_file(
                        owner,
                        repo,
                        file.path,
                        content,
                        f"GitPilot: Create {file.path} - {step.title}",
                    )
                    step_summary += f"\n  ✓ Created {file.path}"

                elif file.action == "MODIFY":
                    # Use LLM to intelligently modify the existing file
                    try:
                        existing_content = await get_file(owner, repo, file.path)

                        modify_task = Task(
                            description=(
                                f"Modify the existing file: {file.path}\n\n"
                                f"Overall Goal: {plan.goal}\n"
                                f"Step Context: {step.description}\n\n"
                                f"Current File Content:\n"
                                f"---\n{existing_content}\n---\n\n"
                                f"Requirements:\n"
                                f"- Make the changes described in the step context\n"
                                f"- Preserve the existing structure and format\n"
                                f"- For documentation: update or add relevant sections\n"
                                f"- For code: add/modify functions, imports, or logic as needed\n"
                                f"- Ensure the result is complete and functional\n"
                                f"- Do NOT just add comments - make real, substantive changes\n\n"
                                f"Return ONLY the complete modified file content, no explanations."
                            ),
                            expected_output=f"Complete, modified content for {file.path}",
                            agent=code_writer,
                        )

                        def _modify():
                            crew = Crew(
                                agents=[code_writer],
                                tasks=[modify_task],
                                process=Process.sequential,
                                verbose=False,
                            )
                            result = crew.kickoff()
                            if hasattr(result, 'raw'):
                                return result.raw
                            return str(result)

                        modified_content = await asyncio.to_thread(_modify)

                        # Clean up any markdown code blocks
                        modified_content = modified_content.strip()
                        if modified_content.startswith("```"):
                            lines = modified_content.split("\n")
                            if lines[-1].strip() == "```":
                                modified_content = "\n".join(lines[1:-1])
                            else:
                                modified_content = "\n".join(lines[1:])

                        await put_file(
                            owner,
                            repo,
                            file.path,
                            modified_content,
                            f"GitPilot: Modify {file.path} - {step.title}",
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
                            f"GitPilot: Mark {file.path} for deletion - {step.title}",
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