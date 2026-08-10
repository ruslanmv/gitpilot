from __future__ import annotations

import asyncio
import contextvars
import logging
from textwrap import dedent
from typing import Any, Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field, ValidationError as _PydanticValidationError
from .agent_router import AgentType, RequestCategory, WorkflowPlan, route as route_request
from .context_pack import build_context_pack
from .topology_registry import (
    get_topology,
    get_topology_graph,
    classify_message,
    get_saved_topology_preference,
    ExecutionStyle,
    RoutingStrategy,
)
from fastapi import HTTPException

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Incompatible model detection
# ---------------------------------------------------------------------------
# Models that struggle with CrewAI's multi-agent ReAct format.
# Two categories:
#   1. REASONING models (deepseek-r1, qwq, marco-o1) — produce <think> tokens
#      that break CrewAI's parser regardless of model size
#   2. SMALL models (<7B params) — return empty responses when they can't
#      follow "Thought: Action: Action Input:" format
#
# All of these are auto-routed to Lite Mode for reliability.
_INCOMPATIBLE_MODEL_PATTERNS = (
    # Reasoning models (ALL sizes fail — the <think> tag breaks ReAct parser)
    "deepseek-r1",
    "qwq",
    "marco-o1",
    "o1-",
    # Small models (<7B)
    "qwen2.5:0.5b", "qwen2.5:1.5b", "qwen2.5:3b",
    "qwen2:0.5b", "qwen2:1.5b",
    "llama3.2:1b", "llama3.2:3b",
    "phi3:mini", "phi-3-mini", "phi3.5:mini", "phi3:3.8b",
    "gemma:2b", "gemma2:2b",
    "deepseek-coder:1.3b", "deepseek-coder:6.7b",
    "tinyllama", "tinydolphin",
    "stablelm2", "smollm", "granite3",
)


def _is_incompatible_model(settings) -> bool:
    """Check if the active model is incompatible with multi-agent ReAct.

    Uses substring matching so "deepseek-r1" catches all variants
    (deepseek-r1:1.5b, deepseek-r1:7b, deepseek-r1:14b, deepseek-r1:latest).
    """
    try:
        provider = str(getattr(settings, "provider", "")).lower()
        # Only applies to local Ollama/OllaBridge providers — cloud APIs
        # (OpenAI, Claude) have native tool-calling that handles this
        if provider not in ("ollama", "ollabridge"):
            return False

        if provider == "ollama":
            model = str(getattr(settings.ollama, "model", "")).lower()
        else:
            model = str(getattr(settings.ollabridge, "model", "")).lower()

        for pattern in _INCOMPATIBLE_MODEL_PATTERNS:
            if pattern in model:
                return True
        return False
    except Exception:
        return False


def _split_repo_full_name(repo_full_name: str) -> tuple[str, str]:
    """Safely split 'owner/repo' into (owner, repo).

    Raises a clear ValueError if the input is missing, empty, or malformed.
    This replaces `owner, repo = _split_repo_full_name(repo_full_name)` which produces
    a cryptic "not enough values to unpack" error on folder/local-git
    sessions that have no GitHub repository.
    """
    if not isinstance(repo_full_name, str) or not repo_full_name.strip():
        raise ValueError(
            "repo_full_name is required but was empty. "
            "This session is not connected to a GitHub repository — "
            "the multi-agent planner needs a repo in 'owner/repo' format. "
            "Open the Workspace tab and add a repository before chatting."
        )
    parts = repo_full_name.strip().split("/")
    if len(parts) != 2 or not all(p.strip() for p in parts):
        raise ValueError(
            f"repo_full_name must be in 'owner/repo' format, got: {repo_full_name!r}. "
            "Example: 'octocat/hello-world'"
        )
    return parts[0].strip(), parts[1].strip()


# ---------------------------------------------------------------------------
# Resilient agent execution: timeout + circuit breaker
# ---------------------------------------------------------------------------
async def _guarded_agent_call(ctx, func, *, label: str = "agent"):
    """Run a CrewAI kickoff in a thread with timeout and circuit breaker.

    - Checks circuit breaker before starting.
    - Applies a hard timeout (default 5 min, configurable via GITPILOT_AGENT_TIMEOUT).
    - Records success/failure in the circuit breaker.
    """
    from .resilience import llm_circuit, run_with_timeout

    if not llm_circuit.allow_request():
        raise RuntimeError(
            f"LLM provider circuit breaker is OPEN after repeated failures. "
            f"Requests are temporarily rejected. Try again in "
            f"{int(llm_circuit.recovery_timeout)}s."
        )

    try:
        result = await run_with_timeout(
            asyncio.to_thread(ctx.run, func),
            label=label,
        )
        llm_circuit.record_success()
        return result
    except (TimeoutError, RuntimeError):
        llm_circuit.record_failure()
        raise
    except Exception:
        llm_circuit.record_failure()
        raise


# ---------------------------------------------------------------------------
# Lazy-load heavy dependencies (CrewAI, tool modules, LLM provider)
# so that importing this module does NOT block FastAPI startup on HF Spaces.
# The actual import happens on first call to any agent function.
# ---------------------------------------------------------------------------
_crewai_cache: dict = {}


def _crewai():
    """Return cached CrewAI classes (Agent, Crew, Process, Task)."""
    if not _crewai_cache:
        from crewai import Agent, Crew, Process, Task  # noqa: F811
        _crewai_cache.update(Agent=Agent, Crew=Crew, Process=Process, Task=Task)
    return _crewai_cache


_tools_cache: dict = {}


async def _execute_index_action(
    owner: str, repo: str, *, token: str | None, branch_name: str | None,
) -> str:
    """Handle the ``INDEX`` plan-step pseudo-action (Batch B9).

    Triggers a one-time RAG index build for the active repo:
    fetches every file via the GitHub tree, runs them through the
    chunker / embedder, persists the ChromaDB collection, and grants
    per-repo consent so future fuzzy queries auto-build incrementally.

    Returns a one-line summary suitable for the execution-log step
    output.  Failures are surfaced as their own line; we never raise
    because that would abort sibling steps in the same plan.
    """
    from .github_api import get_file, get_repo_tree
    from .rag.indexer import build_index_from_files
    from .rag_consent import grant_consent

    try:
        tree = await get_repo_tree(owner, repo, token=token, ref=branch_name)
    except Exception as exc:
        logger.warning("[index] could not list repo tree: %s", exc)
        return f"! Failed to list repo for indexing: {exc}"

    paths = [item["path"] for item in (tree or []) if item.get("path")]
    if not paths:
        return "i Repo is empty — nothing to index."

    # Cap how many files we'll embed in one user-approved build to
    # bound time + disk.  Anything over the cap still produces a
    # usable index covering the most-important files; the rest can
    # be added incrementally on subsequent builds.
    INDEX_FETCH_CAP = 500
    paths = paths[:INDEX_FETCH_CAP]

    async def _fetch(p: str) -> tuple[str, str | None]:
        try:
            return p, await get_file(owner, repo, p, token=token, ref=branch_name)
        except Exception:
            return p, None

    import asyncio as _aio
    results = await _aio.gather(*(_fetch(p) for p in paths))
    files: list[tuple[str, str]] = [
        (p, c) for p, c in results if isinstance(c, str) and c
    ]
    if not files:
        return "! Could not fetch any repo files for indexing."

    # Build synchronously inside the await — embedding is CPU-bound
    # and we want the user to see "indexing complete" before the
    # next plan step runs.
    try:
        report = build_index_from_files(
            files,
            owner=owner,
            repo=repo,
            branch=branch_name or "HEAD",
        )
    except Exception as exc:
        logger.warning("[index] build failed: %s", exc)
        return f"! Index build failed: {exc}"

    try:
        grant_consent(owner, repo)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[index] could not grant consent: %s", exc)

    return (
        f"+ Indexed {report.files_indexed} file(s) "
        f"({report.chunks_added} chunks, embedder={report.embedder_name}, "
        f"skipped={report.files_skipped}).  "
        f"Semantic search is now available for {owner}/{repo}."
    )


def _tools():
    """Return cached tool collections (lazy-loaded on first use)."""
    if not _tools_cache:
        from .agent_tools import PLANNER_TOOLS, REPOSITORY_TOOLS, WRITE_TOOLS, set_repo_context, get_repository_context_summary
        from .issue_tools import ISSUE_TOOLS
        from .pr_tools import PR_TOOLS
        from .search_tools import SEARCH_TOOLS
        from .local_tools import LOCAL_TOOLS, LOCAL_FILE_TOOLS, LOCAL_GIT_TOOLS, LOCAL_SHELL_TOOLS
        _tools_cache.update(
            REPOSITORY_TOOLS=REPOSITORY_TOOLS,
            PLANNER_TOOLS=PLANNER_TOOLS,
            WRITE_TOOLS=WRITE_TOOLS,
            set_repo_context=set_repo_context,
            get_repository_context_summary=get_repository_context_summary,
            ISSUE_TOOLS=ISSUE_TOOLS,
            PR_TOOLS=PR_TOOLS,
            SEARCH_TOOLS=SEARCH_TOOLS,
            LOCAL_TOOLS=LOCAL_TOOLS,
            LOCAL_FILE_TOOLS=LOCAL_FILE_TOOLS,
            LOCAL_GIT_TOOLS=LOCAL_GIT_TOOLS,
            LOCAL_SHELL_TOOLS=LOCAL_SHELL_TOOLS,
        )
    return _tools_cache


def _build_llm():
    """Lazy-import and call build_llm."""
    from .llm_provider import build_llm as _build
    return _build()


def agent_verbose(default: bool = True) -> bool:
    """Whether CrewAI should narrate what the agents are doing.

    CrewAI's verbose output — the agent banners, the task status, the final
    answer — goes to the server console and is the only view anyone has of a
    multi-agent run in progress. Most of the crews here already ask for it;
    the Lite paths did not, so the exact configuration a small local model
    needs was also the one that ran silently.

    ``GITPILOT_AGENT_VERBOSE=0`` turns it off for anyone who wants a quiet
    log, without touching the code.
    """
    import os

    raw = os.getenv("GITPILOT_AGENT_VERBOSE")
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


