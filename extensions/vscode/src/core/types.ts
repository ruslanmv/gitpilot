/**
 * GitPilot Redesign — Core TypeScript Interfaces
 * Canonical types for extension, webview, and backend API contract.
 */

// ─── Enums & Literals ────────────────────────────────────

export type WorkspaceMode = "folder" | "local_git" | "github";

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

// ─── Workflow / Topology ────────────────────────────────

export type WorkflowMode =
  | "auto"
  | "default" // T1 Dispatch (CrewAI Routing)
  | "gitpilot_code" // T2 ReAct Loop
  | "lite_mode" // T3 Lite Mode
  | "feature_builder" // T4 Feature Builder
  | "bug_hunter" // T5 Bug Hunter
  | "code_inspector" // T6 Code Inspector
  | "architect_mode" // T7 Architect Mode
  | "quick_fix"; // T8 Quick Fix

export interface WorkflowState {
  selectedMode: WorkflowMode;
  effectiveMode?: WorkflowMode;
  source: "user" | "auto";
  reason?: string;
}

// ─── State Models ────────────────────────────────────────

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
}

// ─── Shared Context Types ────────────────────────────────

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

// ─── Message Contract ────────────────────────────────────

export interface ChatMessagePayload {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  createdAt: string;
  plan?: PlanSummary;
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

// Extension → Webview
export type ExtensionToWebviewMessage =
  | { type: "STATE_SYNC"; payload: GitPilotState }
  | { type: "CHAT_RESPONSE"; payload: ChatMessagePayload }
  | { type: "ACTION_RESULT"; payload: ActionResultPayload }
  | { type: "ERROR"; payload: UiErrorPayload }
  | { type: "SESSION_UPDATED"; payload: SessionState };

// Webview → Extension
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
  | { type: "REFRESH_STATUS" };

// ─── Backend API Types ───────────────────────────────────

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