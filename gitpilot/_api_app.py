# gitpilot/api.py

from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Query, Path as FPath, Header, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .version import __version__
# Batch P1-D — error-envelope decorator (opt-in via the `error_envelope` flag).
# Re-exported here so endpoint authors can `@wrap_errors_envelope` without
# reaching into the implementation module.  Importing the symbol is a no-op
# when the flag is off, so this is fully backwards compatible.
from .commit_attribution import with_attribution
from .errors import GitPilotError, wrap_errors_envelope  # noqa: F401
from .github_api import (
    list_user_repos,
    list_user_repos_paginated,  # Pagination support
    search_user_repos,  # Search across all repos
    get_repo_tree,
    get_file,
    put_file,
    execution_context,
    github_request,
)
from .github_app import check_repo_write_access
from .settings import AppSettings, get_settings, set_provider, update_settings, autoconfigure_local_provider, LLMProvider
from .agentic import (
    generate_plan,
    execute_plan,
    generate_plan_lite,
    execute_plan_lite,
    PlanResult,
    get_flow_definition,
    dispatch_request,
    create_pr_after_execution,
)
from .agent_router import route as route_request
from . import github_issues
from . import github_pulls
from . import github_search
from .session import SessionManager, Session
from .hooks import HookManager, HookEvent
from .permissions import PermissionManager, PermissionMode
from .memory import MemoryManager
from .context_vault import ContextVault
from .use_case import UseCaseManager
from .mcp_client import MCPClient
from .plugins import PluginManager
from .skills import SkillManager
from .smart_model_router import ModelRouter, ModelRouterConfig
from .topology_registry import (
    list_topologies as _list_topologies,
    get_topology_graph as _get_topology_graph,
    classify_message as _classify_message,
    get_saved_topology_preference,
    save_topology_preference,
)
import httpx
import logging
from fastapi import HTTPException

logger = logging.getLogger(__name__)

def _is_small_local_model() -> bool:
    """Detect if the active provider is Ollama/OllaBridge with a model
    that can't handle multi-agent CrewAI prompts reliably.

    Delegates to agentic._is_incompatible_model (single source of truth)
    so that /api/chat/plan, /api/chat/execute, and /ws/sessions/ all
    share the same detection logic.
    """
    try:
        from .agentic import _is_incompatible_model
        s = autoconfigure_local_provider()
        return _is_incompatible_model(s)
    except Exception as exc:
        logger.debug("[GitPilot] _is_small_local_model check failed: %s", exc)
        return False


def _is_lite_mode_active() -> bool:
    """Check if Lite Mode should be used.

    Returns True if ANY of:
    - settings.lite_mode is True (explicit toggle), OR
    - the saved topology preference is "lite_mode" (selected in flow viewer), OR
    - the active provider is a small local model that cannot handle
      multi-agent CrewAI prompts (auto-detected for reliability)
    """
    s = autoconfigure_local_provider()
    if s.lite_mode:
        return True
    pref = get_saved_topology_preference()
    if pref == "lite_mode":
        return True
    # Auto-route small local models to lite mode for reliability
    if _is_small_local_model():
        logger.info("[GitPilot] Auto-enabling Lite Mode for small local model")
        return True
    return False
# ═════════════════════════════════════════════════════════════════════
# LAZY IMPORT STRATEGY — Phase 3 heavy modules
# ═════════════════════════════════════════════════════════════════════
# agent_teams, learning, cross_repo, predictions, security, nl_database
# are deferred until first access via _LazyProxy. This saves 200-500ms
# on WSL cold start (each import triggers disk I/O + pydantic compilation).
# The proxy pattern means NO code changes are needed at call sites —
# _agent_team.plan_and_split(...) works identically to the original.
# NL database types are imported lazily at call site (see /api/nl-db endpoint)
from .github_oauth import (
    generate_authorization_url,
    exchange_code_for_token,
    validate_token,
    initiate_device_flow,
    poll_device_token,
    web_flow_available,
    AuthSession,
    GitHubUser,
)
import os
import logging
from .model_catalog import list_models_for_provider

# Optional A2A adapter (MCP ContextForge)
from .a2a_adapter import router as a2a_router

logger = logging.getLogger(__name__)


class _LazyProxy:
    """Lazy singleton proxy — instantiates the wrapped class on first attribute access.

    Used to defer heavy imports (agent_teams, learning, cross_repo, etc.) until
    they're actually needed, reducing backend startup time on slow filesystems
    (WSL, HF Spaces cold start).

    All attribute access is transparently forwarded to the underlying instance,
    so existing code like `_agent_team.plan_and_split(...)` works unchanged.
    """

    def __init__(self, module_path: str, class_name: str) -> None:
        object.__setattr__(self, "_module_path", module_path)
        object.__setattr__(self, "_class_name", class_name)
        object.__setattr__(self, "_instance", None)

    def _get_instance(self) -> object:
        inst = object.__getattribute__(self, "_instance")
        if inst is None:
            import importlib
            module = importlib.import_module(self._module_path, package=__package__)
            cls = getattr(module, self._class_name)
            inst = cls()
            object.__setattr__(self, "_instance", inst)
            logger.debug("[LazyProxy] Instantiated %s.%s on first access",
                         self._module_path, self._class_name)
        return inst

    def __getattr__(self, name: str):
        return getattr(self._get_instance(), name)

    def __setattr__(self, name: str, value):
        setattr(self._get_instance(), name, value)

    def __call__(self, *args, **kwargs):
        return self._get_instance()(*args, **kwargs)

    def __repr__(self) -> str:
        inst = object.__getattribute__(self, "_instance")
        if inst is None:
            return f"<_LazyProxy {self._module_path}.{self._class_name} (not yet loaded)>"
        return repr(inst)


# --- Phase 1 singletons (lightweight, instantiate eagerly) ---
_session_mgr = SessionManager()
_hook_mgr = HookManager()
_perm_mgr = PermissionManager()

# --- Phase 2 singletons (lightweight, instantiate eagerly) ---
_mcp_client = MCPClient()
_plugin_mgr = PluginManager()
_skill_mgr = SkillManager()
_model_router = ModelRouter()

# --- Phase 3 singletons (HEAVY, lazy-loaded) ---
# Each of these pulls in several MB of Python code and takes 50-200ms on WSL.
# Deferred via _LazyProxy until first endpoint call that actually uses them.
_agent_team = _LazyProxy(".agent_teams", "AgentTeam")
_learning_engine = _LazyProxy(".learning", "LearningEngine")
_cross_repo = _LazyProxy(".cross_repo", "CrossRepoAnalyzer")
_predictive_engine = _LazyProxy(".predictions", "PredictiveEngine")
_security_scanner = _LazyProxy(".security", "SecurityScanner")
_nl_engine = _LazyProxy(".nl_database", "NLQueryEngine")

import asyncio as _asyncio
import signal
from contextlib import asynccontextmanager

_shutdown_event = _asyncio.Event()


@asynccontextmanager
async def _lifespan(application: FastAPI):
    """Manage startup (pre-warm) and graceful shutdown."""
    import time as _time

    _startup_start = _time.monotonic()
    logger.info("═══════════════════════════════════════════════════")
    logger.info("🚀 [STARTUP] GitPilot backend initializing...")
    logger.info("═══════════════════════════════════════════════════")

    # -- Startup: pre-warm CrewAI in background ---
    async def _warmup():
        _t0 = _time.monotonic()
        logger.info("[STARTUP] ⏳ Phase 1/3: Waiting 2s for health endpoint...")
        await _asyncio.sleep(2)

        _t1 = _time.monotonic()
        logger.info("[STARTUP] ⏳ Phase 2/3: Importing CrewAI modules...")
        try:
            from .agentic import _crewai, _tools  # noqa: F811
            _crewai()
            _t_crewai = _time.monotonic() - _t1
            logger.info("[STARTUP] ✅ CrewAI imports complete in %.2fs", _t_crewai)

            _t2 = _time.monotonic()
            logger.info("[STARTUP] ⏳ Phase 3/3: Loading agent tools...")
            _tools()
            _t_tools = _time.monotonic() - _t2
            logger.info("[STARTUP] ✅ Agent tools loaded in %.2fs", _t_tools)

            _total = _time.monotonic() - _startup_start
            logger.info("═══════════════════════════════════════════════════")
            logger.info("[STARTUP] 🎉 Backend fully ready in %.2fs total", _total)
            logger.info("═══════════════════════════════════════════════════")
        except Exception as exc:
            _t_fail = _time.monotonic() - _t1
            logger.warning(
                "[STARTUP] ⚠️  CrewAI pre-warm failed after %.2fs (will retry on first request): %s",
                _t_fail, exc,
            )

        # Log memory usage after warmup
        try:
            import resource
            rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            logger.info("[STARTUP] 📊 Memory after warmup: %.1f MB RSS", rss_mb)
        except Exception:
            pass

    _asyncio.create_task(_warmup())

    # -- Graceful shutdown handler ---
    def _handle_signal(sig, _frame):
        logger.info("Received %s — initiating graceful shutdown", signal.Signals(sig).name)
        _shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle_signal)
        except (OSError, ValueError):
            pass  # not main thread or unsupported

    _ready_time = _time.monotonic() - _startup_start
    logger.info(
        "[STARTUP] ✅ FastAPI ready to accept requests after %.2fs "
        "(CrewAI warmup continues in background)",
        _ready_time,
    )

    yield

    # Cleanup on shutdown
    logger.info("[SHUTDOWN] GitPilot shutting down gracefully")
    _shutdown_event.set()