class PlanFile(BaseModel):
    """Represents a file operation in a plan step.

    ``INDEX`` (Batch B9) is a special pseudo-action: the ``path`` is
    treated as a marker ("__repo__") rather than a real file, and the
    executor branch triggers a one-time RAG index build for the active
    repo.  Surfaced as its own plan step so the user approves the
    indexing cost (time + disk) just like any other action.
    """
    path: str
    action: Literal[
        "CREATE", "MODIFY", "DELETE", "READ", "INDEX", "EXECUTE",
    ] = "MODIFY"


class PlanStep(BaseModel):
    """A single step in the execution plan."""
    step_number: int
    title: str
    description: str
    # Important: avoid mutable default list
    files: List[PlanFile] = Field(default_factory=list)
    risks: str | None = None


class PlanResult(BaseModel):
    """The complete execution plan.

    ``execution_plan`` is the approval-first Sandbox ExecutionPlan
    described in :mod:`gitpilot.sandbox_plan`.  When the router
    detects an unambiguous RUN_FILE intent it is populated here so
    the chat UI can render the green ExecutionPlanCard *instead of*
    the orange Action Plan — sandbox execution and repo changes
    require visibly different consent surfaces.

    Always ``None`` for create/modify/delete plans.  Always present
    for execute plans the short-circuit accepts.
    """
    goal: str
    summary: str
    steps: List[PlanStep]
    execution_plan: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# EXECUTE short-circuit
#
# When the user's request is unambiguously "run this file" and the file
# exists in the repo with a runnable extension, we skip the LLM planner
# entirely.  Two wins:
#
# 1. Correctness — the planner running through a small / ReAct-trained
#    LLM has been observed to interpret "EXECUTE" as a CrewAI tool name
#    rather than a JSON action value, fail repeatedly, then downgrade
#    the action to "READ".  A deterministic plan eliminates that
#    failure class entirely.
#
# 2. Latency — no LLM round-trip; the next request reaches the
#    executor in milliseconds and the user sees an execution card
#    almost immediately.

_RUNNABLE_EXTENSIONS = frozenset({"py", "js", "mjs", "cjs", "sh", "bash"})


def _is_runnable_path(path: str) -> bool:
    """True when ``path`` ends in a sandbox-runnable extension."""
    if "." not in path:
        return False
    ext = path.rsplit(".", 1)[-1].lower()
    return ext in _RUNNABLE_EXTENSIONS


_MATPLOTLIB_HINTS = (
    "import matplotlib", "from matplotlib", "plt.show", "pyplot",
)


def _looks_like_matplotlib(code: str) -> bool:
    """Quick check for matplotlib usage so we can inject the Agg backend
    only when needed.  False positives are harmless (Agg works for any
    Python script); false negatives cause the sandbox to hang on
    ``plt.show()``, which is what this guard prevents."""
    lowered = code.lower()
    return any(hint in lowered for hint in _MATPLOTLIB_HINTS)


# Last-ditch extractor for runnable-looking paths in a goal string.
# Used when the router's verified ``target_files`` is empty — typical
# when the user names a file that's freshly created on the branch and
# the cached tree fetch doesn't show it yet.  Trusting the user's text
# is much better UX than rejecting the short-circuit and falling through
# to the LLM, which then returns an empty plan.
import re as _re_runnable
_RUNNABLE_PATH_RE = _re_runnable.compile(
    r"[\w\-./]+\.(?:py|js|mjs|cjs|sh|bash)\b", _re_runnable.IGNORECASE,
)


def _extract_runnable_paths_from_goal(goal: str) -> List[str]:
    """Return runnable-extension path tokens mentioned in ``goal``.

    Deduplicated, original order preserved. Trims surrounding
    punctuation that the bareword regex sometimes pulls in.
    """
    out: List[str] = []
    seen: set[str] = set()
    for m in _RUNNABLE_PATH_RE.finditer(goal or ""):
        tok = m.group(0).strip(".,:;()[]{}'\"`")
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def try_execute_short_circuit(
    *,
    goal: str,
    intent: Optional[str],
    target_files: Sequence[str],
    repo_files: Sequence[str],
) -> Optional["PlanResult"]:
    """Return a deterministic EXECUTE plan when one is unambiguous.

    Triggers when:
      * routing intent is ``execute``
      * exactly one runnable file is implied — either named explicitly
        in ``target_files`` (router-verified to exist), extracted from
        the goal text when verification dropped it (race / stale cache),
        or the only runnable file in the repo when the user said
        "run the script" without naming one.

    Returns ``None`` to fall through to the LLM planner otherwise.
    """
    if (intent or "").lower() != "execute":
        return None

    # The "file freshly created but not yet in the tree fetch" race is
    # the most common reason verification drops a perfectly good target
    # — track whether we had to fall back so the executor / UI can
    # adjust the safety check label accordingly.
    file_verified_against_repo = False
    candidates: List[str] = [p for p in target_files if _is_runnable_path(p)]
    if candidates:
        file_verified_against_repo = True
    else:
        # Try extracting runnable-shaped paths directly from the goal.
        # Trust the user — the executor reports a clean "file not found"
        # if the path turns out to be wrong, which is far better UX
        # than the LLM's "empty plan" failure mode.
        extracted = _extract_runnable_paths_from_goal(goal)
        if extracted:
            candidates = extracted
        else:
            # User said "run the script" without naming a file.  Resolve
            # by uniqueness: a single runnable in the repo wins; anything
            # else falls through to the LLM so it can ask.
            repo_runnable = [p for p in repo_files if _is_runnable_path(p)]
            if len(repo_runnable) == 1:
                candidates = repo_runnable
                file_verified_against_repo = True
    if len(candidates) != 1:
        return None

    path = candidates[0]

    # Attach a deterministic ExecutionPlan so the chat UI can render
    # the green approval card.  The Action-Plan-style PlanStep below
    # stays in place so callers that still consume that shape (and
    # the executor itself) work unchanged.
    execution_plan_dict: Optional[Dict[str, Any]] = None
    try:
        from .sandbox_plan import build_execution_plan_for_file
        from .settings import get_settings

        _settings = get_settings()
        _sb = _settings.sandbox
        _sandbox_label = "matrixlab" if (_sb.backend or "").lower() == "matrixlab" else "subprocess"
        # When we couldn't verify the file against the repo tree, pass
        # the path itself as the repo_files list so the builder's
        # membership check passes — the safety check below clarifies
        # the verification state honestly.
        _repo_files_for_plan = list(repo_files) if file_verified_against_repo else [path]
        ep = build_execution_plan_for_file(
            file=path,
            repo_files=_repo_files_for_plan,
            sandbox=_sandbox_label,  # type: ignore[arg-type]
            timeout_sec=int(_sb.timeout_sec or 120),
            network=bool(_sb.allow_network),
            source="chat",
        )
        if ep is not None:
            execution_plan_dict = ep.to_dict()
            if not file_verified_against_repo:
                # Adjust the safety surface: flip the "File exists"
                # check to ok=False and add a low-severity warning so
                # the user can still approve, but with clear context.
                for c in execution_plan_dict.get("safety", {}).get("checks", []):
                    if c.get("label") == "File exists":
                        c["ok"] = False
                        c["label"] = (
                            f"File path: {path} (existence not verified)"
                        )
                execution_plan_dict.setdefault("safety", {}).setdefault(
                    "warnings", []
                ).insert(0, {
                    "severity": "low",
                    "label": "File not in cached repo tree",
                    "detail": (
                        "GitPilot didn't see this file in the latest repo "
                        "tree fetch — usually a stale cache or a file that "
                        "was created on this branch but not yet pushed. "
                        "The sandbox will report a clean 'file not found' "
                        "if the path is wrong."
                    ),
                })
    except Exception:  # pragma: no cover - defensive
        execution_plan_dict = None

    return PlanResult(
        goal=goal,
        summary=(
            f"Run {path} in the configured sandbox and report stdout, "
            f"stderr, exit code, and duration."
        ),
        steps=[PlanStep(
            step_number=1,
            title=f"Run {path}",
            description=(
                f"Execute {path} through the active sandbox backend "
                f"(Local subprocess or MatrixLab Runner). The executor "
                f"reads the file at apply time and POSTs to /api/sandbox/run."
            ),
            files=[PlanFile(path=path, action="EXECUTE")],
            risks=None,
        )],
        execution_plan=execution_plan_dict,
    )


# ---------------------------------------------------------------------------
# Markdown-fence stripper for agent file-content output.
#
# The Code Writer agent's system prompt asks it to return ONLY the file
# content, no markdown code blocks.  In practice every small LLM and
# even some large ones wrap the output in ``` ... ``` (and sometimes
# ~~~ ... ~~~).  This helper removes that wrapper before the content
# is written to disk, including a few real-world variants the previous
# inline logic missed:
#
#   * tilde fences ``~~~python ... ~~~``
#   * fenced block with a leading language tag (``` ```python ... ``` ```)
#   * leading or trailing whitespace / blank lines outside the fence
#   * fenced block embedded in explanatory prose
#     ("Here is the file:\n```python\n...\n```\nLet me know if…")
#
# The fallback is the input unchanged — if no clear single fenced block
# is found, we leave the content alone (better to commit slightly
# wrapped content than to corrupt it by guessing).
# ---------------------------------------------------------------------------

_FENCE_BLOCK_RE = __import__("re").compile(
    r"(?P<f>```|~~~)[^\n]*\n(?P<body>.*?)\n[ \t]*(?P=f)\s*$",
    __import__("re").DOTALL | __import__("re").MULTILINE,
)


