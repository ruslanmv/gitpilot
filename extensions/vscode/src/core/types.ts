/**
 * GitPilot Redesign — Core TypeScript Interfaces
 * Canonical types for extension, webview, and backend API contract.
 */

export type WorkspaceMode = "folder" | "local_git" | "github";
export type WorkspaceUiMode = "idle" | "working" | "diff";
export type ExecutionMode = "auto" | "ask" | "plan";

export type ConnectionState =
  | "connected"
  | "disconnected"
  | "connecting"
  | "error";

export type ProviderName =
  | "openai"
  | "claude"
  | "watsonx"
  | "ollama"
  | "ollabridge"
  | "openwebui"
  | "custom";

export type ProviderConnectionType =
  | "local"
  | "api_key"
  | "pairing"
  | "cloud"
  | "managed";

export type ChatScope = "workspace" | "selection" | "file";

export type ChatIntent =
  | "general_chat"
  | "explain_project"
  | "review_file"
  | "fix_selection"
  | "generate_tests"
  | "security_scan"
  | "implement_feature";

export type WorkflowMode =
  | "auto"
  | "default"
  | "gitpilot_code"
  | "lite_mode"
  | "feature_builder"
  | "bug_hunter"
  | "code_inspector"
  | "architect_mode"
  | "quick_fix";

export interface WorkflowState {
  selectedMode: WorkflowMode;
  effectiveMode?: WorkflowMode;
  source: "user" | "auto";
  reason?: string;
}

export type TaskStatus =
  | "idle"
  | "planning"
  | "generating"
  | "reviewing"
  | "ready_to_apply"
  | "applying"
  | "done"
  | "failed";

export interface FileInScope {
  path: string;
  reason?: string;
  confidence?: "low" | "medium" | "high";
}

export interface ChangedFile {
  path: string;
  kind?: "M" | "A" | "D";
  status: "proposed" | "applied" | "failed";
  summary?: string;
  reason?: string;
  hasDiff: boolean;
  diffPreview?: string;
  /** Supplied by the backend when it knows; otherwise read off diffPreview. */
  additions?: number;
  deletions?: number;
  contentPreview?: string;
}

export interface ProposedEdit {
  file: string;
  kind: "create" | "replace" | "patch";
  summary?: string;
  diff?: string;
  content?: string;
}

export interface PlanStepSummary {
  step: number;
  title: string;
  action: string;
  file?: string;
  description: string;
  status?: "pending" | "ready" | "applied" | "failed";
}

export interface PlanSummary {
  goal: string;
  summary: string;
  steps: PlanStepSummary[];
}

export interface ActiveTaskState {
  id?: string;
  title?: string;
  intent?: ChatIntent | string;
  status: TaskStatus;
  summary?: string;
  filesInScope: FileInScope[];
  changedFiles: ChangedFile[];
  edits: ProposedEdit[];
  plan?: PlanSummary;
  startedAt?: string;
  updatedAt?: string;
  error?: string;
}

export interface ProjectContextSummaryState {
  mode?: WorkspaceMode;
  repoName?: string;
  branch?: string;
  indexedFiles?: number;
  languages?: string[];
  manifests?: string[];
  keyFiles?: string[];
  recentFiles?: string[];
  readmeFound?: boolean;
  indexedAt?: string;
}

export interface ChatMessagePayload {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  createdAt: string;
  plan?: PlanSummary;
}

export interface ChatState {
  messages: ChatMessagePayload[];
  lastIntent?: ChatIntent | string;
}

export interface WorkspaceUiState {
  mode: WorkspaceUiMode;
  focusedDiffPath?: string;
  notice?: string;
}

export interface GitContext {
  isGitRepo: boolean;
  repoRoot?: string;
  repoName?: string;
  branch?: string;
  remotes?: string[];
}

export interface ProviderState {
  configured: boolean;
  providerName?: ProviderName;
  source?: ".env" | "settings" | "unknown";
  model?: string;
  baseUrl?: string;
  connectionType?: ProviderConnectionType;
  hasApiKey?: boolean;
  health?: "ok" | "warning" | "error" | "unknown";
  modelsAvailable?: boolean;
  warning?: string;
}

