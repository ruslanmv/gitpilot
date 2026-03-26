/**
 * GitPilot Redesign — Status API Client
 */

import { GitPilotApiClient } from "./client";
import { StatusResponse, ProviderStatusResponse } from "../core/types";

export class StatusClient {
  constructor(private client: GitPilotApiClient) {}

  async getStatus(): Promise<StatusResponse> {
    return this.client.get<StatusResponse>("/api/status");
  }

  async getProviderStatus(): Promise<ProviderStatusResponse> {
    return this.client.get<ProviderStatusResponse>("/api/providers/status");
  }
}