app = FastAPI(
    title="GitPilot API",
    version=__version__,
    description="Agentic AI assistant for GitHub repositories.",
    lifespan=_lifespan,
    # Disable the built-in Swagger/OpenAPI: schema generation currently 500s, and
    # /docs should point users at the product docs instead. See the redirect below.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/docs", include_in_schema=False)
@app.get("/redoc", include_in_schema=False)
async def _docs_redirect():
    """Send /docs (and /redoc) to the GitPilot product docs."""
    from fastapi.responses import RedirectResponse

    target = os.getenv("GITPILOT_DOCS_URL", "https://ruslanmv.com/gitpilot/")
    return RedirectResponse(url=target, status_code=307)


# ==========================================================================
# Optional A2A Adapter (MCP ContextForge)
# ==========================================================================
# This is feature-flagged and does not affect the existing UI/REST API unless
# explicitly enabled.
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


if _env_bool("GITPILOT_ENABLE_A2A", False):
    logger.info("A2A adapter enabled (mounting /a2a/* endpoints)")
    app.include_router(a2a_router)
else:
    logger.info("A2A adapter disabled (set GITPILOT_ENABLE_A2A=true to enable)")

# MCP Context Forge admin API (Settings → MCP Servers tab).
try:
    from .mcp_admin_api import router as mcp_admin_router

    app.include_router(mcp_admin_router)
    logger.info("MCP admin API enabled (mounting /api/mcp/* endpoints)")
except Exception:  # noqa: BLE001
    logger.exception("MCP admin API failed to mount; tab will show as unavailable")

# Sandbox runtime API (Settings → Sandbox runtime, Run button on chat
# code blocks).  Mounting is non-fatal so a partial deployment can still
# serve chat / planner endpoints if this module fails to import.
try:
    from .sandbox_api import router as sandbox_router

    app.include_router(sandbox_router)
    logger.info("Sandbox API enabled (mounting /api/sandbox/* endpoints)")
except Exception:  # noqa: BLE001
    logger.exception("Sandbox API failed to mount; Run button will be disabled")

# MatrixLab addon admin API (Settings → Sandbox → Install MatrixLab modal).
# Sits on top of /api/sandbox/* and normalises every response so the UI
# never has to interpret raw Docker / HTTP errors.  Same non-fatal mount
# pattern as the sandbox router.
try:
    from .matrixlab_admin_api import router as matrixlab_admin_router

    app.include_router(matrixlab_admin_router)
    logger.info("MatrixLab admin API enabled (mounting /api/matrixlab/* endpoints)")
except Exception:  # noqa: BLE001
    logger.exception("MatrixLab admin API failed to mount; install modal will be disabled")

# Coder API (the generic GitPilot repair pipeline over HTTP): POST /repair +
# GET /repair/health, gated by a bearer token (GITPILOT_API_TOKEN). This is
# what SelfRepair / matrix-maintainer call to turn a repair-plan into a
# dry-run patch preview. Non-fatal mount so the UI/chat still work if it fails.
try:
    from .repair_router import build_repair_router

    app.include_router(build_repair_router())
    logger.info("Coder API enabled (mounting /repair + /repair/health)")
except Exception:  # noqa: BLE001
    logger.exception("Coder API failed to mount; /repair will be unavailable")

# Matrix runs facade (the Matrix-native AI coder path): POST /api/v1/gitpilot/runs.
# Maps a signed Matrix Bundle + contract onto the repair pipeline, always denying
# the Matrix control files, gated by the A2A shared secret. Non-fatal mount.
try:
    from .matrix_runs_router import build_matrix_runs_alias_router, build_matrix_runs_router

    app.include_router(build_matrix_runs_router())
    # Local-bridge alias (/api/matrix/*) used by Matrix Builder's "Send to local
    # GitPilot": same handlers, second namespace.
    app.include_router(build_matrix_runs_alias_router())
    logger.info("Matrix runs API enabled (mounting /api/v1/gitpilot/* + /api/matrix/*)")
except Exception:  # noqa: BLE001
    logger.exception("Matrix runs API failed to mount; /api/v1/gitpilot/runs will be unavailable")

# GitPilot accounts (account-first identity: email/password + verification +
# sessions). Off by default so the existing bring-your-own-GitHub-token flow is
# unchanged; enable with GITPILOT_ENABLE_ACCOUNTS=true. See docs/auth.md.
if _env_bool("GITPILOT_ENABLE_ACCOUNTS", False):
    try:
        from .auth import build_account_router

        app.include_router(build_account_router())
        logger.info("Accounts API enabled (mounting /api/account/*)")
    except Exception:  # noqa: BLE001
        logger.exception("Accounts API failed to mount; /api/account/* will be unavailable")

# GitPilot-as-MCP-server (turns GitPilot into an MCP server other agents
# can drive). Off by default; mount only when GITPILOT_EXPOSE_MCP_SERVER=true.
try:
    from .mcp_server import MCPServerConfig as _GPMCPConfig

    _gp_mcp_config = _GPMCPConfig.from_env()
    if _gp_mcp_config.enabled:
        from . import mcp_server_bridge as _mcp_server_bridge

        _mcp_server_bridge.mount(app, _gp_mcp_config)
        logger.info(
            "GitPilot MCP server enabled (mounting %s)", _gp_mcp_config.mount_path
        )
    else:
        logger.info(
            "GitPilot MCP server disabled (set GITPILOT_EXPOSE_MCP_SERVER=true to enable)"
        )
except Exception:  # noqa: BLE001
    logger.exception("GitPilot MCP server failed to mount; check env config")

# ============================================================================
# CORS Configuration
# ============================================================================
# Enable CORS to allow frontend (local dev or Vercel) to connect to backend
allowed_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:5173")
allowed_origins = [origin.strip() for origin in allowed_origins_str.split(",")]

logger.info(f"CORS enabled for origins: {allowed_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────────────────────────
# Request timing middleware (logs slow startup requests for debugging)
# ──────────────────────────────────────────────────────────────────
@app.middleware("http")
async def _log_slow_requests(request, call_next):
    """Log any request that takes >1s to complete, with path and duration.

    This helps diagnose first-load slowness: if /api/status takes 8s on the
    first call but <100ms afterwards, we know the backend is doing lazy
    initialization on first request.
    """
    import time as _t
    _start = _t.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        _elapsed = _t.monotonic() - _start
        logger.error(
            "[HTTP] ❌ %s %s failed after %.2fs",
            request.method, request.url.path, _elapsed,
        )
        raise

    _elapsed = _t.monotonic() - _start
    # Only log slow requests to avoid spam (>1s is slow for health endpoints)
    if _elapsed > 1.0:
        logger.warning(
            "[HTTP] 🐢 %s %s took %.2fs (status=%s)",
            request.method, request.url.path, _elapsed, response.status_code,
        )
    elif _elapsed > 0.5:
        logger.info(
            "[HTTP] ⚠️  %s %s took %.2fs (status=%s)",
            request.method, request.url.path, _elapsed, response.status_code,
        )

    return response


def _project_context_to_text(project_context) -> str:
    if not project_context:
        return ""

    parts = []
    mode = getattr(project_context, "mode", None)
    repo_name = getattr(project_context, "repoName", None)
    branch = getattr(project_context, "branch", None)
    languages = getattr(project_context, "languages", []) or []
    manifests = getattr(project_context, "manifests", []) or []
    key_files = getattr(project_context, "keyFiles", []) or []
    readme_preview = getattr(project_context, "readmePreview", None)
    tree_summary = getattr(project_context, "treeSummary", []) or []

    if mode:
        parts.append(f"Mode: {mode}")
    if repo_name:
        parts.append(f"Repo: {repo_name}")
    if branch:
        parts.append(f"Branch: {branch}")
    if languages:
        parts.append("Languages: " + ", ".join(languages[:20]))
    if manifests:
        parts.append("Manifests: " + ", ".join(manifests[:20]))
    if key_files:
        parts.append("Key files: " + ", ".join(key_files[:30]))

    if tree_summary:
        rendered = []
        for entry in tree_summary[:200]:
            if isinstance(entry, dict):
                rendered.append(f"- {entry.get('type', 'file')}: {entry.get('path', '')}")
        if rendered:
            parts.append("Project tree:\n" + "\n".join(rendered))

    if readme_preview:
        parts.append("README preview:\n" + readme_preview)

    return "\n".join(parts)


def _working_set_to_text(working_set) -> str:
    if not working_set:
        return ""

    parts = []
    current_file = getattr(working_set, "currentFile", None)
    language_id = getattr(working_set, "languageId", None)
    current_selection = getattr(working_set, "currentSelection", None)
    open_tabs = getattr(working_set, "openTabs", []) or []
    related_files = getattr(working_set, "relatedFiles", []) or []

    if current_file:
        parts.append(f"Current file: {current_file}")
    if language_id:
        parts.append(f"Language: {language_id}")
    if open_tabs:
        parts.append("Open tabs: " + ", ".join(open_tabs[:12]))
    if related_files:
        parts.append("Related files: " + ", ".join(related_files[:12]))
    if current_selection:
        parts.append("Selected code:\n```\n" + current_selection + "\n```")

    return "\n".join(parts)


def _sanitize_relative_path(p: str) -> str | None:
    """Reject absolute paths, .. traversal, drive letters, and empty strings.

    Also strips LLM artifacts like "three_backticks_space" that some models
    produce instead of actual backtick characters.
    """
    import os
    import re as _re
    p = p.strip().strip("`\"'").strip()
    # Strip common LLM artifacts
    # Strip literal descriptions LLMs produce instead of actual backtick chars
    p = _re.sub(r"(?i)three[\s_+]*backtick[s]?[\s_+]*space[\s_+]*", "", p)
    p = _re.sub(r"(?i)three[\s_+]*\+[\s_+]*markdown[\s_+]*\+[\s_+]*space[\s_+]*\+?\s*", "", p)
    p = _re.sub(r"(?i)backtick[s]?[\s_+]*", "", p)
    p = _re.sub(r"(?i)triple[\s_+]*backtick[s]?[\s_+]*", "", p)
    p = _re.sub(r"(?i)fenced?[\s_+]*code[\s_+]*block[\s_+]*", "", p)
    p = p.strip()
    if not p:
        return None
    # Reject absolute / drive / UNC paths
    if os.path.isabs(p) or p.startswith("\\\\") or (len(p) >= 2 and p[1] == ":"):
        return None
    # Reject parent traversal
    parts = p.replace("\\", "/").split("/")
    if ".." in parts:
        return None
    # Normalise to forward slashes
    return "/".join(parts)


def _extract_edits_from_answer(answer: str) -> list[dict]:
    """Extract structured ProposedEdit objects from LLM markdown answers.

    Parses fenced code blocks where the filename appears on the opening
    fence line (e.g. ```python hello.py) — the format we instruct the
    LLM to use in _build_local_repo_aware_prompt.

    Falls back to matching "save as <filename>" / "create file <filename>"
    patterns paired with the nearest code block.

    Returns a list of dicts matching the ProposedEdit schema:
      [{"file": "hello.py", "kind": "create", "content": "...", "summary": "..."}]
    """
    import re

    edits: list[dict] = []
    seen_paths: set[str] = set()
    if not answer:
        return edits

    def _add(raw_path: str, content: str) -> None:
        path = _sanitize_relative_path(raw_path)
        if not path or path in seen_paths:
            return
        seen_paths.add(path)
        edits.append({
            "file": path,
            "kind": "create",
            "content": content.rstrip(),
            "summary": f"Create {path}",
        })

    # Pattern 1 (preferred): ```lang filepath\n...code...\n```
    blocks = re.findall(
        r"```(?:\w+)?\s+([^\n`]+?\.\w+)\s*\n(.*?)```",
        answer,
        re.DOTALL,
    )
    for filepath, content in blocks:
        _add(filepath, content)

    if edits:
        return edits

    # Pattern 2: non-standard format some LLMs produce
    # "```\npython filepath\n---\n...code...\n---\n```"
    # or just "python filepath\n---\n...code...\n" outside fences
    dash_blocks = re.findall(
        r"(?:```\n?)?(\w+)\s+([^\n]+?\.\w+)\s*\n-{3,}\n(.*?)\n-{3,}",
        answer,
        re.DOTALL,
    )
    for _lang, filepath, content in dash_blocks:
        _add(filepath, content)

    if edits:
        return edits

    # Pattern 3: "save this as `filename`" / "create a file called `filename`"
    # followed by a code block
    file_mentions = re.findall(
        r"(?:save\s+(?:this\s+)?(?:as|to|in)|create\s+(?:a\s+)?(?:file\s+)?(?:called|named)?)\s+[`\"']?([^\s`\"']+\.\w+)[`\"']?",
        answer,
        re.IGNORECASE,
    )
    code_blocks = re.findall(r"```\w*\n(.*?)```", answer, re.DOTALL)

    if file_mentions and code_blocks:
        for filename, content in zip(file_mentions, code_blocks):
            _add(filename, content)

    return edits


def _build_local_repo_aware_prompt(req, session) -> str:
    task_summary = getattr(getattr(req, "task_context", None), "summary", None)

    # System instructions — the file-output format uses triple-backtick
    # fences with the filepath on the opening line. We use a raw block
    # to avoid confusion when the prompt is joined with --- separators.
    system_block = (
        "You are GitPilot, a multi-agent AI coding assistant running in VS Code.\n"
        "Use the supplied repository metadata, working-set context, and user request to answer precisely.\n"
        "\n"
        "IMPORTANT FILE OUTPUT FORMAT:\n"
        "When you create or edit files, you MUST use triple-backtick fenced code blocks\n"
        "with the language AND the file path on the SAME opening line.\n"
        "\n"
        "Correct format (you MUST follow this exactly):\n"
        "\n"
        "  ```python hello.py\n"
        "  print('Hello, World!')\n"
        "  ```\n"
        "\n"
        "  ```typescript src/utils/validate.ts\n"
        "  export function validate(input: string): boolean {\n"
        "    return input.length > 0;\n"
        "  }\n"
        "  ```\n"
        "\n"
        "Rules:\n"
        "- The opening fence MUST be triple backticks followed by the language then the filepath.\n"
        "- The closing fence MUST be triple backticks on their own line.\n"
        "- Do NOT use --- separators or any other format.\n"
        "- Output the COMPLETE file content, not just a snippet.\n"
        "- For edits to existing files, output the full updated file.\n"
        "- Be explicit about which files to create or modify and why.\n"
        "- Prefer incremental, production-safe changes over large rewrites.\n"
        "\n"
        "RUNNABLE EXAMPLES (separate from file-output fences):\n"
        "When the user asks for a small example they could try out — "
        "\"write a hello-world\", \"give me a snippet that ...\", "
        "\"show me how to call X\" — emit the example as a fenced block "
        "with ONLY the language on the opening line (no filepath):\n"
        "\n"
        "  ```python\n"
        "  print('Hello, world!')\n"
        "  ```\n"
        "\n"
        "  ```javascript\n"
        "  console.log('Hello, world!');\n"
        "  ```\n"
        "\n"
        "  ```bash\n"
        "  echo 'Hello, world!'\n"
        "  ```\n"
        "\n"
        "The chat UI shows a per-block ▶ Run button next to these "
        "snippets and executes them in the user's selected sandbox "
        "(local subprocess or MatrixLab). Supported languages: python, "
        "javascript (or js/node), bash (or sh/shell). Keep snippets "
        "self-contained — they run in a fresh tempdir with no project "
        "files mounted — and short enough to read at a glance."
    )

    sections = [system_block]

    session_lines = [
        f"Session mode: {getattr(session, 'mode', None)}",
        f"Folder path: {getattr(session, 'folder_path', None)}",
        f"Repo root: {getattr(session, 'repo_root', None)}",
        f"Branch: {getattr(session, 'branch', None)}",
    ]

    valid_session_lines = [
        line for line in session_lines if line and not line.endswith(": None")
    ]
    if valid_session_lines:
        sections.append("Session context:\n" + "\n".join(valid_session_lines))

    project_txt = _project_context_to_text(getattr(req, "project_context", None))
    if project_txt:
        sections.append("Project context:\n" + project_txt)

    working_txt = _working_set_to_text(getattr(req, "working_set", None))
    if working_txt:
        sections.append("Working set:\n" + working_txt)

    if task_summary:
        sections.append("Task context:\n" + task_summary)

    sections.append("User request:\n" + req.message)

    return "\n\n---\n\n".join(sections)

def get_github_token(authorization: Optional[str] = Header(None)) -> Optional[str]:
    """
    Extract GitHub token from Authorization header.

    Supports formats:
    - Bearer <token>
    - token <token>
    - <token>
    """
    if not authorization:
        return None

    if authorization.startswith("Bearer "):
        return authorization[7:]
    elif authorization.startswith("token "):
        return authorization[6:]
    else:
        return authorization


# --- FIXED: Added default_branch to model ---
class RepoSummary(BaseModel):
    id: int
    name: str
    full_name: str
    private: bool
    owner: str
    default_branch: str = "main"  # <--- CRITICAL FIX: Defaults to main, but can be master/dev


class PaginatedReposResponse(BaseModel):
    """Response model for paginated repository listing."""
    repositories: List[RepoSummary]
    page: int
    per_page: int
    total_count: Optional[int] = None
    has_more: bool
    query: Optional[str] = None
    # False when no usable GitHub identity is configured yet. This is a normal
    # pre-link state (not an error): the UI shows a "Connect GitHub" prompt.
    github_connected: bool = True


class FileEntry(BaseModel):
    path: str
    type: str


class FileTreeResponse(BaseModel):
    files: List[FileEntry] = Field(default_factory=list)


class FileContent(BaseModel):
    path: str
    encoding: str = "utf-8"
    content: str


class CommitRequest(BaseModel):
    path: str
    content: str
    message: str


class CommitResponse(BaseModel):
    path: str
    commit_sha: str
    commit_url: Optional[str] = None


class SettingsResponse(BaseModel):
    provider: LLMProvider
    providers: List[LLMProvider]
    openai: dict
    claude: dict
    watsonx: dict
    ollama: dict
    ollabridge: dict
    langflow_url: str
    has_langflow_plan_flow: bool
    # Sandbox runtime selection — populated by settings_response_from.  The
    # field is Optional so older serialised payloads continue to validate
    # even though the runtime always writes a value today.
    sandbox: Optional[dict] = None


class ProviderModelsResponse(BaseModel):
    provider: LLMProvider
    models: List[str] = Field(default_factory=list)
    error: Optional[str] = None


class ProviderUpdate(BaseModel):
    provider: LLMProvider


class ChatPlanRequest(BaseModel):
    repo_owner: str
    repo_name: str
    goal: str
    branch_name: Optional[str] = None
    # Optional: when present, the planner invocation is recorded as a
    # Task on the active session so the right-sidebar Tasks panel can
    # trace it.  Older frontends that omit this field continue to work
    # — no task is recorded, no error raised.
    session_id: Optional[str] = None
    # Batch B9: set by the post-Reject "retry with grep" path so the
    # router suppresses RAG / INDEX recommendations on the next
    # attempt of the same goal.  Default False — older frontends are
    # unaffected.
    force_no_rag: bool = False


class ExecutePlanRequest(BaseModel):
    repo_owner: str
    repo_name: str
    plan: PlanResult
    branch_name: Optional[str] = None
    # Optional: when present, the active session's `branch` (and the
    # matching `repos[i].branch`) is updated to the branch the executor
    # actually wrote to, so reopening the session jumps to that branch
    # instead of the one it was created on.  Older frontends that omit
    # this field continue to work — no session update is attempted.
    session_id: Optional[str] = None


class AuthUrlResponse(BaseModel):
    authorization_url: str
    state: str


class AuthCallbackRequest(BaseModel):
    code: str
    state: str


class TokenValidationRequest(BaseModel):
    access_token: str


class UserInfoResponse(BaseModel):
    user: GitHubUser
    authenticated: bool


class RepoAccessResponse(BaseModel):
    can_write: bool
    app_installed: bool
    auth_type: str


# --- v2 Request/Response models ---

class ChatRequest(BaseModel):
    """Unified chat request for the conversational dispatcher."""
    repo_owner: str
    repo_name: str
    message: str
    branch_name: Optional[str] = None
    auto_pr: bool = False
    topology_id: Optional[str] = None  # Override topology for this request


class IssueCreateRequest(BaseModel):
    title: str
    body: Optional[str] = None
    labels: Optional[List[str]] = None
    assignees: Optional[List[str]] = None
    milestone: Optional[int] = None


class IssueUpdateRequest(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    state: Optional[str] = None
    labels: Optional[List[str]] = None
    assignees: Optional[List[str]] = None
    milestone: Optional[int] = None


class IssueCommentRequest(BaseModel):
    body: str


class PRCreateRequest(BaseModel):
    title: str
    head: str
    base: str
    body: Optional[str] = None
    draft: bool = False


class PRMergeRequest(BaseModel):
    merge_method: str = "merge"
    commit_title: Optional[str] = None
    commit_message: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    per_page: int = 30
    page: int = 1


# ============================================================================
# Repository Endpoints - Enterprise Grade with Pagination & Search
# ============================================================================

@app.get("/api/repos", response_model=PaginatedReposResponse)
async def api_list_repos(
    query: str | None = Query(None, description="Search query"),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=100),
    authorization: str | None = Header(None),
):
    token = get_github_token(authorization)

    try:
        if query:
            result = await search_user_repos(
                query=query,
                page=page,
                per_page=per_page,
                token=token,
            )
        else:
            result = await list_user_repos_paginated(
                page=page,
                per_page=per_page,
                token=token,
            )

        repos = [
            RepoSummary(
                id=r["id"],
                name=r["name"],
                full_name=r["full_name"],
                private=r["private"],
                owner=r["owner"],
                default_branch=r.get("default_branch", "main"),
            )
            for r in result["repositories"]
        ]

        return PaginatedReposResponse(
            repositories=repos,
            page=result["page"],
            per_page=result["per_page"],
            total_count=result.get("total_count"),
            has_more=result["has_more"],
            query=query,
            github_connected=True,
        )

    except HTTPException as he:
        # No usable GitHub identity (not linked yet, or token expired) is NOT an
        # error — it's a normal state for an email-only account. Return an empty,
        # successful result flagged github_connected=False so the UI can show a
        # friendly "Connect GitHub" prompt instead of a red error.
        if he.status_code == 401:
            return PaginatedReposResponse(
                repositories=[],
                page=page,
                per_page=per_page,
                total_count=0,
                has_more=False,
                query=query,
                github_connected=False,
            )
        raise

    except httpx.ConnectTimeout:
        logger.exception("GitHub connection timed out while fetching repositories")
        raise HTTPException(
            status_code=504,
            detail="Timed out while connecting to GitHub. Please try again."
        )

    except httpx.TimeoutException:
        logger.exception("GitHub request timed out while fetching repositories")
        raise HTTPException(
            status_code=504,
            detail="GitHub request timed out. Please try again."
        )

    except httpx.HTTPError as e:
        logger.exception("GitHub HTTP error while fetching repositories")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to contact GitHub: {str(e)}"
        )

    except Exception as e:
        logger.exception("Error fetching repositories")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error fetching repositories: {str(e)}"
        )