export interface ServerState {
  connected: boolean;
  baseUrl: string;
  lastCheckedAt?: string;
  error?: string;
}

export interface SessionState {
  active: boolean;
  sessionId?: string;
  mode?: WorkspaceMode;
  status?: "idle" | "creating" | "active" | "error";
  title?: string;
  branch?: string;
  folderPath?: string;
  repoName?: string;
  error?: string;
}

export interface GithubState {
  connected: boolean;
  tokenConfigured: boolean;
  username?: string;
}

export interface WorkspaceState {
  folderOpen: boolean;
  folderPath?: string;
  folderName?: string;
  git: GitContext;
  mode: WorkspaceMode;
}

export interface ReadinessState {
  canChat: boolean;
  canRunProjectActions: boolean;
  canUseLocalGit: boolean;
  canUseGithub: boolean;
  blockers: string[];
  warnings: string[];
  primaryCta?: {
    id:
      | "start_folder"
      | "start_local_git"
      | "connect_github"
      | "open_provider_setup"
      | "retry";
    label: string;
  };
}

export interface GitPilotState {
  server: ServerState;
  provider: ProviderState;
  github: GithubState;
  workspace: WorkspaceState;
  session: SessionState;
  readiness: ReadinessState;
  workflow: WorkflowState;
  executionMode: ExecutionMode;
  projectContextSummary: ProjectContextSummaryState;
  activeTask: ActiveTaskState;
  chat: ChatState;
  ui: WorkspaceUiState;
}

export interface FileTreeEntry {
  path: string;
  type: "file" | "dir";
}

export interface StructuredProjectContext {
  mode?: WorkspaceMode;
  workspaceRoot?: string;
  repoRoot?: string;
  repoName?: string;
  branch?: string;
  languages?: string[];
  manifests?: string[];
  keyFiles?: string[];
  readmePreview?: string;
  treeSummary?: FileTreeEntry[];
  indexedAt?: string;
}

export interface StructuredWorkingSet {
  currentFile?: string;
  languageId?: string;
  currentSelection?: string;
  openTabs?: string[];
  recentFiles?: string[];
  relatedFiles?: string[];
}

export interface StructuredTaskContext {
  intent?: ChatIntent | string;
  scope?: ChatScope;
  summary?: string;
}

export interface StructuredContextBundle {
  project_context?: StructuredProjectContext;
  working_set?: StructuredWorkingSet;
  task_context?: StructuredTaskContext;
  legacy_prompt: string;
}

export interface ActionResultPayload {
  action: string;
  success: boolean;
  message?: string;
}

export interface UiErrorPayload {
  code: string;
  title: string;
  message: string;
  recoverable: boolean;
  actionLabel?: string;
}

