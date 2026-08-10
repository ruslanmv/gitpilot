/**
 * GitPilot Redesign — Settings API Client
 */

import { GitPilotApiClient, REQUEST_TIMEOUTS } from "./client";
import {
  ProviderName,
  ProviderTestRequest,
  ProviderTestResponse,
  TopologySummary,
} from "../core/types";

/** How long a model list stays fresh enough to reuse. */
const MODEL_CACHE_TTL_MS = 60_000;

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
  openwebui: { base_url?: string; model?: string; api_key?: string };
  custom: {
    base_url?: string;
    model?: string;
    api_key?: string;
    headers?: Record<string, string>;
  };
}

type ProviderConfigMap = {
  openai: SettingsData["openai"];
  claude: SettingsData["claude"];
  watsonx: SettingsData["watsonx"];
  ollama: SettingsData["ollama"];
  ollabridge: SettingsData["ollabridge"];
  openwebui: SettingsData["openwebui"];
  custom: SettingsData["custom"];
};

export class SettingsClient {
  /** Model lists keyed by provider, with the time they were fetched. */
  private modelCache = new Map<
    string,
    { at: number; result: { models: string[]; error?: string } }
  >();

  constructor(private client: GitPilotApiClient) {}

  async getSettings(): Promise<SettingsData> {
    return this.client.request<SettingsData>("/api/settings", {
      timeoutMs: REQUEST_TIMEOUTS.settings,
    });
  }

  async updateSettings(updates: Partial<SettingsData>): Promise<SettingsData> {
    // A write can change what models are on offer, so the cache goes with it.
    this.modelCache.clear();
    return this.client.request<SettingsData>("/api/settings/llm", {
      method: "PUT",
      body: JSON.stringify(updates),
      timeoutMs: REQUEST_TIMEOUTS.settings,
    });
  }

  async setProvider(provider: ProviderName): Promise<SettingsData> {
    return this.client.request<SettingsData>("/api/settings/provider", {
      method: "POST",
      body: JSON.stringify({ provider }),
      timeoutMs: REQUEST_TIMEOUTS.settings,
    });
  }

  async testProvider(req: ProviderTestRequest): Promise<ProviderTestResponse> {
    return this.client.request<ProviderTestResponse>("/api/providers/test", {
      method: "POST",
      body: JSON.stringify(req),
      timeoutMs: REQUEST_TIMEOUTS.providerTest,
      retries: 0,
    });
  }

  async listModels(
    provider?: string
  ): Promise<{ models: string[]; error?: string }> {
    const query = provider ? `?provider=${provider}` : "";
    return this.client.request(`/api/settings/models${query}`, {
      timeoutMs: REQUEST_TIMEOUTS.models,
      retries: 0,
    });
  }

  /**
   * Model discovery, cached for a minute.
   *
   * Discovery reaches out to the provider and regularly takes 4-15s, so a
   * page that lists models on every render is a page that feels broken. Pass
   * `force` for an explicit "Refresh models".
   */
  async listModelsCached(
    provider: string,
    force = false
  ): Promise<{ models: string[]; error?: string }> {
    const hit = this.modelCache.get(provider);
    if (!force && hit && Date.now() - hit.at < MODEL_CACHE_TTL_MS) {
      return hit.result;
    }
    const result = await this.listModels(provider);
    this.modelCache.set(provider, { at: Date.now(), result });
    return result;
  }

  /** Drop cached model lists, e.g. after the server URL changes. */
  clearModelCache(): void {
    this.modelCache.clear();
  }

  // ── Agent topologies ──────────────────────────────────────────────

  /** Every topology preset the server knows about. */
  async listTopologies(): Promise<TopologySummary[]> {
    return this.client.request<TopologySummary[]>("/api/flow/topologies", {
      timeoutMs: REQUEST_TIMEOUTS.settings,
    });
  }

  /**
   * The topology the server will use when a request does not name one.
   *
   * `null` means no preference is saved, and the server routes each request
   * on intent rather than forcing a fixed pipeline.
   */
  async getTopologyPreference(): Promise<string | null> {
    const resp = await this.client.request<{ topology: string | null }>(
      "/api/settings/topology",
      { timeoutMs: REQUEST_TIMEOUTS.settings }
    );
    return resp.topology ?? null;
  }

  async setTopologyPreference(topology: string): Promise<void> {
    await this.client.request("/api/settings/topology", {
      method: "POST",
      body: JSON.stringify({ topology }),
      timeoutMs: REQUEST_TIMEOUTS.settings,
    });
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

  private extractModel(
    settings: SettingsData,
    provider: ProviderName
  ): string | undefined {
    if (provider === "watsonx") {
      return settings.watsonx?.model_id;
    }
    return (settings as any)?.[provider]?.model;
  }

  /**
   * Prefer a local zero-config provider when one is available.
   * This reduces first-run friction dramatically.
   */
  async bootstrapLocalProviderDefaults(): Promise<{
    changed: boolean;
    provider?: ProviderName;
    model?: string;
  }> {
    const settings = await this.getSettings();
    const currentProvider = (settings.provider as ProviderName) || "ollama";

    const candidateOrder: ProviderName[] = (() => {
      if (currentProvider === "ollama") return ["ollama", "ollabridge"];
      if (currentProvider === "ollabridge") return ["ollama", "ollabridge"];
      return [currentProvider, "ollama", "ollabridge"];
    })();

    for (const provider of candidateOrder) {
      if (provider !== "ollama" && provider !== "ollabridge") {
        continue;
      }

      try {
        const modelResponse = await this.listModels(provider);
        const models = modelResponse.models || [];
        if (!models.length) {
          continue;
        }

        let changed = false;

        if (settings.provider !== provider) {
          await this.setProvider(provider);
          changed = true;
        }

        const freshSettings = changed ? await this.getSettings() : settings;
        const currentModel = this.extractModel(freshSettings, provider);
        const selectedModel =
          currentModel && models.includes(currentModel)
            ? currentModel
            : models[0];

        if (currentModel !== selectedModel) {
          await this.updateProviderModel(provider, selectedModel);
          changed = true;
        }

        return {
          changed,
          provider,
          model: selectedModel,
        };
      } catch {
        // keep trying the next provider
      }
    }

    return { changed: false };
  }
}