@app.get("/api/repos/all")
async def api_list_all_repos(
    query: Optional[str] = Query(None, description="Search query"),
    authorization: Optional[str] = Header(None),
):
    """
    Fetch ALL user repositories at once (no pagination).
    Useful for quick searches, but paginated endpoint is preferred.
    """
    token = get_github_token(authorization)

    try:
        # Fetch all repositories (this will make multiple API calls)
        all_repos = []
        page = 1
        max_pages = 15  # Safety limit: 1500 repos max (15 * 100)

        while page <= max_pages:
            result = await list_user_repos_paginated(
                page=page,
                per_page=100,
                token=token
            )

            all_repos.extend(result["repositories"])

            if not result["has_more"]:
                break

            page += 1

        # Filter by query if provided
        if query:
            query_lower = query.lower()
            all_repos = [
                r for r in all_repos
                if query_lower in r["name"].lower() or query_lower in r["full_name"].lower()
            ]

        # --- FIXED: Mapping default_branch ---
        repos = [
            RepoSummary(
                id=r["id"],
                name=r["name"],
                full_name=r["full_name"],
                private=r["private"],
                owner=r["owner"],
                default_branch=r.get("default_branch", "main"),  # <--- CRITICAL FIX
            )
            for r in all_repos
        ]

        return {
            "repositories": repos,
            "total_count": len(repos),
            "query": query,
        }

    except Exception as e:
        logging.exception("Error fetching all repositories")
        return JSONResponse(
            content={"error": f"Failed to fetch repositories: {str(e)}"},
            status_code=500
        )


@app.get("/api/repos/{owner}/{repo}/tree", response_model=FileTreeResponse)
async def api_repo_tree(
    owner: str = FPath(...),
    repo: str = FPath(...),
    ref: Optional[str] = Query(
        None,
        description="Git reference (branch, tag, or commit SHA). If omitted, defaults to HEAD.",
    ),
    authorization: Optional[str] = Header(None),
):
    """
    Get the file tree for a repository.
    Handles 'main' vs 'master' discrepancies and empty repositories gracefully.
    """
    token = get_github_token(authorization)

    # Keep legacy behavior: missing/empty ref behaves like HEAD.
    ref_value = (ref or "").strip() or "HEAD"

    try:
        tree = await get_repo_tree(owner, repo, token=token, ref=ref_value)
        return FileTreeResponse(files=[FileEntry(**f) for f in tree])

    except HTTPException as e:
        if e.status_code == 409:
            return FileTreeResponse(files=[])

        if e.status_code == 404:
            return JSONResponse(
                status_code=404,
                content={
                    "detail": f"Ref '{ref_value}' not found. The repository might be using a different default branch (e.g., 'master')."
                }
            )

        raise e


@app.get("/api/repos/{owner}/{repo}/file", response_model=FileContent)
async def api_get_file(
    owner: str = FPath(...),
    repo: str = FPath(...),
    path: str = Query(...),
    ref: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
):
    token = get_github_token(authorization)
    content = await get_file(owner, repo, path, token=token, ref=ref)
    return FileContent(path=path, content=content)


@app.post("/api/repos/{owner}/{repo}/file", response_model=CommitResponse)
async def api_put_file(
    owner: str = FPath(...),
    repo: str = FPath(...),
    payload: CommitRequest = ...,
    authorization: Optional[str] = Header(None),
):
    token = get_github_token(authorization)
    # Attribute the commit to GitPilot (Co-authored-by trailer) so it shows up as
    # a GitPilot contribution — like Claude Code. See gitpilot.commit_attribution.
    result = await put_file(
        owner, repo, payload.path, payload.content, with_attribution(payload.message), token=token
    )
    return CommitResponse(**result)


# ============================================================================
# Settings Endpoints
# ============================================================================

def settings_response_from(s: AppSettings) -> SettingsResponse:
    sandbox_dump = s.sandbox.model_dump()
    # Strip the secret value before it leaves the process — the frontend
    # only needs to know whether a token is configured, not the token
    # itself.  Keeps GET /api/settings safe to log and to surface in the
    # browser devtools.
    token = sandbox_dump.pop("matrixlab_token", "")
    sandbox_payload = {**sandbox_dump, "has_token": bool(token)}
    return SettingsResponse(
        provider=s.provider,
        providers=[
            LLMProvider.openai,
            LLMProvider.claude,
            LLMProvider.watsonx,
            LLMProvider.ollama,
            LLMProvider.ollabridge,
        ],
        openai=s.openai.model_dump(),
        claude=s.claude.model_dump(),
        watsonx=s.watsonx.model_dump(),
        ollama=s.ollama.model_dump(),
        ollabridge=s.ollabridge.model_dump(),
        langflow_url=s.langflow_url,
        has_langflow_plan_flow=bool(s.langflow_plan_flow_id),
        sandbox=sandbox_payload,
    )


@app.get("/api/settings", response_model=SettingsResponse)
async def api_get_settings():
    """
    Fast path:
    Return persisted settings immediately without probing providers/models.

    This keeps the Admin / LLM Settings page fast on first render.
    """
    s: AppSettings = get_settings()
    return settings_response_from(s)


@app.post("/api/settings/bootstrap", response_model=SettingsResponse)
async def api_bootstrap_settings():
    """
    Slow path:
    Perform local provider/model auto-configuration explicitly.

    This can be called after the page renders, or on startup, without blocking
    the first settings paint.
    """
    s: AppSettings = autoconfigure_local_provider()
    return settings_response_from(s)


@app.get("/api/settings/models", response_model=ProviderModelsResponse)
async def api_list_models(provider: Optional[LLMProvider] = Query(None)):
    """
    Return the list of LLM models available for a provider.

    If 'provider' is not given, use the currently active provider from settings.
    """
    s: AppSettings = get_settings()
    effective_provider = provider or s.provider

    models, error = list_models_for_provider(effective_provider, s)

    return ProviderModelsResponse(
        provider=effective_provider,
        models=models,
        error=error,
    )


@app.post("/api/settings/provider", response_model=SettingsResponse)
async def api_set_provider(update: ProviderUpdate):
    """
    Provider changes may legitimately trigger local bootstrap, but only when
    switching to local providers.
    """
    s = set_provider(update.provider)

    if s.provider in (LLMProvider.ollama, LLMProvider.ollabridge):
        s = autoconfigure_local_provider(force=True)

    return settings_response_from(s)


@app.put("/api/settings/llm", response_model=SettingsResponse)
async def api_update_llm_settings(updates: dict):
    """
    Update full LLM settings including provider-specific configs.

    Important:
    - Do NOT auto-probe providers here on every save.
    - Saving should be fast and deterministic.
    """
    s = update_settings(updates)
    return settings_response_from(s)

    """Update full LLM settings including provider-specific configs."""
    s = update_settings(updates)
    s = autoconfigure_local_provider()
    return SettingsResponse(
        provider=s.provider,
        providers=[LLMProvider.openai, LLMProvider.claude, LLMProvider.watsonx, LLMProvider.ollama, LLMProvider.ollabridge],
        openai=s.openai.model_dump(),
        claude=s.claude.model_dump(),
        watsonx=s.watsonx.model_dump(),
        ollama=s.ollama.model_dump(),
        ollabridge=s.ollabridge.model_dump(),
        langflow_url=s.langflow_url,
        has_langflow_plan_flow=bool(s.langflow_plan_flow_id),
    )


# ============================================================================
# Context-window meter
# ============================================================================

@app.get("/api/context/usage")
async def api_context_usage(session_id: Optional[str] = Query(None)):
    """Return a snapshot of the active model's context-window utilisation.

    When ``session_id`` is supplied, the ``messages`` row reflects the
    real token total of that session's persisted conversation.  Without
    it the row is 0 and the popover shows the structure-only view (still
    useful: tool schemas + system prompt + reserved are all populated).
    """
    from . import flags
    from .context_meter import (
        FLAG_CONTEXT_METER,
        build_usage,
        count_messages_tokens,
        count_system_prompt_tokens,
        count_tool_schema_tokens,
    )

    if not flags.is_on(FLAG_CONTEXT_METER, default=True):
        raise HTTPException(status_code=404, detail="Context meter is disabled")

    s: AppSettings = get_settings()
    lite_mode = _is_lite_mode_active()

    # Tool count + tool-schema tokens — best-effort, lazy import so we
    # don't pay the agent-tools cost on a settings-only client.  In lite
    # mode the planner doesn't see tools at all, so we report zero.
    tool_count = 0
    tool_lists: list[list[object]] = []
    if not lite_mode:
        try:
            from .agentic import _tools

            t = _tools()
            for key in (
                "REPOSITORY_TOOLS",
                "WRITE_TOOLS",
                "ISSUE_TOOLS",
                "PR_TOOLS",
                "SEARCH_TOOLS",
                "LOCAL_TOOLS",
            ):
                group = t.get(key) or []
                tool_lists.append(list(group))
                tool_count += len(group)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("[context-meter] tool count unavailable: %s", exc)

    tool_schema_tokens = count_tool_schema_tokens(tool_lists) if tool_lists else 0
    system_prompt_tokens = count_system_prompt_tokens(lite_mode=lite_mode)

    # Conversation messages — only when the caller passes a session_id.
    # Failure to load is silent: the popover stays useful with messages=0
    # rather than erroring on a freshly-created session.
    messages_tokens = 0
    if session_id:
        try:
            session = _session_mgr.load(session_id)
            messages_tokens = count_messages_tokens(session.messages)
        except Exception as exc:
            logger.debug(
                "[context-meter] session %s not loadable: %s", session_id, exc
            )

    # Repo context summary is computed fresh per plan and not cached
    # per-session, so we leave the row at 0.  When we add per-session
    # caching (planned), populate this from the cache.
    breakdown = {
        "messages": messages_tokens,
        "system_prompt": system_prompt_tokens,
        "repo_context": 0,
        "tool_schemas": tool_schema_tokens,
    }

    usage = build_usage(
        s,
        breakdown=breakdown,
        tool_count=tool_count,
        lite_mode=lite_mode,
    )
    return usage.to_dict()


# ============================================================================
# Chat Endpoints
# ============================================================================


def _track_task(*, kind: str, title_fn=None):
    """Decorator: wrap a chat endpoint so its run is recorded as a Task
    on the active session (right-sidebar trace).

    Reads ``session_id`` directly off the request model.  ``title_fn``
    is a small callable that derives the human title from the request
    object — keeps the decorator decoupled from any specific schema.
    Endpoints whose requests don't carry a session_id behave exactly
    as before — no Task is recorded, no error is raised.
    """
    import functools

    from .task_recorder import begin_task as _begin_task
    from .task_recorder import finish_task as _finish_task

    def _default_title(_req):
        return kind.title()

    extract_title = title_fn or _default_title

    def deco(handler):
        @functools.wraps(handler)
        async def wrapper(req, *args, **kwargs):
            session_id = getattr(req, "session_id", None)
            try:
                raw_title = extract_title(req)
            except Exception:
                raw_title = None
            title = (raw_title or kind.title())[:160]
            task = _begin_task(_session_mgr, session_id, kind=kind, title=title)
            status = "failed"
            err: Optional[str] = None
            try:
                result = await handler(req, *args, **kwargs)
                status = "completed"
                return result
            except HTTPException as exc:
                # HTTPException paths are still "failed" from the
                # tasks-panel point of view (the user did not get a
                # plan / commit).  Preserve the detail as the error.
                err = str(exc.detail) if exc.detail else None
                raise
            except Exception as exc:
                err = str(exc)
                raise
            finally:
                _finish_task(
                    _session_mgr,
                    session_id,
                    task,
                    status=status,
                    error=err,
                )
        return wrapper
    return deco


def _maybe_compact_session_for_request(session_id: Optional[str]) -> None:
    """Best-effort auto-compaction hook (Batch B3).

    Called at the start of /api/chat/plan + /api/chat/execute.  If the
    persisted session is over 70 % of the active model's context
    window, fold the older messages into a single summary entry and
    record a Task row so the user sees what happened.  A failure here
    must never block the agent run.
    """
    if not session_id:
        return
    try:
        from .auto_compact import maybe_compact_session
        from .context_meter import resolve_context_window
        from .task_recorder import begin_task, finish_task

        s = get_settings()
        window = resolve_context_window(s)
        report = maybe_compact_session(
            _session_mgr, session_id, context_window=window
        )
        if report.compacted:
            # Surface the compaction in the right-sidebar trace so the
            # operator can see "Conversation summarised 24 → 1" rather
            # than wonder where their messages went.
            task = begin_task(
                _session_mgr, session_id,
                kind="compact",
                title=(
                    f"Compacted: {report.messages_folded} older messages "
                    f"({report.before_tokens} → {report.after_tokens} tokens)"
                ),
            )
            finish_task(
                _session_mgr, session_id, task,
                status="completed",
                prompt_tokens=report.after_tokens,
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[compact] hook failed: %s", exc)


@app.post("/api/chat/plan")
@_track_task(kind="plan", title_fn=lambda req: req.goal)
async def api_chat_plan(req: ChatPlanRequest, authorization: Optional[str] = Header(None)):
    _maybe_compact_session_for_request(req.session_id)
    token = get_github_token(authorization)

    logger.info(
        "PLAN REQUEST: %s/%s | branch_name=%r",
        req.repo_owner,
        req.repo_name,
        req.branch_name,
    )

    with execution_context(token, ref=req.branch_name):
        full_name = f"{req.repo_owner}/{req.repo_name}"

        # Use lite planner when Lite Mode is active (setting OR topology)
        planner = generate_plan_lite if _is_lite_mode_active() else generate_plan

        # Batch B9 — deterministic query router.  Runs BEFORE the LLM
        # so even small models that pick poorly without guidance see
        # a strategy hint up front.  Best-effort: any failure falls
        # back to today's no-hint behaviour rather than 500-ing.
        routing_hint = None
        routing_intent: Optional[str] = None
        routing_targets: list[str] = []
        repo_paths: list[str] = []
        try:
            from . import flags as _flags
            if _flags.is_on("query_router", default=True):
                from .query_router import classify, render_planner_hint
                from .rag_consent import has_consent

                # Cheap path: a flat list of repo files for the
                # classifier's path-verification step.  Failure is
                # tolerated — router falls back to "no targets".
                try:
                    from .github_api import get_repo_tree
                    _tree = await get_repo_tree(
                        req.repo_owner, req.repo_name,
                        token=token, ref=req.branch_name,
                    )
                    repo_paths = [t["path"] for t in (_tree or []) if t.get("path")]
                except Exception:
                    pass

                rag_index_present = (
                    has_consent(req.repo_owner, req.repo_name)
                )

                decision = classify(
                    req.goal,
                    repo_files=repo_paths,
                    rag_index_exists=rag_index_present,
                    force_no_rag=bool(req.force_no_rag),
                )
                routing_hint = render_planner_hint(decision)
                routing_intent = decision.intent
                routing_targets = list(decision.target_files or [])
                logger.info("[router] %s", decision.rationale)

                # EXECUTE short-circuit — skip the LLM when "run this
                # file" is unambiguous.  Cheap, deterministic, and
                # avoids the small-LLM failure mode where the planner
                # tries to call EXECUTE as a CrewAI tool, fails, then
                # downgrades to READ — the bug pattern we documented
                # in agentic.try_execute_short_circuit's docstring.
                from .agentic import try_execute_short_circuit
                short = try_execute_short_circuit(
                    goal=req.goal,
                    intent=routing_intent,
                    target_files=decision.target_files or [],
                    repo_files=repo_paths,
                )
                if short is not None:
                    logger.info(
                        "[router] EXECUTE short-circuit: skipping LLM planner; "
                        "target=%s", short.steps[0].files[0].path,
                    )
                    return short
        except Exception as _route_err:  # pragma: no cover - defensive
            logger.debug("[router] skipped: %s", _route_err)
            routing_hint = None
            routing_intent = None

        try:
            plan = await planner(
                req.goal, full_name,
                token=token, branch_name=req.branch_name,
                routing_hint=routing_hint,
                intent=routing_intent,
            )

            # Belt-and-suspenders for the "execute" path: if the
            # short-circuit didn't fire pre-LLM (router didn't classify,
            # or no path candidates surfaced) and the planner came back
            # with an empty / answer-only plan, retry the short-circuit
            # post-LLM so the user gets a real EXECUTE action instead
            # of "execute the existing file" plain text.
            try:
                from .agentic import try_execute_short_circuit
                no_actionable = (
                    not getattr(plan, "steps", None)
                    or all(
                        not getattr(step, "files", None)
                        or all(
                            getattr(f, "action", None) in {None, "READ"}
                            for f in step.files
                        )
                        for step in plan.steps
                    )
                )
                if no_actionable:
                    goal_lower = req.goal.lower()
                    looks_like_execute = any(
                        kw in goal_lower
                        for kw in ("execute", "run the", "run hello",
                                   "run main", "run demo", "run my",
                                   "in the sandbox", "in sandbox",
                                   "please run", "can you run")
                    )
                    if looks_like_execute:
                        # ``repo_paths`` was computed during routing; if
                        # routing was skipped, fall back to a quick tree
                        # fetch so the short-circuit has a chance.
                        if not repo_paths:
                            try:
                                from .github_api import get_repo_tree
                                _tree = await get_repo_tree(
                                    req.repo_owner, req.repo_name,
                                    token=token, ref=req.branch_name,
                                )
                                repo_paths = [t["path"] for t in (_tree or []) if t.get("path")]
                            except Exception:
                                pass

                        # Try the explicitly-mentioned target first, then
                        # fall back to "single runnable in repo".
                        fallback = try_execute_short_circuit(
                            goal=req.goal, intent="execute",
                            target_files=routing_targets,
                            repo_files=repo_paths,
                        )
                        if fallback is not None:
                            logger.info(
                                "[router] post-LLM EXECUTE rescue: planner returned no "
                                "actionable steps for an execute-looking goal — "
                                "substituting deterministic plan for %s",
                                fallback.steps[0].files[0].path,
                            )
                            return fallback
            except Exception as _rescue_err:  # pragma: no cover - defensive
                logger.debug("[router] post-LLM execute rescue skipped: %s", _rescue_err)

            return plan
        except Exception as exc:
            error_msg = str(exc)

            # ── Quota / rate-limit detection ────────────────
            _quota_keywords = [
                "insufficient_quota", "exceeded your current quota",
                "rate_limit_exceeded", "429",
                "billing", "plan and billing",
            ]
            _is_quota = any(kw in error_msg.lower() for kw in _quota_keywords)
            if _is_quota:
                logger.warning("[GitPilot] LLM quota/rate-limit error: %s", error_msg)
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Your LLM provider credits have been exhausted or you've hit "
                        "a rate limit. Please check your plan and billing details at "
                        "your provider's dashboard, or switch to a different provider "
                        "in Settings (e.g. Ollama or OllaBridge for free local models)."
                    ),
                ) from exc

            # ── Empty/invalid LLM response (small model can't follow ReAct) ─
            _empty_llm_errors = (
                "No valid task outputs",
                "Invalid response from LLM call",
                "None or empty",
            )
            if any(kw in error_msg for kw in _empty_llm_errors):
                logger.warning(
                    "[GitPilot] LLM returned empty/invalid response — "
                    "model may be too small for multi-agent CrewAI prompts: %s",
                    error_msg,
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "The LLM could not complete the multi-agent reasoning. "
                        "This usually happens with small local models "
                        "(qwen2.5:0.5b, tinyllama, phi3:mini, etc.) that struggle "
                        "with the ReAct format. Solutions:\n"
                        "• Switch to a larger model (llama3, qwen2.5:7b, mistral)\n"
                        "• Enable Lite Mode in Settings for simpler prompts\n"
                        "• Use a cloud provider (OpenAI, Claude) for complex tasks"
                    ),
                ) from exc

            # ── Structured-output parse failure (common with small models) ─
            # New markers match the friendly RuntimeError surfaces we
            # raise in gitpilot/agentic.py::generate_plan for refusal /
            # ValidationError / tool-loop hallucination paths.  Catching
            # them here routes the user to the single-agent Lite planner
            # automatically — much better than the previous outcome where
            # those RuntimeErrors leaked through as raw HTTP 500.
            _plan_parse_markers = (
                "validation error for planresult",
                "json_invalid",
                "invalid json: key must be a string",
                "did not return a valid plan structure",
                "did not return a usable result",
                "the planner refused to produce a plan",
                "the planner produced paths that do not match",
            )
            if any(marker in error_msg.lower() for marker in _plan_parse_markers):
                logger.warning(
                    "[GitPilot] Planner returned malformed structured output. "
                    "Falling back to Lite planner. Error: %s",
                    error_msg,
                )
                try:
                    return await generate_plan_lite(
                        req.goal,
                        full_name,
                        token=token,
                        branch_name=req.branch_name,
                        routing_hint=routing_hint,
                        intent=routing_intent,
                    )
                except Exception as lite_exc:
                    logger.exception(
                        "[GitPilot] Lite planner fallback also failed after parse error: %s",
                        lite_exc,
                    )
                    # Surface a clear 502 with actionable guidance rather
                    # than leaking the raw RuntimeError as a generic 500.
                    raise HTTPException(
                        status_code=502,
                        detail=(
                            "The planner couldn't produce a usable plan even "
                            "with the simplified Lite-mode fallback.  This is "
                            "almost always a small-model issue — the LLM is "
                            "looping on tool calls or losing its instruction "
                            "format mid-task.  Solutions:\n"
                            "• Switch to a larger Ollama model (llama3.1:8b → "
                            "llama3.1:70b, qwen2.5:14b+, mistral)\n"
                            "• Use a cloud provider (OpenAI, Claude) for "
                            "complex multi-step tasks\n"
                            "• Try simplifying the request (one file at a time)"
                        ),
                    ) from lite_exc

            # Anything else — surface a clean 500 with a clear message
            # so the UI's existing error handler renders something
            # actionable instead of a bare "Internal Server Error".
            logger.exception("[GitPilot] /api/chat/plan failed: %s", error_msg)
            raise HTTPException(
                status_code=500,
                detail=error_msg or "Plan generation failed.",
            ) from exc