export type ExtensionToWebviewMessage =
  | { type: "STATE_SYNC"; payload: GitPilotState }
  | { type: "CHAT_RESPONSE"; payload: ChatMessagePayload }
  | { type: "ACTION_RESULT"; payload: ActionResultPayload }
  | { type: "ERROR"; payload: UiErrorPayload }
  | { type: "SESSION_UPDATED"; payload: SessionState }
  | { type: "TASK_STATE_UPDATED"; payload: ActiveTaskState }
  // ── V2 streaming events (additive) ──
  | { type: "CHAT_STREAM_CHUNK"; payload: { content: string } }
  | { type: "CHAT_STREAM_END"; payload: { id?: string; usage?: { prompt_tokens: number; completion_tokens: number } } }
  //: `durationMs` and `parentCallId` arrive with the agentic engine (Batch
  //: V4-E5): the first turns a list of tool names into a readable trace, the
  //: second is what lets a subagent's activity nest under the call that spawned
  //: it rather than appearing as the parent's own work.
  | { type: "AGENT_TOOL_ACTIVITY"; payload: { id: string; name: string; status: "running" | "completed" | "failed"; args?: Record<string, unknown>; result?: string; is_error?: boolean; durationMs?: number; parentCallId?: string; subagent?: string } }
  //: `commandClass` and `sandbox` say *what kind of thing* is being approved.
  //: "Run a shell command?" left the user to read the string themselves.
  | { type: "TOOL_APPROVAL_REQUEST"; payload: { id: string; tool: string; args: Record<string, unknown>; summary: string; diffPreview?: string; riskLevel: "low" | "medium" | "high"; commandClass?: string; sandbox?: { backend?: string; network?: boolean; timeoutSec?: number; workspace?: string } } }
  //: The checklist the model maintains (Batch V4-E1). For a small model the
  //: engine maintains it instead, so the panel looks the same either way.
  | { type: "AGENT_TODO_UPDATE"; payload: { items: Array<{ id: string; text: string; status: string }>; source?: string } }
  //: A subagent's lifecycle, so the trace can show a nested block rather than a
  //: gap where the parent appeared to stall (Batch V4-E2).
  | { type: "AGENT_DELEGATION"; payload: { parentCallId: string; agent: string; title?: string; status: "running" | "completed" | "partial" | "failed" } }
  //: A run that changed files without a passing test run (Batch V4-E3).
  | { type: "AGENT_VERIFICATION"; payload: { cycle: number; maxCycles: number; command: string } }
  //: An interrupted run that could be picked up again (active once V4-F1 lands).
  | { type: "AGENT_RESUMABLE"; payload: { runId: string; sessionId: string; reason: string } }
  | { type: "PLAN_STEP_UPDATE"; payload: { stepIndex: number; stepTitle: string; action: string; status: string } }
  | { type: "TERMINAL_OUTPUT"; payload: { stream: "stdout" | "stderr" | "exit"; text: string; exitCode?: number } }
  | { type: "DIAGNOSTICS_RESULT"; payload: { file?: string; errors: number; warnings: number; entries: Array<{ file: string; line: number; severity: string; message: string }> } }
  | { type: "TEST_RESULT"; payload: { framework: string; passed: number; failed: number; skipped: number; exitCode: number } }
  // ── Composer context: what GitPilot will look at, stated before you send ──
  | {
      type: "EDITOR_CONTEXT";
      payload: { file?: string; range?: string; text?: string } | undefined;
    }
  | { type: "FILE_INDEX"; payload: { files: string[] } }
  | { type: "ATTACH_CONTEXT_FILE"; payload: { path: string } }
  //: A new task begins: the panel drops everything the last one left behind.
  | { type: "SESSION_RESET" };

export type WebviewToExtensionMessage =
  | { type: "INIT" }
  | { type: "START_SESSION"; payload: { mode: WorkspaceMode } }
  | { type: "CHANGE_MODE"; payload: { mode: WorkspaceMode } }
  | { type: "SEND_CHAT"; payload: { text: string } }
  | { type: "REQUEST_FILE_INDEX" }
  | { type: "REWIND" }
  | { type: "PICK_CONTEXT_FILE" }
  | {
      type: "RUN_QUICK_ACTION";
      payload: {
        action:
          | "explain_project"
          | "review_file"
          | "fix_selection"
          | "generate_tests"
          | "security_scan";
      };
    }
  | { type: "OPEN_SETTINGS" }
  | { type: "OPEN_ADMIN_UI" }
  | { type: "OPEN_PROVIDER_SETUP" }
  | { type: "OPEN_MODEL_SETUP" }
  | { type: "OPEN_LLM_SETTINGS" }
  | { type: "OPEN_WORKSPACE" }
  | { type: "SET_WORKFLOW_MODE"; payload: { mode: WorkflowMode } }
  | { type: "REFRESH_STATUS" }
  | { type: "OPEN_SETUP_WIZARD" }
  | { type: "REFRESH_PROJECT_CONTEXT" }
  | { type: "OPEN_CHANGED_FILE"; payload: { path: string } }
  | { type: "OPEN_CHANGED_DIFF"; payload: { path: string } }
  | { type: "REVEAL_FILE"; payload: { path: string } }
  | { type: "APPLY_PROPOSED_CHANGES" }
  | { type: "REVERT_PROPOSED_CHANGES" }
  | { type: "REGENERATE_TASK_PLAN" }
  // ── V2 messages from webview (additive) ──
  | { type: "TOOL_APPROVAL_RESPONSE"; payload: { id: string; approved: boolean; scope?: "once" | "session" | "always" } }
  | { type: "CANCEL_TASK" }
  | { type: "NEW_SESSION" }
  | { type: "APPROVE_PLAN" }
  | { type: "REJECT_PLAN" }
  | { type: "SET_EXECUTION_MODE"; payload: { mode: "auto" | "ask" | "plan" } };

