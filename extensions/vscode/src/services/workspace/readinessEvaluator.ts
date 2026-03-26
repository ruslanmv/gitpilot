/**
 * GitPilot Redesign — Readiness Evaluator
 * Determines blockers, warnings, CTAs, and enabled actions.
 */

import {
  ReadinessState,
  ServerState,
  ProviderState,
  WorkspaceState,
  GithubState,
  SessionState,
} from "../../core/types";

export interface ReadinessInput {
  server: ServerState;
  provider: ProviderState;
  workspace: WorkspaceState;
  github: GithubState;
  session: SessionState;
}

export class ReadinessEvaluator {
  evaluate(input: ReadinessInput): ReadinessState {
    const blockers: string[] = [];
    const warnings: string[] = [];

    // Server check
    if (!input.server.connected) {
      blockers.push("Server not connected");
    }

    // Provider check
    if (!input.provider.configured) {
      blockers.push("AI provider not configured");
    } else if (input.provider.health === "error") {
      blockers.push("AI provider connection failed");
    } else if (input.provider.health === "warning") {
      warnings.push("AI provider connection unstable");
    }

    // Workspace check
    if (!input.workspace.folderOpen) {
      blockers.push("No folder open");
    }

    // Git check
    if (!input.workspace.git.isGitRepo) {
      warnings.push("No Git repository detected");
    }

    // GitHub check
    if (!input.github.tokenConfigured) {
      warnings.push("GitHub not connected (optional)");
    }

    // Session check
    if (!input.session.active) {
      warnings.push("No active session");
    }

    const canChat =
      input.server.connected &&
      input.provider.configured &&
      input.workspace.folderOpen;

    const canRunProjectActions = canChat;

    const canUseLocalGit = canChat && input.workspace.git.isGitRepo;

    const canUseGithub = canUseLocalGit && input.github.connected;

    // Determine primary CTA
    let primaryCta: ReadinessState["primaryCta"] = undefined;

    if (!input.workspace.folderOpen) {
      // No CTA from extension (VS Code handles this)
    } else if (!input.server.connected) {
      primaryCta = { id: "retry", label: "Retry Connection" };
    } else if (!input.provider.configured) {
      primaryCta = {
        id: "open_provider_setup",
        label: "Configure AI Provider",
      };
    } else if (!input.session.active && input.workspace.git.isGitRepo) {
      primaryCta = {
        id: "start_local_git",
        label: "Start Local Git Session",
      };
    } else if (!input.session.active) {
      primaryCta = {
        id: "start_folder",
        label: "Start Folder Session",
      };
    }

    return {
      canChat,
      canRunProjectActions,
      canUseLocalGit,
      canUseGithub,
      blockers,
      warnings,
      primaryCta,
    };
  }
}
