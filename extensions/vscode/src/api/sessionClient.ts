/**
 * GitPilot Redesign — Session API Client
 */

import { GitPilotApiClient } from "./client";
import {
  StartSessionRequest,
  StartSessionResponse,
  WorkspaceMode,
} from "../core/types";

export interface SessionListItem {
  id: string;
  name: string;
  status: string;
  mode?: string;
  message_count?: number;
  created_at?: string;
}

export class SessionClient {
  constructor(private client: GitPilotApiClient) {}

  async startSession(req: StartSessionRequest): Promise<StartSessionResponse> {
    return this.client.post<StartSessionResponse>("/api/session/start", req);
  }

  async listSessions(): Promise<SessionListItem[]> {
    const data = await this.client.get<{ sessions: SessionListItem[] }>(
      "/api/sessions"
    );
    return data.sessions || [];
  }

  async getSession(sessionId: string): Promise<SessionListItem> {
    return this.client.get<SessionListItem>(`/api/sessions/${sessionId}`);
  }

  async restoreSession(sessionId: string): Promise<StartSessionResponse> {
    return this.client.post<StartSessionResponse>(
      `/api/sessions/${sessionId}/restore`,
      {}
    );
  }

  async deleteSession(sessionId: string): Promise<void> {
    await this.client.delete(`/api/sessions/${sessionId}`);
  }
}
