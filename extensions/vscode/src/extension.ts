/**
 * GitPilot VS Code Extension — Main Entry Point
 *
 * Enterprise-grade agentic AI assistant for repositories.
 *
 * Responsibilities:
 * - boot core API/client services
 * - register the primary GitPilot sidebar UI
 * - register tree views, providers, commands, and diagnostics
 * - synchronize backend/server state into the redesigned state store
 * - route webview actions into commands and service calls
 *
 * Notes:
 * - The redesigned GitPilotPanel is the primary visible chat UI.
 * - The legacy ChatViewProvider is retained only for compatibility with
 *   older command handlers during migration.
 * - All AI/provider/orchestration logic remains server-side.
 */

import * as vscode from "vscode";

import { GitPilotApiClient } from "./api/client";
import { getConfig, onConfigChange } from "./utils/config";
import { getWorkspaceContext, ensureGitRepo } from "./utils/context";
import { StatusBarManager } from "./utils/statusBar";

import { ChatViewProvider } from "./views/chatViewProvider";
import { SessionsTreeProvider } from "./tree/sessionsTreeProvider";
import { SkillsTreeProvider } from "./tree/skillsTreeProvider";

import { SecurityDiagnosticsProvider } from "./providers/securityDiagnostics";
import { GitPilotCodeLensProvider } from "./providers/codeLensProvider";
import { GitPilotCodeActionProvider } from "./providers/codeActionProvider";

import { AgentFlowPanel } from "./panels/agentFlowPanel";

import { registerChatCommands } from "./commands/chat";
import { registerReviewCommands } from "./commands/review";
import { registerSecurityCommands } from "./commands/security";
import { registerSkillCommands } from "./commands/skills";
import { registerServerCommands } from "./commands/server";
import { registerGitCommands } from "./commands/git";

// Redesigned service layer
import { StateStore } from "./core/stateStore";
import { GitPilotEvents } from "./core/events";
import { StatusClient } from "./api/statusClient";
import { SessionClient } from "./api/sessionClient";
import { ChatClient } from "./api/chatClient";
import { SettingsClient } from "./api/settingsClient";
import { RepoClient } from "./api/repoClient";
import { WorkspaceResolver } from "./services/workspace/workspaceResolver";
import { GitContextService } from "./services/workspace/gitContextService";
import { ModeResolver } from "./services/workspace/modeResolver";
import { ReadinessEvaluator } from "./services/workspace/readinessEvaluator";
import { SessionCoordinator } from "./services/workspace/sessionCoordinator";
import { ErrorTranslator } from "./services/workspace/errorTranslator";
import { GitPilotPanel } from "./ui/webview/GitPilotPanel";
import { registerWorkspaceCommands } from "./commands/workspaceCommands";
import { registerSetupCommands } from "./commands/setupCommands";
import { registerProviderCommands } from "./commands/providerCommands";
import { registerSessionCommands } from "./commands/sessionCommands";
import { registerChatCommandsV2 } from "./commands/chatCommands";
import {
  detectIntent,
  buildIntentPrefix,
} from "./services/chat/intentDetector";

type DisposableLike = { dispose(): void };

