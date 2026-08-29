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

# Streaming framing (Batch V4-E4).  ``StreamMetrics`` is already exported from
# `streaming` below; the bus's implementation is the one the loop uses, so it is
# aliased rather than exported twice — two names for one concept is the drift this
# batch removed.
from gitpilot.agent_events import HEARTBEAT_INTERVAL_SEC
from gitpilot.agent_events import StreamMetrics as BusStreamMetrics

# Sandbox approval tokens (Batch V4-D6) --------------------------------
from gitpilot.sandbox_tokens import (
    ApprovalToken,
    SandboxApprovalStore,
    get_store as get_sandbox_approval_store,
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
from gitpilot.agent import AgentTurn, Dialect, Finality
from gitpilot.agent.model_profile import (
    FLAG_MODEL_PROBE,
    FLAG_MODEL_PROFILES,
    ModelProfile,
    profile_for_settings,
    resolve_profile,
)
from gitpilot.agent.providers import ProviderError, build_provider

# v4 phase C — the loop.  The permission stubs (``Decision``,
# ``PermissivePolicy``, ``Approvals``, ``Hooks``) are deliberately *not* exported:
# Batches V4-D1/D3/D5 replace their bodies, and a stable-contract promise over a
# placeholder would be a promise we intend to break.
from gitpilot.agent.context import (
    AgentContext,
    AgentResult,
    BudgetExceeded,
    BudgetState,
    LedgerEntry,
    State as RunState,
    TodoItem,
    ToolLedger,
)
from gitpilot.agent.headless import HeadlessResult, run_loop_headless
from gitpilot.agent.journal import (
    LineType,
    RunJournal,
    SeedContext,
    list_runs as list_journal_runs,
    project_events,
    read_journal,
    read_seed,
    read_spill,
    terminal_status as journal_terminal_status,
)
from gitpilot.agent.approvals import (
    ApprovalRegistry,
    LoopApprovals,
    PendingApproval,
    get_registry as get_approval_registry,
)
from gitpilot.agent.checkpointing import SessionCheckpointer
from gitpilot.agent.command_class import (
    CommandVerdict,
    classify as classify_command_class,
    classify_command,
    describe as describe_command,
)
from gitpilot.agent.delegation import (
    TEMPLATES as SUBAGENT_TEMPLATES,
    SubagentResult,
    SubagentTemplate,
    template_for as subagent_template,
    template_names as subagent_template_names,
)
from gitpilot.agent.compaction import (
    CompactionReport,
    compact as compact_transcript,
    threshold as compaction_threshold,
)
from gitpilot.agent.hook_bridge import LoopHooks
from gitpilot.agent.replay_events import (
    parse_last_event_id,
    replay_events,
    sse_lines as replay_sse_lines,
)
from gitpilot.agent.resume import (
    FLAG_AGENT_RESUME,
    ReplayedRun,
    ResumeError,
    replay as replay_journal,
    resumable_runs,
)
from gitpilot.agent.system_payload import (
    FLAG_AGENT_SYSTEM_PAYLOAD,
    ActiveMode,
    build_payload as build_agent_system_payload,
    build_system_text as build_agent_system_text,
    mask_for_mode,
    resolve_mode,
)
from gitpilot.toolkit.mcp import (
    FLAG_MCP_TOOLS,
    MCPRegistration,
    MCPToolBinding,
    build_binding as build_mcp_binding,
    prune_for_profile as prune_mcp_for_profile,
    register_from_store as register_mcp_tools,
    risk_for as mcp_risk_for,
)
from gitpilot.yaml_lite import load_yaml_or_json
from gitpilot.topology.schema import (
    ApprovalMode as TopologyApprovalMode,
    ApprovalSpec,
    DialectChoice as TopologyDialect,
    Engine as TopologyEngine,
    LimitsSpec,
    SchemaError as TopologySchemaError,
    Topology,
    TopologyPolicy,
    VerificationSpec,
    build_flow_graph as build_topology_graph,
    parse_document as parse_topology_document,
)
from gitpilot.topology.registry import (
    get_policy as topology_policy,
    registry_for as topologies_for,
    resolve_id as resolve_topology_id,
)
from gitpilot.agent.loop import (
    FLAG_AGENT_LOOP,
    AgentLoop,
    LoopEvents,
    Verification,
    new_run_id,
)
from gitpilot.agent.policy import (
    FLAG_AGENT_POLICY,
    CapabilityMask,
    Decision as PolicyDecision,
    PolicyEngine,
    Qualifier as CapabilityQualifier,
    ResolvedPolicy,
    mask_from_tool_policy,
    resolve_policy,
    restrict_mode,
)
from gitpilot.agent.prompts import build_system_prompt
from gitpilot.agent.runner import (
    AGENT_LOOP_DEFAULT_ON,
    CLASSIC_TOPOLOGY_ID,
    DEFAULT_AGENTIC_TOPOLOGY_ID,
    FLAG_AGENT_LOOP_DEFAULT,
    FLAG_LEGACY_CREW_ENGINES,
    PILOT_TOPOLOGY_ID,
    AgentRunner,
    RunRequest,
    apply_topology_policy,
    should_use_loop,
)
from gitpilot.toolkit import (
    ALL_CAPABILITIES,
    LEGACY_ALIASES,
    Effect as ToolEffect,
    LocalWorkspace,
    PrunedTool,
    RepoBinding,
    Risk as ToolRisk,
    SchemaProfile,
    SchemaRender,
    ToolCall,
    ToolError,
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    validate_arguments as validate_tool_arguments,
)
from gitpilot.toolkit.builtins import (
    FLAG_TOOL_REGISTRY,
    build_default_registry,
)
from gitpilot.toolkit.todo import (
    TODO_STATUSES,
    TodoTransition,
    normalise as normalise_todo,
    phase_todo,
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
    # v4 phase A — canonical tool surface
    "ALL_CAPABILITIES", "FLAG_TOOL_REGISTRY", "LEGACY_ALIASES",
    "LocalWorkspace", "PrunedTool", "RepoBinding",
    "SchemaProfile", "SchemaRender",
    "ToolCall", "ToolEffect", "ToolError", "ToolExecutionContext",
    "ToolRegistry", "ToolResult", "ToolRisk", "ToolSpec",
    "build_default_registry", "validate_tool_arguments",
    # v4 phase B — model layer
    "AgentTurn", "Dialect", "Finality",
    "FLAG_MODEL_PROBE", "FLAG_MODEL_PROFILES", "ModelProfile",
    "ProviderError", "build_provider", "profile_for_settings", "resolve_profile",
    # v4 phase C — the loop
    "FLAG_AGENT_LOOP", "PILOT_TOPOLOGY_ID",
    "AgentContext", "AgentLoop", "AgentResult", "AgentRunner",
    "BudgetExceeded", "BudgetState", "HeadlessResult", "LedgerEntry",
    "LineType", "LoopEvents", "RunJournal", "RunRequest", "RunState",
    "SeedContext", "TodoItem", "ToolLedger",
    "build_system_prompt", "journal_terminal_status", "list_journal_runs",
    "new_run_id", "project_events", "read_journal", "read_seed", "read_spill",
    "run_loop_headless", "should_use_loop",
    # v4 phase D — safety wiring
    "FLAG_AGENT_POLICY",
    "ApprovalRegistry", "ApprovalToken", "CapabilityMask", "CapabilityQualifier",
    "CommandVerdict", "LoopApprovals", "LoopHooks", "PendingApproval",
    "PolicyDecision", "PolicyEngine", "ResolvedPolicy", "SandboxApprovalStore",
    "SessionCheckpointer",
    "classify_command", "classify_command_class", "describe_command",
    "get_approval_registry", "get_sandbox_approval_store",
    "mask_from_tool_policy", "resolve_policy", "restrict_mode",
    # v4 phase E — Claude-Code UX
    "SUBAGENT_TEMPLATES", "SubagentResult", "SubagentTemplate",
    "TODO_STATUSES", "TodoTransition", "Verification",
    "normalise_todo", "phase_todo",
    "subagent_template", "subagent_template_names",
    "BusStreamMetrics", "HEARTBEAT_INTERVAL_SEC",
    # v4 phase F — resume & context
    "FLAG_AGENT_RESUME", "CompactionReport", "ReplayedRun", "ResumeError",
    "compact_transcript", "compaction_threshold", "parse_last_event_id",
    "replay_events", "replay_journal", "replay_sse_lines", "resumable_runs",
    # v4 phase G — topology schema v2 and the flip
    "AGENT_LOOP_DEFAULT_ON", "CLASSIC_TOPOLOGY_ID", "DEFAULT_AGENTIC_TOPOLOGY_ID",
    "FLAG_AGENT_LOOP_DEFAULT", "FLAG_LEGACY_CREW_ENGINES",
    "ApprovalSpec", "LimitsSpec", "Topology", "TopologyApprovalMode",
    "TopologyDialect", "TopologyEngine", "TopologyPolicy", "TopologySchemaError",
    "VerificationSpec",
    "apply_topology_policy", "build_topology_graph", "load_yaml_or_json",
    "parse_topology_document", "resolve_topology_id", "topologies_for",
    "topology_policy",
    # v4 phase H — MCP unification
    "FLAG_MCP_TOOLS", "MCPRegistration", "MCPToolBinding",
    "build_mcp_binding", "mcp_risk_for", "prune_mcp_for_profile",
    "register_mcp_tools",
    # v4 phase H — the live system payload
    "FLAG_AGENT_SYSTEM_PAYLOAD", "ActiveMode",
    "build_agent_system_payload", "build_agent_system_text",
    "mask_for_mode", "resolve_mode",
]
