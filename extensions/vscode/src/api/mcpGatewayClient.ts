/**
 * GitPilot — MCP Context Forge gateway client.
 *
 * The extension does not hold its own copy of the gateway settings. It reads
 * and writes the same three endpoints the desktop and mobile UIs use, so
 * configuring the gateway in one surface configures it everywhere — one
 * authority, three front ends.
 *
 * Secrets travel one way only. Reads report whether a credential exists
 * (`hasPassword`, `hasApiToken`); they never return the value, so a credential
 * cannot leak into the editor, a log, or a settings file.
 */

import { GitPilotApiClient } from "./client";

/** The gateway connection, as a UI is allowed to see it. */
export interface McpGatewayConfig {
  url: string;
  savedUrl: string;
  /** "settings" | "environment" | "default" — where the URL came from. */
  urlSource: string;
  authMode: "auto" | "none" | "token" | "login";
  email: string;
  label: string;
  hasPassword: boolean;
  hasApiToken: boolean;
  passwordFromEnv: boolean;
  apiTokenFromEnv: boolean;
}

/**
 * A partial edit. Omit a secret to keep the stored one — a blank field must
 * never erase a saved credential.
 */
export interface McpGatewayUpdate {
  url?: string;
  authMode?: string;
  email?: string;
  password?: string;
  apiToken?: string;
  label?: string;
  clearPassword?: boolean;
  clearApiToken?: boolean;
}

/** What the gateway actually said when we probed it. */
export interface McpGatewayTestResult {
  ok: boolean;
  url: string;
  reachable: boolean;
  authRequired: boolean | null;
  authenticated: boolean;
  authModeUsed: string;
  /** One sentence naming the next action when something is wrong. */
  detail: string;
  serverCount: number | null;
  warnings: string[];
}

export class McpGatewayClient {
  constructor(private client: GitPilotApiClient) {}

  async get(): Promise<McpGatewayConfig> {
    return this.client.get<McpGatewayConfig>("/api/mcp/gateway");
  }

  async save(update: McpGatewayUpdate): Promise<McpGatewayConfig> {
    return this.client.put<McpGatewayConfig>("/api/mcp/gateway", update);
  }

  /** Probe a draft before committing it, so a bad credential is caught here. */
  async test(update: McpGatewayUpdate = {}): Promise<McpGatewayTestResult> {
    return this.client.post<McpGatewayTestResult>("/api/mcp/gateway/test", update);
  }

  /** Pull the gateway's registry into GitPilot's local server list. */
  async sync(): Promise<{ added?: string[]; kept?: string[]; forge_unreachable?: boolean; error?: string }> {
    return this.client.post("/api/mcp/sync", {});
  }
}