@app.post("/api/chat/execute")
@_track_task(
    kind="execute",
    title_fn=lambda req: getattr(getattr(req, "plan", None), "goal", None) or "Execute plan",
)
async def api_chat_execute(
    req: ExecutePlanRequest,
    authorization: Optional[str] = Header(None)
):
    _maybe_compact_session_for_request(req.session_id)
    token = get_github_token(authorization)

    with execution_context(token, ref=req.branch_name):
        full_name = f"{req.repo_owner}/{req.repo_name}"
        executor = execute_plan_lite if _is_lite_mode_active() else execute_plan
        try:
            result = await executor(
                req.plan, full_name, token=token, branch_name=req.branch_name
            )
        except Exception as exc:
            error_msg = str(exc)
            _quota_keywords = [
                "insufficient_quota", "exceeded your current quota",
                "rate_limit_exceeded", "429", "billing",
            ]
            if any(kw in error_msg.lower() for kw in _quota_keywords):
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Your LLM provider credits have been exhausted or you've hit "
                        "a rate limit. Please check your plan and billing details, "
                        "or switch to a free local provider in Settings."
                    ),
                ) from exc
            _empty_llm_errors = (
                "No valid task outputs",
                "Invalid response from LLM call",
                "None or empty",
            )
            if any(kw in error_msg for kw in _empty_llm_errors):
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "The LLM could not complete the task. This usually happens "
                        "with small local models (qwen2.5:0.5b, tinyllama, phi3:mini). "
                        "Try a larger model (llama3, qwen2.5:7b), enable Lite Mode "
                        "in Settings, or use a cloud provider."
                    ),
                ) from exc
            if isinstance(exc, TimeoutError) or "timed out" in error_msg.lower():
                raise HTTPException(
                    status_code=504,
                    detail=(
                        "The agent operation timed out. The LLM provider may be "
                        "overloaded. Try again or switch to a faster provider."
                    ),
                ) from exc
            if "circuit breaker" in error_msg.lower():
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "The LLM provider is temporarily unavailable after repeated "
                        "failures. Please wait and try again shortly."
                    ),
                ) from exc
            raise
        if isinstance(result, dict):
            result.setdefault(
                "mode",
                "sticky" if req.branch_name else "hard-switch",
            )

        # Persist the branch the executor actually wrote to onto the
        # session record so reopening this session jumps back to that
        # branch (instead of the master/default it was created on).
        # Best-effort: a failure to update the session must never block
        # the user-facing execute result.
        new_branch = (
            result.get("branch") if isinstance(result, dict) else None
        ) or req.branch_name
        if req.session_id and new_branch:
            try:
                session = _session_mgr.load(req.session_id)
                session.branch = new_branch
                # Multi-repo support: update the matching repos[] entry
                # too if it exists, so callers that read from there see
                # a consistent value.
                if session.repos:
                    for entry in session.repos:
                        if entry.get("full_name") == full_name:
                            entry["branch"] = new_branch
                _session_mgr.save(session)
            except FileNotFoundError:
                logger.debug(
                    "[exec] session %s not found — skipping branch persist",
                    req.session_id,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "[exec] could not persist branch on session %s: %s",
                    req.session_id,
                    exc,
                )

        return result


@app.get("/api/flow/current")
async def api_get_flow(topology: Optional[str] = Query(None)):
    """Return the agent flow definition as a graph.

    If ``topology`` query param is provided, returns the graph for that
    topology.  Otherwise falls back to the user's saved preference, and
    finally to the legacy ``get_flow_definition()`` output for full
    backward compatibility.
    """
    tid = topology or get_saved_topology_preference()
    if tid:
        return _get_topology_graph(tid)
    # Legacy path — returns the original hardcoded graph
    flow = await get_flow_definition()
    return flow


# ============================================================================
# Topology Registry Endpoints (additive — no existing behaviour changed)
# ============================================================================

@app.get("/api/flow/topologies")
async def api_list_topologies():
    """Return lightweight summaries of all available topology presets."""
    return _list_topologies()


@app.get("/api/flow/topology/{topology_id}")
async def api_get_topology(topology_id: str):
    """Return the full flow graph for a specific topology."""
    return _get_topology_graph(topology_id)


class ClassifyRequest(BaseModel):
    message: str


@app.post("/api/flow/classify")
async def api_classify_message(req: ClassifyRequest):
    """Auto-detect the best topology for a given user message.

    Returns the recommended topology, confidence score, and up to 4
    alternatives ranked by relevance.
    """
    result = _classify_message(req.message)
    return result.to_dict()


class TopologyPrefRequest(BaseModel):
    topology: str


@app.get("/api/settings/topology")
async def api_get_topology_pref():
    """Return the user's saved topology preference (or null)."""
    pref = get_saved_topology_preference()
    return {"topology": pref}


@app.post("/api/settings/topology")
async def api_set_topology_pref(req: TopologyPrefRequest):
    """Save the user's preferred topology."""
    save_topology_preference(req.topology)
    return {"status": "ok", "topology": req.topology}


# ============================================================================
# Conversational Chat Endpoint (v2 upgrade)
# ============================================================================

@app.post("/api/chat/message")
async def api_chat_message(req: ChatRequest, authorization: Optional[str] = Header(None)):
    """
    Unified conversational endpoint.  The router analyses the message and
    dispatches to the appropriate agent (issue, PR, search, review, learning,
    or the existing plan+execute pipeline).
    """
    token = get_github_token(authorization)

    logger.info(
        "CHAT MESSAGE: %s/%s | message=%r | branch=%r",
        req.repo_owner,
        req.repo_name,
        req.message[:80],
        req.branch_name,
    )

    with execution_context(token, ref=req.branch_name):
        full_name = f"{req.repo_owner}/{req.repo_name}"
        try:
            result = await dispatch_request(
                req.message, full_name, token=token, branch_name=req.branch_name,
                topology_id=req.topology_id,
            )
        except Exception as exc:
            error_msg = str(exc)
            _quota_keywords = [
                "insufficient_quota", "exceeded your current quota",
                "rate_limit_exceeded", "429", "billing",
            ]
            if any(kw in error_msg.lower() for kw in _quota_keywords):
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Your LLM provider credits have been exhausted or you've hit "
                        "a rate limit. Please check your plan and billing details, "
                        "or switch to a free local provider in Settings."
                    ),
                ) from exc
            _empty_llm_errors = (
                "No valid task outputs",
                "Invalid response from LLM call",
                "None or empty",
            )
            if any(kw in error_msg for kw in _empty_llm_errors):
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "The LLM could not complete the task. This usually happens "
                        "with small local models (qwen2.5:0.5b, tinyllama, phi3:mini). "
                        "Try a larger model (llama3, qwen2.5:7b), enable Lite Mode "
                        "in Settings, or use a cloud provider."
                    ),
                ) from exc
            if isinstance(exc, TimeoutError) or "timed out" in error_msg.lower():
                raise HTTPException(
                    status_code=504,
                    detail=(
                        "The agent operation timed out. The LLM provider may be "
                        "overloaded. Try again or switch to a faster provider."
                    ),
                ) from exc
            if "circuit breaker" in error_msg.lower():
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "The LLM provider is temporarily unavailable after repeated "
                        "failures. Please wait and try again shortly."
                    ),
                ) from exc
            raise

        # If auto_pr is requested and execution completed, create PR
        if (
            req.auto_pr
            and isinstance(result, dict)
            and result.get("category") == "plan_execute"
            and result.get("plan")
        ):
            result["auto_pr_hint"] = (
                "Plan generated. Execute it first, then auto-PR will be created."
            )

        return result


@app.post("/api/chat/execute-with-pr")
async def api_chat_execute_with_pr(
    req: ExecutePlanRequest,
    authorization: Optional[str] = Header(None),
):
    """Execute a plan AND automatically create a pull request afterwards."""
    token = get_github_token(authorization)

    with execution_context(token, ref=req.branch_name):
        full_name = f"{req.repo_owner}/{req.repo_name}"
        executor = execute_plan_lite if _is_lite_mode_active() else execute_plan
        try:
            result = await executor(
                req.plan, full_name, token=token, branch_name=req.branch_name,
            )
        except Exception as exc:
            error_msg = str(exc)
            _quota_keywords = [
                "insufficient_quota", "exceeded your current quota",
                "rate_limit_exceeded", "429", "billing",
            ]
            if any(kw in error_msg.lower() for kw in _quota_keywords):
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Your LLM provider credits have been exhausted. "
                        "Check billing or switch to a free local provider."
                    ),
                ) from exc
            if "No valid task outputs" in error_msg:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "The LLM returned an empty response. Try enabling "
                        "Lite Mode for better results with small models."
                    ),
                ) from exc
            raise

        if isinstance(result, dict) and result.get("status") == "completed":
            branch = result.get("branch", req.branch_name)
            if branch:
                pr = await create_pr_after_execution(
                    full_name,
                    branch,
                    req.plan.goal,
                    result.get("executionLog", {}),
                    token=token,
                )
                if pr:
                    result["pull_request"] = {
                        "number": pr.get("number"),
                        "url": pr.get("html_url"),
                        "title": pr.get("title"),
                    }

            result.setdefault(
                "mode",
                "sticky" if req.branch_name else "hard-switch",
            )

        return result


# ============================================================================
# Issue Endpoints (v2 upgrade)
# ============================================================================

@app.get("/api/repos/{owner}/{repo}/issues")
async def api_list_issues(
    owner: str = FPath(...),
    repo: str = FPath(...),
    state: str = Query("open"),
    labels: Optional[str] = Query(None),
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
    authorization: Optional[str] = Header(None),
):
    """List issues for a repository."""
    token = get_github_token(authorization)
    issues = await github_issues.list_issues(
        owner, repo, state=state, labels=labels,
        per_page=per_page, page=page, token=token,
    )
    return {"issues": issues, "page": page, "per_page": per_page}


@app.get("/api/repos/{owner}/{repo}/issues/{issue_number}")
async def api_get_issue(
    owner: str = FPath(...),
    repo: str = FPath(...),
    issue_number: int = FPath(...),
    authorization: Optional[str] = Header(None),
):
    """Get a single issue."""
    token = get_github_token(authorization)
    return await github_issues.get_issue(owner, repo, issue_number, token=token)


@app.post("/api/repos/{owner}/{repo}/issues")
async def api_create_issue(
    owner: str = FPath(...),
    repo: str = FPath(...),
    payload: IssueCreateRequest = ...,
    authorization: Optional[str] = Header(None),
):
    """Create a new issue."""
    token = get_github_token(authorization)
    return await github_issues.create_issue(
        owner, repo, payload.title,
        body=payload.body, labels=payload.labels,
        assignees=payload.assignees, milestone=payload.milestone,
        token=token,
    )


@app.patch("/api/repos/{owner}/{repo}/issues/{issue_number}")
async def api_update_issue(
    owner: str = FPath(...),
    repo: str = FPath(...),
    issue_number: int = FPath(...),
    payload: IssueUpdateRequest = ...,
    authorization: Optional[str] = Header(None),
):
    """Update an existing issue."""
    token = get_github_token(authorization)
    return await github_issues.update_issue(
        owner, repo, issue_number,
        title=payload.title, body=payload.body, state=payload.state,
        labels=payload.labels, assignees=payload.assignees,
        milestone=payload.milestone, token=token,
    )