export interface StatusResponse {
  server_ready: boolean;
  provider: ProviderStatusResponse;
  workspace: {
    folder_mode_available: boolean;
    local_git_available: boolean;
    github_mode_available: boolean;
  };
  github: {
    connected: boolean;
    token_configured: boolean;
    username?: string;
  };
}

export interface ProviderStatusResponse {
  configured: boolean;
  name: ProviderName;
  source: ".env" | "settings" | "unknown";
  model?: string;
  base_url?: string;
  connection_type?: ProviderConnectionType;
  has_api_key?: boolean;
  health?: "ok" | "warning" | "error" | "unknown";
  models_available?: boolean;
  warning?: string;
}

export interface StartSessionRequest {
  mode: WorkspaceMode;
  folder_path?: string;
  repo_root?: string;
  repo_full_name?: string;
  branch?: string;
}

export interface StartSessionResponse {
  session_id: string;
  mode: WorkspaceMode;
  title: string;
  status: "active";
  folder_path?: string;
  repo_root?: string;
  repo_full_name?: string;
  branch?: string;
}

export interface ChatMessageRequest {
  session_id: string;
  message: string;
  scope?: ChatScope;
  topology_id?: string;
  intent?: ChatIntent | string;
  project_context?: StructuredProjectContext;
  working_set?: StructuredWorkingSet;
  task_context?: StructuredTaskContext;
}

export interface ChatMessageResponse {
  session_id: string;
  answer: string;
  message_id?: string;
  plan?: PlanSummary;
  filesInScope?: FileInScope[];
  edits?: ProposedEdit[];
  references?: Array<{
    path: string;
    line?: number;
  }>;
}

export interface ProviderTestRequest {
  provider: ProviderName;
  openai?: {
    api_key?: string;
    base_url?: string;
    model?: string;
  };
  claude?: {
    api_key?: string;
    base_url?: string;
    model?: string;
  };
  watsonx?: {
    api_key?: string;
    project_id?: string;
    base_url?: string;
    model_id?: string;
  };
  ollama?: {
    base_url?: string;
    model?: string;
  };
  ollabridge?: {
    base_url?: string;
    model?: string;
    api_key?: string;
    connection_type?: "local" | "api_key" | "pairing";
  };
  openwebui?: {
    base_url?: string;
    model?: string;
    api_key?: string;
  };
  custom?: {
    base_url?: string;
    model?: string;
    api_key?: string;
    headers?: Record<string, string>;
  };
}

export interface ProviderTestResponse extends ProviderStatusResponse {
  details?: string;
}

// ── AI Provider setup (VS Code settings webview) ──────────────────────────
//
// The settings webview never sees a secret. The extension host reads provider
// settings from the GitPilot backend, strips API keys down to a boolean and a
// last-four hint, and sends only that. Keys travel in one direction: from an
// input box, through the host, to the backend.

/** Which OllaBridge connection method the user is configuring. */
export type OllaBridgeMode = "cloud" | "api_key" | "local";

/** A provider's stored configuration, with secrets replaced by a hint. */
export interface SanitizedProviderConfig {
  model?: string;
  base_url?: string;
  /** Watsonx only. */
  project_id?: string;
  /**
   * Custom endpoint only: extra request headers.
   *
   * These are not secrets — they carry attribution and routing values such as
   * a user id — so they round-trip to the webview intact. A header used to
   * pass a bearer token belongs in the API key field instead.
   */
  headers?: Record<string, string>;
  /** True when the backend holds an API key for this provider. */
  hasApiKey: boolean;
  /** Masked tail of the stored key, e.g. "••••A7X2". Never the key itself. */
  apiKeyHint?: string;
}

/** What the overview page renders for one provider. */
export interface ProviderOverviewEntry {
  name: ProviderName;
  label: string;
  description: string;
  /** Model currently configured, when there is one. */
  model?: string;
  active: boolean;
  configured: boolean;
}

