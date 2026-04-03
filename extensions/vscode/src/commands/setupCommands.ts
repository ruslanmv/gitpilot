/**
 * GitPilot Redesign — Setup Commands
 */

import * as vscode from "vscode";
import { StateStore } from "../core/stateStore";

export function registerSetupCommands(
  context: vscode.ExtensionContext,
  stateStore: StateStore
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("gitpilot.openProviderSetup", () => {
      vscode.commands.executeCommand("gitpilot.selectProvider");
    }),

    vscode.commands.registerCommand("gitpilot.connectGithub", async () => {
      const token = await vscode.window.showInputBox({
        prompt: "Enter your GitHub Personal Access Token",
        password: true,
        placeHolder: "ghp_...",
      });
      if (token) {
        const config = vscode.workspace.getConfiguration("gitpilot");
        await config.update("githubToken", token, vscode.ConfigurationTarget.Global);
        vscode.window.showInformationMessage("GitHub token saved. Refreshing...");
        vscode.commands.executeCommand("gitpilot.refreshStatus");
      }
    })
  );
}
