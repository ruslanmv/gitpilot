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
  // ── Select Provider (V2) ────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("gitpilot.selectProviderV2", async () => {
      const providers: ProviderName[] = ["openai", "claude", "watsonx", "ollama", "ollabridge"];
      const items = providers.map((p) => ({
        label: PROVIDER_LABELS[p] || p,
        description: PROVIDER_DESCRIPTIONS[p] || "",
        provider: p,
      }));

      const selected = await vscode.window.showQuickPick(items, {
        placeHolder: "Choose your AI provider",
        title: "GitPilot — Select Provider",
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

  // ── Select Model (V2) ──────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("gitpilot.selectModelV2", async () => {
      try {
        const { provider, config } = await settingsClient.getActiveProviderConfig();
        const currentModel =
          (config as any).model ||
          (config as any).model_id ||
          "";

        let models: string[] = [];

        // Prefer live model listing when available.
        if (provider === "ollabridge" || provider === "ollama") {
          try {
            const response = await settingsClient.listModels(provider);
            models = response.models || [];
          } catch {
            // Fall through to manual input
          }
        }

        if (models.length === 0) {
          const manual = await vscode.window.showInputBox({
            prompt: `Enter ${PROVIDER_LABELS[provider] || provider} model`,
            value: currentModel,
            placeHolder: "e.g. qwen2.5-coder, llama3, gpt-4o",
          });
          if (!manual) {
            return;
          }

          await settingsClient.updateProviderModel(provider, manual);
          stateStore.updateProvider({ model: manual });
          vscode.window.showInformationMessage(`Model set to ${manual}`);
          vscode.commands.executeCommand("gitpilot.refreshStatus");
          return;
        }

        const selected = await vscode.window.showQuickPick(
          models.map((model) => ({
            label: model + (model === currentModel ? "  (active)" : ""),
            model,
          })),
          {
            placeHolder: `Current model: ${currentModel || "none"}`,
            title: `Select ${PROVIDER_LABELS[provider] || provider} model`,
          }
        );

        if (!selected) {
          return;
        }

        await settingsClient.updateProviderModel(provider, selected.model);
        stateStore.updateProvider({ model: selected.model });
        vscode.window.showInformationMessage(`Model set to ${selected.model}`);
        vscode.commands.executeCommand("gitpilot.refreshStatus");
      } catch (err: any) {
        vscode.window.showErrorMessage(`Failed to change model: ${err.message || err}`);
      }
    })
  );

  // ── LLM Settings Menu ──────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("gitpilot.openLlmSettings", async () => {
      try {
        const { provider, config } = await settingsClient.getActiveProviderConfig();

        const selectedAction = await vscode.window.showQuickPick(
          [
            { label: "$(server) Change provider", action: "provider" },
            { label: "$(symbol-misc) Change model", action: "model" },
            { label: "$(globe) Edit base URL", action: "base_url" },
            { label: "$(key) Edit API key", action: "api_key" },
            { label: "$(debug-start) Test connection", action: "test" },
          ],
          {
            title: `GitPilot LLM Settings · ${PROVIDER_LABELS[provider] || provider}`,
            placeHolder: "Choose what to edit",
          }
        );

        if (!selectedAction) {
          return;
        }

        if (selectedAction.action === "provider") {
          await vscode.commands.executeCommand("gitpilot.selectProviderV2");
          return;
        }

        if (selectedAction.action === "model") {
          await vscode.commands.executeCommand("gitpilot.selectModelV2");
          return;
        }

        if (selectedAction.action === "base_url") {
          const baseUrl = await vscode.window.showInputBox({
            prompt: `Base URL for ${PROVIDER_LABELS[provider] || provider}`,
            value: (config as any).base_url || "",
            placeHolder: "http://localhost:3000",
          });
          if (typeof baseUrl !== "string") {
            return;
          }
          await settingsClient.updateProviderBaseUrl(provider, baseUrl);
          vscode.window.showInformationMessage("Base URL updated");
          vscode.commands.executeCommand("gitpilot.refreshStatus");
          return;
        }

        if (selectedAction.action === "api_key") {
          if (
            provider !== "openai" &&
            provider !== "claude" &&
            provider !== "watsonx" &&
            provider !== "ollabridge"
          ) {
            vscode.window.showWarningMessage(
              `${PROVIDER_LABELS[provider] || provider} does not use an API key in this setup.`
            );
            return;
          }

          const apiKey = await vscode.window.showInputBox({
            prompt: `API key for ${PROVIDER_LABELS[provider] || provider}`,
            value: (config as any).api_key || "",
            password: true,
          });
          if (typeof apiKey !== "string") {
            return;
          }

          await settingsClient.updateProviderConfig(provider as any, {
            api_key: apiKey,
          } as any);
          vscode.window.showInformationMessage("API key updated");
          vscode.commands.executeCommand("gitpilot.refreshStatus");
          return;
        }

        if (selectedAction.action === "test") {
          try {
            const result = await settingsClient.testProvider({
              provider,
              [provider]: config,
            } as any);
            if (result.health === "ok") {
              vscode.window.showInformationMessage(
                `Connection OK — ${result.details || "Provider is reachable"}`
              );
            } else {
              vscode.window.showWarningMessage(
                `Connection issue: ${result.warning || result.details || "Unknown"}`
              );
            }
          } catch (err: any) {
            vscode.window.showErrorMessage(
              `Connection test failed: ${err.message || err}`
            );
          }
        }
      } catch (err: any) {
        vscode.window.showErrorMessage(
          `Failed to open LLM settings: ${err.message || err}`
        );
      }
    })
  );

  // Keep legacy command name as alias
  context.subscriptions.push(
    vscode.commands.registerCommand("gitpilot.openProviderSetupV2", async () => {
      vscode.commands.executeCommand("gitpilot.selectProviderV2");
    })
  );
}
