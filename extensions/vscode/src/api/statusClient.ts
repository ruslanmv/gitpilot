/**
 * GitPilot Redesign — Status API Client
 * Uses /api/status when available and falls back to /api/settings
 * so the UI does not show false "Disconnected" states.
 */

import { GitPilotApiClient } from "./client";
import {
  ProviderName,
  ProviderStatusResponse,
  StatusResponse,
} from "../core/types";

type RawSettingsResponse = {
  provider?: string;
  openai?: { model?: string };
  claude?: { model?: string };
  watsonx?: { model_id?: string };
  ollama?: { model?: string };
  ollabridge?: { model?: string };
};

export class StatusClient {
  constructor(private client: GitPilotApiClient) {}

  async getStatus(): Promise<StatusResponse> {
    try {
      return await this.client.get<StatusResponse>("/api/status");
    } catch {
      const settings = await this.client.get<RawSettingsResponse>("/api/settings");
      const provider = (settings.provider || "ollama") as ProviderName;

      const model =
        (provider === "watsonx" ? settings.watsonx?.model_id : undefined) ||
        (provider === "openai" ? settings.openai?.model : undefined) ||
        (provider === "claude" ? settings.claude?.model : undefined) ||
        (provider === "ollama" ? settings.ollama?.model : undefined) ||
        (provider === "ollabridge" ? settings.ollabridge?.model : undefined);

      return {
        server_ready: true,
        provider: {
          configured: Boolean(provider),
          name: provider,
          source: "settings",
          model,
          health: model ? "ok" : "unknown",
          warning: model ? undefined : "Provider reachable, but no model selected yet",
        },
        workspace: {
          folder_mode_available: true,
          local_git_available: true,
          github_mode_available: false,
        },
        github: {
          connected: false,
          token_configured: false,
        },
      };
    }
  }

  async getProviderStatus(): Promise<ProviderStatusResponse> {
    try {
      return await this.client.get<ProviderStatusResponse>("/api/providers/status");
    } catch {
      const status = await this.getStatus();
      return status.provider;
    }
  }
}
