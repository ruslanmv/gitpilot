/**
 * MCP admin API client.
 *
 * Everything the "MCP Servers" settings page needs: what is attached, what is
 * available to attach, and the per-tool switches that decide which of those
 * tools the agents may actually call.
 *
 * Two deliberate absences:
 *
 *  - **No credential ever crosses this boundary.** A server's auth token lives
 *    in an environment variable on the GitPilot host; this client sends the
 *    variable's *name*, never its value.
 *  - **No install implies an enable.** Attaching a server and letting the
 *    agents use it are separate calls, because gaining a capability should
 *    never be a side effect of browsing a catalogue.
 */

import { GitPilotApiClient, REQUEST_TIMEOUTS } from "./client";

/** How long a registry search may take before it is abandoned. */
const REGISTRY_TIMEOUT_MS = 20_000;

/** How long a live probe of one MCP server may take. */
const SERVER_TEST_TIMEOUT_MS = 20_000;

export type ToolRisk = "high" | "medium" | "low";

/** One tool exposed by an attached server. */
export interface McpTool {
  name: string;
  risk: ToolRisk;
  /** Whether the agents may call it right now. */
  enabled: boolean;
  enabled_default: boolean;
  /** Agents that call this tool, for the "Used by" chip. */
  used_by: string[];
  destructive: boolean;
  mutation: boolean;
}

/** An MCP server GitPilot knows about, attached or not. */
export interface McpServer {
  id: string;
  installed: boolean;
  enabled: boolean;
  endpoint: string;
  auth_token_env: string;
  description: string;
  tags: string[];
  tool_count: number;
  tools: McpTool[];
  is_known: boolean;
  orphan: boolean;
  source: string;
}

/** A server on offer — from the bundled catalogue or a remote registry. */
export interface McpCatalogEntry {
  id: string;
  slug: string;
  description: string;
  endpoint: string;
  tags: string[];
  auth?: { type?: string; env?: string };
  metadata?: Record<string, unknown>;
  installed: boolean;
  source?: string;
  homepage?: string;
  version?: string;
  transport?: string;
}

export interface McpStatus {
  gateway_url: string;
  gateway_reachable: boolean;
  gateway_detail: string;
  plugin_enabled: boolean;
  servers_installed: number;
  servers_enabled: number;
  tools_advertised: number;
}

export interface McpServerTestResult {
  ok?: boolean;
  reachable?: boolean;
  detail?: string;
  tools?: string[];
  [key: string]: unknown;
}

export class McpClient {
  constructor(private client: GitPilotApiClient) {}

  /** Gateway reachability and the attached/enabled counts. */
  async status(): Promise<McpStatus> {
    return this.client.request<McpStatus>("/api/mcp/status", {
      timeoutMs: REQUEST_TIMEOUTS.settings,
    });
  }

  async listServers(): Promise<McpServer[]> {
    const resp = await this.client.request<{ servers: McpServer[] }>(
      "/api/mcp/servers",
      { timeoutMs: REQUEST_TIMEOUTS.settings }
    );
    return resp.servers || [];
  }

  /** The catalogue that ships with GitPilot. */
  async listCatalog(): Promise<McpCatalogEntry[]> {
    const resp = await this.client.request<{ items: McpCatalogEntry[] }>(
      "/api/mcp/catalog",
      { timeoutMs: REQUEST_TIMEOUTS.settings }
    );
    return resp.items || [];
  }

  /**
   * Search a remote registry.
   *
   * A registry that is down answers with an empty list and a reason rather
   * than throwing, so the page it is rendered in survives the outage.
   */
  async searchRegistry(
    query: string,
    registryUrl?: string
  ): Promise<{ items: McpCatalogEntry[]; error?: string; registry_url: string }> {
    const params = new URLSearchParams();
    if (query.trim()) {
      params.set("q", query.trim());
    }
    if (registryUrl) {
      params.set("registry_url", registryUrl);
    }
    const qs = params.toString();
    return this.client.request(`/api/mcp/registry/search${qs ? `?${qs}` : ""}`, {
      timeoutMs: REGISTRY_TIMEOUT_MS,
      retries: 0,
    });
  }

  /** Attach a server from the bundled catalogue. It arrives disabled. */
  async installFromCatalog(serverId: string): Promise<void> {
    await this.client.request("/api/mcp/servers/install", {
      method: "POST",
      body: JSON.stringify({ server_id: serverId }),
      timeoutMs: REQUEST_TIMEOUTS.settings,
    });
  }

  /** Attach a server discovered in a remote registry. It arrives disabled. */
  async installFromRegistry(
    entryId: string,
    registryUrl?: string
  ): Promise<void> {
    await this.client.request("/api/mcp/registry/install", {
      method: "POST",
      body: JSON.stringify({ entry_id: entryId, registry_url: registryUrl }),
      timeoutMs: REGISTRY_TIMEOUT_MS,
    });
  }

  /**
   * Attach a server by hand.
   *
   * `authTokenEnv` names an environment variable on the GitPilot host. The
   * token itself is never sent from the editor.
   */
  async installCustom(params: {
    id: string;
    endpoint: string;
    description?: string;
    authTokenEnv?: string;
    tags?: string[];
  }): Promise<void> {
    await this.client.request("/api/mcp/servers/install-custom", {
      method: "POST",
      body: JSON.stringify({
        register_json: {
          name: params.id,
          endpoint: params.endpoint,
          description: params.description || "",
          auth: params.authTokenEnv ? { env: params.authTokenEnv } : {},
          tags: params.tags || [],
        },
      }),
      timeoutMs: REQUEST_TIMEOUTS.settings,
    });
  }

  async uninstall(serverId: string): Promise<void> {
    await this.client.request(
      `/api/mcp/servers/${encodeURIComponent(serverId)}/uninstall`,
      { method: "POST", timeoutMs: REQUEST_TIMEOUTS.settings }
    );
  }

  /** Enable or disable a whole server's tools for the agents. */
  async setServerEnabled(serverId: string, enabled: boolean): Promise<void> {
    const action = enabled ? "enable" : "disable";
    await this.client.request(
      `/api/mcp/servers/${encodeURIComponent(serverId)}/${action}`,
      { method: "POST", timeoutMs: REQUEST_TIMEOUTS.settings }
    );
  }

  /** Enable or disable one tool, overriding its risk-based default. */
  async setToolEnabled(
    serverId: string,
    toolName: string,
    enabled: boolean
  ): Promise<void> {
    await this.client.request(
      `/api/mcp/servers/${encodeURIComponent(serverId)}/tools/${encodeURIComponent(
        toolName
      )}/toggle`,
      {
        method: "POST",
        body: JSON.stringify({ enabled }),
        timeoutMs: REQUEST_TIMEOUTS.settings,
      }
    );
  }

  /** Probe a server for real, so a misconfiguration surfaces here. */
  async testServer(serverId: string): Promise<McpServerTestResult> {
    return this.client.request(
      `/api/mcp/servers/${encodeURIComponent(serverId)}/test`,
      { method: "POST", timeoutMs: SERVER_TEST_TIMEOUT_MS, retries: 0 }
    );
  }

  /** Re-read the gateway's registry and reconcile it with the local store. */
  async syncGateway(): Promise<Record<string, unknown>> {
    return this.client.request("/api/mcp/sync", {
      method: "POST",
      timeoutMs: REGISTRY_TIMEOUT_MS,
      retries: 0,
    });
  }
}