def _strip_markdown_fences(content: str) -> str:
    """Strip a wrapping markdown code fence from agent-produced file
    content.  Returns the bare body when a clean fence pair is found;
    returns the input unchanged otherwise."""
    if not isinstance(content, str) or not content:
        return content
    stripped = content.strip()

    # Fast path: the whole payload is one fenced block with nothing
    # before it.  Walk every fence occurrence and pick the largest body
    # — this gives the right answer when the agent prepends a sentence
    # like "Here is the file:".
    best_body: str | None = None
    for match in _FENCE_BLOCK_RE.finditer(stripped):
        body = match.group("body")
        if best_body is None or len(body) > len(best_body):
            best_body = body
    if best_body is not None:
        return best_body

    return stripped


async def generate_plan(
    goal: str,
    repo_full_name: str,
    token: str | None = None,
    branch_name: str | None = None,
    *,
    routing_hint: str | None = None,
    intent: str | None = None,
) -> PlanResult:
    """Agentic planning: create a structured plan but DO NOT modify the repo.

    ``intent`` is the literal from :class:`gitpilot.query_router.RouterDecision`
    (fix / find / info / create / delete / modify).  When supplied AND
    the ``lean_prompts`` flag is on, the planner's task description
    uses only the rule block matching the intent — small models stop
    drowning in irrelevant create-vs-delete-vs-modify rules.

    ``routing_hint`` is an optional pre-classified directive from
    :mod:`gitpilot.query_router` that gets concatenated into the
    planner's context_pack.  Advisory — the planner can override
    when context demands more exploration.

    Two-phase approach:
    1) Explore and understand the repository (on the correct branch)
    2) Create a plan based on actual repository state
    """
    llm = _build_llm()

    owner, repo = _split_repo_full_name(repo_full_name)

    # CRITICAL: Set context INCLUDING branch so tools never fall back to HEAD/main
    active_ref = branch_name or "HEAD"
    _tools()["set_repo_context"](owner, repo, token=token, branch=active_ref)

    # CONTEXT PACK: Load project context (conventions, active use case, asset chunks)
    # This is additive — if nothing exists, context_pack is empty and agents behave as before.
    from pathlib import Path as _P
    workspace_path = _P.home() / ".gitpilot" / "workspaces" / owner / repo
    context_pack = build_context_pack(workspace_path, query=goal)
    if context_pack:
        logger.info("[GitPilot] Context pack loaded (%d chars)", len(context_pack))

    # Batch B9 — append the API-layer router's strategy hint so the
    # planner sees the recommended intent / target files / tool order.
    if routing_hint:
        context_pack = (context_pack or "") + ("\n\n" if context_pack else "") + routing_hint
        logger.info("[GitPilot] Router hint injected (%d chars)", len(routing_hint))

    # PHASE 1: Explore repository (correct branch)
    logger.info("[GitPilot] Phase 1: Exploring repository %s (ref=%s)...", repo_full_name, active_ref)

    repo_context_data = await _tools()["get_repository_context_summary"](owner, repo, token=token, branch=active_ref)
    logger.info(
        "[GitPilot] Repository context gathered: %s files found (ref=%s)",
        repo_context_data.get("total_files", 0),
        active_ref,
    )

    # Batch B6: pin a compact "repo map" into the planner's context.
    # Same idea Aider, Cursor and Claude Code use — give the planner a
    # high-level site map (key files + modules + language histogram)
    # in <= 500 tokens, persisted to disk so we don't rebuild it on
    # every turn.  Best-effort: a failure here must never block the
    # planner.
    try:
        from . import flags as _flags
        from .repo_map import FLAG_REPO_MAP, build_repo_map

        if _flags.is_on(FLAG_REPO_MAP, default=True):
            _all_files = list(repo_context_data.get("all_files") or [])
            if _all_files:
                _map = build_repo_map(
                    owner=owner, repo=repo, branch=active_ref or "HEAD",
                    paths=_all_files,
                )
                if _map.agents_md:
                    context_pack = (context_pack or "") + (
                        "\n\n" if context_pack else ""
                    ) + _map.agents_md
                    logger.info(
                        "[GitPilot] Repo map pinned (%d tokens, %d modules, %d key files)",
                        len(_map.agents_md.split()),  # rough proxy
                        len(_map.modules),
                        len(_map.key_files),
                    )
    except Exception as _map_err:  # pragma: no cover - defensive
        logger.debug("[GitPilot] repo map injection skipped: %s", _map_err)

    # Batch B12 — when ``lean_prompts`` is on, every persona / task
    # description is sourced from ``gitpilot.agent_prompts`` so prompt
    # budgets are pinned by tests and never accidentally bloated.
    from . import agent_prompts as _ap

    _lean = _ap.lean_prompts_enabled()

    explorer = _crewai()["Agent"](
        role="Repository Explorer",
        goal=_ap.EXPLORER_GOAL if _lean else (
            "Thoroughly explore and document the current state of the repository"
        ),
        backstory=_ap.EXPLORER_BACKSTORY if _lean else (
            "You are a meticulous code archaeologist who explores repositories "
            "to understand their complete structure before any changes are made. "
            "You use all available tools to build a comprehensive picture."
        ),
        llm=llm,
        tools=_tools()["REPOSITORY_TOOLS"],
        verbose=True,
        allow_delegation=False,
    )

    if _lean:
        _explore_description = _ap.render_explorer_task(
            repo_full_name=repo_full_name, active_ref=active_ref,
        )
        _explore_expected = "A repository exploration report in the documented format"
    else:
        _explore_description = dedent(f"""
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
        """)
        _explore_expected = (
            "A detailed exploration report listing ALL files found in the repository"
        )

    explore_task = _crewai()["Task"](
        description=_explore_description,
        expected_output=_explore_expected,
        agent=explorer,
    )

    explore_crew = _crewai()["Crew"](
        agents=[explorer],
        tasks=[explore_task],
        process=_crewai()["Process"].sequential,
        verbose=True,
    )

    def _explore():
        return explore_crew.kickoff()

    # Propagate context to thread for CrewAI execution
    ctx = contextvars.copy_context()
    try:
        exploration_result = await _guarded_agent_call(ctx, _explore, label="explore_repo")
    except _PydanticValidationError as exc:
        # Same failure mode as the planner-side validation error: the
        # explorer's Final Answer didn't match the expected schema, so
        # CrewAI's converter blew up before we could even ask the
        # planner anything.  Surface the same friendly message — the
        # underlying agent-quality issue is identical.
        logger.warning(
            "[GitPilot] Explorer emitted output that failed schema "
            "validation: %s",
            (exc.errors()[0].get("msg") if exc.errors() else "(no detail)"),
        )
        raise RuntimeError(
            "The repository explorer did not return a usable result.  "
            "This usually means the LLM lost its instruction format "
            "(common with smaller / quantised models).  Re-run the "
            "request, or switch to a stronger LLM via Settings → Provider."
        ) from exc

    exploration_report_raw = exploration_result.raw if hasattr(exploration_result, "raw") else str(exploration_result)
    logger.info("[GitPilot] Exploration complete. Report length: %s chars", len(exploration_report_raw))

    # Batch B5: protect the planner's context by compressing the
    # explorer's free-form report into a fixed-budget summary.  When
    # the report already fits (small repos, small models) this is a
    # no-op; on big repos it can shave 3–6 KB off the planner prompt
    # without losing any concrete file paths.
    try:
        from .explorer_summary import compress_exploration_report

        exploration_report, _exp_metrics = compress_exploration_report(exploration_report_raw)
        if _exp_metrics.compressed_tokens < _exp_metrics.original_tokens:
            logger.info(
                "[GitPilot] Compressed exploration report: %d → %d tokens "
                "(%d/%d files kept)",
                _exp_metrics.original_tokens,
                _exp_metrics.compressed_tokens,
                _exp_metrics.files_kept,
                _exp_metrics.files_in_original,
            )
    except Exception as _exp_err:  # pragma: no cover - defensive
        logger.debug("[GitPilot] explorer compression failed: %s", _exp_err)
        exploration_report = exploration_report_raw

    # PHASE 2: Plan creation based on exploration
    logger.info("[GitPilot] Phase 2: Creating plan based on repository exploration (ref=%s)...", active_ref)

    # Build planner backstory with optional context pack injection
    if _lean:
        _planner_backstory = _ap.PLANNER_BACKSTORY
        _planner_goal = _ap.PLANNER_GOAL
    else:
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
        _planner_goal = (
            "Design safe, step-by-step refactor plans based on ACTUAL repository state "
            "discovered during exploration"
        )
    # context_pack additions (B6 repo map + B9 routing hint) are only
    # appended in non-lean mode; on small models they bloat the prompt
    # and push the JSON-schema rules out of the attention window.
    if context_pack and not _lean:
        _planner_backstory += "\n\n" + context_pack

    planner = _crewai()["Agent"](
        role="Repository Refactor Planner",
        goal=_planner_goal,
        backstory=_planner_backstory,
        llm=llm,
        # PLANNER_TOOLS, not REPOSITORY_TOOLS: the planner is the agent taught
        # the JSON action vocabulary (CREATE/MODIFY/READ/...), so it is the one
        # that emits "Action: READ" and needs that to resolve.
        tools=_tools()["PLANNER_TOOLS"],
        verbose=True,
        allow_delegation=False,
    )

    if _lean:
        # Use the per-intent compact template from agent_prompts.
        # Pass the verified file list directly so the planner sees the
        # facts block at the bottom of the prompt — highest attention
        # weight on small models.
        _plan_description = _ap.render_plan_task(
            goal="{goal}",     # CrewAI inputs substitution happens later
            repo_full_name=repo_full_name,
            active_ref=active_ref or "HEAD",
            file_list=list(repo_context_data.get("all_files") or []),
            intent=intent,
        )
    else:
        _plan_description = dedent(f"""
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
                    {{"path": "README.md", "action": "READ"}},
                    {{"path": "hello.py", "action": "EXECUTE"}}
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
            - "action" MUST be exactly one of: "CREATE", "MODIFY", "DELETE", "READ", "EXECUTE"
              (use EXECUTE to run an existing runnable file — .py, .js, .sh — through the configured sandbox)
            - "EXECUTE" is a JSON STRING VALUE only.  It is NOT a tool name.
              Never write "Action: EXECUTE" or attempt to invoke EXECUTE as a tool —
              your tools are exactly the ones listed above (repository inspection +
              file reading).  Execution happens AFTER you return this JSON plan;
              the GitPilot executor reads it and runs the file through the sandbox.
            - "step_number" MUST be an integer starting from 1
            - "risks" can be either a string or null (the JSON null value, without quotes)
            - Do NOT wrap the JSON in markdown code fences
            - Do NOT add any explanation before or after the JSON
            - The ENTIRE response MUST be ONLY the JSON object, starting with '{{' and ending with '}}'
        """)
    plan_task = _crewai()["Task"](
        description=_plan_description,
        expected_output=dedent("""
            A single valid JSON object matching the PlanResult schema:
            - goal: string
            - summary: string
            - steps: array of objects, each with:
              - step_number: integer
              - title: string
              - description: string
              - files: array of { "path": string, "action": "CREATE" | "MODIFY" | "DELETE" | "READ" | "EXECUTE" }
              - risks: string or null
            The response must contain ONLY pure JSON (no markdown, no prose, no code fences, NO COMMENTS).
        """),
        agent=planner,
        output_pydantic=PlanResult,
    )

    plan_crew = _crewai()["Crew"](
        agents=[planner],
        tasks=[plan_task],
        process=_crewai()["Process"].sequential,
        verbose=True,
    )

    def _plan():
        return plan_crew.kickoff(inputs={"goal": goal})

    ctx = contextvars.copy_context()
    try:
        result = await _guarded_agent_call(ctx, _plan, label="generate_plan")
    except _PydanticValidationError as exc:
        # CrewAI tried to coerce the planner's Final Answer into the
        # ``PlanResult`` schema and failed.  We have seen two real
        # production payloads cause this:
        #
        #   1. The agent emitted a ReAct-format "Thought / Action /
        #      Action Input" block instead of JSON (its instruction
        #      formatting collapsed).  CrewAI's converter still tries
        #      to find a ``{...}`` substring, lands on ``Input: {}``,
        #      validates that, and Pydantic complains:
        #        "3 validation errors for PlanResult: goal / summary
        #         / steps - Field required"
        #
        #   2. The agent returned plain refusal prose with an empty
        #      ``{}`` somewhere in it.
        #
        # Both cases are agent-quality failures, not user errors.
        # Translate to the same friendly RuntimeError surface the
        # refusal path already uses so the UI shows "couldn't produce
        # a plan" rather than a 500 with a Pydantic traceback.
        logger.warning(
            "[GitPilot] Planner emitted output that failed PlanResult "
            "validation (%d error%s).  First error: %s",
            len(exc.errors()),
            "" if len(exc.errors()) == 1 else "s",
            (exc.errors()[0].get("msg") if exc.errors() else "(no detail)"),
        )
        raise RuntimeError(
            "The planner did not return a valid plan structure.  This "
            "usually means the LLM lost its instruction format mid-task "
            "(common with smaller / quantised models).  Re-run the "
            "request, or switch to a stronger LLM via Settings → Provider."
        ) from exc

    # ------------------------------------------------------------------
    # Post-hoc guards — catch the failure mode where the planner LLM
    # returns either a refusal or a hallucinated stock plan that has
    # nothing to do with the user's repository.
    # ------------------------------------------------------------------
    from .plan_guards import (
        PlanHallucinationError,
        assess_plan,
        detect_refusal,
        enrich_plan_with_reads,
    )

    refusal = detect_refusal(result)
    if refusal is not None:
        logger.warning(
            "[GitPilot] Planner returned a refusal-shaped response (%r); "
            "treating as failure rather than rendering a hallucinated plan.",
            refusal,
        )
        raise RuntimeError(
            "The planner refused to produce a plan.  This usually means "
            "the explorer could not read repository content.  Re-run the "
            "request, or switch to a stronger LLM via Settings → Provider."
        )

    if hasattr(result, "pydantic") and result.pydantic:
        plan = result.pydantic
        logger.info("[GitPilot] Plan created with %s steps (ref=%s)", len(plan.steps), active_ref)

        # Cross-check the plan against the real repo file list.  Suspicious
        # placeholder-shaped paths combined with a 0% hit-rate on
        # MODIFY/DELETE actions strongly suggests the planner hallucinated
        # a generic stock plan rather than working from the actual repo.
        try:
            repo_files: list[str] = []
            tools_cache = _tools()
            owner, repo, token, branch = await _resolve_repo_target(tools_cache)
            if owner and repo:
                ctx_summary = await tools_cache["get_repository_context_summary"](
                    owner, repo, token=token, branch=branch,
                )
                repo_files = list(ctx_summary.get("all_files", []) or [])
        except Exception:
            logger.debug("[GitPilot] could not fetch repo file list for plausibility check", exc_info=True)
            repo_files = []

        if repo_files:
            # Small / quantised LLMs (llama3:8b is the canonical case)
            # consistently drop READ entries from plan steps even when
            # the step's description clearly says "Read the content of
            # README.md".  Enrich the plan before the plausibility
            # check so the Action Plan card surfaces the complete set
            # of files the agent will touch — both the READ inputs and
            # the CREATE / MODIFY / DELETE outputs.
            added_reads = enrich_plan_with_reads(plan, repo_files)
            if added_reads:
                logger.info(
                    "[GitPilot] Auto-injected %d READ entr%s based on plan "
                    "step descriptions (small-model READ-drop mitigation).",
                    added_reads, "y" if added_reads == 1 else "ies",
                )

            assessment = assess_plan(plan, repo_files)
            if assessment.hallucinated:
                logger.warning(
                    "[GitPilot] Plausibility check failed (suspicious=%s, hit_ratio=%.2f); "
                    "treating plan as hallucinated.",
                    len(assessment.suspicious_paths), assessment.hit_ratio,
                )
                raise PlanHallucinationError(
                    "The planner produced paths that do not match this "
                    "repository.  Re-run the request, or switch to a "
                    "stronger LLM via Settings → Provider.",
                    assessment=assessment,
                )

        return plan

    logger.warning("[GitPilot] Unexpected planning result type: %r", type(result))
    return result


