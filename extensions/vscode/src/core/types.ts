/**
 * GitPilot Redesign — Core TypeScript Interfaces
 * Canonical types for extension, webview, and backend API contract.
 */

export type WorkspaceMode = "folder" | "local_git" | "github";
export type WorkspaceUiMode = "idle" | "working" | "diff";

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
  | "ollabridge";

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
  | { type: "AGENT_TOOL_ACTIVITY"; payload: { id: string; name: string; status: "running" | "completed" | "failed"; args?: Record<string, unknown>; result?: string; is_error?: boolean } }
  | { type: "TOOL_APPROVAL_REQUEST"; payload: { id: string; tool: string; args: Record<string, unknown>; summary: string; diffPreview?: string; riskLevel: "low" | "medium" | "high" } }
  | { type: "PLAN_STEP_UPDATE"; payload: { stepIndex: number; stepTitle: string; action: string; status: string } }
  | { type: "TERMINAL_OUTPUT"; payload: { stream: "stdout" | "stderr" | "exit"; text: string; exitCode?: number } }
  | { type: "DIAGNOSTICS_RESULT"; payload: { file?: string; errors: number; warnings: number; entries: Array<{ file: string; line: number; severity: string; message: string }> } }
  | { type: "TEST_RESULT"; payload: { framework: string; passed: number; failed: number; skipped: number; exitCode: number } };

export type WebviewToExtensionMessage =
  | { type: "INIT" }
  | { type: "START_SESSION"; payload: { mode: WorkspaceMode } }
  | { type: "CHANGE_MODE"; payload: { mode: WorkspaceMode } }
  | { type: "SEND_CHAT"; payload: { text: string } }
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
  | { type: "REJECT_PLAN" };

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
}

export interface ProviderTestResponse extends ProviderStatusResponse {
  details?: string;
}
