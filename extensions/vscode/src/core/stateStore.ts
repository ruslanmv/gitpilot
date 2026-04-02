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
} from "./types";

type StateChangeListener = (state: GitPilotState) => void;

const DEFAULT_GIT_CONTEXT: GitContext = {
  isGitRepo: false,
};

const DEFAULT_STATE: GitPilotState = {
  server: {
    connected: false,
    baseUrl: "http://127.0.0.1:8000",
  },
  provider: {
    configured: false,
  },
  github: {
    connected: false,
    tokenConfigured: false,
  },
  workspace: {
    folderOpen: false,
    git: DEFAULT_GIT_CONTEXT,
    mode: "folder",
  },
  session: {
    active: false,
  },
  readiness: {
    canChat: false,
    canRunProjectActions: false,
    canUseLocalGit: false,
    canUseGithub: false,
    blockers: [],
    warnings: [],
  },
  workflow: {
    selectedMode: "auto",
    source: "auto",
  },
};

export class StateStore {
  private _state: GitPilotState;
  private _listeners: StateChangeListener[] = [];
  private _onDidChangeState = new vscode.EventEmitter<GitPilotState>();

  /** VS Code event for state changes */
  public readonly onDidChangeState = this._onDidChangeState.event;

  constructor() {
    this._state = JSON.parse(JSON.stringify(DEFAULT_STATE));
  }

  /** Get a readonly snapshot of the current state */
  get state(): Readonly<GitPilotState> {
    return this._state;
  }

  /** Subscribe to state changes */
  subscribe(listener: StateChangeListener): vscode.Disposable {
    this._listeners.push(listener);
    return new vscode.Disposable(() => {
      const idx = this._listeners.indexOf(listener);
      if (idx >= 0) {
        this._listeners.splice(idx, 1);
      }
    });
  }

  /** Merge a partial state update */
  update(partial: Partial<GitPilotState>): void {
    this._state = {
      ...this._state,
      ...partial,
      // Deep merge nested objects
      server: partial.server
        ? { ...this._state.server, ...partial.server }
        : this._state.server,
      provider: partial.provider
        ? { ...this._state.provider, ...partial.provider }
        : this._state.provider,
      github: partial.github
        ? { ...this._state.github, ...partial.github }
        : this._state.github,
      workspace: partial.workspace
        ? {
            ...this._state.workspace,
            ...partial.workspace,
            git: partial.workspace.git
              ? { ...this._state.workspace.git, ...partial.workspace.git }
              : this._state.workspace.git,
          }
        : this._state.workspace,
      session: partial.session
        ? { ...this._state.session, ...partial.session }
        : this._state.session,
      readiness: partial.readiness
        ? { ...this._state.readiness, ...partial.readiness }
        : this._state.readiness,
      workflow: partial.workflow
        ? { ...this._state.workflow, ...partial.workflow }
        : this._state.workflow,
    };
    this._notify();
  }

  /** Update server state */
  updateServer(server: Partial<ServerState>): void {
    this.update({ server: { ...this._state.server, ...server } });
  }

  /** Update provider state */
  updateProvider(provider: Partial<ProviderState>): void {
    this.update({ provider: { ...this._state.provider, ...provider } });
  }

  /** Update GitHub state */
  updateGithub(github: Partial<GithubState>): void {
    this.update({ github: { ...this._state.github, ...github } });
  }

  /** Update workspace state */
  updateWorkspace(workspace: Partial<WorkspaceState>): void {
    this.update({
      workspace: { ...this._state.workspace, ...workspace },
    });
  }

  /** Update session state */
  updateSession(session: Partial<SessionState>): void {
    this.update({ session: { ...this._state.session, ...session } });
  }

  /** Update readiness state */
  updateReadiness(readiness: Partial<ReadinessState>): void {
    this.update({
      readiness: { ...this._state.readiness, ...readiness },
    });
  }

  /** Update workflow state */
  updateWorkflow(workflow: Partial<WorkflowState>): void {
    this.update({
      workflow: { ...this._state.workflow, ...workflow },
    });
  }

  /** Reset to default state */
  reset(): void {
    this._state = JSON.parse(JSON.stringify(DEFAULT_STATE));
    this._notify();
  }

  private _notify(): void {
    this._onDidChangeState.fire(this._state);
    for (const listener of this._listeners) {
      try {
        listener(this._state);
      } catch {
        // Swallow listener errors
      }
    }
  }

  dispose(): void {
    this._listeners = [];
    this._onDidChangeState.dispose();
  }
}