/** Everything the provider pages need, refreshed on every load and save. */
export interface ProviderSetupData {
  activeProvider: ProviderName;
  providers: ProviderOverviewEntry[];
  configs: Partial<Record<ProviderName, SanitizedProviderConfig>>;
  /** Which OllaBridge tab the stored configuration corresponds to. */
  ollabridgeMode: OllaBridgeMode;
  serverUrl: string;
}

/**
 * Values a provider page submits.
 *
 * `api_key` follows the "blank means keep" rule: absent or empty leaves the
 * stored key untouched, so a page can save a model change without ever having
 * held the secret. Clearing a key is an explicit REMOVE_PROVIDER_KEY message.
 */
export interface ProviderConfigInput {
  model?: string;
  base_url?: string;
  project_id?: string;
  api_key?: string;
  /** Custom endpoint only: extra request headers, replacing what is stored. */
  headers?: Record<string, string>;
  /** OllaBridge only: which tab produced these values. */
  mode?: OllaBridgeMode;
}

/** Why the provider pages cannot reach the backend right now. */
export type ServerConnectionState =
  | "connecting"
  | "starting"
  | "online"
  | "offline";

/** Messages the settings webview sends to the extension host. */
export type ProviderSettingsMessage =
  | { type: "LOAD_PROVIDER_OVERVIEW" }
  | { type: "LOAD_PROVIDER_MODELS"; provider: ProviderName; requestId: number; force?: boolean }
  | { type: "TEST_PROVIDER"; provider: ProviderName; requestId: number; config: ProviderConfigInput }
  | { type: "SAVE_AND_ACTIVATE_PROVIDER"; provider: ProviderName; requestId: number; config: ProviderConfigInput }
  | { type: "REMOVE_PROVIDER_KEY"; provider: ProviderName; requestId: number }
  | { type: "START_OLLABRIDGE_LOGIN"; baseUrl?: string }
  | { type: "PAIR_OLLABRIDGE"; requestId: number; code: string; baseUrl?: string }
  | { type: "SIGN_OUT_OLLABRIDGE"; requestId: number }
  | { type: "RECONNECT_SERVER" }
  | { type: "START_LOCAL_SERVER" }
  | { type: "CHANGE_SERVER_URL" }
  | { type: "COPY_DIAGNOSTICS" }
  | { type: "OPEN_WEB_ADMIN" }
  | { type: "OPEN_EXTERNAL"; url: string };

/**
 * A topology preset: which agents run, in what shape.
 *
 * `agents_used` is empty for routed topologies — those pick agents per
 * request rather than running a fixed sequence.
 */
export interface TopologySummary {
  id: string;
  name: string;
  description: string;
  category: "system" | "pipeline" | string;
  icon?: string;
  agents_used: string[];
  execution_style: string;
}

// ── MCP servers (VS Code settings webview) ────────────────────────────────
//
// MCP servers augment what the agents can do: attaching a Postgres server
// gives the Explorer schema discovery and the Coder safe queries, for the
// duration it stays enabled. The settings page is where that surface is
// chosen, so it shows not just "which servers" but "which tools, and which
// agents call them".

/** Where a server on offer came from. */
export type McpCatalogSource = "bundled" | "registry";

/** Messages the MCP settings pages send to the extension host. */
export type McpSettingsMessage =
  | { type: "LOAD_MCP_OVERVIEW" }
  | { type: "OPEN_MCP_SERVER"; serverId: string }
  | { type: "SET_MCP_SERVER_ENABLED"; requestId: number; serverId: string; enabled: boolean }
  | { type: "SET_MCP_TOOL_ENABLED"; requestId: number; serverId: string; tool: string; enabled: boolean }
  | { type: "TEST_MCP_SERVER"; requestId: number; serverId: string }
  | { type: "UNINSTALL_MCP_SERVER"; requestId: number; serverId: string }
  | { type: "INSTALL_MCP_SERVER"; requestId: number; entryId: string; source: McpCatalogSource }
  | { type: "SEARCH_MCP_REGISTRY"; requestId: number; query: string }
  | { type: "ADD_CUSTOM_MCP_SERVER"; requestId: number }
  | { type: "INSTALL_MCP_FORGE" }
  | { type: "SYNC_MCP_GATEWAY"; requestId: number }
  | { type: "CONFIGURE_MCP_GATEWAY" };