async def _resolve_repo_target(tools_cache: dict) -> tuple[str, str, str | None, str | None]:
    """Best-effort lookup of (owner, repo, token, branch) for the active
    planning session.  Returns empty strings when the context is not
    available — callers must tolerate that and skip the plausibility
    check rather than fail."""
    try:
        from .agent_tools import get_repo_context
        owner, repo, token, branch = get_repo_context()
        return owner, repo, token, branch
    except Exception:
        return "", "", None, None


# ============================================================================
# Lite Mode — Simplified single-agent for small LLMs (< 7B parameters)
# ============================================================================

# Regex-based intent classifier — no LLM needed, runs instantly.
_QUESTION_PATTERNS = [
    r"\b(what|which|where|how|why|who|when|does|is|are|can|could|tell|show|list|describe|explain|summarize|overview)\b",
    r"\?$",
]
_ACTION_PATTERNS = [
    r"\b(create|add|delete|remove|modify|change|update|rename|fix|write|implement|refactor|move|generate code)\b",
]


def _classify_lite_intent(goal: str) -> str:
    """Classify user intent as 'question' or 'action' using regex only."""
    import re as _re
    goal_lower = goal.strip().lower()

    action_score = sum(1 for p in _ACTION_PATTERNS if _re.search(p, goal_lower))
    question_score = sum(1 for p in _QUESTION_PATTERNS if _re.search(p, goal_lower))

    # Action words dominate — user wants to change something
    if action_score > 0 and action_score >= question_score:
        return "action"
    return "question"


async def _lite_prefetch_context(
    owner: str,
    repo: str,
    token: str | None,
    branch: str,
    key_file_limit: int = 3,
) -> str:
    """Pre-fetch repo context programmatically and format as plain text.

    Returns a string ready to inject into the LLM prompt.  No LLM
    tool-calling involved — everything comes from the GitHub API.
    """
    from .github_api import get_file as _get_file

    ctx = await _tools()["get_repository_context_summary"](owner, repo, token=token, branch=branch)

    all_files = ctx.get("all_files", [])
    extensions = ctx.get("extensions", {})
    directories = ctx.get("directories", set())
    key_files = ctx.get("key_files", [])

    parts = []

    # File listing (cap at 80 to stay within small-model context)
    if all_files:
        shown = all_files[:80]
        file_lines = "\n".join(f"  {f}" for f in shown)
        parts.append(f"Files ({len(all_files)} total):\n{file_lines}")
        if len(all_files) > 80:
            parts.append(f"  ... and {len(all_files) - 80} more")
    else:
        parts.append("Files: (none found)")

    # Extensions summary
    if extensions:
        ext_str = ", ".join(f"{ext} ({n})" for ext, n in sorted(extensions.items(), key=lambda x: -x[1])[:10])
        parts.append(f"File types: {ext_str}")

    # Top-level directories
    if directories:
        dir_list = sorted(directories)[:15]
        parts.append(f"Top directories: {', '.join(dir_list)}")

    # Read content of key files (README, etc.) — give LLM real context
    for kf in key_files[:key_file_limit]:
        try:
            content = await _get_file(owner, repo, kf, token=token, ref=branch)
            # Truncate to keep prompt small for 1.5B models
            snippet = content[:1500] if content else ""
            if snippet:
                parts.append(f"--- {kf} ---\n{snippet}")
                if len(content) > 1500:
                    parts.append(f"  [truncated, {len(content)} chars total]")
        except Exception:
            pass  # File unreadable — skip silently

    return "\n\n".join(parts)


