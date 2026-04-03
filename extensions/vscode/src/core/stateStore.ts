/**
 * GitPilot Redesign — Centralized State Store
 * Single source of truth for all UI decisions.
 */

import * as vscode from "vscode";
import {
  GitPilotState,
  ServerState,
  ProviderState,
  GithubState,
  WorkspaceState,
  SessionState,
  ReadinessState,
  WorkflowState,
  GitContext,
  ProjectContextSummaryState,
  ActiveTaskState,
  TaskStatus,
  FileInScope,
  ChangedFile,
  ProposedEdit,
  ChatMessagePayload,
  ChatState,
  WorkspaceUiState,
} from "./types";

type StateChangeListener = (state: GitPilotState) => void;

const DEFAULT_GIT_CONTEXT: GitContext = { isGitRepo: false };

const DEFAULT_PROJECT_CONTEXT_SUMMARY: ProjectContextSummaryState = {
  mode: undefined,
  repoName: undefined,
  branch: undefined,
  indexedFiles: 0,
  languages: [],
  manifests: [],
  keyFiles: [],
  recentFiles: [],
  readmeFound: false,
  indexedAt: undefined,
};

const DEFAULT_ACTIVE_TASK: ActiveTaskState = {
  id: undefined,
  title: undefined,
  intent: undefined,
  status: "idle",
  summary: undefined,
  filesInScope: [],
  changedFiles: [],
  edits: [],
  plan: undefined,
  startedAt: undefined,
  updatedAt: undefined,
  error: undefined,
};

const DEFAULT_CHAT: ChatState = {
  messages: [],
  lastIntent: undefined,
};

const DEFAULT_UI: WorkspaceUiState = {
  mode: "idle",
  focusedDiffPath: undefined,
  notice: undefined,
};

const DEFAULT_STATE: GitPilotState = {
  server: { connected: false, baseUrl: "http://127.0.0.1:8000" },
  provider: { configured: false },
  github: { connected: false, tokenConfigured: false },
  workspace: { folderOpen: false, git: DEFAULT_GIT_CONTEXT, mode: "folder" },
  session: { active: false },
  readiness: {
    canChat: false,
    canRunProjectActions: false,
    canUseLocalGit: false,
    canUseGithub: false,
    blockers: [],
    warnings: [],
  },
  workflow: { selectedMode: "auto", source: "auto" },
  projectContextSummary: DEFAULT_PROJECT_CONTEXT_SUMMARY,
  activeTask: DEFAULT_ACTIVE_TASK,
  chat: DEFAULT_CHAT,
  ui: DEFAULT_UI,
};

export class StateStore {
  private _state: GitPilotState;
  private _listeners: StateChangeListener[] = [];
  private _onDidChangeState = new vscode.EventEmitter<GitPilotState>();

  public readonly onDidChangeState = this._onDidChangeState.event;

  constructor() {
    this._state = JSON.parse(JSON.stringify(DEFAULT_STATE));
  }

  get state(): Readonly<GitPilotState> {
    return this._state;
  }

  subscribe(listener: StateChangeListener): vscode.Disposable {
    this._listeners.push(listener);
    return new vscode.Disposable(() => {
      const idx = this._listeners.indexOf(listener);
      if (idx >= 0) this._listeners.splice(idx, 1);
    });
  }

  update(partial: Partial<GitPilotState>): void {
    this._state = {
      ...this._state,
      ...partial,
      server: partial.server ? { ...this._state.server, ...partial.server } : this._state.server,
      provider: partial.provider ? { ...this._state.provider, ...partial.provider } : this._state.provider,
      github: partial.github ? { ...this._state.github, ...partial.github } : this._state.github,
      workspace: partial.workspace
        ? {
            ...this._state.workspace,
            ...partial.workspace,
            git: partial.workspace.git
              ? { ...this._state.workspace.git, ...partial.workspace.git }
              : this._state.workspace.git,
          }
        : this._state.workspace,
      session: partial.session ? { ...this._state.session, ...partial.session } : this._state.session,
      readiness: partial.readiness ? { ...this._state.readiness, ...partial.readiness } : this._state.readiness,
      workflow: partial.workflow ? { ...this._state.workflow, ...partial.workflow } : this._state.workflow,
      projectContextSummary: partial.projectContextSummary
        ? {
            ...this._state.projectContextSummary,
            ...partial.projectContextSummary,
            languages: partial.projectContextSummary.languages ?? this._state.projectContextSummary.languages,
            manifests: partial.projectContextSummary.manifests ?? this._state.projectContextSummary.manifests,
            keyFiles: partial.projectContextSummary.keyFiles ?? this._state.projectContextSummary.keyFiles,
            recentFiles: partial.projectContextSummary.recentFiles ?? this._state.projectContextSummary.recentFiles,
          }
        : this._state.projectContextSummary,
      activeTask: partial.activeTask
        ? {
            ...this._state.activeTask,
            ...partial.activeTask,
            filesInScope: partial.activeTask.filesInScope ?? this._state.activeTask.filesInScope,
            changedFiles: partial.activeTask.changedFiles ?? this._state.activeTask.changedFiles,
            edits: partial.activeTask.edits ?? this._state.activeTask.edits,
            plan: partial.activeTask.plan !== undefined ? partial.activeTask.plan : this._state.activeTask.plan,
          }
        : this._state.activeTask,
      chat: partial.chat
        ? {
            ...this._state.chat,
            ...partial.chat,
            messages: partial.chat.messages ?? this._state.chat.messages,
          }
        : this._state.chat,
      ui: partial.ui ? { ...this._state.ui, ...partial.ui } : this._state.ui,
    };

    this._notify();
  }

