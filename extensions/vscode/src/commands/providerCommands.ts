/**
 * GitPilot Redesign — Provider Commands
 */

import * as vscode from "vscode";
import { StateStore } from "../core/stateStore";
import { SettingsClient } from "../api/settingsClient";
import { PROVIDER_LABELS, PROVIDER_DESCRIPTIONS } from "../core/constants";
import { ProviderName } from "../core/types";

export function registerProviderCommands(
  context: vscode.ExtensionContext,
  stateStore: StateStore,
  settingsClient: SettingsClient
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("gitpilot.openProviderSetupV2", async () => {
      const providers: ProviderName[] = ["openai", "claude", "watsonx", "ollama", "ollabridge"];
      const items = providers.map((p) => ({
        label: PROVIDER_LABELS[p] || p,
        description: PROVIDER_DESCRIPTIONS[p] || "",
        provider: p,
      }));

      const selected = await vscode.window.showQuickPick(items, {
        placeHolder: "Choose your AI provider",
      });

      if (selected) {
        try {
          await settingsClient.setProvider(selected.provider);
          stateStore.updateProvider({
            providerName: selected.provider,
            configured: true,
          });
          vscode.window.showInformationMessage(
            `Provider set to ${PROVIDER_LABELS[selected.provider]}`
          );
          vscode.commands.executeCommand("gitpilot.refreshStatus");
        } catch (err: any) {
          vscode.window.showErrorMessage(`Failed to set provider: ${err.message || err}`);
        }
      }
    })
  );
}
