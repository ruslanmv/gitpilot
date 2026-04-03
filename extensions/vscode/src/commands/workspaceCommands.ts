/**
 * GitPilot Redesign — Workspace Commands
 */

import * as vscode from "vscode";
import { StateStore } from "../core/stateStore";
import { WorkspaceResolver } from "../services/workspace/workspaceResolver";
import { GitContextService } from "../services/workspace/gitContextService";
import { ModeResolver } from "../services/workspace/modeResolver";
import { ReadinessEvaluator } from "../services/workspace/readinessEvaluator";
import { StatusClient } from "../api/statusClient";

export function registerWorkspaceCommands(
  context: vscode.ExtensionContext,
  stateStore: StateStore,
  workspaceResolver: WorkspaceResolver,
  gitContextService: GitContextService,
  modeResolver: ModeResolver,
  readinessEvaluator: ReadinessEvaluator,
  statusClient: StatusClient
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("gitpilot.refreshStatus", async () => {
      try {
        // Refresh workspace
        const wsInfo = workspaceResolver.resolve();
        stateStore.updateWorkspace({
          folderOpen: wsInfo.folderOpen,
          folderPath: wsInfo.folderPath,
          folderName: wsInfo.folderName,
        });

        // Refresh git context
        if (wsInfo.folderPath) {
          const gitCtx = await gitContextService.detect(wsInfo.folderPath);
          stateStore.updateWorkspace({ git: gitCtx });
        }

        // Refresh server status
        try {
          const status = await statusClient.getStatus();
          stateStore.updateServer({ connected: true });
          stateStore.updateProvider({
            configured: status.provider.configured,
            providerName: status.provider.name,
            source: status.provider.source,
            model: status.provider.model,
            health: status.provider.health,
          });
          stateStore.updateGithub({
            connected: status.github.connected,
            tokenConfigured: status.github.token_configured,
            username: status.github.username,
          });
        } catch {
          stateStore.updateServer({ connected: false });
        }

        // Resolve mode
        const state = stateStore.state;
        const mode = modeResolver.resolve({
          folderOpen: state.workspace.folderOpen,
          git: state.workspace.git,
          github: state.github,
        });
        stateStore.updateWorkspace({ mode });

        // Evaluate readiness
        const readiness = readinessEvaluator.evaluate({
          server: state.server,
          provider: state.provider,
          workspace: { ...state.workspace, mode },
          github: state.github,
          session: state.session,
        });
        stateStore.updateReadiness(readiness);
      } catch (err: any) {
        vscode.window.showErrorMessage(`GitPilot refresh failed: ${err.message || err}`);
      }
    })
  );
}