export function activate(context: vscode.ExtensionContext): void {
  const config = getConfig();
  const output = vscode.window.createOutputChannel("GitPilot");

  output.appendLine("[GitPilot] Activating extension...");

  // ─────────────────────────────────────────────────────────────
  // Core API + legacy UI support
  // ─────────────────────────────────────────────────────────────
  const client = new GitPilotApiClient(config.serverUrl, config.githubToken);
  const statusBar = new StatusBarManager();

  // Legacy provider retained for older command handlers during migration.
  const legacyChatProvider = new ChatViewProvider(context.extensionUri, client);

  const sessionsTree = new SessionsTreeProvider(client);
  const skillsTree = new SkillsTreeProvider(client);
  const securityProvider = new SecurityDiagnosticsProvider(client);
  const codeLensProvider = new GitPilotCodeLensProvider(config.showInlineHints);

  // ─────────────────────────────────────────────────────────────
  // Redesigned state + service layer
  // ─────────────────────────────────────────────────────────────
  const stateStore = new StateStore();
  const events = new GitPilotEvents();

  const statusClient = new StatusClient(client);
  const sessionClient = new SessionClient(client);
  const chatClientV2 = new ChatClient(client);
  const settingsClient = new SettingsClient(client);
  const repoClient = new RepoClient(client);

  const workspaceResolver = new WorkspaceResolver();
  const gitContextService = new GitContextService();
  const modeResolver = new ModeResolver();
  const readinessEvaluator = new ReadinessEvaluator();
  const errorTranslator = new ErrorTranslator();

  const sessionCoordinator = new SessionCoordinator(
    sessionClient,
    stateStore,
    errorTranslator
  );

  void repoClient; // reserved for upcoming repo-aware actions
  void events; // currently retained for planned event-driven flows

  context.subscriptions.push(
    output,
    statusBar,
    securityProvider,
    stateStore,
    events,
    workspaceResolver,
    {
      dispose: () => {
        try {
          client.dispose();
        } catch {
          // no-op
        }
      },
    } satisfies DisposableLike
  );

  // ─────────────────────────────────────────────────────────────
  // Connection state → legacy UI + redesigned UI sync
  // ─────────────────────────────────────────────────────────────
  client.onStateChange((connectionState) => {
    const connected = connectionState === "connected";

    statusBar.update(connectionState);
    legacyChatProvider.updateConnectionState(connected);
    stateStore.updateServer({ connected });

    if (connected) {
      sessionsTree.refresh();
      skillsTree.refresh();
      void vscode.commands.executeCommand("gitpilot.refreshStatus");
    }
  });

  // ─────────────────────────────────────────────────────────────
  // Primary redesigned webview
  // ─────────────────────────────────────────────────────────────
  const gitpilotPanel = new GitPilotPanel(
    context.extensionUri,
    stateStore,
    async (msg) => {
      try {
        switch (msg.type) {
          case "INIT": {
            return;
          }

          case "START_SESSION": {
            await sessionCoordinator.startSession(msg.payload.mode);
            return;
          }

          case "CHANGE_MODE": {
            stateStore.updateWorkspace({ mode: msg.payload.mode });
            return;
          }

          case "SEND_CHAT": {
            const sessionId = stateStore.state.session.sessionId;
            const text = msg.payload.text?.trim();

            if (!sessionId || !text) {
              return;
            }

            try {
              const { intent, cleanMessage } = detectIntent(text);
              const intentPrefix = buildIntentPrefix(intent);

              const enrichedMessage = intentPrefix
                ? `[${intentPrefix}]\n\n${cleanMessage || text}`
                : text;

              const workflowMode = stateStore.state.workflow.selectedMode;
              const topologyId =
                workflowMode && workflowMode !== "auto"
                  ? workflowMode
                  : undefined;

              const response = await chatClientV2.sendMessage({
                session_id: sessionId,
                message: enrichedMessage,
                topology_id: topologyId,
              });

              gitpilotPanel.postMessage({
                type: "CHAT_RESPONSE",
                payload: {
                  id: response.message_id || Date.now().toString(),
                  role: "assistant",
                  content: response.answer,
                  createdAt: new Date().toISOString(),
                  plan: response.plan,
                },
              });
            } catch (error: unknown) {
              gitpilotPanel.postMessage({
                type: "ERROR",
                payload: {
                  code: "CHAT_ERROR",
                  title: "Chat Error",
                  message: errorTranslator.translate(error),
                  recoverable: true,
                },
              });
            }
            return;
          }

          case "RUN_QUICK_ACTION": {
            await vscode.commands.executeCommand(
              `gitpilot.${msg.payload.action}`
            );
            return;
          }

          case "OPEN_SETTINGS":
          case "OPEN_WORKSPACE": {
            await vscode.commands.executeCommand(
              "workbench.action.openFolder"
            );
            return;
          }

          case "OPEN_ADMIN_UI": {
            await vscode.commands.executeCommand("gitpilot.showServerInfo");
            return;
          }

          case "OPEN_PROVIDER_SETUP": {
            await vscode.commands.executeCommand("gitpilot.selectProviderV2");
            return;
          }

          case "OPEN_MODEL_SETUP": {
            await vscode.commands.executeCommand("gitpilot.selectModelV2");
            return;
          }

          case "OPEN_LLM_SETTINGS": {
            await vscode.commands.executeCommand("gitpilot.openLlmSettings");
            return;
          }

          case "SET_WORKFLOW_MODE": {
            const selectedMode = msg.payload.mode;

            stateStore.updateWorkflow({
              selectedMode,
              source: selectedMode === "auto" ? "auto" : "user",
            });

            if (selectedMode !== "auto") {
              try {
                await client.setTopology(selectedMode);
              } catch {
                output.appendLine(
                  `[GitPilot] Warning: failed to persist topology "${selectedMode}" to backend.`
                );
              }
            }

            return;
          }

          case "REFRESH_STATUS": {
            await vscode.commands.executeCommand("gitpilot.refreshStatus");
            return;
          }

          default: {
            const exhaustiveCheck: never = msg;
            void exhaustiveCheck;
            return;
          }
        }
      } catch (error: unknown) {
        output.appendLine(
          `[GitPilot] Webview message handling error: ${String(error)}`
        );

        gitpilotPanel.postMessage({
          type: "ERROR",
          payload: {
            code: "WEBVIEW_ACTION_ERROR",
            title: "Action Error",
            message: errorTranslator.translate(error),
            recoverable: true,
          },
        });
      }
    }
  );

  context.subscriptions.push(
    gitpilotPanel,
    vscode.window.registerWebviewViewProvider(
      GitPilotPanel.viewType,
      gitpilotPanel
    )
  );

  // ─────────────────────────────────────────────────────────────
  // Tree views
  // ─────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.window.registerTreeDataProvider(
      "gitpilot.sessionsView",
      sessionsTree
    ),
    vscode.window.registerTreeDataProvider("gitpilot.skillsView", skillsTree)
  );

  // ─────────────────────────────────────────────────────────────
  // Language providers
  // ─────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider(
      { scheme: "file" },
      codeLensProvider
    ),
    vscode.languages.registerCodeActionsProvider(
      { scheme: "file" },
      new GitPilotCodeActionProvider(),
      {
        providedCodeActionKinds:
          GitPilotCodeActionProvider.providedCodeActionKinds,
      }
    )
  );

  // ─────────────────────────────────────────────────────────────
  // Legacy command groups
  // ─────────────────────────────────────────────────────────────
  registerChatCommands(context, client, legacyChatProvider);
  registerReviewCommands(context, legacyChatProvider);
  registerSecurityCommands(context, securityProvider);
  registerSkillCommands(context, client, legacyChatProvider);
  registerServerCommands(context, client, legacyChatProvider);
  registerGitCommands(context, client, legacyChatProvider);

  // ─────────────────────────────────────────────────────────────
  // Redesigned command groups
  // ─────────────────────────────────────────────────────────────
  registerWorkspaceCommands(
    context,
    stateStore,
    workspaceResolver,
    gitContextService,
    modeResolver,
    readinessEvaluator,
    statusClient
  );

  registerSetupCommands(context, stateStore);
  registerProviderCommands(context, stateStore, settingsClient);
  registerSessionCommands(context, stateStore, sessionCoordinator);
  registerChatCommandsV2(context, stateStore, chatClientV2);

  // ─────────────────────────────────────────────────────────────
  // Additional utility commands
  // ─────────────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.commands.registerCommand("gitpilot.showAgentFlow", () => {
      AgentFlowPanel.show(client, context.extensionUri);
    }),

    vscode.commands.registerCommand("gitpilot.refreshSessions", () => {
      sessionsTree.refresh();
    }),

    vscode.commands.registerCommand("gitpilot.refreshSkills", () => {
      skillsTree.refresh();
    }),

    vscode.commands.registerCommand("gitpilot.runCommand", async () => {
      const command = await vscode.window.showInputBox({
        prompt: "Enter command to run via GitPilot",
        placeHolder: "e.g. npm test",
      });

      if (!command) {
        return;
      }

      legacyChatProvider.sendMessageFromCommand(`Run this command: ${command}`);
      await vscode.commands.executeCommand("gitpilot.chatView.focus");
    })
  );

  // ─────────────────────────────────────────────────────────────
  // Configuration change handling
  // ─────────────────────────────────────────────────────────────
  context.subscriptions.push(
    onConfigChange((newConfig) => {
      output.appendLine("[GitPilot] Configuration updated.");

      client.setServerUrl(newConfig.serverUrl);
      client.setToken(newConfig.githubToken);
      codeLensProvider.setEnabled(newConfig.showInlineHints);

      if (newConfig.scanOnSave) {
        securityProvider.enableScanOnSave();
      }
    })
  );

  // ─────────────────────────────────────────────────────────────
  // Security scanning
  // ─────────────────────────────────────────────────────────────
  if (config.scanOnSave) {
    securityProvider.enableScanOnSave();
  }

  // ─────────────────────────────────────────────────────────────
  // Auto-connect
  // ─────────────────────────────────────────────────────────────
  if (config.autoConnect) {
    void client.connect().then((connected) => {
      if (connected) {
        output.appendLine(
          `[GitPilot] Connected to server at ${config.serverUrl}`
        );
      } else {
        output.appendLine(
          `[GitPilot] Auto-connect failed for ${config.serverUrl}`
        );
      }
    });
  }

  // ─────────────────────────────────────────────────────────────
  // Initial workspace + git sync
  // ─────────────────────────────────────────────────────────────
  void initializeWorkspaceState({
    output,
    stateStore,
    workspaceResolver,
    gitContextService,
  });

  // ─────────────────────────────────────────────────────────────
  // Workspace change sync
  // ─────────────────────────────────────────────────────────────
  context.subscriptions.push(
    workspaceResolver.onDidChange(async (info) => {
      stateStore.updateWorkspace({
        folderOpen: info.folderOpen,
        folderPath: info.folderPath,
        folderName: info.folderName,
      });

      if (info.folderPath) {
        try {
          const gitContext = await gitContextService.detect(info.folderPath);
          stateStore.updateWorkspace({ git: gitContext });
        } catch (error: unknown) {
          output.appendLine(
            `[GitPilot] Failed to refresh git context: ${String(error)}`
          );
        }
      }

      await vscode.commands.executeCommand("gitpilot.refreshStatus");
    })
  );

  // ─────────────────────────────────────────────────────────────
  // Workspace repo detection prompt
  // ─────────────────────────────────────────────────────────────
  const workspaceContext = getWorkspaceContext();
  if (workspaceContext.workspaceRoot && !workspaceContext.isGitRepo) {
    setTimeout(() => {
      void ensureGitRepo();
    }, 3000);
  }

  if (workspaceContext.workspaceRoot) {
    if (workspaceContext.isGitRepo) {
      output.appendLine(
        `[GitPilot] Workspace: ${workspaceContext.repoOwner}/${workspaceContext.repoName} (branch: ${workspaceContext.branch || "HEAD"})`
      );
    } else {
      output.appendLine(
        `[GitPilot] Workspace: ${workspaceContext.workspaceRoot} (no git repo detected)`
      );
    }
  }

  output.appendLine("[GitPilot] Extension activated.");
}

export function deactivate(): void {
  // VS Code disposes registered resources automatically.
}

async function initializeWorkspaceState(args: {
  output: vscode.OutputChannel;
  stateStore: StateStore;
  workspaceResolver: WorkspaceResolver;
  gitContextService: GitContextService;
}): Promise<void> {
  const { output, stateStore, workspaceResolver, gitContextService } = args;

  const workspaceInfo = workspaceResolver.resolve();

  stateStore.updateWorkspace({
    folderOpen: workspaceInfo.folderOpen,
    folderPath: workspaceInfo.folderPath,
    folderName: workspaceInfo.folderName,
  });

  if (!workspaceInfo.folderPath) {
    return;
  }

  try {
    const gitContext = await gitContextService.detect(workspaceInfo.folderPath);
    stateStore.updateWorkspace({ git: gitContext });
  } catch (error: unknown) {
    output.appendLine(
      `[GitPilot] Failed to detect initial git context: ${String(error)}`
    );
  }
}