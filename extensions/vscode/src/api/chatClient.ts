/**
 * GitPilot Redesign — Chat API Client
 */

import { GitPilotApiClient } from "./client";
import {
  ChatMessageRequest,
  ChatMessageResponse,
} from "../core/types";

export class ChatClient {
  constructor(private client: GitPilotApiClient) {}

  async sendMessage(req: ChatMessageRequest): Promise<ChatMessageResponse> {
    return this.client.post<ChatMessageResponse>("/api/chat/send", req);
  }

  async reviewPlan(
    repoOwner: string,
    repoName: string,
    goal: string,
    branchName?: string
  ): Promise<any> {
    return this.client.post("/api/chat/plan", {
      repo_owner: repoOwner,
      repo_name: repoName,
      goal,
      branch_name: branchName,
    });
  }

  async applyPlan(
    repoOwner: string,
    repoName: string,
    plan: any,
    branchName?: string
  ): Promise<any> {
    return this.client.post("/api/chat/execute", {
      repo_owner: repoOwner,
      repo_name: repoName,
      plan,
      branch_name: branchName,
    });
  }
}
