/**
 * GitPilot Redesign — Session Coordinator
 * Handles session lifecycle using the new backend contract.
 */

import * as vscode from "vscode";
import { SessionClient } from "../../api/sessionClient";
import { StateStore } from "../../core/stateStore";
import { WorkspaceMode, SessionState } from "../../core/types";
import { ErrorTranslator } from "./errorTranslator";

export class SessionCoordinator {
  constructor(
    private sessionClient: SessionClient,
    private stateStore: StateStore,
    private errorTranslator: ErrorTranslator
  ) {}

  async startSession(mode: WorkspaceMode): Promise<void> {
    const state = this.stateStore.state;

    this.stateStore.updateSession({
      active: false,
      status: "creating",
    });

    try {
      const req: any = { mode };

      if (mode === "folder") {
        req.folder_path = state.workspace.folderPath;
      } else if (mode === "local_git") {
        req.repo_root = state.workspace.git.repoRoot || state.workspace.folderPath;
        req.branch = state.workspace.git.branch;
      } else if (mode === "github") {
        // Build repo_full_name from git remote if available
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
      });
    } catch (err: any) {
      const message = this.errorTranslator.translate(err);
      this.stateStore.updateSession({
        active: false,
        status: "error",
        error: message,
      });
      vscode.window.showErrorMessage(`GitPilot: ${message}`);
    }
  }

  async resumeSession(sessionId: string): Promise<void> {
    try {
      this.stateStore.updateSession({ status: "creating" });
      const response = await this.sessionClient.restoreSession(sessionId);
      this.stateStore.updateSession({
        active: true,
        sessionId: response.session_id,
        mode: response.mode,
        status: "active",
        title: response.title,
        branch: response.branch,
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
    });
  }
}
