from __future__ import annotations

import asyncio
import contextvars
import logging
from textwrap import dedent
from typing import List, Literal

from crewai import Agent, Crew, Process, Task
from pydantic import BaseModel, Field

from .llm_provider import build_llm
from .agent_tools import REPOSITORY_TOOLS, set_repo_context, get_repository_context_summary

logger = logging.getLogger(__name__)


class PlanFile(BaseModel):
    """Represents a file operation in a plan step."""
    path: str
    action: Literal["CREATE", "MODIFY", "DELETE", "READ"] = "MODIFY"


class PlanStep(BaseModel):
    """A single step in the execution plan."""
    step_number: int
    title: str
    description: str
    # Important: avoid mutable default list
    files: List[PlanFile] = Field(default_factory=list)
    risks: str | None = None


class PlanResult(BaseModel):
    """The complete execution plan."""
    goal: str
    summary: str
    steps: List[PlanStep]


async def generate_plan(goal: str, repo_full_name: str, token: str | None = None) -> PlanResult:
    """Agentic planning: create a structured plan but DO NOT modify the repo.

    This function uses a two-phase approach:
    1. First, explore and understand the repository
    2. Then, create a plan based on actual repository state
    """
    llm = build_llm()

    # Set repository context for tools
    owner, repo = repo_full_name.split("/")
    # CRITICAL: Pass token to context so tools can use it in threads
    set_repo_context(owner, repo, token=token)

    # PHASE 1: Explore the repository and gather context
    logger.info("[GitPilot] Phase 1: Exploring repository %s...", repo_full_name)
    # Pass token explicitly here too
    repo_context_data = await get_repository_context_summary(owner, repo, token=token)
    logger.info(
        "[GitPilot] Repository context gathered: %s files found",
        repo_context_data.get("total_files", 0),
    )

    # Create explorer agent
    explorer = Agent(
        role="Repository Explorer",
        goal="Thoroughly explore and document the current state of the repository",
        backstory=(
            "You are a meticulous code archaeologist who explores repositories "
            "to understand their complete structure before any changes are made. "
            "You use all available tools to build a comprehensive picture."
        ),
        llm=llm,
        tools=REPOSITORY_TOOLS,
        verbose=True,
        allow_delegation=False,
    )

    # Exploration task - gather ALL repository information
    explore_task = Task(
        description=dedent(f"""
            Repository: {repo_full_name}

            Your mission is to THOROUGHLY explore this repository and document its current state.
            You MUST use your tools to gather the following information:

            1. Call "Get repository summary" - to get overall statistics
            2. Call "List all files in repository" - to see EVERY file that exists
            3. Call "Get directory structure" - to understand the organization
            4. If there are key files (README.md, package.json, etc.), read them

            CRITICAL: You must ACTUALLY CALL these tools. Do not make assumptions.

            After exploring, provide a detailed report in this EXACT format:

            REPOSITORY EXPLORATION REPORT
            =============================

            Files Found: [list all file paths you discovered]

            Key Files: [list important files like README.md, .gitignore, etc.]

            Directory Structure: [describe the folder organization]

            File Types: [count files by extension]

            Your report MUST be based on ACTUAL tool calls, not assumptions.
        """),
        expected_output="A detailed exploration report listing ALL files found in the repository",
        agent=explorer,
    )

    explore_crew = Crew(
        agents=[explorer],
        tasks=[explore_task],
        process=Process.sequential,
        verbose=True,
    )

    def _explore():
        return explore_crew.kickoff()

    # FIX: Propagate context (token) to the thread for CrewAI execution
    ctx = contextvars.copy_context()
    exploration_result = await asyncio.to_thread(ctx.run, _explore)

    # Extract exploration report
    exploration_report = exploration_result.raw if hasattr(exploration_result, "raw") else str(exploration_result)
    logger.info(
        "[GitPilot] Exploration complete. Report length: %s chars",
        len(exploration_report),
    )

    # PHASE 2: Create the plan using the exploration context
    logger.info("[GitPilot] Phase 2: Creating plan based on repository exploration...")

    planner = Agent(
        role="Repository Refactor Planner",
        goal=(
            "Design safe, step-by-step refactor plans based on ACTUAL repository state "
            "discovered during exploration"
        ),
        backstory=(
            "You are an experienced staff engineer who creates plans based on FACTS, not assumptions. "
            "You have received a complete exploration report of the repository. "
            "You ONLY create plans for files that actually exist in the exploration report. "
            "You are extremely careful with DELETE actions - you verify the file exists "
            "and that it's not on the 'keep' list before marking it for deletion. "
            "When users ask to delete files, you delete individual FILES, not directory names. "
            "When users ask to ANALYZE files and GENERATE new content (code, docs, examples), "
            "you create plans that READ existing files and CREATE new files with generated content. "
            "You understand that 'analyze X and create Y' means: use tools to read X, then plan to CREATE Y. "
            "You never make changes yourself, only create detailed plans."
        ),
        llm=llm,
        tools=REPOSITORY_TOOLS,  # Still has access to verify if needed
        verbose=True,
        allow_delegation=False,
    )

    plan_task = Task(
        description=dedent(f"""
            User goal: {{goal}}
            Repository: {repo_full_name}

            REPOSITORY EXPLORATION REPORT (CRITICAL CONTEXT):
            ==================================================
            {exploration_report}
            ==================================================

            Based on the ACTUAL files listed in the exploration report above, create a plan.

            CRITICAL RULES FOR ANALYSIS AND GENERATION TASKS:
            - If the goal mentions "analyze" or "generate" or "create examples/demos", you MUST create NEW files
            - When the user asks to "analyze X and create Y":
              * Step 1: Use "Read file content" tool to analyze existing files (if needed)
              * Step 2: Plan CREATE actions for new files (e.g., demo.py, example.py, tutorial.md)
            - NEW files can include: Python scripts, examples, demos, tutorials, documentation
            - Examples of analysis tasks that should CREATE files:
              * "analyze README and generate Python code" → CREATE: demo.py, example.py
              * "create demo based on documentation" → CREATE: demo.py, test_example.py
              * "generate tutorial from existing code" → CREATE: tutorial.md, examples/
            - IMPORTANT: Empty plans (steps: []) are ONLY acceptable if the goal is purely informational
            - If the user wants something generated/created, you MUST include CREATE actions

            CRITICAL RULES FOR DELETION SCENARIOS:
            - If the goal mentions "delete files" or "keep only", you MUST identify which files to DELETE
            - For EACH file in the exploration report:
              * If it should be KEPT (e.g., README.md if goal says "keep README.md"), do NOT include it in the plan
              * If it should be DELETED (e.g., all other files), mark it with action "DELETE"
            - ONLY delete files that actually exist (check the exploration report)
            - NEVER delete files that the user wants to keep
            - Be explicit: if the goal is "delete all files except README.md", then:
              * README.md should NOT appear in your plan (it's being kept)
              * ALL other files from the exploration report should have action "DELETE"

            CRITICAL RULES FOR VERIFICATION:
            - ONLY include files that appear in the exploration report
            - For "CREATE" actions: file must NOT be in the exploration report
            - For "MODIFY" or "DELETE" actions: file MUST be in the exploration report
            - If you're unsure, you can still call your tools to double-check

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
                    {{"path": "file/path.py", "action": "CREATE"}},
                    {{"path": "another/file.py", "action": "MODIFY"}},
                    {{"path": "old/file.py", "action": "DELETE"}},
                    {{"path": "README.md", "action": "READ"}}
                  ],
                  "risks": "Optional risk description or null"
                }}
              ]
            }}

            CRITICAL JSON RULES:
            - Output MUST be valid JSON.
            - STRICTLY NO COMMENTS allowed (no // or #).
            - Double quotes around all keys and string values.
            - No trailing commas.
            - "action" MUST be exactly one of: "CREATE", "MODIFY", "DELETE", "READ"
            - "step_number" MUST be an integer starting from 1
            - "risks" can be either a string or null (the JSON null value, without quotes)
            - Do NOT wrap the JSON in markdown code fences
            - Do NOT add any explanation before or after the JSON
            - The ENTIRE response MUST be ONLY the JSON object, starting with '{{' and ending with '}}'

            EXAMPLE 1 - Analysis and Generation:
            Goal: "analyze README.md and generate Python code to explain the content"
            Exploration found: ["README.md"]
            Your plan should:
              Step 1: "Analyze README.md content and generate demonstration code"
                Files: [
                  {{"path": "demo.py", "action": "CREATE"}},
                  {{"path": "example.py", "action": "CREATE"}}
                ]

            EXAMPLE 2 - Deletion:
            Goal: "delete all files except README.md"
            Exploration found: ["README.md", "old_file.py", "test.txt"]
            Your plan should DELETE: old_file.py, test.txt
            Your plan should NOT mention: README.md (it's being kept)

            EXAMPLE 3 - Tutorial Creation:
            Goal: "create a tutorial based on the existing code"
            Exploration found: ["src/main.py", "README.md"]
            Your plan should:
              Step 1: "Create comprehensive tutorial files"
                Files: [
                  {{"path": "docs/tutorial.md", "action": "CREATE"}},
                  {{"path": "examples/basic_example.py", "action": "CREATE"}},
                  {{"path": "examples/advanced_example.py", "action": "CREATE"}}
                ]
        """),
        expected_output=dedent("""
            A single valid JSON object matching the PlanResult schema:
            - goal: string
            - summary: string
            - steps: array of objects, each with:
              - step_number: integer
              - title: string
              - description: string
              - files: array of { "path": string, "action": "CREATE" | "MODIFY" | "DELETE" | "READ" }
              - risks: string or null
            The response must contain ONLY pure JSON (no markdown, no prose, no code fences, NO COMMENTS).

            IMPORTANT:
            - For analysis/generation tasks: Include CREATE actions for new files (demos, examples, tutorials)
            - For deletion scenarios: Only include files that should be DELETED or MODIFIED
            - Empty steps array is ONLY acceptable if the goal is purely informational (no action needed)
            - If the user wants content generated/created, steps array MUST contain CREATE actions
        """),
        agent=planner,
        output_pydantic=PlanResult,
    )

    plan_crew = Crew(
        agents=[planner],
        tasks=[plan_task],
        process=Process.sequential,
        verbose=True,
    )

    def _plan():
        return plan_crew.kickoff(inputs={"goal": goal})

    # FIX: Propagate context to the planning thread
    ctx = contextvars.copy_context()
    result = await asyncio.to_thread(ctx.run, _plan)

    # CrewAI returns CrewOutput with .pydantic attribute containing the PlanResult
    if hasattr(result, "pydantic") and result.pydantic:
        plan = result.pydantic
        logger.info("[GitPilot] Plan created with %s steps", len(plan.steps))
        return plan

    # Fallback: this should not usually happen if output_pydantic is respected
    logger.warning("[GitPilot] Unexpected planning result type: %r", type(result))
    return result


