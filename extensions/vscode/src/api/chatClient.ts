/**
 * GitPilot Redesign — Chat API Client
 */

import { GitPilotApiClient, llmRequestOptions } from "./client";
import {
  ChatMessageRequest,
  ChatMessageResponse,
} from "../core/types";

/**
 * Every call here runs a model, so none of them take the default deadline
 * or the default retry budget. See `llmRequestOptions` for what each half
 * is for; the short version is that inference is slower than a request and
 * re-sending it is worse than waiting.
 */
export class ChatClient {
  constructor(private client: GitPilotApiClient) {}

  async sendMessage(req: ChatMessageRequest): Promise<ChatMessageResponse> {
    return this.client.post<ChatMessageResponse>(
      "/api/chat/send",
      req,
      llmRequestOptions()
    );
  }

  async reviewPlan(
    repoOwner: string,
    repoName: string,
    goal: string,
    branchName?: string
  ): Promise<any> {
    return this.client.post(
      "/api/chat/plan",
      {
        repo_owner: repoOwner,
        repo_name: repoName,
        goal,
        branch_name: branchName,
      },
      llmRequestOptions()
    );
  }

  async applyPlan(
    repoOwner: string,
    repoName: string,
    plan: any,
    branchName?: string
  ): Promise<any> {
    return this.client.post(
      "/api/chat/execute",
      {
        repo_owner: repoOwner,
        repo_name: repoName,
        plan,
        branch_name: branchName,
      },
      llmRequestOptions()
    );
  }
}