async def generate_plan_lite(
    goal: str,
    repo_full_name: str,
    token: str | None = None,
    branch_name: str | None = None,
    *,
    routing_hint: str | None = None,
    intent: str | None = None,
) -> PlanResult:
    """Lite Mode planning: smart intent detection + single agent + pre-fetched context.

    ``routing_hint`` is accepted for signature parity with
    :func:`generate_plan`.  Lite Mode has its own simpler routing
    via regex intent classification, so the hint is currently
    treated as advisory metadata only — it does not change the
    Lite planner's behaviour.  Kept here so call sites can use a
    single signature for both planners.

    The topology is:
      1. Classify intent (regex — instant, no LLM)
      2. Pre-fetch repo context from GitHub API (no LLM tool-calling)
      3. Build a short, focused prompt based on intent type
      4. Single LLM call → parse response

    For QUESTION intents: LLM answers directly, plan has 0 file actions.
    For ACTION intents: LLM lists file changes, plan has file actions.
    """
    llm = _build_llm()

    owner, repo = _split_repo_full_name(repo_full_name)
    active_ref = branch_name or "HEAD"
    _tools()["set_repo_context"](owner, repo, token=token, branch=active_ref)

    intent = _classify_lite_intent(goal)
    logger.info("[GitPilot Lite] Intent: %s | Goal: %s", intent, goal[:80])

    # PRE-FETCH: real data from GitHub API
    logger.info("[GitPilot Lite] Pre-fetching context for %s (ref=%s)...", repo_full_name, active_ref)
    context_text = await _lite_prefetch_context(owner, repo, token, active_ref)

    # BUILD PROMPT based on intent
    if intent == "question":
        lite_prompt = (
            f"Repository: {repo_full_name} (branch: {active_ref})\n\n"
            f"{context_text}\n\n"
            f"Question: {goal}\n\n"
            f"Answer the question based on the repository information above. "
            f"Be specific — mention actual file names and directories you can see."
        )
        expected = "A direct answer to the user's question about the repository"
    else:
        lite_prompt = (
            f"Repository: {repo_full_name} (branch: {active_ref})\n\n"
            f"{context_text}\n\n"
            f"Task: {goal}\n\n"
            f"You MUST respond with ONLY a list of file actions. One per line.\n"
            f"Format: ACTION filepath\n"
            f"ACTION is one of: CREATE, MODIFY, DELETE\n\n"
            f"Examples:\n"
            f"DELETE demo.py\n"
            f"DELETE example.py\n"
            f"CREATE src/main.py\n"
            f"MODIFY README.md\n\n"
            f"Rules:\n"
            f"- Only use MODIFY or DELETE for files that EXIST in the repository.\n"
            f"- Only use CREATE for NEW files that do not exist yet.\n"
            f"- Do NOT add explanations. ONLY output ACTION lines.\n"
            f"- Output NOTHING else — no comments, no code, no explanations."
        )
        expected = "ONLY action lines like: DELETE demo.py"

    lite_agent = _crewai()["Agent"](
        role="GitPilot Lite",
        goal="Help the user with their repository",
        backstory="You are a helpful coding assistant. Be concise.",
        llm=llm,
        tools=[],
        verbose=True,
        allow_delegation=False,
    )

    lite_task = _crewai()["Task"](
        description=lite_prompt,
        expected_output=expected,
        agent=lite_agent,
    )

    lite_crew = _crewai()["Crew"](
        agents=[lite_agent],
        tasks=[lite_task],
        process=_crewai()["Process"].sequential,
        verbose=True,
    )

    def _run_lite():
        return lite_crew.kickoff()

    ctx = contextvars.copy_context()
    result = await _guarded_agent_call(ctx, _run_lite, label="lite_mode")

    raw_text = result.raw if hasattr(result, "raw") else str(result)
    logger.info("[GitPilot Lite] Response (%d chars, intent=%s)", len(raw_text), intent)

    # PARSE RESPONSE based on intent
    if intent == "question":
        # Pure Q&A — no file actions, just wrap the answer.
        # summary = full answer text (shown in the "Answer" section of the chat)
        return PlanResult(
            goal=goal,
            summary=raw_text,
            steps=[PlanStep(
                step_number=1,
                title="Answer",
                description=raw_text,
                files=[],
                risks=None,
            )],
        )

    # Action intent — parse ACTION lines
    import re as _re
    action_pattern = _re.compile(r"^(CREATE|MODIFY|DELETE)\s+(\S+)", _re.MULTILINE)
    matches = action_pattern.findall(raw_text)

    # Strip raw ACTION lines from description to get the human-readable parts
    clean_description = _re.sub(
        r"^(CREATE|MODIFY|DELETE)\s+\S+.*$", "", raw_text, flags=_re.MULTILINE,
    ).strip()

    # Get actual repo files for validation
    repo_ctx = await _tools()["get_repository_context_summary"](owner, repo, token=token, branch=active_ref)
    real_files = set(repo_ctx.get("all_files", []))

    # ── Fuzzy fallback: if the LLM didn't use ACTION format, try to infer ──
    if not matches and real_files:
        logger.info("[GitPilot Lite] No ACTION lines found — trying fuzzy extraction")
        goal_lower = goal.strip().lower()
        response_lower = raw_text.lower()

        # Pattern: "delete all files except X"
        except_match = _re.search(
            r"(?:delete|remove)\s+(?:all\s+)?(?:files?\s+)?(?:except|but|besides|other\s+than)\s+(.+)",
            goal_lower,
        )
        if except_match:
            keep_raw = except_match.group(1).strip()
            keep_files = {f.strip().rstrip(",.") for f in _re.split(r"[,\s]+and\s+|,\s*|\s+", keep_raw) if f.strip()}
            for f in real_files:
                fname = f.rsplit("/", 1)[-1] if "/" in f else f
                if f not in keep_files and fname not in keep_files:
                    matches.append(("DELETE", f))
            if matches:
                logger.info("[GitPilot Lite] Fuzzy: keep=%s, delete=%d files", keep_files, len(matches))

        # Pattern: LLM mentions specific filenames with delete/remove verbs
        if not matches:
            for verb in ("delete", "remove", "rm", "git rm"):
                for f in real_files:
                    if f in response_lower or f in goal_lower:
                        if verb in response_lower or verb in goal_lower:
                            matches.append(("DELETE", f))

        # Pattern: LLM mentions files with create/add verbs
        if not matches:
            create_match = _re.findall(r"(?:create|add|write|generate)\s+(\S+\.(?:py|js|ts|md|txt|yaml|json|sh))", goal_lower)
            for path in create_match:
                if path not in real_files:
                    matches.append(("CREATE", path))

    valid_files = []
    for action, path in matches:
        path = path.strip().rstrip(",-:")
        if action in ("MODIFY", "DELETE"):
            if path in real_files:
                valid_files.append(PlanFile(path=path, action=action))
            else:
                logger.warning("[GitPilot Lite] Skipping %s %s — file not in repo", action, path)
        elif action == "CREATE":
            if path not in real_files:
                valid_files.append(PlanFile(path=path, action=action))

    steps = []
    if valid_files:
        # Build a clean summary: "Create 2 files, modify 1 file"
        counts = {}
        for f in valid_files:
            counts[f.action] = counts.get(f.action, 0) + 1
        action_labels = {"CREATE": "create", "MODIFY": "modify", "DELETE": "delete"}
        summary_parts = []
        for act in ("CREATE", "MODIFY", "DELETE"):
            n = counts.get(act, 0)
            if n > 0:
                label = action_labels[act]
                summary_parts.append(f"{label} {n} file{'s' if n > 1 else ''}")
        clean_summary = "Plan: " + ", ".join(summary_parts) + "."

        # Use the clean description if available, otherwise a generic one
        step_desc = clean_description if clean_description else f"Apply changes to {len(valid_files)} file(s) in {repo_full_name}."

        steps.append(PlanStep(
            step_number=1,
            title="Execute changes",
            description=step_desc,
            files=valid_files,
            risks=None,
        ))
        return PlanResult(goal=goal, summary=clean_summary, steps=steps)

    # No valid files after validation — the LLM hallucinated paths.
    # Return as a Q&A-style answer (no Action Plan section shown in UI).
    fallback_text = clean_description if clean_description else raw_text
    # Strip any remaining ACTION-like artifacts
    fallback_text = _re.sub(r"\bACTION\b", "", fallback_text).strip()
    if not fallback_text:
        fallback_text = (
            f"I analyzed {repo_full_name} but couldn't determine specific file "
            f"changes for your request. The repository has {len(real_files)} file(s). "
            f"Try being more specific about what you'd like to create or modify."
        )

    steps.append(PlanStep(
        step_number=1,
        title="Analysis",
        description=fallback_text,
        files=[],
        risks=None,
    ))
    return PlanResult(goal=goal, summary=fallback_text, steps=steps)


