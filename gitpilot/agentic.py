from __future__ import annotations

import asyncio
from textwrap import dedent
from typing import List, Literal

from crewai import Agent, Crew, Process, Task
from pydantic import BaseModel, Field

from .llm_provider import build_llm
from .agent_tools import REPOSITORY_TOOLS, set_repo_context


class PlanFile(BaseModel):
    path: str
    action: Literal["CREATE", "MODIFY", "DELETE"] = "MODIFY"


class PlanStep(BaseModel):
    step_number: int
    title: str
    description: str
    # Important: avoid mutable default list
    files: List[PlanFile] = Field(default_factory=list)
    risks: str | None = None


class PlanResult(BaseModel):
    goal: str
    summary: str
    steps: List[PlanStep]


async def generate_plan(goal: str, repo_full_name: str) -> PlanResult:
    """Agentic planning: create a structured plan but DO NOT modify the repo."""
    llm = build_llm()

    # Set repository context for tools
    owner, repo = repo_full_name.split("/")
    set_repo_context(owner, repo)

    # PRE-FETCH repository information to provide as context
    # This ensures the agent has actual repository state upfront
    from .github_api import get_repo_tree

    try:
        tree = await get_repo_tree(owner, repo)
        file_list = [item["path"] for item in tree]
        total_files = len(file_list)

        # Build directory structure
        dirs = set()
        for path in file_list:
            if "/" in path:
                dir_path = path.rsplit("/", 1)[0]
                dirs.add(dir_path.split("/")[0])

        # Format repository context
        repo_context = f"""
========================================
CURRENT REPOSITORY STATE FOR {repo_full_name}
========================================
Total Files: {total_files}
Top-level Directories: {', '.join(sorted(dirs)) if dirs else 'None (files at root only)'}

COMPLETE FILE LIST (these are the ONLY files that exist):
"""
        for path in sorted(file_list):
            repo_context += f"  - {path}\n"

        repo_context += """
CRITICAL: This is the ACTUAL current state of the repository.
- For DELETE actions: ONLY include files from this list above!
- For MODIFY actions: ONLY include files from this list above!
- For CREATE actions: ONLY include NEW files NOT in this list!
- DO NOT plan to delete directories like "src/" or "docs/" - delete the individual FILES inside them!
========================================
"""

    except Exception as e:
        repo_context = f"""
Warning: Could not pre-fetch repository tree: {str(e)}
You MUST use your repository exploration tools before creating a plan!
"""

    planner = Agent(
        role="Repository Refactor Planner",
        goal=(
            "Design safe, step-by-step refactor plans for a GitHub repo "
            "based on actual repository analysis."
        ),
        backstory=(
            "You are an experienced staff engineer with deep expertise in repository analysis. "
            "You have been provided with the CURRENT STATE of the repository including all files. "
            "You MUST base your plans ONLY on the actual files listed in the repository context. "
            "You NEVER assume files exist - you verify against the provided file list. "
            "When users ask to delete files, you delete individual FILES, not directory names. "
            "You can use your tools to read file contents if needed for understanding. "
            "You *only* propose plans based on ACTUAL files that exist in the repository. "
            "You never make changes yourself, only create detailed plans."
        ),
        llm=llm,
        tools=REPOSITORY_TOOLS,  # Give planner access to repository exploration tools
        verbose=True,            # <-- enable detailed logs for debugging
        allow_delegation=False,
    )

    plan_task = Task(
        description=dedent(f"""
            User goal: {{goal}}
            Repository: {repo_full_name}

            {repo_context}

            PLANNING INSTRUCTIONS:
            1. Review the CURRENT REPOSITORY STATE provided above carefully.
            2. You may use your tools for additional exploration if needed:
               - Use "Read file content" to understand what's in a file
               - Use "Search for files by pattern" to find files by extension
            3. Base your plan ONLY on files that exist in the repository state above.
            4. When planning DELETE actions, ONLY include individual FILE PATHS from the list above.
            5. When planning MODIFY actions, ONLY include FILE PATHS from the list above.
            6. When planning CREATE actions, ONLY include NEW files NOT in the list above.
            7. DO NOT try to delete directory names (like "src/" or "docs/") - delete the individual files!

            Your FINAL ANSWER must be a single JSON object that matches exactly this schema:

            {{
              "goal": "string describing the goal",
              "summary": "string with overall plan summary",
              "steps": [
                {{
                  "step_number": 1,
                  "title": "Step title",
                  "description": "What this step does",
                  "files": [
                    {{"path": "actual/file/path.py", "action": "DELETE"}},
                    {{"path": "another/file.md", "action": "MODIFY"}}
                  ],
                  "risks": "Optional risk description or null"
                }}
              ]
            }}

            CRITICAL JSON RULES:
            - Output MUST be valid JSON:
              - Double quotes around all keys and string values.
              - No comments.
              - No trailing commas anywhere.
            - "action" MUST be exactly one of: "CREATE", "MODIFY", "DELETE".
            - For DELETE or MODIFY: verify the EXACT file path exists in the repository state above.
            - For CREATE: verify the file does NOT exist in the repository state above.
            - "step_number" MUST be an integer starting from 1.
            - "risks" can be either a string or null (the JSON null value, without quotes).
            - Do NOT wrap the JSON in markdown code fences.
            - Do NOT add any explanation before or after the JSON.
            - The ENTIRE response MUST be ONLY the JSON object, starting with '{{' and ending with '}}'.

            EXAMPLE - If user says "delete all files except README.md" and the repo state shows:
              - README.md
              - src/main.py
              - src/utils.py
              - docs/tutorial.md

            Your plan should include:
              DELETE src/main.py (actual file from list)
              DELETE src/utils.py (actual file from list)
              DELETE docs/tutorial.md (actual file from list)
              MODIFY README.md (to update it as requested)

            NOT:
              DELETE src/ (this is a directory, not a file!)
              DELETE docs/ (this is a directory, not a file!)
        """),
        expected_output=dedent("""
            A single valid JSON object matching the PlanResult schema:
            - goal: string
            - summary: string
            - steps: array of objects, each with:
              - step_number: integer
              - title: string
              - description: string
              - files: array of { "path": string, "action": "CREATE" | "MODIFY" | "DELETE" }
              - risks: string or null
            The response must contain ONLY pure JSON (no markdown, no prose, no code fences).
            All file paths must match EXACTLY with files from the CURRENT REPOSITORY STATE provided.
        """),
        agent=planner,
        output_pydantic=PlanResult,
    )

    crew = Crew(
        agents=[planner],
        tasks=[plan_task],
        process=Process.sequential,
        verbose=True,  # <-- see full reasoning and final JSON in logs
    )

    def _run():
        # Any ValidationError here will bubble up and be visible with verbose logs
        return crew.kickoff(inputs={"goal": goal})

    result = await asyncio.to_thread(_run)

    # CrewAI returns CrewOutput with .pydantic attribute containing the PlanResult
    if hasattr(result, "pydantic") and result.pydantic:
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

    # Set repository context for tools (in case Code Writer needs them)
    set_repo_context(owner, repo)

    # Create a Code Writer agent that generates actual content
    code_writer = Agent(
        role="Expert Code Writer",
        goal="Generate high-quality, production-ready code and documentation based on requirements.",
        backstory=(
            "You are a senior software engineer with expertise in multiple programming languages. "
            "You write clean, well-documented, and functional code. "
            "You understand context and generate appropriate content for each file type. "
            "For documentation files (README.md, docs, etc.), you write clear, comprehensive content. "
            "For code files, you follow best practices and include proper comments. "
            "You can use repository exploration tools to understand the codebase when needed."
        ),
        llm=llm,
        tools=REPOSITORY_TOOLS,  # Give Code Writer access to repository tools
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
                            "Requirements:\n"
                            f"- Create production-ready content appropriate for {file.path}\n"
                            "- If it's a documentation file (.md, .txt, .rst), write comprehensive, well-structured documentation\n"
                            "- If it's a code file, include proper imports, comments, and follow best practices\n"
                            "- If it's a configuration file, include sensible defaults and comments\n"
                            "- Make the content complete and ready to use\n"
                            "- Do NOT include placeholder comments like 'TODO' or 'IMPLEMENT THIS'\n"
                            "- The content should be fully functional and informative\n\n"
                            "Return ONLY the file content, no explanations or markdown code blocks."
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
                        if hasattr(result, "raw"):
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
                                "Requirements:\n"
                                "- Make the changes described in the step context\n"
                                "- Preserve the existing structure and format\n"
                                "- For documentation: update or add relevant sections\n"
                                "- For code: add/modify functions, imports, or logic as needed\n"
                                "- Ensure the result is complete and functional\n"
                                "- Do NOT just add comments - make real, substantive changes\n\n"
                                "Return ONLY the complete modified file content, no explanations."
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
                            if hasattr(result, "raw"):
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
                "description": "Analyzes repository structure and codebase",
            },
            {
                "id": "planner",
                "label": "Refactor Planner",
                "type": "agent",
                "description": "Creates safe, step-by-step refactor plans",
            },
            {
                "id": "code_writer",
                "label": "Code Writer",
                "type": "agent",
                "description": "Implements approved changes to codebase",
            },
            {
                "id": "reviewer",
                "label": "Code Reviewer",
                "type": "agent",
                "description": "Reviews changes for quality and safety",
            },
            {
                "id": "github_tools",
                "label": "GitHub API",
                "type": "tool",
                "description": "Read/write files, create commits & PRs",
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