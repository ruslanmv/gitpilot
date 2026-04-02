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

type ProviderConfigMap = {
  openai: SettingsData["openai"];
  claude: SettingsData["claude"];
  watsonx: SettingsData["watsonx"];
  ollama: SettingsData["ollama"];
  ollabridge: SettingsData["ollabridge"];
};

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

  async getActiveProvider(): Promise<ProviderName> {
    const settings = await this.getSettings();
    return settings.provider as ProviderName;
  }

  async getActiveProviderConfig(): Promise<{
    provider: ProviderName;
    config: ProviderConfigMap[ProviderName];
  }> {
    const settings = await this.getSettings();
    const provider = settings.provider as ProviderName;
    return {
      provider,
      config: settings[provider] as ProviderConfigMap[ProviderName],
    };
  }

  async updateProviderConfig<T extends ProviderName>(
    provider: T,
    updates: Partial<ProviderConfigMap[T]>
  ): Promise<SettingsData> {
    return this.updateSettings({
      [provider]: updates,
    } as Partial<SettingsData>);
  }

  async updateProviderModel(
    provider: ProviderName,
    model: string
  ): Promise<SettingsData> {
    if (provider === "watsonx") {
      return this.updateProviderConfig("watsonx", { model_id: model });
    }
    return this.updateProviderConfig(provider, { model } as any);
  }

  async updateProviderBaseUrl(
    provider: ProviderName,
    baseUrl: string
  ): Promise<SettingsData> {
    return this.updateProviderConfig(provider, {
      base_url: baseUrl,
    } as any);
  }
}