@app.get("/api/repos/{owner}/{repo}/issues/{issue_number}/comments")
async def api_list_issue_comments(
    owner: str = FPath(...),
    repo: str = FPath(...),
    issue_number: int = FPath(...),
    authorization: Optional[str] = Header(None),
):
    """List comments on an issue."""
    token = get_github_token(authorization)
    return await github_issues.list_issue_comments(owner, repo, issue_number, token=token)


@app.post("/api/repos/{owner}/{repo}/issues/{issue_number}/comments")
async def api_add_issue_comment(
    owner: str = FPath(...),
    repo: str = FPath(...),
    issue_number: int = FPath(...),
    payload: IssueCommentRequest = ...,
    authorization: Optional[str] = Header(None),
):
    """Add a comment to an issue."""
    token = get_github_token(authorization)
    return await github_issues.add_issue_comment(
        owner, repo, issue_number, payload.body, token=token,
    )


# ============================================================================
# Pull Request Endpoints (v2 upgrade)
# ============================================================================

@app.get("/api/repos/{owner}/{repo}/pulls")
async def api_list_pulls(
    owner: str = FPath(...),
    repo: str = FPath(...),
    state: str = Query("open"),
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
    authorization: Optional[str] = Header(None),
):
    """List pull requests."""
    token = get_github_token(authorization)
    prs = await github_pulls.list_pull_requests(
        owner, repo, state=state, per_page=per_page, page=page, token=token,
    )
    return {"pull_requests": prs, "page": page, "per_page": per_page}


@app.get("/api/repos/{owner}/{repo}/pulls/{pull_number}")
async def api_get_pull(
    owner: str = FPath(...),
    repo: str = FPath(...),
    pull_number: int = FPath(...),
    authorization: Optional[str] = Header(None),
):
    """Get a single pull request."""
    token = get_github_token(authorization)
    return await github_pulls.get_pull_request(owner, repo, pull_number, token=token)


@app.post("/api/repos/{owner}/{repo}/pulls")
async def api_create_pull(
    owner: str = FPath(...),
    repo: str = FPath(...),
    payload: PRCreateRequest = ...,
    authorization: Optional[str] = Header(None),
):
    """Create a new pull request."""
    token = get_github_token(authorization)
    return await github_pulls.create_pull_request(
        owner, repo, title=payload.title, head=payload.head,
        base=payload.base, body=payload.body, draft=payload.draft,
        token=token,
    )


@app.put("/api/repos/{owner}/{repo}/pulls/{pull_number}/merge")
async def api_merge_pull(
    owner: str = FPath(...),
    repo: str = FPath(...),
    pull_number: int = FPath(...),
    payload: PRMergeRequest = ...,
    authorization: Optional[str] = Header(None),
):
    """Merge a pull request."""
    token = get_github_token(authorization)
    return await github_pulls.merge_pull_request(
        owner, repo, pull_number,
        merge_method=payload.merge_method,
        commit_title=payload.commit_title,
        commit_message=payload.commit_message,
        token=token,
    )


@app.get("/api/repos/{owner}/{repo}/pulls/{pull_number}/files")
async def api_list_pr_files(
    owner: str = FPath(...),
    repo: str = FPath(...),
    pull_number: int = FPath(...),
    authorization: Optional[str] = Header(None),
):
    """List files changed in a pull request."""
    token = get_github_token(authorization)
    return await github_pulls.list_pr_files(owner, repo, pull_number, token=token)


# ============================================================================
# Search Endpoints (v2 upgrade)
# ============================================================================

@app.get("/api/search/code")
async def api_search_code(
    q: str = Query(..., description="Search query"),
    owner: Optional[str] = Query(None),
    repo: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
    authorization: Optional[str] = Header(None),
):
    """Search for code across GitHub."""
    token = get_github_token(authorization)
    return await github_search.search_code(
        q, owner=owner, repo=repo, language=language,
        per_page=per_page, page=page, token=token,
    )


@app.get("/api/search/issues")
async def api_search_issues(
    q: str = Query(..., description="Search query"),
    owner: Optional[str] = Query(None),
    repo: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    label: Optional[str] = Query(None),
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
    authorization: Optional[str] = Header(None),
):
    """Search issues and pull requests."""
    token = get_github_token(authorization)
    return await github_search.search_issues(
        q, owner=owner, repo=repo, state=state, label=label,
        per_page=per_page, page=page, token=token,
    )


@app.get("/api/search/repositories")
async def api_search_repositories(
    q: str = Query(..., description="Search query"),
    language: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
    authorization: Optional[str] = Header(None),
):
    """Search for repositories."""
    token = get_github_token(authorization)
    return await github_search.search_repositories(
        q, language=language, sort=sort,
        per_page=per_page, page=page, token=token,
    )


@app.get("/api/search/users")
async def api_search_users(
    q: str = Query(..., description="Search query"),
    type_filter: Optional[str] = Query(None, alias="type"),
    location: Optional[str] = Query(None),
    language: Optional[str] = Query(None),
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
    authorization: Optional[str] = Header(None),
):
    """Search for GitHub users and organizations."""
    token = get_github_token(authorization)
    return await github_search.search_users(
        q, type_filter=type_filter, location=location, language=language,
        per_page=per_page, page=page, token=token,
    )


# ============================================================================
# Route Analysis Endpoint (v2 upgrade)
# ============================================================================

@app.post("/api/chat/route")
async def api_chat_route(payload: dict):
    """Preview how a message would be routed without executing it.

    Useful for the frontend to display which agent(s) will handle the request.
    """
    message = payload.get("message", "")
    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)

    workflow = route_request(message)
    return {
        "category": workflow.category.value,
        "agents": [a.value for a in workflow.agents],
        "description": workflow.description,
        "requires_repo_context": workflow.requires_repo_context,
        "entity_number": workflow.entity_number,
        "metadata": workflow.metadata,
    }


# ============================================================================
# Authentication Endpoints (Web Flow + Device Flow)
# ============================================================================

@app.get("/api/auth/url", response_model=AuthUrlResponse)
async def api_get_auth_url():
    """
    Generate GitHub OAuth authorization URL (Web Flow).

    The Web Flow needs GITHUB_CLIENT_SECRET to exchange the code for a token.
    When it is not configured we return an empty authorization_url so the
    frontend transparently falls back to the Device Flow (no secret / no
    callback URL required). This prevents bouncing the browser to the GitHub
    App's default localhost callback, which is unreachable in production.
    """
    if not web_flow_available():
        return AuthUrlResponse(authorization_url="", state="")
    auth_url, state = generate_authorization_url()
    return AuthUrlResponse(authorization_url=auth_url, state=state)


@app.post("/api/auth/callback", response_model=AuthSession)
async def api_auth_callback(request: AuthCallbackRequest):
    """
    Handle GitHub OAuth callback (Web Flow).
    Exchange the authorization code for an access token.
    """
    try:
        session = await exchange_code_for_token(request.code, request.state)
        return session
    except ValueError as e:
        return JSONResponse(
            {"error": str(e)},
            status_code=400,
        )


@app.post("/api/auth/validate", response_model=UserInfoResponse)
async def api_validate_token(request: TokenValidationRequest):
    """
    Validate a GitHub access token and return user information.
    """
    user = await validate_token(request.access_token)
    if user:
        return UserInfoResponse(user=user, authenticated=True)
    return UserInfoResponse(
        user=GitHubUser(login="", id=0, avatar_url=""),
        authenticated=False,
    )


@app.post("/api/auth/device/code")
async def api_device_code():
    """
    Start the device login flow (Step 1).
    Does NOT require a client secret.
    """
    try:
        data = await initiate_device_flow()
        return data
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/auth/device/poll")
async def api_device_poll(payload: dict):
    """
    Poll GitHub to check if user authorized the device (Step 2).
    """
    device_code = payload.get("device_code")
    if not device_code:
        return JSONResponse({"error": "Missing device_code"}, status_code=400)

    try:
        session = await poll_device_token(device_code)
        if session:
            return session

        return JSONResponse({"status": "pending"}, status_code=202)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/auth/status")
