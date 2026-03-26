/**
 * GitPilot Redesign — Git Context Service
 * Detects local git repo, branch, remotes.
 * Prefers VS Code Git API, falls back to git CLI.
 */

import * as vscode from "vscode";
import * as cp from "child_process";
import { GitContext } from "../../core/types";

export class GitContextService {
  async detect(folderPath?: string): Promise<GitContext> {
    if (!folderPath) {
      return { isGitRepo: false };
    }

    // Try VS Code Git extension first
    const vsCodeGit = await this._tryVSCodeGitApi(folderPath);
    if (vsCodeGit) {
      return vsCodeGit;
    }

    // Fallback to git CLI
    return this._tryGitCli(folderPath);
  }

  private async _tryVSCodeGitApi(
    folderPath: string
  ): Promise<GitContext | null> {
    try {
      const gitExtension = vscode.extensions.getExtension("vscode.git");
      if (!gitExtension) {
        return null;
      }
      const git = gitExtension.isActive
        ? gitExtension.exports
        : await gitExtension.activate();
      const api = git.getAPI(1);
      if (!api) {
        return null;
      }

      const repo = api.repositories.find(
        (r: any) => r.rootUri.fsPath === folderPath
      );
      if (!repo) {
        return null;
      }

      const branch = repo.state?.HEAD?.name;
      const remotes = (repo.state?.remotes || []).map((r: any) => r.name);

      return {
        isGitRepo: true,
        repoRoot: repo.rootUri.fsPath,
        repoName: repo.rootUri.fsPath.split(/[\\/]/).pop(),
        branch,
        remotes,
      };
    } catch {
      return null;
    }
  }

  private _tryGitCli(folderPath: string): GitContext {
    try {
      // Check if git repo
      const repoRoot = cp
        .execSync("git rev-parse --show-toplevel", {
          cwd: folderPath,
          encoding: "utf-8",
          timeout: 5000,
        })
        .trim();

      // Get branch
      let branch: string | undefined;
      try {
        branch = cp
          .execSync("git rev-parse --abbrev-ref HEAD", {
            cwd: folderPath,
            encoding: "utf-8",
            timeout: 5000,
          })
          .trim();
      } catch {
        // ignore
      }

      // Get remotes
      let remotes: string[] = [];
      try {
        const remotesOutput = cp
          .execSync("git remote", {
            cwd: folderPath,
            encoding: "utf-8",
            timeout: 5000,
          })
          .trim();
        remotes = remotesOutput ? remotesOutput.split("\n") : [];
      } catch {
        // ignore
      }

      return {
        isGitRepo: true,
        repoRoot,
        repoName: repoRoot.split(/[\\/]/).pop(),
        branch,
        remotes,
      };
    } catch {
      return { isGitRepo: false };
    }
  }
}
