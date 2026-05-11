# gitpilot/public_api/__init__.py
"""GitPilot supported public API surface — stable contract.

Anything re-exported here is part of the stable contract: removals or
breaking signature changes ship behind a deprecation cycle.  Anything
*not* re-exported here is internal and may change in any release.

Import sites should prefer::

    from gitpilot.public_api import ToolPolicy, get_sandbox

over reaching into the implementation modules directly.

Deprecation policy (Batch P4-C)
-------------------------------
* Symbols marked deprecated keep working for **at least one minor
  release** after the deprecation lands.
* Every deprecated callable emits a :class:`DeprecationWarning` on the
  first call per process, with a fixed-format message naming the
  replacement and the removal milestone.
* See :doc:`docs/API_STABILITY.md` for the full contract.

Naming
------
The legacy :mod:`gitpilot.api` module is the FastAPI application
entrypoint and is left untouched; this package is a separate, additive
namespace so neither side disturbs the other.
"""
from __future__ import annotations

# Deprecation pipeline — Batch P4-C.  No public symbols are currently
# scheduled for removal, so the helper is imported but unused at the
# module level.  The first real deprecation will look like::
#
#     from gitpilot._deprecation import deprecated_alias
#     old_name = deprecated_alias(
#         "old_name", new_name,
#         replacement="gitpilot.public_api.new_name", removed_in="2.0",
#     )
#
# See docs/API_STABILITY.md for the full policy.
from gitpilot._deprecation import deprecated_alias  # noqa: F401 — re-exported for callers

# Feature flags ---------------------------------------------------------
from gitpilot.flags import (
    clear_all_overrides,
    clear_override,
    enabled_flags,
    is_on,
    reload as reload_flags,
    set_override,
    set_workspace as set_flags_workspace,
)

# Persistent project context -------------------------------------------
from gitpilot.agents_md import (
    AgentsDoc,
    AgentsLoader,
    InitReport,
    load_for_session,
    run_init,
)

# @-mentions -----------------------------------------------------------
from gitpilot.mentions import (
    ExpandedMention,
    MentionParser,
    MentionResult,
    expand as expand_mentions,
)

# Conversation budget --------------------------------------------------
from gitpilot.context_budget import (
    BudgetPolicy,
    ContextBudgetManager,
    ContextStats,
    Message,
    estimate_tokens,
)

# Tool policy ----------------------------------------------------------
from gitpilot.tool_groups import (
    EditGuard,
    MCPGuard,
    ToolCategory,
    ToolPolicy,
    classify as classify_tool,
    register_category as register_tool_category,
)

# MCP toggles + output validator ---------------------------------------
from gitpilot.mcp_toggles import (
    MCPServerToggles,
    MCPToggleRegistry,
    ToolOutputCheck,
    validate_tool_output,
)

# Custom modes ---------------------------------------------------------
from gitpilot.modes import (
    ActiveModeContext,
    Mode,
    ModeMCPServer,
    ModeRegistry,
    activate_mode,
)

# Slash commands -------------------------------------------------------
from gitpilot.slash_commands import (
    SlashCommand,
    SlashCommandRegistry,
)

# Checkpointing --------------------------------------------------------
from gitpilot.checkpoints import (
    CheckpointRecord,
    CheckpointStore,
    ToolCallDescriptor,
)

# Custom rules ---------------------------------------------------------
from gitpilot.rules import (
    Rule,
    RuleSet,
    compose_rules,
    load_rules,
)

# Sandbox --------------------------------------------------------------
from gitpilot.sandbox import (
    BACKEND_MATRIXLAB,
    BACKEND_OFF,
    BACKEND_SUBPROCESS,
    MatrixLabSandbox,
    NullSandbox,
    Sandbox,
    SandboxError,
    SandboxPolicy,
    SandboxResult,
    SandboxRunError,
    SandboxUnavailableError,
    SubprocessSandbox,
    get_sandbox,
)

# Trusted folders ------------------------------------------------------
from gitpilot.trusted_folders import (
    TrustEntry,
    TrustStatus,
    TrustStore,
    fingerprint as workspace_fingerprint,
)

# Doctor (Batch P1-E) -------------------------------------------------
from gitpilot.doctor import (
    CheckResult,
    DoctorReport,
    render_json as doctor_render_json,
    render_text as doctor_render_text,
    run_checks as doctor_run_checks,
)

