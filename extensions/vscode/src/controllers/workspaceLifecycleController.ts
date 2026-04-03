import * as vscode from "vscode";
import { SettingsClient } from "../api/settingsClient";
import { StateStore } from "../core/stateStore";
import { ProjectContextService } from "../services/context/projectContextService";
import { ProjectIndexCache } from "../services/context/projectIndexCache";
import { SessionCoordinator } from "../services/workspace/sessionCoordinator";
import { ErrorTranslator } from "../services/workspace/errorTranslator";
import type { StructuredProjectContext } from "../core/types";

export class WorkspaceLifecycleController {
  constructor(
    private readonly settingsClient: SettingsClient,
    private readonly stateStore: StateStore,
    private readonly sessionCoordinator: SessionCoordinator,
    private readonly projectContextService: ProjectContextService,
    private readonly projectIndexCache: ProjectIndexCache<StructuredProjectContext>,
    private readonly errorTranslator: ErrorTranslator,
  ) {}

  clearCaches(): void { this.projectIndexCache.clear(); }

  refreshProjectSummary(input: { workspaceRoot?: string; repoRoot?: string; repoName?: string; branch?: string; mode?: "folder" | "local_git" | "github" }): void {
    this.clearCaches();
    const context = this.projectContextService.build(input);
    this.stateStore.updateProjectContextSummary({
      mode: context?.mode,
      repoName: context?.repoName,
      branch: context?.branch,
      indexedFiles: context?.treeSummary.length,
      languages: context?.languages,
      manifests: context?.manifests,
      keyFiles: context?.keyFiles,
      indexedAt: context?.indexedAt,
    });
  }

  async ensureSessionReady(): Promise<string | undefined> {
    if (!this.stateStore.state.session.sessionId && this.stateStore.state.readiness.canChat) {
      await this.sessionCoordinator.ensureActiveSession();
      await vscode.commands.executeCommand("gitpilot.refreshStatus");
    }
    return this.stateStore.state.session.sessionId;
  }

  async bootstrapProviderDefaults(): Promise<void> {
    const result = await this.settingsClient.bootstrapLocalProviderDefaults();
    if (result.changed) {
      await vscode.commands.executeCommand("gitpilot.refreshStatus");
    }
  }

  async runSetupWizard(): Promise<void> {
    const choices: Array<vscode.QuickPickItem & { run: () => Promise<void> }> = [
      {
        label: "Select provider",
        description: "Choose or configure the AI provider for GitPilot",
        run: async () => { await vscode.commands.executeCommand("gitpilot.selectProviderV2"); },
      },
      {
        label: "Select model",
        description: "Choose the model used by GitPilot",
        run: async () => { await vscode.commands.executeCommand("gitpilot.selectModelV2"); },
      },
      {
        label: "Refresh project context",
        description: "Re-index the current workspace for repo-aware assistance",
        run: async () => { await vscode.commands.executeCommand("gitpilot.refreshProjectContext"); },
      },
    ];

    const picked = await vscode.window.showQuickPick(choices, {
      title: "GitPilot Setup Wizard",
      placeHolder: "Choose the next setup step",
      matchOnDescription: true,
    });
    if (!picked) return;
    try { await picked.run(); }
    catch (error) { vscode.window.showErrorMessage(this.errorTranslator.translate(error)); }
  }
}
