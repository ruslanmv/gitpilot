/**
 * GitPilot Redesign — Settings API Client
 */

import { GitPilotApiClient } from "./client";
import {
  ProviderName,
  ProviderTestRequest,
  ProviderTestResponse,
} from "../core/types";

export interface SettingsData {
  provider: string;
  providers: string[];
  openai: { api_key?: string; model?: string; base_url?: string };
  claude: { api_key?: string; model?: string; base_url?: string };
  watsonx: {
    api_key?: string;
    project_id?: string;
    model_id?: string;
    base_url?: string;
  };
  ollama: { base_url?: string; model?: string };
  ollabridge: { base_url?: string; model?: string; api_key?: string };
}

export class SettingsClient {
  constructor(private client: GitPilotApiClient) {}

  async getSettings(): Promise<SettingsData> {
    return this.client.get<SettingsData>("/api/settings");
  }

  async updateSettings(updates: Partial<SettingsData>): Promise<SettingsData> {
    return this.client.put<SettingsData>("/api/settings/llm", updates);
  }

  async setProvider(provider: ProviderName): Promise<SettingsData> {
    return this.client.post<SettingsData>("/api/settings/provider", {
      provider,
    });
  }

  async testProvider(req: ProviderTestRequest): Promise<ProviderTestResponse> {
    return this.client.post<ProviderTestResponse>("/api/providers/test", req);
  }

  async listModels(
    provider?: string
  ): Promise<{ models: string[]; error?: string }> {
    const query = provider ? `?provider=${provider}` : "";
    return this.client.get(`/api/settings/models${query}`);
  }
}