# Phase 2 — performance --------------------------------------------------
from gitpilot.prompt_cache import (
    FLAG_PROMPT_CACHE,
    Provider as PromptCacheProvider,
    SystemBlock,
    SystemPayload,
    build_system_blocks,
    to_anthropic_kwargs,
    to_legacy_system_string,
)
from gitpilot.tool_def_pruner import (
    FLAG_LAZY_TOOL_DEFS,
    PruneReport,
    prune_descriptors,
)
from gitpilot.context_cache import (
    FLAG_CONTEXT_CACHE,
    CacheStats as ContextCacheStats,
    build_cached as build_context_cached,
    clear_cache as clear_context_cache,
    get_cache_stats as get_context_cache_stats,
)
from gitpilot.streaming import (
    FLAG_STREAM_V2_SERVER,
    FLAG_STREAM_V2_UI,
    AgentStreamRunner,
    StreamEvent,
    StreamMetrics,
    fallback_adapter as stream_fallback_adapter,
    format_sse_event,
    register_stream_routes,
)
from gitpilot.warmup import (
    FLAG_MODEL_WARMUP,
    WarmupResult,
    register_warmup,
    run_warmup_async,
    run_warmup_now,
)

# Phase 3 — usability ---------------------------------------------------
from gitpilot.init_wizard import (
    FLAG_INIT_WIZARD,
    Prompter as WizardPrompter,
    ScriptedPrompter,
    WizardAnswers,
    WizardError,
    WizardResult,
    render_env as wizard_render_env,
    render_modes as wizard_render_modes,
    run_wizard,
    starter_mode_slugs,
    supported_provider_slugs,
)

# Error envelope (Batch P1-D) -----------------------------------------
from gitpilot.errors import (
    FLAG_ERROR_ENVELOPE,
    GitPilotError,
    NotFoundError,
    UpstreamError,
    ValidationError,
    error_envelope,
    error_envelope_response,
    wrap_errors_envelope,
)


__all__ = [
    # flags
    "clear_all_overrides", "clear_override", "enabled_flags", "is_on",
    "reload_flags", "set_override", "set_flags_workspace",
    # agents.md
    "AgentsDoc", "AgentsLoader", "InitReport", "load_for_session", "run_init",
    # mentions
    "ExpandedMention", "MentionParser", "MentionResult", "expand_mentions",
    # context budget
    "BudgetPolicy", "ContextBudgetManager", "ContextStats", "Message",
    "estimate_tokens",
    # tool policy
    "EditGuard", "MCPGuard", "ToolCategory", "ToolPolicy",
    "classify_tool", "register_tool_category",
    # mcp toggles
    "MCPServerToggles", "MCPToggleRegistry", "ToolOutputCheck",
    "validate_tool_output",
    # modes
    "ActiveModeContext", "Mode", "ModeMCPServer", "ModeRegistry",
    "activate_mode",
    # slash commands
    "SlashCommand", "SlashCommandRegistry",
    # checkpoints
    "CheckpointRecord", "CheckpointStore", "ToolCallDescriptor",
    # rules
    "Rule", "RuleSet", "compose_rules", "load_rules",
    # sandbox
    "BACKEND_MATRIXLAB", "BACKEND_OFF", "BACKEND_SUBPROCESS",
    "MatrixLabSandbox", "NullSandbox", "Sandbox", "SandboxError",
    "SandboxPolicy", "SandboxResult", "SandboxRunError",
    "SandboxUnavailableError", "SubprocessSandbox", "get_sandbox",
    # trusted folders
    "TrustEntry", "TrustStatus", "TrustStore", "workspace_fingerprint",
    # error envelope
    "FLAG_ERROR_ENVELOPE", "GitPilotError", "NotFoundError",
    "UpstreamError", "ValidationError",
    "error_envelope", "error_envelope_response", "wrap_errors_envelope",
    # doctor
    "CheckResult", "DoctorReport",
    "doctor_render_json", "doctor_render_text", "doctor_run_checks",
    # phase 2 — prompt cache
    "FLAG_PROMPT_CACHE", "PromptCacheProvider", "SystemBlock", "SystemPayload",
    "build_system_blocks", "to_anthropic_kwargs", "to_legacy_system_string",
    # phase 2 — lazy tool defs
    "FLAG_LAZY_TOOL_DEFS", "PruneReport", "prune_descriptors",
    # phase 2 — context cache
    "FLAG_CONTEXT_CACHE", "ContextCacheStats",
    "build_context_cached", "clear_context_cache", "get_context_cache_stats",
    # phase 2 — streaming
    "FLAG_STREAM_V2_SERVER", "FLAG_STREAM_V2_UI",
    "AgentStreamRunner", "StreamEvent", "StreamMetrics",
    "stream_fallback_adapter", "format_sse_event", "register_stream_routes",
    # phase 2 — warmup
    "FLAG_MODEL_WARMUP", "WarmupResult",
    "register_warmup", "run_warmup_async", "run_warmup_now",
    # phase 3 — first-run wizard
    "FLAG_INIT_WIZARD", "WizardPrompter", "ScriptedPrompter",
    "WizardAnswers", "WizardError", "WizardResult",
    "wizard_render_env", "wizard_render_modes", "run_wizard",
    "starter_mode_slugs", "supported_provider_slugs",
    # phase 4 — deprecation helper (used by future removals)
    "deprecated_alias",
]