async def execute_plan(
    plan: PlanResult,
    repo_full_name: str,
    token: str | None = None,
    branch_name: str | None = None,
) -> dict:
    """Execute the approved plan by applying changes to the GitHub repository.

    All changes are committed to a feature branch (not the default branch).
    This allows for safe review and manual PR creation by the user.

    Args:
        plan: The execution plan to apply
        repo_full_name: Repository in format "owner/repo"
        token: GitHub authentication token
        branch_name: Optional branch name. If not provided, auto-generated from goal.

    Returns an execution log with details about each step, including the branch name.
    """
    from .github_api import get_file, put_file, create_branch, get_repo
    import re
    import time

    owner, repo = repo_full_name.split("/")
    execution_steps: list[dict] = []
    llm = build_llm()

    # 1. Decide branch name (auto-generate if not provided)
    if branch_name is None:
        # Create a deterministic, clean branch name from the goal
        # Format: gitpilot-<sanitized-goal>-<timestamp>
        sanitized = re.sub(r'[^a-z0-9-]+', '-', plan.goal.lower())
        sanitized = sanitized[:40].strip('-')
        timestamp = str(int(time.time()))[-6:]  # Last 6 digits of timestamp
        branch_name = f"gitpilot-{sanitized}-{timestamp}"

    # 2. Create the feature branch from the default branch
    try:
        logger.info("[GitPilot] Creating feature branch: %s", branch_name)
        await create_branch(owner, repo, branch_name, from_ref="HEAD", token=token)
        logger.info("[GitPilot] Branch created successfully: %s", branch_name)
        branch_created = True
    except HTTPException as e:
        # If branch already exists, we can either reuse it or fail
        # For now, let's try to continue with the existing branch
        logger.warning(
            "[GitPilot] Branch %s already exists or creation failed: %s. Attempting to use existing branch.",
            branch_name,
            e.detail,
        )
        branch_created = False

    # Set repository context for tools (in case Code Writer needs them)
    set_repo_context(owner, repo, token=token)

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
            "IMPORTANT: You ALWAYS use repository exploration tools before creating new content. "
            "When asked to create demos/examples/tutorials, you first READ the existing files to understand "
            "the project, then generate content that is relevant and accurate. "
            "You never create generic examples - you create content specific to THIS repository."
        ),
        llm=llm,
        tools=REPOSITORY_TOOLS,  # Give Code Writer access to repository tools
        verbose=True,  # Enable verbose to see tool usage
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
                            "CRITICAL INSTRUCTIONS:\n"
                            "- You have access to repository exploration tools - USE THEM!\n"
                            "- If the goal mentions 'analyze' or 'based on', first read the relevant files:\n"
                            "  * Use 'Read file content' to read existing files (README.md, source code, etc.)\n"
                            "  * Use 'List all files in repository' to see what files exist\n"
                            "- Generate content that is INFORMED by the actual repository content\n"
                            "- If creating a demo/example, make it relevant to the actual project\n"
                            "- If creating documentation, reference actual files and code in the repository\n\n"
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

                    # FIX: Propagate context to the creation thread
                    ctx = contextvars.copy_context()
                    content = await asyncio.to_thread(ctx.run, _create)

                    # Clean up any markdown code blocks that might be included
                    content = content.strip()
                    if content.startswith("```"):
                        lines = content.split("\n")
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
                        token=token,
                        branch=branch_name,
                    )
                    step_summary += f"\n  ✓ Created {file.path}"

                elif file.action == "MODIFY":
                    # Use LLM to intelligently modify the existing file
                    try:
                        existing_content = await get_file(
                            owner, repo, file.path, token=token, ref=branch_name
                        )

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

                        # FIX: Propagate context to the modification thread
                        ctx = contextvars.copy_context()
                        modified_content = await asyncio.to_thread(ctx.run, _modify)

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
                            token=token,
                            branch=branch_name,
                        )
                        step_summary += f"\n  ✓ Modified {file.path}"
                    except Exception as e:  # noqa: BLE001
                        logger.exception(
                            "Failed to modify file %s in step %s: %s",
                            file.path,
                            step.step_number,
                            e,
                        )
                        step_summary += f"\n  ✗ Failed to modify {file.path}: {str(e)}"

                elif file.action == "DELETE":
                    # Actually delete the file from the repository
                    from .github_api import delete_file

                    try:
                        await delete_file(
                            owner,
                            repo,
                            file.path,
                            f"GitPilot: Delete {file.path} - {step.title}",
                            token=token,
                            branch=branch_name,
                        )
                        step_summary += f"\n  ✓ Deleted {file.path}"
                    except Exception as e:  # noqa: BLE001
                        logger.exception(
                            "Failed to delete file %s in step %s: %s",
                            file.path,
                            step.step_number,
                            e,
                        )
                        step_summary += f"\n  ✗ Failed to delete {file.path}: {str(e)}"

                elif file.action == "READ":
                    # READ is a planning/analysis action only, no repo change
                    step_summary += f"\n  ℹ️ READ-only: inspected {file.path}"

            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "Error processing file %s in step %s: %s",
                    file.path,
                    step.step_number,
                    e,
                )
                step_summary += f"\n  ✗ Error processing {file.path}: {str(e)}"

        execution_steps.append({"step_number": step.step_number, "summary": step_summary})

    return {
        "status": "completed",
        "message": f"Successfully executed {len(plan.steps)} steps on {repo_full_name} in branch '{branch_name}'",
        "branch": branch_name,
        "branch_url": f"https://github.com/{repo_full_name}/tree/{branch_name}",
        "executionLog": {"steps": execution_steps},
    }


async def get_flow_definition() -> dict:
    """Return the current CrewAI agent workflow as a visual graph.

    This represents the multi-agent system used for planning and execution.
    """
    return {
        "nodes": [
            {
                "id": "repo_explorer",
                "label": "Repository Explorer",
                "type": "agent",
                "description": "Explores repository to gather current state",
            },
            {
                "id": "planner",
                "label": "Refactor Planner",
                "type": "agent",
                "description": "Creates safe, step-by-step refactor plans based on exploration",
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
                "description": "Read/write/delete files, create commits & PRs",
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "repo_explorer",
                "target": "planner",
                "label": "Complete repository state & file listing",
            },
            {
                "id": "e2",
                "source": "planner",
                "target": "code_writer",
                "label": "Approved plan with verified file actions",
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
                "target": "repo_explorer",
                "label": "Updated repository state",
            },
        ],
    }
