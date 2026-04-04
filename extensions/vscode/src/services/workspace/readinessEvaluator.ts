/**
 * GitPilot Redesign — Readiness Evaluator
 * Keeps first-run friction low. Missing session is a warning, not a blocker.
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

    if (!input.workspace.folderOpen) {
      blockers.push("No folder open");
    }

    if (!input.server.connected) {
      blockers.push("Server not connected");
    }

    if (!input.provider.configured) {
      blockers.push("AI provider not configured");
    } else if (input.provider.health === "error") {
      blockers.push("AI provider connection failed");
    } else if (input.provider.health === "warning") {
      warnings.push("AI provider connection unstable");
    }

    if (!input.workspace.git.isGitRepo) {
      warnings.push("No Git repository detected");
    }

    if (!input.github.tokenConfigured) {
      warnings.push("GitHub not connected (optional)");
    }

    if (!input.session.active) {
      warnings.push("Session will be created automatically");
    }

    const canChat =
      input.workspace.folderOpen &&
      input.server.connected &&
      input.provider.configured &&
      input.provider.health !== "error";

    const canRunProjectActions = canChat;
    const canUseLocalGit = canChat && input.workspace.git.isGitRepo;
    const canUseGithub = canUseLocalGit && input.github.connected;

    let primaryCta: ReadinessState["primaryCta"] = undefined;

    if (!input.workspace.folderOpen) {
      primaryCta = undefined;
    } else if (!input.server.connected) {
      primaryCta = { id: "retry", label: "Retry Connection" };
    } else if (!input.provider.configured) {
      primaryCta = {
        id: "open_provider_setup",
        label: "Configure AI Provider",
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
