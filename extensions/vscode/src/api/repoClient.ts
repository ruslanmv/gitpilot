/**
 * GitPilot Redesign — Repository API Client
 */

import { GitPilotApiClient } from "./client";

export interface RepoInfo {
  name: string;
  full_name: string;
  owner: string;
  private: boolean;
  default_branch: string;
}

export interface WorkspaceSummaryResponse {
  folder_open: boolean;
  folder_path?: string;
  folder_name?: string;
  git_detected: boolean;
  repo_root?: string;
  repo_name?: string;
  branch?: string;
  remotes?: string[];
}

export class RepoClient {
  constructor(private client: GitPilotApiClient) {}

  async getWorkspaceSummary(
    folderPath?: string
  ): Promise<WorkspaceSummaryResponse> {
    const query = folderPath
      ? `?folder_path=${encodeURIComponent(folderPath)}`
      : "";
    return this.client.get<WorkspaceSummaryResponse>(
      `/api/workspace/summary${query}`
    );
  }

  async listRepos(
    page: number = 1,
    perPage: number = 100
  ): Promise<{ repositories: RepoInfo[]; has_more: boolean }> {
    return this.client.get(
      `/api/repos?page=${page}&per_page=${perPage}`
    );
  }

  async getRepoTree(
    owner: string,
    repo: string,
    ref?: string
  ): Promise<{ files: string[] }> {
    const query = ref ? `?ref=${encodeURIComponent(ref)}` : "";
    return this.client.get(`/api/repos/${owner}/${repo}/tree${query}`);
  }
}