async def api_auth_status():
    """
    Smart check: Do we have a secret (Web Flow) or just ID (Device Flow)?
    This tells the frontend which UI to render.
    """
    has_secret = bool(os.getenv("GITHUB_CLIENT_SECRET"))
    has_id = bool(os.getenv("GITHUB_CLIENT_ID", "Iv23litmRp80Z6wmlyRn"))

    return {
        "mode": "web" if has_secret else "device",
        "configured": has_id,
        "oauth_configured": has_secret,
        "pat_configured": bool(os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")),
    }


@app.get("/api/auth/app-url")
async def api_get_app_url():
    """Get GitHub App installation URL."""
    app_slug = os.getenv("GITHUB_APP_SLUG", "gitpilota")
    app_url = f"https://github.com/apps/{app_slug}"
    return {
        "app_url": app_url,
        "app_slug": app_slug,
    }


@app.get("/api/auth/installation-status")
async def api_check_installation_status():
    """Check if GitHub App is installed for the current user."""
    pat_token = os.getenv("GITPILOT_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")

    if pat_token:
        user = await validate_token(pat_token)
        if user:
            return {
                "installed": True,
                "access_token": pat_token,
                "user": user,
                "auth_type": "pat",
            }

    github_app_id = os.getenv("GITHUB_APP_ID", "2313985")
    if not github_app_id:
        return {
            "installed": False,
            "message": "GitHub authentication not configured.",
            "auth_type": "none",
        }

    return {
        "installed": False,
        "message": "GitHub App not installed.",
        "auth_type": "github_app",
    }


@app.get("/api/auth/repo-access", response_model=RepoAccessResponse)
async def api_check_repo_access(
    owner: str = Query(...),
    repo: str = Query(...),
    authorization: Optional[str] = Header(None),
):
    """
    Check if we have write access to a repository via User token or GitHub App.

    This endpoint helps the frontend determine if it should show
    installation prompts or if the user already has sufficient permissions.
    """
    token = get_github_token(authorization)
    access_info = await check_repo_write_access(owner, repo, user_token=token)

    return RepoAccessResponse(
        can_write=access_info["can_write"],
        app_installed=access_info["app_installed"],
        auth_type=access_info["auth_type"],
    )


# ============================================================================
# Session Endpoints (Phase 1)
# ============================================================================

@app.get("/api/sessions")
async def api_list_sessions():
    """List all saved sessions."""
    return {"sessions": _session_mgr.list_sessions()}


@app.post("/api/sessions")
async def api_create_session(payload: dict):
    """Create a new session.

    Accepts either legacy single-repo or multi-repo format:
      Legacy: {"repo_full_name": "owner/repo", "branch": "main"}
      Multi:  {"repos": [{full_name, branch, mode}], "active_repo": "owner/repo"}
    """
    repo = payload.get("repo_full_name", "")
    branch = payload.get("branch")
    name = payload.get("name")  # optional — derived from first user prompt
    session = _session_mgr.create(repo_full_name=repo, branch=branch, name=name)

    # Multi-repo context support
    if payload.get("repos"):
        session.repos = payload["repos"]
        session.active_repo = payload.get("active_repo", repo)
    elif repo:
        session.repos = [{"full_name": repo, "branch": branch or "main", "mode": "write"}]
        session.active_repo = repo

    _session_mgr.save(session)
    return {"session_id": session.id, "status": session.status}


@app.get("/api/sessions/{session_id}")
async def api_get_session(session_id: str):
    """Get session details."""
    session = _session_mgr.load(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "id": session.id,
        "status": session.status,
        "repo_full_name": session.repo_full_name,
        "branch": session.branch,
        "created_at": session.created_at,
        "message_count": len(session.messages),
        "checkpoint_count": len(session.checkpoints),
        "repos": session.repos,
        "active_repo": session.active_repo,
    }


@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    """Delete a session."""
    deleted = _session_mgr.delete(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": True}


@app.patch("/api/sessions/{session_id}/context")
async def api_update_session_context(session_id: str, payload: dict):
    """Add, remove, or activate repos in a session's multi-repo context.

    Actions:
      {"action": "add", "repo_full_name": "owner/repo", "branch": "main"}
      {"action": "remove", "repo_full_name": "owner/repo"}
      {"action": "set_active", "repo_full_name": "owner/repo"}
    """
    session = _session_mgr.load(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    action = payload.get("action")
    repo_name = payload.get("repo_full_name")
    if not action or not repo_name:
        raise HTTPException(status_code=400, detail="action and repo_full_name required")

    if action == "add":
        branch = payload.get("branch", "main")
        if not any(r.get("full_name") == repo_name for r in session.repos):
            session.repos.append({
                "full_name": repo_name,
                "branch": branch,
                "mode": "read",
            })
        if not session.active_repo:
            session.active_repo = repo_name
    elif action == "remove":
        session.repos = [r for r in session.repos if r.get("full_name") != repo_name]
        if session.active_repo == repo_name:
            session.active_repo = session.repos[0]["full_name"] if session.repos else None
    elif action == "set_active":
        if any(r.get("full_name") == repo_name for r in session.repos):
            # Update mode flags
            for r in session.repos:
                r["mode"] = "write" if r.get("full_name") == repo_name else "read"
            session.active_repo = repo_name
        else:
            raise HTTPException(status_code=400, detail="Repo not in session context")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    _session_mgr.save(session)
    return {
        "repos": session.repos,
        "active_repo": session.active_repo,
    }


@app.post("/api/sessions/{session_id}/checkpoint")
async def api_create_checkpoint(session_id: str, payload: dict):
    """Create a checkpoint for a session."""
    session = _session_mgr.load(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    label = payload.get("label", "checkpoint")
    cp = _session_mgr.create_checkpoint(session, label=label)
    return {"checkpoint_id": cp.id, "label": cp.label, "created_at": cp.created_at}


# ============================================================================
# Hooks Endpoints (Phase 1)
# ============================================================================

@app.get("/api/hooks")
async def api_list_hooks():
    """List registered hooks."""
    return {"hooks": _hook_mgr.list_hooks()}


@app.post("/api/hooks")
async def api_register_hook(payload: dict):
    """Register a new hook."""
    from .hooks import HookDefinition
    try:
        hook = HookDefinition(
            event=HookEvent(payload["event"]),
            name=payload["name"],
            command=payload.get("command"),
            blocking=payload.get("blocking", False),
            timeout=payload.get("timeout", 30),
        )
        _hook_mgr.register(hook)
        return {"registered": True, "name": hook.name, "event": hook.event.value}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/hooks/{event}/{name}")
async def api_unregister_hook(event: str, name: str):
    """Unregister a hook by event and name."""
    try:
        _hook_mgr.unregister(HookEvent(event), name)
        return {"unregistered": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# Permissions Endpoints (Phase 1)
# ============================================================================

@app.get("/api/permissions")
async def api_get_permissions():
    """Get current permission policy."""
    return _perm_mgr.to_dict()


@app.put("/api/permissions/mode")
async def api_set_permission_mode(payload: dict):
    """Set the permission mode (normal, plan, auto)."""
    mode_str = payload.get("mode", "normal")
    try:
        _perm_mgr.policy.mode = PermissionMode(mode_str)
        return {"mode": _perm_mgr.policy.mode.value}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {mode_str}")


# ============================================================================
# Project Context / Memory Endpoints (Phase 1)
# ============================================================================

@app.get("/api/repos/{owner}/{repo}/context")
async def api_get_project_context(
    owner: str = FPath(...),
    repo: str = FPath(...),
):
    """Get project conventions and memory for a repository workspace."""
    from pathlib import Path as StdPath
    workspace_path = StdPath.home() / ".gitpilot" / "workspaces" / owner / repo
    if not workspace_path.exists():
        return {"conventions": "", "rules": [], "auto_memory": {}, "system_prompt": ""}
    mgr = MemoryManager(workspace_path)
    ctx = mgr.load_context()
    return {
        "conventions": ctx.conventions,
        "rules": ctx.rules,
        "auto_memory": ctx.auto_memory,
        "system_prompt": ctx.to_system_prompt(),
    }


@app.post("/api/repos/{owner}/{repo}/context/init")
async def api_init_project_context(
    owner: str = FPath(...),
    repo: str = FPath(...),
):
    """Initialize .gitpilot/ directory with template GITPILOT.md."""
    from pathlib import Path as StdPath
    workspace_path = StdPath.home() / ".gitpilot" / "workspaces" / owner / repo
    workspace_path.mkdir(parents=True, exist_ok=True)
    mgr = MemoryManager(workspace_path)
    md_path = mgr.init_project()
    return {"initialized": True, "path": str(md_path)}


@app.post("/api/repos/{owner}/{repo}/context/pattern")
async def api_add_learned_pattern(
    owner: str = FPath(...),
    repo: str = FPath(...),
    payload: dict = ...,
):
    """Add a learned pattern to auto-memory."""
    from pathlib import Path as StdPath
    pattern = payload.get("pattern", "")
    if not pattern:
        raise HTTPException(status_code=400, detail="pattern is required")
    workspace_path = StdPath.home() / ".gitpilot" / "workspaces" / owner / repo
    workspace_path.mkdir(parents=True, exist_ok=True)
    mgr = MemoryManager(workspace_path)
    mgr.add_learned_pattern(pattern)
    return {"added": True, "pattern": pattern}


# ============================================================================
# Context Vault Endpoints (additive — Context + Use Case system)
# ============================================================================

def _workspace_path(owner: str, repo: str) -> Path:
    """Resolve the local workspace path for a repo."""
    return Path.home() / ".gitpilot" / "workspaces" / owner / repo


@app.get("/api/repos/{owner}/{repo}/context/assets")
async def api_list_context_assets(
    owner: str = FPath(...),
    repo: str = FPath(...),
):
    """List all uploaded context assets for a repository."""
    vault = ContextVault(_workspace_path(owner, repo))
    assets = vault.list_assets()
    return {"assets": [a.to_dict() for a in assets]}


@app.post("/api/repos/{owner}/{repo}/context/assets/upload")
async def api_upload_context_asset(
    owner: str = FPath(...),
    repo: str = FPath(...),
    file: UploadFile = File(...),
):
    """Upload a file to the project context vault."""
    vault = ContextVault(_workspace_path(owner, repo))
    content = await file.read()
    mime = file.content_type or ""
    filename = file.filename or "upload"

    try:
        meta = vault.upload_asset(filename, content, mime=mime)
        return {"asset": meta.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/repos/{owner}/{repo}/context/assets/{asset_id}")
async def api_delete_context_asset(
    owner: str = FPath(...),
    repo: str = FPath(...),
    asset_id: str = FPath(...),
):
    """Delete a context asset."""
    vault = ContextVault(_workspace_path(owner, repo))
    vault.delete_asset(asset_id)
    return {"deleted": True, "asset_id": asset_id}


@app.get("/api/repos/{owner}/{repo}/context/assets/{asset_id}/download")
async def api_download_context_asset(
    owner: str = FPath(...),
    repo: str = FPath(...),
    asset_id: str = FPath(...),
):
    """Download a raw context asset file."""
    vault = ContextVault(_workspace_path(owner, repo))
    asset_path = vault.get_asset_path(asset_id)
    if not asset_path:
        raise HTTPException(status_code=404, detail="Asset not found")
    filename = vault.get_asset_filename(asset_id)
    return FileResponse(asset_path, filename=filename)


# ============================================================================
# Use Case Endpoints (additive — guided requirement clarification)
# ============================================================================

@app.get("/api/repos/{owner}/{repo}/use-cases")
async def api_list_use_cases(
    owner: str = FPath(...),
    repo: str = FPath(...),
):
    """List all use cases for a repository."""
    mgr = UseCaseManager(_workspace_path(owner, repo))
    return {"use_cases": mgr.list_use_cases()}


@app.post("/api/repos/{owner}/{repo}/use-cases")
async def api_create_use_case(
    owner: str = FPath(...),
    repo: str = FPath(...),
    payload: dict = ...,
):
    """Create a new use case."""
    title = payload.get("title", "New Use Case")
    initial_notes = payload.get("initial_notes", "")
    mgr = UseCaseManager(_workspace_path(owner, repo))
    uc = mgr.create_use_case(title=title, initial_notes=initial_notes)
    return {"use_case": uc.to_dict()}


@app.get("/api/repos/{owner}/{repo}/use-cases/{use_case_id}")
async def api_get_use_case(
    owner: str = FPath(...),
    repo: str = FPath(...),
    use_case_id: str = FPath(...),
):
    """Get a single use case with messages and spec."""
    mgr = UseCaseManager(_workspace_path(owner, repo))
    uc = mgr.get_use_case(use_case_id)
    if not uc:
        raise HTTPException(status_code=404, detail="Use case not found")
    return {"use_case": uc.to_dict()}


@app.post("/api/repos/{owner}/{repo}/use-cases/{use_case_id}/chat")
async def api_use_case_chat(
    owner: str = FPath(...),
    repo: str = FPath(...),
    use_case_id: str = FPath(...),
    payload: dict = ...,
):
    """Send a guided chat message and get assistant response + updated spec."""
    message = payload.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="message is required")
    mgr = UseCaseManager(_workspace_path(owner, repo))
    uc = mgr.chat(use_case_id, message)
    if not uc:
        raise HTTPException(status_code=404, detail="Use case not found")
    return {"use_case": uc.to_dict()}


@app.post("/api/repos/{owner}/{repo}/use-cases/{use_case_id}/finalize")
async def api_finalize_use_case(
    owner: str = FPath(...),
    repo: str = FPath(...),
    use_case_id: str = FPath(...),
):
    """Finalize a use case: mark active, export markdown spec."""
    mgr = UseCaseManager(_workspace_path(owner, repo))
    uc = mgr.finalize(use_case_id)
    if not uc:
        raise HTTPException(status_code=404, detail="Use case not found")
    return {"use_case": uc.to_dict()}


# ============================================================================
# MCP Endpoints (Phase 2)
# ============================================================================

@app.get("/api/mcp/servers")
async def api_mcp_list_servers():
    """List configured MCP servers and their connection status."""
    return _mcp_client.to_dict()


@app.post("/api/mcp/connect/{server_name}")
async def api_mcp_connect(server_name: str):
    """Connect to a named MCP server."""
    try:
        conn = await _mcp_client.connect(server_name)
        return {
            "connected": True,
            "server": server_name,
            "tools": [{"name": t.name, "description": t.description} for t in conn.tools],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/mcp/disconnect/{server_name}")
async def api_mcp_disconnect(server_name: str):
    """Disconnect from a named MCP server."""
    await _mcp_client.disconnect(server_name)
    return {"disconnected": True, "server": server_name}


@app.post("/api/mcp/call")
async def api_mcp_call_tool(payload: dict):
    """Call a tool on a connected MCP server."""
    server = payload.get("server", "")
    tool_name = payload.get("tool", "")
    params = payload.get("params", {})
    if not server or not tool_name:
        raise HTTPException(status_code=400, detail="server and tool are required")
    conn = _mcp_client._connections.get(server)
    if not conn:
        raise HTTPException(status_code=404, detail=f"Not connected to server: {server}")
    try:
        result = await _mcp_client.call_tool(conn, tool_name, params)
        return {"result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Plugin Endpoints (Phase 2)
# ============================================================================

@app.get("/api/plugins")
async def api_list_plugins():
    """List installed plugins."""
    plugins = _plugin_mgr.list_installed()
    return {"plugins": [p.to_dict() for p in plugins]}


@app.post("/api/plugins/install")
async def api_install_plugin(payload: dict):
    """Install a plugin from a git URL or local path."""
    source = payload.get("source", "")
    if not source:
        raise HTTPException(status_code=400, detail="source is required")
    try:
        info = _plugin_mgr.install(source)
        return {"installed": True, "plugin": info.to_dict()}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/plugins/{name}")
async def api_uninstall_plugin(name: str):
    """Uninstall a plugin by name."""
    removed = _plugin_mgr.uninstall(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Plugin not found: {name}")
    return {"uninstalled": True, "name": name}


# ============================================================================
# Skills Endpoints (Phase 2)
# ============================================================================

@app.get("/api/skills")
async def api_list_skills():
    """List all available skills."""
    return {"skills": _skill_mgr.list_skills()}


@app.post("/api/skills/invoke")
async def api_invoke_skill(payload: dict):
    """Invoke a skill by name."""
    name = payload.get("name", "")
    context = payload.get("context", {})
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    prompt = _skill_mgr.invoke(name, context)
    if prompt is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")
    return {"skill": name, "rendered_prompt": prompt}


@app.post("/api/skills/reload")
async def api_reload_skills():
    """Reload skills from all sources."""
    count = _skill_mgr.load_all()
    return {"reloaded": True, "count": count}


# ============================================================================
# Vision Endpoints (Phase 2)
# ============================================================================

@app.post("/api/vision/analyze")
async def api_vision_analyze(payload: dict):
    """Analyze an image with a text prompt."""
    from .vision import VisionAnalyzer
    image_path = payload.get("image_path", "")
    prompt = payload.get("prompt", "Describe this image.")
    provider = payload.get("provider", "openai")
    if not image_path:
        raise HTTPException(status_code=400, detail="image_path is required")
    try:
        analyzer = VisionAnalyzer(provider=provider)
        result = await analyzer.analyze_image(Path(image_path), prompt)
        return result.to_dict()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# Model Router Endpoints (Phase 2)
# ============================================================================

@app.post("/api/model-router/select")
async def api_model_select(payload: dict):
    """Preview which model would be selected for a request."""
    request = payload.get("request", "")
    category = payload.get("category")
    if not request:
        raise HTTPException(status_code=400, detail="request is required")
    selection = _model_router.select(request, category)
    return {
        "model": selection.model,
        "tier": selection.tier.value,
        "complexity": selection.complexity.value,
        "provider": selection.provider,
        "reason": selection.reason,
    }


@app.get("/api/model-router/usage")
async def api_model_usage():
    """Get model usage summary and budget status."""
    return _model_router.get_usage_summary()


# ============================================================================
# Agent Teams Endpoints (Phase 3)
# ============================================================================

@app.post("/api/agent-teams/plan")
async def api_team_plan(payload: dict):
    """Split a complex task into parallel subtasks."""
    task = payload.get("task", "")
    if not task:
        raise HTTPException(status_code=400, detail="task is required")
    subtasks = _agent_team.plan_and_split(task)
    return {"subtasks": [{"id": s.id, "title": s.title, "description": s.description} for s in subtasks]}


@app.post("/api/agent-teams/execute")
async def api_team_execute(payload: dict):
    """Execute subtasks in parallel and merge results."""
    task = payload.get("task", "")
    if not task:
        raise HTTPException(status_code=400, detail="task is required")
    subtasks = _agent_team.plan_and_split(task)
    result = await _agent_team.execute_parallel(subtasks)
    return result.to_dict()


# ============================================================================
# Learning Engine Endpoints (Phase 3)
# ============================================================================

@app.post("/api/learning/evaluate")
async def api_learning_evaluate(payload: dict):
    """Evaluate an action outcome for learning."""
    action = payload.get("action", "")
    outcome = payload.get("outcome", {})
    repo = payload.get("repo", "")
    if not action:
        raise HTTPException(status_code=400, detail="action is required")
    evaluation = _learning_engine.evaluate_outcome(action, outcome, repo=repo)
    return {
        "action": evaluation.action,
        "success": evaluation.success,
        "score": evaluation.score,
        "feedback": evaluation.feedback,
    }


@app.get("/api/learning/insights/{owner}/{repo}")
async def api_learning_insights(owner: str = FPath(...), repo: str = FPath(...)):
    """Get learned insights for a repository."""
    repo_name = f"{owner}/{repo}"
    insights = _learning_engine.get_repo_insights(repo_name)
    return {
        "repo": repo_name,
        "patterns": insights.patterns,
        "preferred_style": insights.preferred_style,
        "success_rate": insights.success_rate,
        "total_evaluations": insights.total_evaluations,
    }


@app.post("/api/learning/style")
async def api_learning_set_style(payload: dict):
    """Set preferred coding style for a repository."""
    repo = payload.get("repo", "")
    style = payload.get("style", {})
    if not repo:
        raise HTTPException(status_code=400, detail="repo is required")
    _learning_engine.set_preferred_style(repo, style)
    return {"repo": repo, "style": style}


# ============================================================================
# Cross-Repo Intelligence Endpoints (Phase 3)
# ============================================================================

@app.post("/api/cross-repo/dependencies")
async def api_cross_repo_dependencies(payload: dict):
    """Analyze dependencies from provided file contents."""
    files = payload.get("files", {})
    if not files:
        raise HTTPException(status_code=400, detail="files dict is required (filename -> content)")
    graph = _cross_repo.analyze_dependencies_from_files(files)
    return graph.to_dict()


@app.post("/api/cross-repo/impact")
async def api_cross_repo_impact(payload: dict):
    """Analyze impact of updating a package."""
    files = payload.get("files", {})
    package_name = payload.get("package", "")
    new_version = payload.get("new_version")
    if not package_name:
        raise HTTPException(status_code=400, detail="package is required")
    graph = _cross_repo.analyze_dependencies_from_files(files)
    report = _cross_repo.impact_analysis(graph, package_name, new_version)
    return report.to_dict()


# ============================================================================
# Predictions Endpoints (Phase 3)
# ============================================================================

@app.post("/api/predictions/suggest")
async def api_predictions_suggest(payload: dict):
    """Get proactive suggestions based on context."""
    context = payload.get("context", "")
    if not context:
        raise HTTPException(status_code=400, detail="context is required")
    suggestions = _predictive_engine.predict(context)
    return {"suggestions": [s.to_dict() for s in suggestions]}


@app.get("/api/predictions/rules")
async def api_predictions_rules():
    """List all prediction rules."""
    return {"rules": _predictive_engine.list_rules()}


# ============================================================================
# Security Scanner Endpoints (Phase 3)
# ============================================================================

@app.post("/api/security/scan-file")
async def api_security_scan_file(payload: dict):
    """Scan a single file for security issues."""
    file_path = payload.get("file_path", "")
    if not file_path:
        raise HTTPException(status_code=400, detail="file_path is required")
    findings = _security_scanner.scan_file(file_path)
    return {"findings": [f.to_dict() for f in findings], "count": len(findings)}


@app.post("/api/security/scan-directory")
async def api_security_scan_directory(payload: dict):
    """Recursively scan a directory for security issues."""
    directory = payload.get("directory", "")
    if not directory:
        raise HTTPException(status_code=400, detail="directory is required")
    result = _security_scanner.scan_directory(directory)
    return result.to_dict()


@app.post("/api/security/scan-diff")
async def api_security_scan_diff(payload: dict):
    """Scan a git diff for security issues in added lines."""
    diff_text = payload.get("diff", "")
    if not diff_text:
        raise HTTPException(status_code=400, detail="diff is required")
    findings = _security_scanner.scan_diff(diff_text)
    return {"findings": [f.to_dict() for f in findings], "count": len(findings)}


# ============================================================================
# Natural Language Database Endpoints (Phase 3)
# ============================================================================

@app.post("/api/nl-database/translate")
async def api_nl_translate(payload: dict):
    """Translate natural language to SQL."""
    question = payload.get("question", "")
    dialect = payload.get("dialect", "postgresql")
    tables = payload.get("tables", [])
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    # Lazy import — nl_database pulls in SQL parsing libraries
    from .nl_database import NLQueryEngine, QueryDialect, TableSchema
    engine = NLQueryEngine(dialect=QueryDialect(dialect))
    for t in tables:
        engine.add_table(TableSchema(
            name=t["name"],
            columns=t.get("columns", []),
            primary_key=t.get("primary_key"),
        ))
    sql = engine.translate(question)
    error = engine.validate_query(sql)
    return {"question": question, "sql": sql, "valid": error is None, "error": error}


@app.post("/api/nl-database/explain")
async def api_nl_explain(payload: dict):
    """Explain what a SQL query does in plain English."""
    sql = payload.get("sql", "")
    if not sql:
        raise HTTPException(status_code=400, detail="sql is required")
    explanation = _nl_engine.explain(sql)
    return {"sql": sql, "explanation": explanation}


# ============================================================================
# Branch Listing Endpoint (Claude-Code-on-Web Parity)
# ============================================================================

class BranchInfo(BaseModel):
    name: str
    is_default: bool = False
    protected: bool = False
    commit_sha: Optional[str] = None


class BranchListResponse(BaseModel):
    repository: str
    default_branch: str
    page: int
    per_page: int
    has_more: bool
    branches: List[BranchInfo]


@app.get("/api/repos/{owner}/{repo}/branches", response_model=BranchListResponse)
async def api_list_branches(
    owner: str = FPath(...),
    repo: str = FPath(...),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=100),
    query: Optional[str] = Query(None, description="Substring filter"),
    authorization: Optional[str] = Header(None),
):
    """List branches for a repository with optional search filtering."""
    import httpx as _httpx

    token = get_github_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="GitHub token required")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "gitpilot",
    }
    timeout = _httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)

    async with _httpx.AsyncClient(
        base_url="https://api.github.com", headers=headers, timeout=timeout
    ) as client:
        # Fetch repo info for default_branch
        repo_resp = await client.get(f"/repos/{owner}/{repo}")
        if repo_resp.status_code >= 400:
            logging.warning(
                "branches: repo lookup failed %s/%s → %s %s",
                owner, repo, repo_resp.status_code, repo_resp.text[:200],
            )
            raise HTTPException(
                status_code=repo_resp.status_code,
                detail=f"Cannot access repository: {repo_resp.status_code}",
            )

        repo_data = repo_resp.json()
        default_branch_name = repo_data.get("default_branch", "main")

        # Fetch ALL branch pages (GitHub caps at 100 per page)
        all_raw = []
        current_page = page
        while True:
            branch_resp = await client.get(
                f"/repos/{owner}/{repo}/branches",
                params={"page": current_page, "per_page": per_page},
            )
            if branch_resp.status_code >= 400:
                logging.warning(
                    "branches: list failed %s/%s page=%s → %s %s",
                    owner, repo, current_page, branch_resp.status_code, branch_resp.text[:200],
                )
                raise HTTPException(
                    status_code=branch_resp.status_code,
                    detail=f"Failed to list branches: {branch_resp.status_code}",
                )

            page_data = branch_resp.json() if isinstance(branch_resp.json(), list) else []
            all_raw.extend(page_data)

            # Check if there are more pages
            link_header = branch_resp.headers.get("Link", "") or ""
            if 'rel="next"' not in link_header or len(page_data) < per_page:
                break
            current_page += 1
            # Safety: cap at 10 pages (1000 branches)
            if current_page - page >= 10:
                break

    q = (query or "").strip().lower()

    branches = []
    for b in all_raw:
        name = (b.get("name") or "").strip()
        if not name:
            continue
        if q and q not in name.lower():
            continue
        branches.append(BranchInfo(
            name=name,
            is_default=(name == default_branch_name),
            protected=bool(b.get("protected", False)),
            commit_sha=(b.get("commit") or {}).get("sha"),
        ))

    # Sort: default branch first, then alphabetical
    branches.sort(key=lambda x: (0 if x.is_default else 1, x.name.lower()))

    return BranchListResponse(
        repository=f"{owner}/{repo}",
        default_branch=default_branch_name,
        page=page,
        per_page=per_page,
        has_more=False,
        branches=branches,
    )


# ============================================================================
# Environment Configuration Endpoints (Claude-Code-on-Web Parity)
# ============================================================================

import json as _json
_ENV_ROOT = Path.home() / ".gitpilot" / "environments"


class EnvironmentConfig(BaseModel):
    id: Optional[str] = None
    name: str = "Default"
    network_access: str = Field("limited", description="limited | full | none")
    env_vars: dict = Field(default_factory=dict)


class EnvironmentListResponse(BaseModel):
    environments: List[EnvironmentConfig]


@app.get("/api/environments", response_model=EnvironmentListResponse)
async def api_list_environments():
    """List all environment configurations."""
    _ENV_ROOT.mkdir(parents=True, exist_ok=True)
    envs = []
    for path in sorted(_ENV_ROOT.glob("*.json")):
        try:
            data = _json.loads(path.read_text())
            envs.append(EnvironmentConfig(**data))
        except Exception:
            continue
    if not envs:
        envs.append(EnvironmentConfig(id="default", name="Default", network_access="limited"))
    return EnvironmentListResponse(environments=envs)


@app.post("/api/environments")
async def api_create_environment(config: EnvironmentConfig):
    """Create a new environment configuration."""
    import uuid
    _ENV_ROOT.mkdir(parents=True, exist_ok=True)
    config.id = config.id or uuid.uuid4().hex[:12]
    path = _ENV_ROOT / f"{config.id}.json"
    path.write_text(_json.dumps(config.model_dump(), indent=2))
    return config.model_dump()


@app.put("/api/environments/{env_id}")
async def api_update_environment(env_id: str, config: EnvironmentConfig):
    """Update an environment configuration."""
    _ENV_ROOT.mkdir(parents=True, exist_ok=True)
    path = _ENV_ROOT / f"{env_id}.json"
    config.id = env_id
    path.write_text(_json.dumps(config.model_dump(), indent=2))
    return config.model_dump()


@app.delete("/api/environments/{env_id}")
async def api_delete_environment(env_id: str):
    """Delete an environment configuration."""
    path = _ENV_ROOT / f"{env_id}.json"
    if path.exists():
        path.unlink()
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="Environment not found")


# ============================================================================
# Session Messages + Diff Endpoints (Claude-Code-on-Web Parity)
# ============================================================================

@app.post("/api/sessions/{session_id}/message")
async def api_add_session_message(session_id: str, payload: dict):
    """Add a message to a session's conversation history."""
    try:
        session = _session_mgr.load(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    role = payload.get("role", "user")
    content = payload.get("content", "")
    session.add_message(role, content, **payload.get("metadata", {}))
    _session_mgr.save(session)
    return {"message_count": len(session.messages)}


@app.get("/api/sessions/{session_id}/messages")
async def api_get_session_messages(session_id: str):
    """Get all messages for a session."""
    try:
        session = _session_mgr.load(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session.id,
        "messages": [
            {
                "role": m.role,
                "content": m.content,
                "timestamp": m.timestamp,
                "metadata": m.metadata,
            }
            for m in session.messages
        ],
    }


@app.get("/api/sessions/{session_id}/tasks")
async def api_get_session_tasks(session_id: str):
    """Return the right-sidebar Tasks trace for one session.

    Read-only.  Gated behind the ``tasks_sidebar`` flag — when off the
    endpoint 404s so an old frontend can detect "feature absent" with
    the same code path it uses for "session deleted".
    """
    from . import flags
    from .task_recorder import FLAG_TASKS_SIDEBAR

    if not flags.is_on(FLAG_TASKS_SIDEBAR, default=True):
        raise HTTPException(status_code=404, detail="Tasks sidebar is disabled")

    try:
        session = _session_mgr.load(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": session.id,
        "tasks": [
            {
                "id": t.id,
                "kind": t.kind,
                "title": t.title,
                "status": t.status,
                "started_at": t.started_at,
                "completed_at": t.completed_at,
                "duration_ms": t.duration_ms,
                "prompt_tokens": t.prompt_tokens,
                "completion_tokens": t.completion_tokens,
                "error": t.error,
            }
            for t in session.tasks
        ],
    }


@app.get("/api/sessions/{session_id}/diff")
async def api_get_session_diff(session_id: str):
    """Get diff stats for a session (placeholder for sandbox integration)."""
    try:
        session = _session_mgr.load(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    diff = session.metadata.get("diff", {
        "files_changed": 0,
        "additions": 0,
        "deletions": 0,
        "files": [],
    })
    return {"session_id": session.id, "diff": diff}


@app.post("/api/sessions/{session_id}/status")
async def api_update_session_status(session_id: str, payload: dict):
    """Update session status (active, completed, failed, waiting)."""
    try:
        session = _session_mgr.load(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    new_status = payload.get("status", "active")
    if new_status not in ("active", "paused", "completed", "failed", "waiting"):
        raise HTTPException(status_code=400, detail="Invalid status")
    session.status = new_status
    _session_mgr.save(session)
    return {"session_id": session.id, "status": session.status}


# ============================================================================
# WebSocket Streaming Endpoint (Claude-Code-on-Web Parity)
# ============================================================================

from fastapi import WebSocket, WebSocketDisconnect


async def _safe_ws_send_json(websocket: WebSocket, data: dict) -> bool:
    """Send JSON over a WebSocket, swallowing disconnect errors.

    Returns True if the send succeeded, False if the client has disconnected.
    This prevents ClientDisconnected / WebSocketDisconnect from crashing the
    handler when the client closes mid-response (common with Vite HMR,
    browser tab close, or network drops).

    Best-practice pattern from Starlette docs:
    https://www.starlette.io/websockets/#disconnect
    """
    try:
        await websocket.send_json(data)
        return True
    except WebSocketDisconnect:
        return False
    except Exception as exc:
        # Catches uvicorn.protocols.utils.ClientDisconnected and other
        # transport-layer errors without importing uvicorn internals
        exc_name = type(exc).__name__
        if exc_name in ("ClientDisconnected", "ConnectionClosedError",
                        "ConnectionClosedOK", "WebSocketDisconnect"):
            return False
        # Re-raise unexpected errors so they show up in logs
        raise


@app.websocket("/ws/sessions/{session_id}")
async def session_websocket(websocket: WebSocket, session_id: str):
    """
    Real-time bidirectional communication for a coding session.

    Server events:
      { type: "agent_message", content: "..." }
      { type: "tool_use", tool: "bash", input: "npm test" }
      { type: "tool_result", tool: "bash", output: "All tests passed" }
      { type: "diff_update", stats: { additions: N, deletions: N, files: N } }
      { type: "status_change", status: "completed" }
      { type: "error", message: "..." }

    Client events:
      { type: "user_message", content: "..." }
      { type: "cancel" }
    """
    await websocket.accept()

    # Verify session exists
    try:
        session = _session_mgr.load(session_id)
    except FileNotFoundError:
        await _safe_ws_send_json(websocket, {"type": "error", "message": "Session not found"})
        try:
            await websocket.close()
        except Exception:
            pass
        return

    # Send session history on connect (may fail if client already gone)
    if not await _safe_ws_send_json(websocket, {
        "type": "session_restored",
        "session_id": session.id,
        "status": session.status,
        "message_count": len(session.messages),
    }):
        logger.info(f"WebSocket disconnected before handshake for session {session_id}")
        return

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                break

            event_type = data.get("type", "")

            if event_type == "user_message":
                content = data.get("content", "")
                session.add_message("user", content)
                _session_mgr.save(session)

                # Acknowledge receipt
                if not await _safe_ws_send_json(websocket, {
                    "type": "message_received",
                    "message_index": len(session.messages) - 1,
                }):
                    break

                # Stream agent response (integration point for agentic.py)
                if not await _safe_ws_send_json(websocket, {
                    "type": "status_change",
                    "status": "active",
                }):
                    break

                # Agent processing hook — when the agent orchestrator is wired,
                # replace this with actual streaming from agentic.py
                try:
                    repo_full = session.repo_full_name or ""
                    parts = repo_full.split("/", 1)
                    if len(parts) == 2 and content.strip():
                        # Use canonical dispatcher signature
                        result = await dispatch_request(
                            user_request=content,
                            repo_full_name=f"{parts[0]}/{parts[1]}",
                            branch_name=session.branch,
                        )
                        answer = ""
                        if isinstance(result, dict):
                            answer = (
                                result.get("result")
                                or result.get("answer")
                                or result.get("message")
                                or result.get("summary")
                                or (result.get("plan", {}) or {}).get("summary")
                                or str(result)
                            )
                        else:
                            answer = str(result)

                        # Stream the response
                        if not await _safe_ws_send_json(websocket, {
                            "type": "agent_message",
                            "content": answer,
                        }):
                            # Client disconnected — still persist the answer for session history
                            session.add_message("assistant", answer)
                            _session_mgr.save(session)
                            break

                        session.add_message("assistant", answer)
                        _session_mgr.save(session)
                    else:
                        if not await _safe_ws_send_json(websocket, {
                            "type": "agent_message",
                            "content": "Session is not connected to a repository.",
                        }):
                            break
                except Exception as agent_err:
                    logger.error(f"Agent error in WS session {session_id}: {agent_err}")
                    err_str = str(agent_err)
                    # Friendly messages for common LLM errors
                    _q_kw = ["insufficient_quota", "exceeded your current quota", "rate_limit_exceeded", "429"]
                    if any(kw in err_str.lower() for kw in _q_kw):
                        err_str = (
                            "Your LLM provider credits have been exhausted or you've "
                            "hit a rate limit. Please check your billing details or "
                            "switch to a free local provider (Ollama / OllaBridge) in Settings."
                        )
                    elif "No valid task outputs" in err_str or "Invalid response from LLM call" in err_str:
                        err_str = (
                            "The LLM returned an empty response. This often happens "
                            "with small/reasoning models. Try a larger model or enable Lite Mode."
                        )
                    if not await _safe_ws_send_json(websocket, {
                        "type": "error",
                        "message": err_str,
                    }):
                        break

                if not await _safe_ws_send_json(websocket, {
                    "type": "status_change",
                    "status": "waiting",
                }):
                    break

            elif event_type == "cancel":
                if not await _safe_ws_send_json(websocket, {
                    "type": "status_change",
                    "status": "waiting",
                }):
                    break

            elif event_type == "ping":
                if not await _safe_ws_send_json(websocket, {"type": "pong"}):
                    break

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for session {session_id}")
    except Exception as e:
        # Don't log as error if it's a disconnect-related exception
        exc_name = type(e).__name__
        if exc_name in ("ClientDisconnected", "ConnectionClosedError", "ConnectionClosedOK"):
            logger.info(f"WebSocket client disconnected for session {session_id}")
        else:
            logger.error(f"WebSocket error for session {session_id}: {e}")
            await _safe_ws_send_json(websocket, {"type": "error", "message": str(e)})


# ─── Redesigned API Endpoints (Phase 1–4) ────────────────────────────────

from gitpilot.models import (
    ProviderTestRequest as _ProviderTestRequest,
    StartSessionRequest as _StartSessionRequest,
    ChatMessageRequest as _ChatMessageRequest,
)


@app.get("/api/status")
async def api_status():
    """Normalized status endpoint for the redesigned extension/UI."""
    from gitpilot.models import (
        StatusResponse, ProviderStatusResponse, ProviderName,
        WorkspaceCapabilitySummary, GithubStatusSummary, ProviderHealth,
    )
    from gitpilot.settings import autoconfigure_local_provider
    from gitpilot.github_api import get_github_status_summary

    s = autoconfigure_local_provider()
    provider_summary = s.get_provider_summary()

    # Build provider status
    provider = ProviderStatusResponse(
        configured=provider_summary.configured,
        name=ProviderName(provider_summary.name.value if hasattr(provider_summary.name, 'value') else str(provider_summary.name)),
        source=provider_summary.source,
        model=provider_summary.model,
        base_url=provider_summary.base_url,
        connection_type=provider_summary.connection_type,
        has_api_key=provider_summary.has_api_key,
        health=provider_summary.health,
        models_available=provider_summary.models_available,
        warning=provider_summary.warning,
    )

    # Workspace capabilities — gated by runtime. In a cloud workspace there is
    # no user-owned filesystem, so local folder / Git modes are not offered;
    # GitHub is the way to bring in code. Locally, the opposite: folder/Git are
    # first-class and GitHub is optional.
    from gitpilot.settings import runtime_environment

    _runtime = runtime_environment()
    _is_cloud = _runtime == "cloud"
    workspace = WorkspaceCapabilitySummary(
        runtime=_runtime,
        folder_mode_available=not _is_cloud,
        local_git_available=not _is_cloud,
        github_mode_available=False,
    )

    # GitHub status — wrap with timeout to prevent slow first-load
    # (GitHub API calls over WSL/slow networks can take 5-10s first time)
    github = GithubStatusSummary()
    try:
        github = await _asyncio.wait_for(get_github_status_summary(), timeout=3.0)
        workspace.github_mode_available = github.connected
    except _asyncio.TimeoutError:
        logger.warning("[api/status] GitHub status check timed out after 3s, returning cached/default")
    except Exception as exc:
        logger.debug("[api/status] GitHub status check failed: %s", exc)

    return StatusResponse(
        server_ready=True,
        provider=provider,
        workspace=workspace,
        github=github,
    )


@app.get("/api/providers/status")
async def api_providers_status():
    """Get detailed status for the active provider."""
    from gitpilot.settings import autoconfigure_local_provider
    from gitpilot.llm_provider import test_provider_connection

    s = autoconfigure_local_provider()
    summary = await test_provider_connection(s)
    return summary


@app.post("/api/providers/test")
async def api_providers_test(req: _ProviderTestRequest):
    """Test a specific provider configuration."""
    from gitpilot.models import (
        ProviderTestRequest, ProviderTestResponse, ProviderName,
        ProviderHealth,
    )
    from gitpilot.settings import get_settings, AppSettings
    from gitpilot.llm_provider import test_provider_connection
    import copy

    s = autoconfigure_local_provider()
    # Apply test overrides temporarily
    test_settings = copy.deepcopy(s)

    provider = req.provider
    if provider == ProviderName.openai and req.openai:
        if req.openai.api_key:
            test_settings.openai.api_key = req.openai.api_key
        if req.openai.base_url:
            test_settings.openai.base_url = req.openai.base_url
        if req.openai.model:
            test_settings.openai.model = req.openai.model
        test_settings.provider = test_settings.provider.__class__("openai")
    elif provider == ProviderName.claude and req.claude:
        if req.claude.api_key:
            test_settings.claude.api_key = req.claude.api_key
        if req.claude.base_url:
            test_settings.claude.base_url = req.claude.base_url
        if req.claude.model:
            test_settings.claude.model = req.claude.model
        test_settings.provider = test_settings.provider.__class__("claude")
    elif provider == ProviderName.watsonx and req.watsonx:
        if req.watsonx.api_key:
            test_settings.watsonx.api_key = req.watsonx.api_key
        if req.watsonx.project_id:
            test_settings.watsonx.project_id = req.watsonx.project_id
        if req.watsonx.base_url:
            test_settings.watsonx.base_url = req.watsonx.base_url
        if req.watsonx.model_id:
            test_settings.watsonx.model_id = req.watsonx.model_id
        test_settings.provider = test_settings.provider.__class__("watsonx")
    elif provider == ProviderName.ollama and req.ollama:
        if req.ollama.base_url:
            test_settings.ollama.base_url = req.ollama.base_url
        if req.ollama.model:
            test_settings.ollama.model = req.ollama.model
        test_settings.provider = test_settings.provider.__class__("ollama")
    elif provider == ProviderName.ollabridge and req.ollabridge:
        if req.ollabridge.base_url:
            test_settings.ollabridge.base_url = req.ollabridge.base_url
        if req.ollabridge.model:
            test_settings.ollabridge.model = req.ollabridge.model
        if req.ollabridge.api_key:
            test_settings.ollabridge.api_key = req.ollabridge.api_key
        test_settings.provider = test_settings.provider.__class__("ollabridge")

    summary = await test_provider_connection(test_settings)
    return ProviderTestResponse(
        configured=summary.configured,
        name=summary.name,
        source=summary.source,
        model=summary.model,
        base_url=summary.base_url,
        connection_type=summary.connection_type,
        has_api_key=summary.has_api_key,
        health=summary.health,
        models_available=summary.models_available,
        warning=summary.warning,
        details=f"Provider {provider.value} test completed",
    )


@app.post("/api/session/start")
async def api_session_start(req: _StartSessionRequest):
    """Start a new session by mode (folder, local_git, github)."""
    from gitpilot.models import (
        StartSessionRequest, StartSessionResponse, WorkspaceMode,
    )
    from gitpilot.session import SessionManager

    mgr = SessionManager()

    if req.mode == WorkspaceMode.folder:
        if not req.folder_path:
            raise HTTPException(status_code=422, detail="folder_path is required for folder mode")
        session = mgr.create_folder_session(req.folder_path)
    elif req.mode == WorkspaceMode.local_git:
        repo_root = req.repo_root or req.folder_path
        if not repo_root:
            raise HTTPException(status_code=422, detail="repo_root is required for local_git mode")
        session = mgr.create_local_git_session(repo_root, req.branch)
    elif req.mode == WorkspaceMode.github:
        if not req.repo_full_name:
            raise HTTPException(status_code=422, detail="repo_full_name is required for github mode")
        session = mgr.create_github_session(req.repo_full_name, req.branch)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown mode: {req.mode}")

    return StartSessionResponse(
        session_id=session.id,
        mode=req.mode,
        title=session.name,
        folder_path=session.folder_path,
        repo_root=session.repo_root,
        repo_full_name=session.repo_full_name,
        branch=session.branch,
    )


@app.post("/api/chat/send")
async def api_chat_message_v2(req: _ChatMessageRequest):
    """Normalized chat message endpoint for the redesigned extension."""
    from gitpilot.models import ChatMessageRequest, ChatMessageResponse
    from gitpilot.session import SessionManager
    import uuid

    mgr = SessionManager()

    # Load session
    try:
        session = mgr.load(req.session_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Session {req.session_id} not found")

    # Use the canonical dispatcher for chat
    answer = ""
    plan = None
    references = []

    repo_full = session.repo_full_name or ""
    try:
        if repo_full:
            result = await dispatch_request(
                user_request=req.message,
                repo_full_name=repo_full,
                branch_name=session.branch,
            )
            if isinstance(result, dict):
                answer = (
                    result.get("result")
                    or result.get("answer")
                    or result.get("message")
                    or result.get("summary")
                    or str(result)
                )
                plan = result.get("plan")
                references = result.get("references", [])
            else:
                answer = str(result)
        else:
            # Folder-mode: use LLM directly for simple chat
            from gitpilot.llm_provider import build_llm
            llm = build_llm()
            local_prompt = _build_local_repo_aware_prompt(req, session)
            answer = llm.call(
                [{"role": "user", "content": local_prompt}]
            )
    except Exception as e:
        err_str = str(e)
        _q_kw = ["insufficient_quota", "exceeded your current quota", "rate_limit_exceeded", "429"]
        if any(kw in err_str.lower() for kw in _q_kw):
            answer = (
                "Your LLM provider credits have been exhausted or you've hit a "
                "rate limit. Please check your billing details or switch to a "
                "free local provider (Ollama / OllaBridge) in Settings."
            )
        elif "No valid task outputs" in err_str:
            answer = (
                "The LLM returned an empty response. This often happens with "
                "small models. Try enabling Lite Mode in Settings."
            )
        else:
            answer = f"Error processing message: {err_str}"

    # Store message in session
    from gitpilot.session import Message
    session.messages.append(Message(role="user", content=req.message))
    session.messages.append(Message(role="assistant", content=answer))
    mgr.save(session)

    # Extract structured edits from the LLM answer so the VS Code
    # extension can offer an "Apply Patch" button for file creation.
    edits = _extract_edits_from_answer(answer) if answer else []

    return ChatMessageResponse(
        session_id=req.session_id,
        answer=answer,
        message_id=str(uuid.uuid4()),
        plan=plan,
        edits=edits,
        references=references,
    )


@app.get("/api/workspace/summary")
async def api_workspace_summary(folder_path: str = Query(default=".")):
    """Get workspace summary for UI display."""
    from gitpilot.workspace import summarize_workspace
    return await summarize_workspace(folder_path)


@app.get("/api/security/scan-workspace")
async def api_security_scan_workspace(path: str = Query(default=".")):
    """Quick action security scan for workspace."""
    from gitpilot.security import scan_current_workspace
    return scan_current_workspace(path)


# ============================================================================
# Static Files & Frontend Serving (SPA Support)
# ============================================================================

STATIC_DIR = Path(__file__).resolve().parent / "web"
ASSETS_DIR = STATIC_DIR / "assets"

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/ping")
async def ping():
    """Zero-dependency ping — used by frontend initApp() to detect when
    the backend is accepting requests. Returns immediately without touching
    any modules, settings, or external APIs. Always fast even during
    CrewAI warmup or GitHub API outages.
    """
    return {"ok": True, "service": "gitpilot", "version": __version__}


@app.get("/api/health")
async def health_check():
    """Lightweight health check — always fast, used by HF Spaces HEALTHCHECK."""
    return {"status": "healthy", "service": "gitpilot-backend"}


@app.get("/api/health/deep")
async def deep_health():
    """Deep health check — verifies LLM provider connectivity and system status."""
    from .resilience import deep_health_check
    result = await deep_health_check()
    status_code = 200 if result["status"] == "healthy" else 503
    return JSONResponse(content=result, status_code=status_code)


@app.get("/healthz")
async def healthz():
    """Health check endpoint (Render/Kubernetes standard)."""
    return {"status": "healthy", "service": "gitpilot-backend"}


@app.get("/", include_in_schema=False)
async def index():
    """Serve the React App entry point."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse(
        {"message": "GitPilot UI not built. The static files directory is missing."},
        status_code=500,
    )


@app.get("/{full_path:path}", include_in_schema=False)
async def catch_all_spa_routes(full_path: str):
    """
    Catch-all route to serve index.html for frontend routing.
    Excludes '/api' paths to ensure genuine API 404s are returned as JSON.
    """
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)

    return JSONResponse(
        {"message": "GitPilot UI not built. The static files directory is missing."},
        status_code=500,
    )

# ---------------------------------------------------------------------------
# OllaBridge Cloud Extension (additive, non-destructive)
# ---------------------------------------------------------------------------
try:
    from .api_ollabridge_ext import apply_ollabridge_extension as _apply_ob
    _apply_ob(app)
    del _apply_ob
except ImportError:
    pass  # Extension not available, skip gracefully


# ============================================================================
# V2 Streaming Agent Endpoints (additive, non-destructive)
#
# These endpoints use the unified AgentEventBus protocol so every client
# (VS Code, React web, HF Spaces) receives the same JSON event shapes.
#
# Existing endpoints are NOT modified. These are /api/v2/ prefixed.
# ============================================================================

import asyncio as _asyncio
from fastapi import Request as _Request
from fastapi.responses import StreamingResponse as _StreamingResponse
from gitpilot.agent_events import get_bus as _get_bus, remove_bus as _remove_bus, EventType as _EvType
from gitpilot.agent_executor import StreamingAgentExecutor as _StreamingExecutor
from gitpilot.approval_protocol import ApprovalGate as _ApprovalGate
from gitpilot.workspace import WorkspaceManager as _V2WorkspaceManager

# Track active executors for cancellation
_active_executors: dict[str, _StreamingExecutor] = {}


@app.post("/api/v2/chat/stream", tags=["v2-streaming"])
async def v2_chat_stream(request: _Request):
    """
    Server-Sent Events endpoint for agent execution.

    Returns text/event-stream. Each line is:
      data: {"type": "text_delta", "text": "..."}\n\n
      data: {"type": "tool_start", "name": "read_file", ...}\n\n
      data: {"type": "done", ...}\n\n

    This is the PREFERRED endpoint for:
      - Hugging Face Spaces (SSE works through nginx/proxies)
      - VS Code extension (can consume SSE via fetch ReadableStream)
      - Any HTTP client that supports streaming
    """
    body = await request.json()
    user_message = body.get("message", "")
    session_id = body.get("session_id", "")
    permission_mode = body.get("permission_mode", "normal")

    if not user_message:
        return JSONResponse({"error": "message is required"}, status_code=400)

    # Load session (reuse existing session manager)
    session = None
    repo_full_name = ""
    branch = None
    token = body.get("token")

    if session_id:
        try:
            session = _session_mgr.load(session_id)
            repo_full_name = session.repo_full_name or ""
            branch = session.branch
        except FileNotFoundError:
            return JSONResponse({"error": "Session not found"}, status_code=404)

    bus = _get_bus(session_id or "ephemeral")
    gate = _ApprovalGate(bus, mode=permission_mode)

    # Resolve workspace (if session has a local workspace)
    workspace = None
    if session and repo_full_name:
        try:
            parts = repo_full_name.split("/", 1)
            if len(parts) == 2:
                ws_mgr = _V2WorkspaceManager()
                workspace = await ws_mgr.ensure_workspace(
                    owner=parts[0], repo=parts[1],
                    token=token, branch=branch,
                )
        except Exception as ws_err:
            logger.warning("Could not resolve workspace: %s", ws_err)

    executor = _StreamingExecutor(
        bus=bus, gate=gate, workspace=workspace,
        ws_manager=_V2WorkspaceManager(),
    )
    _active_executors[session_id or "ephemeral"] = executor

    sub_id, _queue = bus.subscribe()

    async def event_generator():
        """Run agent in background, yield events as SSE."""
        # Start execution as a background task
        exec_task = _asyncio.create_task(
            executor.execute(
                user_message=user_message,
                repo_full_name=repo_full_name,
                branch=branch,
                token=token,
            )
        )

        try:
            async for event in bus.stream(sub_id):
                yield event.to_sse()
                if event.type in (_EvType.DONE, _EvType.ERROR):
                    break
        finally:
            bus.unsubscribe(sub_id)
            _active_executors.pop(session_id or "ephemeral", None)

            # Ensure the task completes
            if not exec_task.done():
                exec_task.cancel()
                try:
                    await exec_task
                except (_asyncio.CancelledError, Exception):
                    pass

            # Save assistant message to session
            if session and exec_task.done() and not exec_task.cancelled():
                try:
                    result = exec_task.result()
                    if result:
                        summary = result.get("summary", "") if isinstance(result, dict) else str(result)
                        session.add_message("assistant", summary[:5000])
                        _session_mgr.save(session)
                except Exception:
                    pass

            _remove_bus(session_id or "ephemeral")

    return _StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/v2/approval/respond", tags=["v2-streaming"])
async def v2_approval_respond(request: _Request):
    """
    Client sends approval/denial for a tool execution.
    Used by all clients (web, VS Code, HF Spaces).
    """
    body = await request.json()
    session_id = body.get("session_id", "ephemeral")
    request_id = body.get("request_id", "")
    approved = body.get("approved", False)
    scope = body.get("scope", "once")

    if not request_id:
        return JSONResponse({"error": "request_id is required"}, status_code=400)

    # The approval gate is created per-stream, so we emit an event
    # that the gate's listener will pick up
    bus = _get_bus(session_id)
    from gitpilot.agent_events import approval_resolved
    await bus.emit(approval_resolved(request_id, approved))

    return {"status": "resolved", "request_id": request_id, "approved": approved}


@app.post("/api/v2/agent/cancel", tags=["v2-streaming"])
async def v2_agent_cancel(request: _Request):
    """Cancel the running agent stream for a session."""
    body = await request.json()
    session_id = body.get("session_id", "ephemeral")

    executor = _active_executors.get(session_id)
    if executor:
        executor.cancel()
        return {"status": "cancelled", "session_id": session_id}

    return JSONResponse({"error": "No active executor for this session"}, status_code=404)


@app.websocket("/ws/v2/sessions/{session_id}")
async def v2_session_websocket(websocket: WebSocket, session_id: str):
    """
    V2 WebSocket with full agent streaming protocol.

    Same event types as SSE endpoint. Client can also send:
      { type: "user_message", content: "..." }
      { type: "approval_response", request_id: "...", approved: true, scope: "session" }
      { type: "cancel" }
      { type: "ping" }
    """
    await websocket.accept()

    try:
        session = _session_mgr.load(session_id)
    except FileNotFoundError:
        await _safe_ws_send_json(websocket, {"type": "error", "message": "Session not found"})
        try:
            await websocket.close()
        except Exception:
            pass
        return

    if not await _safe_ws_send_json(websocket, {
        "type": "session_restored",
        "session_id": session.id,
        "status": session.status,
        "protocol": "v2",
    }):
        logger.info("V2 WebSocket disconnected before handshake for session %s", session_id)
        return

    bus = _get_bus(session_id)
    gate = _ApprovalGate(bus)
    sub_id, _queue = bus.subscribe()

    # Forward bus events -> WebSocket
    async def forward_events():
        try:
            async for event in bus.stream(sub_id):
                if not await _safe_ws_send_json(websocket, event.to_dict()):
                    break
        except Exception:
            pass

    forwarder = _asyncio.create_task(forward_events())

    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            event_type = data.get("type", "")

            if event_type == "user_message":
                content = data.get("content", "")
                if not content:
                    continue

                session.add_message("user", content)
                _session_mgr.save(session)

                # Resolve workspace
                workspace = None
                repo_full = session.repo_full_name or ""
                parts = repo_full.split("/", 1)
                if len(parts) == 2:
                    try:
                        ws_mgr = _V2WorkspaceManager()
                        workspace = await ws_mgr.ensure_workspace(
                            owner=parts[0], repo=parts[1],
                            token=data.get("token"),
                            branch=session.branch,
                        )
                    except Exception:
                        pass

                executor = _StreamingExecutor(
                    bus=bus, gate=gate, workspace=workspace,
                    ws_manager=_V2WorkspaceManager(),
                )
                _active_executors[session_id] = executor

                # Run agent (non-blocking)
                _asyncio.create_task(executor.execute(
                    user_message=content,
                    repo_full_name=repo_full,
                    branch=session.branch,
                    token=data.get("token"),
                ))

            elif event_type == "approval_response":
                gate.resolve(
                    request_id=data.get("request_id", ""),
                    approved=data.get("approved", False),
                    scope=data.get("scope", "once"),
                )

            elif event_type == "cancel":
                executor = _active_executors.get(session_id)
                if executor:
                    executor.cancel()

            elif event_type == "ping":
                if not await _safe_ws_send_json(websocket, {"type": "pong"}):
                    break

    except WebSocketDisconnect:
        logger.info("V2 WebSocket disconnected for session %s", session_id)
    except Exception as e:
        logger.error("V2 WebSocket error for session %s: %s", session_id, e)
    finally:
        forwarder.cancel()
        bus.unsubscribe(sub_id)
        _active_executors.pop(session_id, None)
        gate.cancel_all()
        _remove_bus(session_id)
