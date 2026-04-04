/**
 * GitPilot Redesign — Session Coordinator
 * Handles explicit and automatic session lifecycle.
 */

import * as vscode from "vscode";
import { SessionClient } from "../../api/sessionClient";
import { StateStore } from "../../core/stateStore";
import { WorkspaceMode } from "../../core/types";
import { ErrorTranslator } from "./errorTranslator";

export class SessionCoordinator {
  constructor(
    private sessionClient: SessionClient,
    private stateStore: StateStore,
    private errorTranslator: ErrorTranslator
  ) {}

  private resolveBestMode(): WorkspaceMode {
    const state = this.stateStore.state;

    if (state.workspace.git.isGitRepo) {
      return "local_git";
    }

    return "folder";
  }

  async ensureActiveSession(): Promise<void> {
    const session = this.stateStore.state.session;
    if (session.active || session.status === "creating") {
      return;
    }

    await this.startSession(this.resolveBestMode(), true);
  }

  async startSession(mode: WorkspaceMode, quiet = false): Promise<void> {
    const state = this.stateStore.state;

    this.stateStore.updateSession({
      active: false,
      status: "creating",
      error: undefined,
    });

    try {
      const req: any = { mode };

      if (mode === "folder") {
        req.folder_path = state.workspace.folderPath;
      } else if (mode === "local_git") {
        req.repo_root = state.workspace.git.repoRoot || state.workspace.folderPath;
        req.branch = state.workspace.git.branch;
      } else if (mode === "github") {
        req.repo_full_name = state.workspace.git.repoName;
        req.branch = state.workspace.git.branch;
      }

      const response = await this.sessionClient.startSession(req);

      this.stateStore.updateSession({
        active: true,
        sessionId: response.session_id,
        mode: response.mode,
        status: "active",
        title: response.title,
        branch: response.branch,
        folderPath: response.folder_path,
        repoName: response.repo_full_name,
        error: undefined,
      });
    } catch (err: any) {
      const message = this.errorTranslator.translate(err);

      this.stateStore.updateSession({
        active: false,
        status: "error",
        error: message,
      });

      if (!quiet) {
        vscode.window.showErrorMessage(`GitPilot: ${message}`);
      }
    }
  }

  async resumeSession(sessionId: string): Promise<void> {
    try {
      this.stateStore.updateSession({ status: "creating", error: undefined });
      const response = await this.sessionClient.restoreSession(sessionId);

      this.stateStore.updateSession({
        active: true,
        sessionId: response.session_id,
        mode: response.mode,
        status: "active",
        title: response.title,
        branch: response.branch,
        folderPath: response.folder_path,
        repoName: response.repo_full_name,
        error: undefined,
      });
    } catch (err: any) {
      const message = this.errorTranslator.translate(err);
      this.stateStore.updateSession({
        active: false,
        status: "error",
        error: message,
      });
    }
  }

  async endSession(): Promise<void> {
    this.stateStore.updateSession({
      active: false,
      sessionId: undefined,
      status: "idle",
      title: undefined,
      branch: undefined,
      folderPath: undefined,
      repoName: undefined,
      error: undefined,
    });
  }
}
