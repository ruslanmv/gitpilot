from __future__ import annotations

import asyncio
import contextvars
import logging
from textwrap import dedent
from typing import Any, Dict, List, Literal, Optional

from crewai import Agent, Crew, Process, Task
from pydantic import BaseModel, Field

from .llm_provider import build_llm
from .agent_tools import REPOSITORY_TOOLS, set_repo_context, get_repository_context_summary
from .issue_tools import ISSUE_TOOLS
from .pr_tools import PR_TOOLS
from .search_tools import SEARCH_TOOLS
from .local_tools import LOCAL_TOOLS, LOCAL_FILE_TOOLS, LOCAL_GIT_TOOLS, LOCAL_SHELL_TOOLS
from .agent_router import AgentType, RequestCategory, WorkflowPlan, route as route_request
from .context_pack import build_context_pack
from fastapi import HTTPException

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


async def generate_plan(
    goal: str,
    repo_full_name: str,
    token: str | None = None,
    branch_name: str | None = None,
) -> PlanResult:
    """Agentic planning: create a structured plan but DO NOT modify the repo.

    Two-phase approach:
    1) Explore and understand the repository (on the correct branch)
    2) Create a plan based on actual repository state
    """
    llm = build_llm()

    owner, repo = repo_full_name.split("/")

    # CRITICAL: Set context INCLUDING branch so tools never fall back to HEAD/main
    active_ref = branch_name or "HEAD"
    set_repo_context(owner, repo, token=token, branch=active_ref)

    # CONTEXT PACK: Load project context (conventions, active use case, asset chunks)
    # This is additive — if nothing exists, context_pack is empty and agents behave as before.
    from pathlib import Path as _P
    workspace_path = _P.home() / ".gitpilot" / "workspaces" / owner / repo
    context_pack = build_context_pack(workspace_path, query=goal)
    if context_pack:
        logger.info("[GitPilot] Context pack loaded (%d chars)", len(context_pack))

    # PHASE 1: Explore repository (correct branch)
    logger.info("[GitPilot] Phase 1: Exploring repository %s (ref=%s)...", repo_full_name, active_ref)

    repo_context_data = await get_repository_context_summary(owner, repo, token=token, branch=active_ref)
    logger.info(
        "[GitPilot] Repository context gathered: %s files found (ref=%s)",
        repo_context_data.get("total_files", 0),
        active_ref,
    )

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

    explore_task = Task(
        description=dedent(f"""
            Repository: {repo_full_name}
            Active Ref (branch/tag/SHA): {active_ref}

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

    # Propagate context to thread for CrewAI execution
    ctx = contextvars.copy_context()
    exploration_result = await asyncio.to_thread(ctx.run, _explore)

    exploration_report = exploration_result.raw if hasattr(exploration_result, "raw") else str(exploration_result)
    logger.info("[GitPilot] Exploration complete. Report length: %s chars", len(exploration_report))

    # PHASE 2: Plan creation based on exploration
    logger.info("[GitPilot] Phase 2: Creating plan based on repository exploration (ref=%s)...", active_ref)

    # Build planner backstory with optional context pack injection
    _planner_backstory = (
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
    )
    if context_pack:
        _planner_backstory += "\n\n" + context_pack

    planner = Agent(
        role="Repository Refactor Planner",
        goal=(
            "Design safe, step-by-step refactor plans based on ACTUAL repository state "
            "discovered during exploration"
        ),
        backstory=_planner_backstory,
        llm=llm,
        tools=REPOSITORY_TOOLS,
        verbose=True,
        allow_delegation=False,
    )

    plan_task = Task(
        description=dedent(f"""
            User goal: {{goal}}
            Repository: {repo_full_name}
            Active Ref (branch/tag/SHA): {active_ref}

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

    ctx = contextvars.copy_context()
    result = await asyncio.to_thread(ctx.run, _plan)

    if hasattr(result, "pydantic") and result.pydantic:
        plan = result.pydantic
        logger.info("[GitPilot] Plan created with %s steps (ref=%s)", len(plan.steps), active_ref)
        return plan

    logger.warning("[GitPilot] Unexpected planning result type: %r", type(result))
    return result


async def execute_plan(
    plan: PlanResult,
    repo_full_name: str,
    token: str | None = None,
    branch_name: str | None = None,
) -> dict:
    """Execute the approved plan by applying changes to the GitHub repository."""
    from .github_api import get_file, put_file, create_branch, get_repo
    import re
    import time

    owner, repo = repo_full_name.split("/")
    execution_steps: list[dict] = []
    llm = build_llm()

    if branch_name is None:
        sanitized = re.sub(r"[^a-z0-9-]+", "-", plan.goal.lower())
        sanitized = sanitized[:40].strip("-")
        timestamp = str(int(time.time()))[-6:]
        branch_name = f"gitpilot-{sanitized}-{timestamp}"

    try:
        logger.info("[GitPilot] Creating feature branch: %s", branch_name)
        await create_branch(owner, repo, branch_name, from_ref="HEAD", token=token)
        logger.info("[GitPilot] Branch created successfully: %s", branch_name)
    except HTTPException as e:
        logger.warning(
            "[GitPilot] Branch %s already exists or creation failed: %s. Attempting to use existing branch.",
            branch_name,
            e.detail,
        )

    # CRITICAL: ensure tools read from the ACTIVE execution branch
    set_repo_context(owner, repo, token=token, branch=branch_name)

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
        tools=REPOSITORY_TOOLS,
        verbose=True,
        allow_delegation=False,
    )

    for step in plan.steps:
        step_summary = f"Step {step.step_number}: {step.title}"

        for file in step.files:
            try:
                if file.action == "CREATE":
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
                        if hasattr(result, "raw"):
                            return result.raw
                        return str(result)

                    ctx = contextvars.copy_context()
                    content = await asyncio.to_thread(ctx.run, _create)

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

                        ctx = contextvars.copy_context()
                        modified_content = await asyncio.to_thread(ctx.run, _modify)

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


# ============================================================================
# New Agent Builders (v2 upgrade)
# ============================================================================

def _build_issue_agent(llm) -> Agent:
    return Agent(
        role="GitHub Issue Management Specialist",
        goal="Create, modify, and manage GitHub issues with proper metadata and relationships",
        backstory=(
            "You are an expert in GitHub issue management. You can create new issues "
            "with detailed descriptions, modify existing issues and their metadata, "
            "manage labels, milestones, and assignees, and add comments. "
            "You ensure issues are well-organised and provide clear status updates. "
            "When creating issues you always include a concise title and a structured body."
        ),
        llm=llm,
        tools=ISSUE_TOOLS,
        verbose=True,
        allow_delegation=False,
    )


def _build_pr_agent(llm) -> Agent:
    return Agent(
        role="Pull Request Management Specialist",
        goal="Create, list, review, and merge pull requests",
        backstory=(
            "You are skilled in pull request workflows. You can create PRs from "
            "feature branches, list open PRs, inspect changed files, add reviews, "
            "and merge PRs using the appropriate strategy (merge, squash, rebase). "
            "You always verify the source and target branches before acting."
        ),
        llm=llm,
        tools=PR_TOOLS,
        verbose=True,
        allow_delegation=False,
    )


def _build_search_agent(llm) -> Agent:
    return Agent(
        role="Search & Discovery Specialist",
        goal="Find code, repositories, issues, and users across GitHub",
        backstory=(
            "You are an expert at finding resources on GitHub. You can search for "
            "code by keywords, symbols, or patterns within a repository or globally. "
            "You can find users and organisations, discover repositories by topic, "
            "and locate issues or PRs matching specific criteria. "
            "You present results in a clear, structured format."
        ),
        llm=llm,
        tools=SEARCH_TOOLS + REPOSITORY_TOOLS,
        verbose=True,
        allow_delegation=False,
    )


def _build_code_review_agent(llm) -> Agent:
    return Agent(
        role="Code Review & Analysis Specialist",
        goal="Review code quality, identify patterns, and suggest improvements",
        backstory=(
            "You are an experienced code reviewer who analyses code for quality, "
            "security issues, and performance problems. You inspect files in the "
            "repository, read their contents, and provide constructive feedback. "
            "For pull requests you examine the changed files and produce a detailed "
            "review with actionable suggestions."
        ),
        llm=llm,
        tools=REPOSITORY_TOOLS + PR_TOOLS + SEARCH_TOOLS,
        verbose=True,
        allow_delegation=False,
    )


def _build_learning_agent(llm) -> Agent:
    return Agent(
        role="GitHub Learning & Guidance Specialist",
        goal="Provide expert guidance on GitHub features, best practices, and workflows",
        backstory=(
            "You are a GitHub expert who helps users understand GitHub Actions, "
            "CI/CD workflows, authentication, pull request best practices, "
            "repository maintenance, GitHub Pages, Packages, Discussions, "
            "and security best practices. You provide clear, actionable guidance "
            "with examples. You can also read the repository to give contextualised advice."
        ),
        llm=llm,
        tools=REPOSITORY_TOOLS + SEARCH_TOOLS,
        verbose=True,
        allow_delegation=False,
    )


def _build_local_editor_agent(llm) -> Agent:
    """Phase 1: Agent for direct local file editing with verification."""
    return Agent(
        role="Local File Editor",
        goal="Read, write, and modify files in the local workspace with verification",
        backstory=(
            "You are an expert code editor that operates directly on the local "
            "filesystem. You read files, make precise edits, write new files, "
            "and verify changes using git diff. You always check file contents "
            "before editing and confirm results after. You follow project "
            "conventions and never introduce breaking changes."
        ),
        llm=llm,
        tools=LOCAL_FILE_TOOLS + LOCAL_GIT_TOOLS,
        verbose=True,
        allow_delegation=False,
    )


def _build_terminal_agent(llm) -> Agent:
    """Phase 1: Agent for sandboxed shell command execution."""
    return Agent(
        role="Terminal & Shell Executor",
        goal="Execute shell commands safely in the workspace and report results",
        backstory=(
            "You are a terminal expert that runs shell commands in a sandboxed "
            "environment. You can run tests, linters, build tools, and other "
            "development commands. You always report exit codes and output. "
            "You refuse to run destructive commands like rm -rf / or format disks. "
            "You explain command output clearly to the user."
        ),
        llm=llm,
        tools=LOCAL_SHELL_TOOLS + LOCAL_GIT_TOOLS,
        verbose=True,
        allow_delegation=False,
    )


# ============================================================================
# Unified Dispatcher (v2 upgrade)
# ============================================================================

async def dispatch_request(
    user_request: str,
    repo_full_name: str,
    token: Optional[str] = None,
    branch_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Route a free-form user request to the appropriate agent(s) and return the result.

    This is the single entry-point for the new conversational mode.  For backwards
    compatibility the original ``generate_plan`` / ``execute_plan`` pair is still
    available and untouched.
    """
    workflow = route_request(user_request)
    logger.info(
        "[GitPilot] Router: category=%s agents=%s desc=%s",
        workflow.category.value,
        [a.value for a in workflow.agents],
        workflow.description,
    )

    # Phase 2: Smart model routing
    try:
        from .smart_model_router import ModelRouter
        _router = ModelRouter()
        selection = _router.select(user_request, category=workflow.category.value)
        logger.info(
            "[GitPilot] ModelRouter: model=%s tier=%s complexity=%s reason=%s",
            selection.model, selection.tier.value, selection.complexity.value, selection.reason,
        )
    except Exception:
        pass  # Model routing is optional; fall through to default LLM

    # Set repo context if needed
    if workflow.requires_repo_context and repo_full_name:
        owner, repo = repo_full_name.split("/")
        active_ref = branch_name or "HEAD"
        set_repo_context(owner, repo, token=token, branch=active_ref)

    llm = build_llm()

    # If it's the existing plan+execute workflow, delegate there
    if workflow.category == RequestCategory.PLAN_EXECUTE:
        plan = await generate_plan(user_request, repo_full_name, token=token, branch_name=branch_name)
        return {
            "category": workflow.category.value,
            "workflow": "plan_execute",
            "plan": plan.model_dump() if hasattr(plan, "model_dump") else plan,
            "message": "Plan generated. Review and approve to execute.",
        }

    # CONTEXT PACK: Load project context for non-plan agents too (additive)
    _dispatch_ctx_pack = ""
    if repo_full_name:
        try:
            _d_owner, _d_repo = repo_full_name.split("/")
            from pathlib import Path as _P
            _d_ws = _P.home() / ".gitpilot" / "workspaces" / _d_owner / _d_repo
            _dispatch_ctx_pack = build_context_pack(_d_ws, query=user_request)
        except Exception:
            pass

    # Build the task description
    task_description = _build_task_description(workflow, user_request, repo_full_name, branch_name)
    if _dispatch_ctx_pack:
        task_description += "\n\n" + _dispatch_ctx_pack

    # Build agent(s) for this workflow
    agents = []
    for agent_type in workflow.agents:
        agents.append(_get_agent(agent_type, llm))

    # Use the first agent as the primary executor
    primary_agent = agents[0]
    task = Task(
        description=task_description,
        expected_output="A clear, structured response addressing the user request",
        agent=primary_agent,
    )

    crew = Crew(
        agents=agents,
        tasks=[task],
        process=Process.sequential,
        verbose=True,
    )

    def _run():
        result = crew.kickoff()
        if hasattr(result, "raw"):
            return result.raw
        return str(result)

    ctx = contextvars.copy_context()
    result_text = await asyncio.to_thread(ctx.run, _run)

    return {
        "category": workflow.category.value,
        "agents_used": [a.value for a in workflow.agents],
        "result": result_text,
        "entity_number": workflow.entity_number,
    }


def _get_agent(agent_type: AgentType, llm) -> Agent:
    """Instantiate an agent by type."""
    builders = {
        AgentType.EXPLORER: lambda: Agent(
            role="Repository Explorer",
            goal="Thoroughly explore and document the current state of the repository",
            backstory="You are a meticulous code archaeologist who explores repositories.",
            llm=llm,
            tools=REPOSITORY_TOOLS,
            verbose=True,
            allow_delegation=False,
        ),
        AgentType.PLANNER: lambda: Agent(
            role="Repository Refactor Planner",
            goal="Design safe, step-by-step refactor plans",
            backstory="You are an experienced staff engineer who creates plans based on facts.",
            llm=llm,
            tools=REPOSITORY_TOOLS,
            verbose=True,
            allow_delegation=False,
        ),
        AgentType.CODE_WRITER: lambda: Agent(
            role="Expert Code Writer",
            goal="Generate high-quality, production-ready code",
            backstory="You are a senior software engineer with multi-language expertise.",
            llm=llm,
            tools=REPOSITORY_TOOLS,
            verbose=True,
            allow_delegation=False,
        ),
        AgentType.CODE_REVIEWER: lambda: _build_code_review_agent(llm),
        AgentType.ISSUE_MANAGER: lambda: _build_issue_agent(llm),
        AgentType.PR_MANAGER: lambda: _build_pr_agent(llm),
        AgentType.SEARCH: lambda: _build_search_agent(llm),
        AgentType.LEARNING: lambda: _build_learning_agent(llm),
        AgentType.LOCAL_EDITOR: lambda: _build_local_editor_agent(llm),
        AgentType.TERMINAL: lambda: _build_terminal_agent(llm),
    }
    builder = builders.get(agent_type)
    if not builder:
        raise ValueError(f"Unknown agent type: {agent_type}")
    return builder()


def _build_task_description(
    workflow: WorkflowPlan,
    user_request: str,
    repo_full_name: str,
    branch_name: Optional[str],
) -> str:
    """Build a detailed task description for the agent based on the workflow."""
    parts = [
        f"User request: {user_request}",
        f"Repository: {repo_full_name}",
    ]
    if branch_name:
        parts.append(f"Branch: {branch_name}")
    if workflow.entity_number:
        parts.append(f"Entity number: #{workflow.entity_number}")

    # Category-specific instructions
    if workflow.category == RequestCategory.ISSUE_MANAGEMENT:
        action = workflow.metadata.get("action", "")
        parts.append(
            "\nYou are handling an ISSUE MANAGEMENT request. "
            f"Action hint: {action}. "
            "Use your issue tools to fulfill the request. "
            "If creating an issue, extract title and body from the user request. "
            "If listing issues, present results in a clear table. "
            "If updating, identify the issue number and fields to change. "
            "Always confirm what you did with the issue URL."
        )

    elif workflow.category == RequestCategory.PR_MANAGEMENT:
        action = workflow.metadata.get("action", "")
        parts.append(
            "\nYou are handling a PULL REQUEST request. "
            f"Action hint: {action}. "
            "Use your PR tools to fulfill the request. "
            "If creating a PR, determine the head and base branches. "
            "If merging, confirm the PR number and merge method. "
            "Always confirm with the PR URL."
        )

    elif workflow.category == RequestCategory.CODE_SEARCH:
        search_type = workflow.metadata.get("search_type", "code")
        parts.append(
            f"\nYou are handling a SEARCH request (type: {search_type}). "
            "Use your search tools to find what the user is looking for. "
            "Present results clearly with paths, URLs, and context snippets."
        )

    elif workflow.category == RequestCategory.CODE_REVIEW:
        parts.append(
            "\nYou are handling a CODE REVIEW request. "
            "First explore the repository to understand the codebase, "
            "then analyse code quality, identify potential issues "
            "(security, performance, maintainability), and provide "
            "constructive suggestions with specific file references."
        )

    elif workflow.category == RequestCategory.LEARNING:
        parts.append(
            "\nYou are handling a LEARNING / GUIDANCE request. "
            "Provide clear, actionable guidance about GitHub features. "
            "Include examples and best practices. "
            "If relevant, reference the current repository for context."
        )

    elif workflow.category == RequestCategory.LOCAL_EDIT:
        parts.append(
            "\nYou are handling a LOCAL FILE EDITING request. "
            "Use your local file tools to read, write, and modify files. "
            "Always read the file before editing to understand current content. "
            "After editing, use git_diff or git_status to verify your changes. "
            "Report exactly what was changed."
        )

    elif workflow.category == RequestCategory.TERMINAL:
        parts.append(
            "\nYou are handling a TERMINAL / SHELL COMMAND request. "
            "Use the run_command tool to execute the requested command. "
            "Report the exit code and output. If tests fail, summarise "
            "which tests failed and why. Never run destructive commands."
        )

    elif workflow.category == RequestCategory.CONVERSATIONAL:
        parts.append(
            "\nYou are handling a general question about the repository. "
            "Use repository tools to explore and answer the question. "
            "Be concise and helpful."
        )

    return "\n".join(parts)


# ============================================================================
# Auto PR Creation (v2 upgrade)
# ============================================================================

async def create_pr_after_execution(
    repo_full_name: str,
    branch_name: str,
    goal: str,
    execution_log: Dict[str, Any],
    token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Automatically create a PR after plan execution completes.

    Returns the PR data dict or None if creation fails.
    """
    from .github_pulls import create_pull_request
    from .github_api import get_repo

    owner, repo = repo_full_name.split("/")

    try:
        repo_info = await get_repo(owner, repo, token=token)
        default_branch = repo_info.get("default_branch", "main")
    except Exception:
        default_branch = "main"

    # Build PR body from execution log
    steps = execution_log.get("steps", [])
    body_lines = [f"## GitPilot Auto-PR\n\n**Goal:** {goal}\n"]
    for step in steps:
        body_lines.append(f"- {step.get('summary', '')}")
    body_lines.append(f"\n---\n*Created by GitPilot*")
    body = "\n".join(body_lines)

    # Truncate title to stay within GitHub limits
    title = f"GitPilot: {goal}"
    if len(title) > 256:
        title = title[:253] + "..."

    try:
        pr = await create_pull_request(
            owner,
            repo,
            title=title,
            head=branch_name,
            base=default_branch,
            body=body,
            token=token,
        )
        logger.info("[GitPilot] Auto-PR created: %s", pr.get("html_url", ""))
        return pr
    except Exception as e:
        logger.warning("[GitPilot] Failed to create auto-PR: %s", e)
        return None


# ============================================================================
# Flow Definition (v2 -- expanded graph)
# ============================================================================

async def get_flow_definition() -> dict:
    """Return the current CrewAI agent workflow as a visual graph."""
    return {
        "nodes": [
            {
                "id": "router",
                "label": "Request Router",
                "type": "router",
                "description": "Analyses user intent and delegates to the right agent(s)",
            },
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
                "description": "Reviews code quality, security, and performance",
            },
            {
                "id": "issue_manager",
                "label": "Issue Manager",
                "type": "agent",
                "description": "Creates, updates, and manages GitHub issues",
            },
            {
                "id": "pr_manager",
                "label": "PR Manager",
                "type": "agent",
                "description": "Creates, reviews, and merges pull requests",
            },
            {
                "id": "search_agent",
                "label": "Search & Discovery",
                "type": "agent",
                "description": "Searches code, repos, issues, and users",
            },
            {
                "id": "learning_agent",
                "label": "Learning & Guidance",
                "type": "agent",
                "description": "Provides GitHub feature guidance and best practices",
            },
            {
                "id": "local_editor",
                "label": "Local Editor",
                "type": "agent",
                "description": "Reads and writes files directly in the local workspace",
            },
            {
                "id": "terminal_agent",
                "label": "Terminal",
                "type": "agent",
                "description": "Executes shell commands in a sandboxed environment",
            },
            {
                "id": "github_tools",
                "label": "GitHub API",
                "type": "tool",
                "description": "Read/write/delete files, issues, PRs, search",
            },
            {
                "id": "local_tools",
                "label": "Local Tools",
                "type": "tool",
                "description": "File I/O, git operations, shell commands on local workspace",
            },
        ],
        "edges": [
            {
                "id": "e0",
                "source": "router",
                "target": "repo_explorer",
                "label": "Plan & Execute workflow",
            },
            {
                "id": "e0b",
                "source": "router",
                "target": "issue_manager",
                "label": "Issue management requests",
            },
            {
                "id": "e0c",
                "source": "router",
                "target": "pr_manager",
                "label": "PR management requests",
            },
            {
                "id": "e0d",
                "source": "router",
                "target": "search_agent",
                "label": "Search requests",
            },
            {
                "id": "e0e",
                "source": "router",
                "target": "reviewer",
                "label": "Code review requests",
            },
            {
                "id": "e0f",
                "source": "router",
                "target": "learning_agent",
                "label": "Learning & guidance requests",
            },
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
                "target": "pr_manager",
                "label": "Auto-create PR after execution",
            },
            {
                "id": "e4",
                "source": "reviewer",
                "target": "pr_manager",
                "label": "Review results",
            },
            {
                "id": "e5",
                "source": "issue_manager",
                "target": "github_tools",
                "label": "Issue operations",
            },
            {
                "id": "e6",
                "source": "pr_manager",
                "target": "github_tools",
                "label": "PR operations",
            },
            {
                "id": "e7",
                "source": "search_agent",
                "target": "github_tools",
                "label": "Search queries",
            },
            {
                "id": "e8",
                "source": "router",
                "target": "local_editor",
                "label": "Local file editing requests",
            },
            {
                "id": "e9",
                "source": "router",
                "target": "terminal_agent",
                "label": "Shell command requests",
            },
            {
                "id": "e10",
                "source": "local_editor",
                "target": "local_tools",
                "label": "File and git operations",
            },
            {
                "id": "e11",
                "source": "terminal_agent",
                "target": "local_tools",
                "label": "Command execution",
            },
        ],
    }
