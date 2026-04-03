/**
 * GitPilot Redesign — Session Commands
 */

import * as vscode from "vscode";
import { StateStore } from "../core/stateStore";
import { SessionCoordinator } from "../services/workspace/sessionCoordinator";
import { WorkspaceMode } from "../core/types";
import { WORKSPACE_MODE_LABELS } from "../core/constants";

export function registerSessionCommands(
  context: vscode.ExtensionContext,
  stateStore: StateStore,
  sessionCoordinator: SessionCoordinator
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("gitpilot.startFolderSession", () => {
      sessionCoordinator.startSession("folder");
    }),

    vscode.commands.registerCommand("gitpilot.startLocalGitSession", () => {
      sessionCoordinator.startSession("local_git");
    }),

    vscode.commands.registerCommand("gitpilot.startSessionByMode", async () => {
      const modes: WorkspaceMode[] = ["folder", "local_git", "github"];
      const items = modes.map((m) => ({
        label: WORKSPACE_MODE_LABELS[m] || m,
        mode: m,
      }));

      const selected = await vscode.window.showQuickPick(items, {
        placeHolder: "Choose session mode",
      });

      if (selected) {
        sessionCoordinator.startSession(selected.mode);
      }
    }),

    vscode.commands.registerCommand("gitpilot.endSession", () => {
      sessionCoordinator.endSession();
    })
  );
}