  updateServer(server: Partial<ServerState>): void { this.update({ server: { ...this._state.server, ...server } }); }
  updateProvider(provider: Partial<ProviderState>): void { this.update({ provider: { ...this._state.provider, ...provider } }); }
  updateGithub(github: Partial<GithubState>): void { this.update({ github: { ...this._state.github, ...github } }); }
  updateWorkspace(workspace: Partial<WorkspaceState>): void { this.update({ workspace: { ...this._state.workspace, ...workspace, git: workspace.git ? { ...this._state.workspace.git, ...workspace.git } : this._state.workspace.git } }); }
  updateSession(session: Partial<SessionState>): void { this.update({ session: { ...this._state.session, ...session } }); }
  updateReadiness(readiness: Partial<ReadinessState>): void { this.update({ readiness: { ...this._state.readiness, ...readiness } }); }
  updateWorkflow(workflow: Partial<WorkflowState>): void { this.update({ workflow: { ...this._state.workflow, ...workflow } }); }
  updateUi(ui: Partial<WorkspaceUiState>): void { this.update({ ui: { ...this._state.ui, ...ui } }); }

  updateProjectContextSummary(patch: Partial<ProjectContextSummaryState>): void {
    this.update({ projectContextSummary: { ...this._state.projectContextSummary, ...patch } });
  }

  updateActiveTask(patch: Partial<ActiveTaskState>): void {
    this.update({
      activeTask: {
        ...this._state.activeTask,
        ...patch,
        updatedAt: patch.updatedAt ?? new Date().toISOString(),
      },
    });

    if (patch.status) {
      this.updateUi({ mode: patch.status === "idle" ? "idle" : this._state.ui.mode === "diff" ? "diff" : "working" });
    }
  }

  setTaskStatus(status: TaskStatus, error?: string): void {
    const now = new Date().toISOString();
    this.update({
      activeTask: {
        ...this._state.activeTask,
        status,
        error: error !== undefined ? error : status === "failed" ? this._state.activeTask.error : undefined,
        startedAt: this._state.activeTask.startedAt ?? (status !== "idle" ? now : undefined),
        updatedAt: now,
      },
      ui: { mode: status === "idle" ? "idle" : this._state.ui.mode === "diff" ? "diff" : "working" },
    });
  }

  setTaskPlan(plan: ActiveTaskState["plan"]): void {
    this.update({ activeTask: { ...this._state.activeTask, plan, updatedAt: new Date().toISOString() } });
  }

  setFilesInScope(filesInScope: FileInScope[]): void {
    this.update({ activeTask: { ...this._state.activeTask, filesInScope, updatedAt: new Date().toISOString() } });
  }

  setChangedFiles(changedFiles: ChangedFile[]): void {
    this.update({ activeTask: { ...this._state.activeTask, changedFiles, updatedAt: new Date().toISOString() } });
  }

  setProposedEdits(edits: ProposedEdit[]): void {
    this.update({ activeTask: { ...this._state.activeTask, edits, updatedAt: new Date().toISOString() } });
  }

  setChatMessages(messages: ChatMessagePayload[]): void {
    this.update({ chat: { ...this._state.chat, messages: this.limitMessages(messages) } });
  }

  appendChatMessage(message: ChatMessagePayload): void {
    this.setChatMessages([...this._state.chat.messages, message]);
  }

  clearTaskState(): void {
    this.update({ activeTask: JSON.parse(JSON.stringify(DEFAULT_ACTIVE_TASK)), ui: { mode: "idle", focusedDiffPath: undefined } });
  }

  reset(): void {
    this._state = JSON.parse(JSON.stringify(DEFAULT_STATE));
    this._notify();
  }

  private limitMessages(messages: ChatMessagePayload[]): ChatMessagePayload[] {
    const seen = new Set<string>();
    const normalized: ChatMessagePayload[] = [];
    for (const message of messages) {
      const key = message.id || `${message.role}:${message.content}:${message.createdAt}`;
      if (seen.has(key)) continue;
      seen.add(key);
      normalized.push(message);
    }
    return normalized.slice(-40);
  }

  private _notify(): void {
    this._onDidChangeState.fire(this._state);
    for (const listener of this._listeners) {
      try { listener(this._state); } catch {}
    }
  }

  dispose(): void {
    this._listeners = [];
    this._onDidChangeState.dispose();
  }
}
