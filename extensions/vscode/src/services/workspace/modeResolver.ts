/**
 * GitPilot Redesign — Mode Resolver
 * Determines workspace mode from available context.
 */

import { WorkspaceMode, GitContext, GithubState } from "../../core/types";

export interface ModeResolverInput {
  folderOpen: boolean;
  git: GitContext;
  github: GithubState;
  preferredMode?: WorkspaceMode;
}

export class ModeResolver {
  resolve(input: ModeResolverInput): WorkspaceMode {
    // If user has a preferred mode and it's available, use it
    if (input.preferredMode) {
      if (this.isModeAvailable(input.preferredMode, input)) {
        return input.preferredMode;
      }
    }

    // Auto-resolve: most capable available mode
    if (
      input.git.isGitRepo &&
      input.git.remotes &&
      input.git.remotes.length > 0 &&
      input.github.connected
    ) {
      return "github";
    }

    if (input.git.isGitRepo) {
      return "local_git";
    }

    return "folder";
  }

  isModeAvailable(mode: WorkspaceMode, input: ModeResolverInput): boolean {
    switch (mode) {
      case "folder":
        return input.folderOpen;
      case "local_git":
        return input.folderOpen && input.git.isGitRepo;
      case "github":
        return (
          input.folderOpen &&
          input.git.isGitRepo &&
          input.github.connected &&
          (input.git.remotes?.length ?? 0) > 0
        );
    }
  }

  getAvailableModes(input: ModeResolverInput): WorkspaceMode[] {
    const modes: WorkspaceMode[] = [];
    if (this.isModeAvailable("folder", input)) {
      modes.push("folder");
    }
    if (this.isModeAvailable("local_git", input)) {
      modes.push("local_git");
    }
    if (this.isModeAvailable("github", input)) {
      modes.push("github");
    }
    return modes;
  }
}