async def execute_plan_lite(
    plan: PlanResult,
    repo_full_name: str,
    token: str | None = None,
    branch_name: str | None = None,
) -> dict:
    """Lite Mode execution: single agent generates file content with simplified prompts.

    Unlike the standard execute_plan, the Lite version:
    - Uses a single short prompt per file (no CRITICAL INSTRUCTIONS blocks)
    - Does not require the LLM to call tools
    - Pre-reads existing file content and injects it into the prompt
    """
    from .github_api import get_file, put_file, create_branch, get_repo
    import re
    import time

    owner, repo = _split_repo_full_name(repo_full_name)
    execution_steps: list[dict] = []
    llm = _build_llm()

    if branch_name is None:
        sanitized = re.sub(r"[^a-z0-9-]+", "-", plan.goal.lower())
        sanitized = sanitized[:40].strip("-")
        timestamp = str(int(time.time()))[-6:]
        branch_name = f"gitpilot-{sanitized}-{timestamp}"

    try:
        await create_branch(owner, repo, branch_name, from_ref="HEAD", token=token)
    except HTTPException:
        pass  # Branch may already exist

    _tools()["set_repo_context"](owner, repo, token=token, branch=branch_name)

    for step in plan.steps:
        step_summary = f"Step {step.step_number}: {step.title}"
        # Structured execution results for this step — emitted by the
        # EXECUTE branch so the frontend can render an execution card
        # (command, sandbox, exit_code, stdout, stderr, duration) rather
        # than the plain ``summary`` string.
        step_executions: List[Dict[str, Any]] = []

        for file in step.files:
            try:
                if file.action == "CREATE":
                    # SIMPLIFIED PROMPT for small LLMs
                    create_prompt = (
                        f"Write the content for a new file: {file.path}\n"
                        f"Goal: {plan.goal}\n"
                        f"Context: {step.description[:300]}\n\n"
                        f"Return ONLY the file content, nothing else."
                    )

                    lite_agent = _crewai()["Agent"](
                        role="Code Writer",
                        goal="Write file content",
                        backstory="You write clean, working code.",
                        llm=llm, tools=[], verbose=agent_verbose(), allow_delegation=False,
                    )
                    task = _crewai()["Task"](
                        description=create_prompt,
                        expected_output=f"Content for {file.path}",
                        agent=lite_agent,
                    )
                    crew = _crewai()["Crew"](agents=[lite_agent], tasks=[task], process=_crewai()["Process"].sequential, verbose=agent_verbose())

                    def _create():
                        r = crew.kickoff()
                        return r.raw if hasattr(r, "raw") else str(r)

                    ctx = contextvars.copy_context()
                    content = await _guarded_agent_call(ctx, _create, label="create_file")
                    content = _strip_markdown_fences(content)

                    await put_file(owner, repo, file.path, content,
                                   f"GitPilot Lite: Create {file.path}", token=token, branch=branch_name)
                    step_summary += f"\n  + Created {file.path}"

                elif file.action == "MODIFY":
                    try:
                        existing = await get_file(owner, repo, file.path, token=token, ref=branch_name)
                        modify_prompt = (
                            f"Modify this file: {file.path}\n"
                            f"Goal: {plan.goal}\n"
                            f"What to change: {step.description[:300]}\n\n"
                            f"Current content:\n{existing[:2000]}\n\n"
                            f"Return the complete modified file content, nothing else."
                        )

                        lite_agent = _crewai()["Agent"](
                            role="Code Writer",
                            goal="Modify file content",
                            backstory="You write clean, working code.",
                            llm=llm, tools=[], verbose=agent_verbose(), allow_delegation=False,
                        )
                        task = _crewai()["Task"](description=modify_prompt, expected_output=f"Modified {file.path}", agent=lite_agent)
                        crew = _crewai()["Crew"](agents=[lite_agent], tasks=[task], process=_crewai()["Process"].sequential, verbose=agent_verbose())

                        def _modify():
                            r = crew.kickoff()
                            return r.raw if hasattr(r, "raw") else str(r)

                        ctx = contextvars.copy_context()
                        modified = await _guarded_agent_call(ctx, _modify, label="modify_file")
                        modified = modified.strip()
                        if modified.startswith("```"):
                            lines = modified.split("\n")
                            if lines[-1].strip() == "```":
                                modified = "\n".join(lines[1:-1])
                            else:
                                modified = "\n".join(lines[1:])

                        await put_file(owner, repo, file.path, modified,
                                       f"GitPilot Lite: Modify {file.path}", token=token, branch=branch_name)
                        step_summary += f"\n  ~ Modified {file.path}"
                    except Exception as e:
                        logger.exception("Lite: Failed to modify %s: %s", file.path, e)
                        step_summary += f"\n  ! Failed to modify {file.path}: {e}"

                elif file.action == "DELETE":
                    from .github_api import delete_file
                    try:
                        await delete_file(owner, repo, file.path,
                                          f"GitPilot Lite: Delete {file.path}", token=token, branch=branch_name)
                        step_summary += f"\n  - Deleted {file.path}"
                    except Exception as e:
                        logger.exception("Lite: Failed to delete %s: %s", file.path, e)
                        step_summary += f"\n  ! Failed to delete {file.path}: {e}"

                elif file.action == "READ":
                    step_summary += f"\n  i Inspected {file.path}"

                elif file.action == "INDEX":
                    # Batch B9 — INDEX is a special plan step that
                    # triggers the local RAG index build for this repo.
                    summary_line = await _execute_index_action(
                        owner, repo, token=token, branch_name=branch_name,
                    )
                    step_summary += f"\n  {summary_line}"

            except Exception as e:
                logger.exception("Lite: Error processing %s: %s", file.path, e)
                step_summary += f"\n  ! Error: {file.path}: {e}"

        step_record: Dict[str, Any] = {"step_number": step.step_number, "summary": step_summary}
        if step_executions:
            step_record["executions"] = step_executions
        execution_steps.append(step_record)

    return {
        "status": "completed",
        "message": f"Lite Mode: executed {len(plan.steps)} steps on {repo_full_name} (branch '{branch_name}')",
        "branch": branch_name,
        "branch_url": f"https://github.com/{repo_full_name}/tree/{branch_name}",
        "executionLog": {"steps": execution_steps},
        "lite_mode": True,
    }


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

    owner, repo = _split_repo_full_name(repo_full_name)
    execution_steps: list[dict] = []
    llm = _build_llm()

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
    _tools()["set_repo_context"](owner, repo, token=token, branch=branch_name)

    # Batch B12 — lean persona from agent_prompts when the flag is on.
    from . import agent_prompts as _ap
    _lean_writer = _ap.lean_prompts_enabled()

    code_writer = _crewai()["Agent"](
        role="Expert Code Writer",
        goal=_ap.CODE_WRITER_GOAL if _lean_writer else (
            "Generate high-quality, production-ready code and documentation based on requirements."
        ),
        backstory=_ap.CODE_WRITER_BACKSTORY if _lean_writer else (
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
        tools=_tools()["REPOSITORY_TOOLS"],
        verbose=True,
        allow_delegation=False,
    )

    for step in plan.steps:
        step_summary = f"Step {step.step_number}: {step.title}"
        # Structured execution results for this step — emitted by the
        # EXECUTE branch so the frontend can render an execution card
        # (command, sandbox, exit_code, stdout, stderr, duration) rather
        # than the plain ``summary`` string.
        step_executions: List[Dict[str, Any]] = []

        for file in step.files:
            try:
                if file.action == "CREATE":
                    if _lean_writer:
                        _create_description = _ap.render_create_file_task(
                            file_path=file.path,
                            goal=plan.goal,
                            step_description=step.description,
                        )
                    else:
                        _create_description = (
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
                        )
                    create_task = _crewai()["Task"](
                        description=_create_description,
                        expected_output=f"Complete content for {file.path}",
                        agent=code_writer,
                    )

                    def _create():
                        crew = _crewai()["Crew"](
                            agents=[code_writer],
                            tasks=[create_task],
                            process=_crewai()["Process"].sequential,
                            verbose=agent_verbose(),
                        )
                        result = crew.kickoff()
                        if hasattr(result, "raw"):
                            return result.raw
                        return str(result)

                    ctx = contextvars.copy_context()
                    content = await _guarded_agent_call(ctx, _create, label="exec_create_file")
                    content = _strip_markdown_fences(content)

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

                        modify_task = _crewai()["Task"](
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
                            crew = _crewai()["Crew"](
                                agents=[code_writer],
                                tasks=[modify_task],
                                process=_crewai()["Process"].sequential,
                                verbose=agent_verbose(),
                            )
                            result = crew.kickoff()
                            if hasattr(result, "raw"):
                                return result.raw
                            return str(result)

                        ctx = contextvars.copy_context()
                        modified_content = await _guarded_agent_call(ctx, _modify, label="exec_modify_file")

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

                elif file.action == "EXECUTE":
                    # Pull the file's content from the active branch and
                    # ship it to whichever sandbox the user selected in
                    # Settings → Sandbox (Local subprocess or MatrixLab
                    # Runner). Both reach the runner through the same
                    # /api/sandbox/run handler so behaviour matches the
                    # Run button on chat code blocks.
                    #
                    # The structured ``execution`` dict appended to the
                    # step record is what the frontend renders as an
                    # execution card (command, sandbox, exit code,
                    # stdout, stderr, duration) instead of a plain
                    # answer string.
                    execution_card: Dict[str, Any] = {
                        "path": file.path,
                        "status": "pending",
                    }
                    try:
                        content = await get_file(
                            owner, repo, file.path, token=token, ref=branch_name,
                        )
                        ext = file.path.rsplit(".", 1)[-1].lower() if "." in file.path else ""
                        lang_map = {
                            "py": "python", "js": "javascript", "mjs": "javascript",
                            "cjs": "javascript", "sh": "bash", "bash": "bash",
                        }
                        language = lang_map.get(ext)
                        command_str = {
                            "python": f"python {file.path}",
                            "javascript": f"node {file.path}",
                            "bash": f"bash {file.path}",
                        }.get(language or "")
                        execution_card.update(
                            language=language,
                            command=command_str,
                        )
                        if language is None:
                            execution_card.update(status="skipped", reason=(
                                f"extension {ext!r} is not runnable in the sandbox"
                            ))
                            step_summary += (
                                f"\n  ⚠️ EXECUTE skipped: {file.path} extension "
                                f"{ext!r} is not runnable in the sandbox"
                            )
                        else:
                            # Matplotlib's default backend opens a window — that
                            # hangs forever in a headless sandbox.  Prepend an
                            # ``import os`` shim that forces the Agg backend so
                            # plots are renderable to file but plt.show() is a
                            # no-op.  Only injected when we can see matplotlib
                            # imports so non-plotting scripts run untouched.
                            shipped_code = content
                            if language == "python" and _looks_like_matplotlib(content):
                                shipped_code = (
                                    'import os as _gp_os\n'
                                    '_gp_os.environ.setdefault("MPLBACKEND", "Agg")\n'
                                ) + content
                                execution_card["matplotlib_shim"] = True

                            from .sandbox_api import (
                                SandboxRunRequest,
                                api_sandbox_run,
                            )
                            result = await api_sandbox_run(
                                SandboxRunRequest(language=language, code=shipped_code),
                            )
                            execution_card.update(
                                status="completed" if result.exit_code == 0 else "failed",
                                sandbox=result.backend,
                                exit_code=result.exit_code,
                                stdout=result.stdout or "",
                                stderr=result.stderr or "",
                                duration_ms=result.duration_ms,
                                truncated=bool(result.truncated),
                                timed_out=bool(result.timed_out),
                            )
                            ok_glyph = "✓" if result.exit_code == 0 else "✗"
                            step_summary += (
                                f"\n  {ok_glyph} EXECUTE {file.path} on "
                                f"{result.backend} (exit {result.exit_code}, "
                                f"{result.duration_ms} ms)"
                            )
                            if result.stdout:
                                stdout_preview = result.stdout.strip()
                                if len(stdout_preview) > 1000:
                                    stdout_preview = stdout_preview[:1000] + "…"
                                step_summary += f"\n     stdout: {stdout_preview}"
                            if result.stderr:
                                stderr_preview = result.stderr.strip()
                                if len(stderr_preview) > 500:
                                    stderr_preview = stderr_preview[:500] + "…"
                                step_summary += f"\n     stderr: {stderr_preview}"
                    except HTTPException as exc:
                        execution_card.update(
                            status="failed",
                            error=f"{exc.detail} (HTTP {exc.status_code})",
                        )
                        step_summary += (
                            f"\n  ✗ EXECUTE {file.path} failed: "
                            f"{exc.detail} (HTTP {exc.status_code})"
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception(
                            "Failed to execute file %s in step %s: %s",
                            file.path, step.step_number, exc,
                        )
                        execution_card.update(status="failed", error=str(exc))
                        step_summary += f"\n  ✗ EXECUTE {file.path} failed: {exc}"
                    step_executions.append(execution_card)

                elif file.action == "INDEX":
                    # Batch B9 — triggers the per-repo RAG index build.
                    summary_line = await _execute_index_action(
                        owner, repo, token=token, branch_name=branch_name,
                    )
                    step_summary += f"\n  {summary_line}"

            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "Error processing file %s in step %s: %s",
                    file.path,
                    step.step_number,
                    e,
                )
                step_summary += f"\n  ✗ Error processing {file.path}: {str(e)}"

        step_record: Dict[str, Any] = {"step_number": step.step_number, "summary": step_summary}
        if step_executions:
            step_record["executions"] = step_executions
        execution_steps.append(step_record)

    # Suggest the obvious next action: any runnable file the plan
    # just created or modified gets a "Run <path>" CTA in the
    # completion card.  Deterministic — no LLM needed — so the
    # user never has to type "execute demo.py" after asking
    # GitPilot to create it.
    next_actions: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()
    for step in plan.steps:
        for pf in getattr(step, "files", []) or []:
            action = (getattr(pf, "action", None) or "").upper()
            path = getattr(pf, "path", None)
            if not path or path in seen_paths:
                continue
            if action in {"CREATE", "MODIFY"} and _is_runnable_path(path):
                seen_paths.add(path)
                # "Prepare Run" — not "Run" — because clicking opens an
                # ExecutionPlan approval card, not silent execution.
                # The wording is the single biggest enterprise-UX cue
                # that the click is consented, not autopilot.
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

    return {
        "status": "completed",
        "message": f"Successfully executed {len(plan.steps)} steps on {repo_full_name} in branch '{branch_name}'",
        "branch": branch_name,
        "branch_url": f"https://github.com/{repo_full_name}/tree/{branch_name}",
        "executionLog": {"steps": execution_steps},
        "next_actions": next_actions,
    }


# ============================================================================
# New Agent Builders (v2 upgrade)
# ============================================================================

def _build_issue_agent(llm) -> Agent:
    return _crewai()["Agent"](
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
        tools=_tools()["ISSUE_TOOLS"],
        verbose=True,
        allow_delegation=False,
    )


def _build_pr_agent(llm) -> Agent:
    return _crewai()["Agent"](
        role="Pull Request Management Specialist",
        goal="Create branches, commit changes, and manage pull requests",
        backstory=(
            "You are skilled in pull request workflows. You can create branches, "
            "create PRs from feature branches, list open PRs, inspect changed files, "
            "add reviews, and merge PRs using the appropriate strategy. "
            "You always verify the source and target branches before acting."
        ),
        llm=llm,
        tools=_tools()["PR_TOOLS"] + _tools()["WRITE_TOOLS"],
        verbose=True,
        allow_delegation=False,
    )


def _build_search_agent(llm) -> Agent:
    return _crewai()["Agent"](
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
        tools=_tools()["SEARCH_TOOLS"] + _tools()["REPOSITORY_TOOLS"],
        verbose=True,
        allow_delegation=False,
    )


def _build_code_review_agent(llm) -> Agent:
    return _crewai()["Agent"](
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
        tools=_tools()["REPOSITORY_TOOLS"] + _tools()["PR_TOOLS"] + _tools()["SEARCH_TOOLS"],
        verbose=True,
        allow_delegation=False,
    )


def _build_learning_agent(llm) -> Agent:
    return _crewai()["Agent"](
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
        tools=_tools()["REPOSITORY_TOOLS"] + _tools()["SEARCH_TOOLS"],
        verbose=True,
        allow_delegation=False,
    )


def _build_local_editor_agent(llm) -> Agent:
    """Phase 1: Agent for direct local file editing with verification."""
    return _crewai()["Agent"](
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
        tools=_tools()["LOCAL_FILE_TOOLS"] + _tools()["LOCAL_GIT_TOOLS"],
        verbose=True,
        allow_delegation=False,
    )


def _build_terminal_agent(llm) -> Agent:
    """Phase 1: Agent for sandboxed shell command execution."""
    return _crewai()["Agent"](
        role="Terminal & Shell Executor",
        goal="Execute shell commands safely in the workspace and report results",
        backstory=(
            "You are a terminal expert that runs shell commands in the "
            "sandbox the user picked in Settings (local subprocess by "
            "default, MatrixLab for containerised enterprise isolation). "
            "Both run_command and run_in_sandbox route through the same "
            "backend, so the user's runtime choice applies to your "
            "autonomous loop too — not just to the Run button in chat. "
            "Use run_command for workspace commands (tests, linters, "
            "builds) and run_in_sandbox(language, code) when you want "
            "to validate a self-contained snippet before returning it. "
            "Always report the exit code and surface stderr verbatim "
            "when a run fails: the trace is your debugging signal. "
            "You refuse destructive commands like 'rm -rf /' or 'mkfs'. "
        ),
        llm=llm,
        tools=_tools()["LOCAL_SHELL_TOOLS"] + _tools()["LOCAL_GIT_TOOLS"],
        verbose=True,
        allow_delegation=False,
    )


# ============================================================================
# Unified Dispatcher (v2 upgrade)
# ============================================================================

async def dispatch_request(
    user_request: Optional[str] = None,
    repo_full_name: Optional[str] = None,
    token: Optional[str] = None,
    branch_name: Optional[str] = None,
    topology_id: Optional[str] = None,
    # -----------------------------------------------------------------
    # Backwards-compatible keyword arguments.
    # Older callers (notably early WebSocket and A2A adapters) used:
    #   dispatch_request(repo_owner=..., repo_name=..., message=...)
    # Keeping these kwargs prevents crashes when frontend/backend drift.
    # -----------------------------------------------------------------
    repo_owner: Optional[str] = None,
    repo_name: Optional[str] = None,
    message: Optional[str] = None,
    **_ignored_kwargs: Any,
) -> Dict[str, Any]:
    """Route a free-form user request to the appropriate agent(s) and return the result.

    This is the single entry-point for the new conversational mode.  For backwards
    compatibility the original ``generate_plan`` / ``execute_plan`` pair is still
    available and untouched.

    If *topology_id* is supplied, topology-aware routing is used:
      - ``classify_and_dispatch`` → falls through to the existing agent_router
      - ``always_main_agent`` → all requests go to the primary agent (T2)
      - ``fixed_sequence`` → a CrewAI sequential crew is built from the
        topology's agent sequence (T3-T7)

    When *topology_id* is ``None``, behaviour is identical to the original v2
    dispatcher.
    """
    # ---- Input normalization / compat layer ----
    if user_request is None and message is not None:
        user_request = message
    if repo_full_name is None and repo_owner and repo_name:
        repo_full_name = f"{repo_owner}/{repo_name}"

    if not user_request:
        raise ValueError("dispatch_request: missing user_request (or legacy 'message')")
    if not repo_full_name:
        raise ValueError("dispatch_request: missing repo_full_name (or legacy repo_owner/repo_name)")

    # ---------- Lite Mode check (additive, non-destructive) ----------
    # Lite mode activates if ANY of:
    #   - the explicit setting is on
    #   - the topology is "lite_mode"
    #   - the active model is incompatible with multi-agent ReAct prompts
    #     (deepseek-r1, qwq, small Ollama models)
    from .settings import get_settings as _get_settings

    _current_settings = _get_settings()
    _saved_topology = get_saved_topology_preference()

    # Explicit topology_id must always win.
    if topology_id:
        _resolved_tid = topology_id
    else:
        _resolved_tid = _saved_topology

    # Auto-detect models that can't handle multi-agent ReAct format
    # (deepseek-r1, qwq, small local models) — route them to Lite Mode
    # regardless of explicit settings.
    _auto_lite = _is_incompatible_model(_current_settings)

    # Lite mode only applies when explicitly selected or globally enabled,
    # and it must not override an explicit non-lite topology choice.
    _lite_active = (
        _current_settings.lite_mode
        or _resolved_tid == "lite_mode"
        or _auto_lite
    )

    # Do not force lite mode when the caller explicitly requested another topology.
    if topology_id and topology_id != "lite_mode":
        _lite_active = False

    if _auto_lite and _lite_active:
        logger.info(
            "[GitPilot] Auto-routed to Lite Mode: active model is incompatible "
            "with multi-agent ReAct (deepseek-r1, qwq, or small local model)"
        )

    if _lite_active:
        logger.info("[GitPilot Lite] Lite Mode active — using simplified single-agent path")
        plan = await generate_plan_lite(
            user_request,
            repo_full_name,
            token=token,
            branch_name=branch_name,
        )
        return {
            "category": "plan_execute",
            "workflow": "plan_execute",
            "plan": plan.model_dump() if hasattr(plan, "model_dump") else plan,
            "message": "Lite Mode: Plan generated. Review and approve to execute.",
            "lite_mode": True,
        }

    _active_topology = None
    if _resolved_tid:
        _active_topology = get_topology(_resolved_tid)

    # ---------- Topology-aware routing (additive) ----------
    _active_topology = None
    _resolved_tid = topology_id or get_saved_topology_preference()
    if _resolved_tid:
        _active_topology = get_topology(_resolved_tid)

    if _active_topology and _active_topology.routing_policy.strategy == RoutingStrategy.fixed_sequence:
        # Pipeline topologies (T3-T7): build a multi-task sequential crew
        return await _dispatch_pipeline(
            _active_topology, user_request, repo_full_name,
            token=token, branch_name=branch_name,
        )

    # For ``classify_and_dispatch`` (T1/default) and ``always_main_agent`` (T2)
    # we fall through to the existing routing.  T2's react_loop execution will
    # be wired in a future phase; for now it uses the same single-task path
    # but the *visualization* already shows the correct graph.

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
        owner, repo = _split_repo_full_name(repo_full_name)
        active_ref = branch_name or "HEAD"
        _tools()["set_repo_context"](owner, repo, token=token, branch=active_ref)

    llm = _build_llm()

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
    task = _crewai()["Task"](
        description=task_description,
        expected_output="A clear, structured response addressing the user request",
        agent=primary_agent,
    )

    crew = _crewai()["Crew"](
        agents=agents,
        tasks=[task],
        process=_crewai()["Process"].sequential,
        verbose=True,
    )

    def _run():
        result = crew.kickoff()
        if hasattr(result, "raw"):
            return result.raw
        return str(result)

    ctx = contextvars.copy_context()
    result_text = await _guarded_agent_call(ctx, _run, label="dispatch")

    return {
        "category": workflow.category.value,
        "agents_used": [a.value for a in workflow.agents],
        "result": result_text,
        "entity_number": workflow.entity_number,
    }


# ============================================================================
# Topology Pipeline Dispatcher (additive — T3-T7)
# ============================================================================

# Maps topology agent IDs to AgentType enum + task descriptions.
# This bridge lets the topology registry reference agents by string ID while
# reusing the existing _get_agent() builders.
_TOPO_AGENT_MAP = {
    "explorer":   (AgentType.EXPLORER,      "Explore the codebase: map project structure, discover relevant files, "
                                             "identify patterns, dependencies, and test conventions. "
                                             "Return a structured analysis with file paths and key findings."),
    "planner":    (AgentType.PLANNER,       "Based on the exploration results, create a detailed implementation plan. "
                                             "Include: files to modify, files to create, step-by-step order, "
                                             "and test strategy. Consider trade-offs and alternatives."),
    "developer":  (AgentType.CODE_WRITER,   "Execute the implementation plan step by step. For each step: "
                                             "make the code change, then run tests. If tests fail, fix the issue "
                                             "before moving to the next step. Follow project coding standards."),
    "reviewer":   (AgentType.CODE_REVIEWER, "Review all code changes. Check for: security vulnerabilities, "
                                             "code quality, test coverage, performance issues. "
                                             "Organise findings by severity: Critical, Warning, Suggestion."),
    "git_agent":  (AgentType.PR_MANAGER,    "Create a branch, commit all changes with a descriptive message, "
                                             "push the branch, and create a GitHub PR. PR should summarise "
                                             "the changes clearly with a test plan."),
}


async def _dispatch_pipeline(
    topology,
    user_request: str,
    repo_full_name: str,
    token: Optional[str] = None,
    branch_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a topology's fixed-sequence pipeline as a multi-task CrewAI crew.

    Each agent in the sequence gets its own Task.  Tasks are linked via
    CrewAI's ``context`` parameter so the output of step N feeds step N+1.
    """
    sequence = topology.routing_policy.sequence or []
    if not sequence:
        return {"error": "Topology has no agent sequence defined"}

    # Determine if this pipeline has write-capable agents
    _write_agents = {"developer", "git_agent"}
    _has_writers = bool(set(sequence) & _write_agents)

    # Create a working branch for pipelines that modify files
    pipeline_branch = branch_name
    if repo_full_name and _has_writers and not branch_name:
        import re as _re
        import time as _time
        owner, repo = _split_repo_full_name(repo_full_name)
        sanitized = _re.sub(r"[^a-z0-9-]+", "-", user_request.lower())[:35].strip("-")
        timestamp = str(int(_time.time()))[-6:]
        pipeline_branch = f"gitpilot-{topology.id}-{sanitized}-{timestamp}"
        try:
            from .github_api import create_branch
            await create_branch(owner, repo, pipeline_branch, from_ref="HEAD", token=token)
            logger.info("[Pipeline] Created branch: %s", pipeline_branch)
        except Exception:
            pass  # branch may already exist

    # Set repo context (on the working branch)
    if repo_full_name:
        owner, repo = _split_repo_full_name(repo_full_name)
        active_ref = pipeline_branch or "HEAD"
        _tools()["set_repo_context"](owner, repo, token=token, branch=active_ref)

    llm = _build_llm()

    # Build agents and tasks
    agents = []
    tasks = []
    for i, agent_id in enumerate(sequence):
        mapping = _TOPO_AGENT_MAP.get(agent_id)
        if not mapping:
            logger.warning("[GitPilot] Unknown topology agent ID: %s — skipping", agent_id)
            continue
        agent_type, base_description = mapping
        agent = _get_agent(agent_type, llm)
        agents.append(agent)

        # Build task description: combine base description with user request
        task_desc = (
            f"User request: {user_request}\n"
            f"Repository: {repo_full_name}\n"
        )
        if pipeline_branch:
            task_desc += f"Branch: {pipeline_branch}\n"
        task_desc += f"\nYour role in this pipeline: {base_description}"

        # Tell write-capable agents to actually use their tools
        if agent_id in _write_agents and pipeline_branch:
            task_desc += (
                f"\n\nIMPORTANT: You have tools to write and delete files. "
                f"USE THEM to make real changes on branch '{pipeline_branch}'. "
                f"Do NOT just describe changes — actually write/delete files using your tools."
            )

        # Context chaining: each task after the first receives prior tasks
        context = tasks[:] if tasks else []

        task = _crewai()["Task"](
            description=task_desc,
            expected_output=f"Structured output from the {agent_id} phase",
            agent=agent,
            context=context if context else None,
        )
        tasks.append(task)

    if not agents:
        return {"error": "No valid agents could be built for this topology"}

    # Load optional context pack
    _ctx_pack = ""
    if repo_full_name:
        try:
            from pathlib import Path as _P
            _owner, _repo = repo_full_name.split("/")
            _ws = _P.home() / ".gitpilot" / "workspaces" / _owner / _repo
            _ctx_pack = build_context_pack(_ws, query=user_request)
        except Exception:
            pass
    if _ctx_pack:
        # Append context pack to the first task's description
        tasks[0].description += "\n\n" + _ctx_pack

    crew = _crewai()["Crew"](
        agents=agents,
        tasks=tasks,
        process=_crewai()["Process"].sequential,
        verbose=True,
    )

    def _run():
        result = crew.kickoff()
        if hasattr(result, "raw"):
            return result.raw
        return str(result)

    ctx = contextvars.copy_context()
    result_text = await _guarded_agent_call(ctx, _run, label="topology_pipeline")

    response = {
        "category": "topology_pipeline",
        "topology_id": topology.id,
        "topology_name": topology.name,
        "execution_style": topology.execution_style.value,
        "agents_used": sequence,
        "result": result_text,
    }

    # Add branch info for pipelines that created a working branch
    if pipeline_branch and _has_writers:
        response["branch"] = pipeline_branch
        response["branch_url"] = f"https://github.com/{repo_full_name}/tree/{pipeline_branch}"

    return response


def _get_agent(agent_type: AgentType, llm) -> Agent:
    """Instantiate an agent by type."""
    builders = {
        AgentType.EXPLORER: lambda: _crewai()["Agent"](
            role="Repository Explorer",
            goal="Thoroughly explore and document the current state of the repository",
            backstory="You are a meticulous code archaeologist who explores repositories.",
            llm=llm,
            tools=_tools()["REPOSITORY_TOOLS"],
            verbose=True,
            allow_delegation=False,
        ),
        AgentType.PLANNER: lambda: _crewai()["Agent"](
            role="Repository Refactor Planner",
            goal="Design safe, step-by-step refactor plans",
            backstory="You are an experienced staff engineer who creates plans based on facts.",
            llm=llm,
            tools=_tools()["REPOSITORY_TOOLS"],
            verbose=True,
            allow_delegation=False,
        ),
        AgentType.CODE_WRITER: lambda: _crewai()["Agent"](
            role="Expert Code Writer",
            goal="Generate high-quality, production-ready code and write it to the repository",
            backstory=(
                "You are a senior software engineer with multi-language expertise. "
                "You read existing files, write new code, and update files directly "
                "in the repository using your tools. Always read a file before modifying it."
            ),
            llm=llm,
            tools=_tools()["REPOSITORY_TOOLS"] + _tools()["WRITE_TOOLS"],
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

    owner, repo = _split_repo_full_name(repo_full_name)

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
# Flow Definition (v3 -- topology-aware with legacy fallback)
# ============================================================================

async def get_flow_definition(topology_id: Optional[str] = None) -> dict:
    """Return the agent workflow as a visual graph.

    When *topology_id* is provided (or a saved preference exists), the graph
    is served from the topology registry.  Otherwise the original hardcoded
    graph is returned for backward compatibility.
    """
    tid = topology_id or get_saved_topology_preference()
    if tid:
        return get_topology_graph(tid)

    # Legacy hardcoded graph (unchanged from v2)
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
