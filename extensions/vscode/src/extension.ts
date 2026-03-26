/**
 * GitPilot VS Code Extension — Main Entry Point
 *
 * Enterprise-grade agentic AI assistant for GitHub repositories.
 * The server handles all AI/LLM logic (OllaBridge, OpenAI, Claude,
 * Ollama, Watsonx), agentic workflows (CrewAI), and multi-agent
 * orchestration. The VS Code extension provides the chat UI, code
 * intelligence, provider management, and developer experience.
 *
 * Architecture:
 *   api/           → HTTP client with retry, auth, health monitoring
 *   views/         → Webview providers (Chat sidebar)
 *   tree/          → TreeView providers (Sessions, Skills/Plugins)
 *   providers/     → CodeLens, CodeActions, Security Diagnostics
 *   commands/      → All command handlers (chat, review, security, skills, server, git)
 *   panels/        → Full webview panels (Agent Flow Viewer)
 *   utils/         → Config, context detection, status bar
 */
import * as vscode from 'vscode';

import { GitPilotApiClient } from './api/client';
import { getConfig, onConfigChange } from './utils/config';
import { getWorkspaceContext, ensureGitRepo } from './utils/context';
import { StatusBarManager } from './utils/statusBar';
import { ChatViewProvider } from './views/chatViewProvider';
import { SessionsTreeProvider } from './tree/sessionsTreeProvider';
import { SkillsTreeProvider } from './tree/skillsTreeProvider';
import { SecurityDiagnosticsProvider } from './providers/securityDiagnostics';
import { GitPilotCodeLensProvider } from './providers/codeLensProvider';
import { GitPilotCodeActionProvider } from './providers/codeActionProvider';
import { AgentFlowPanel } from './panels/agentFlowPanel';

import { registerChatCommands } from './commands/chat';
import { registerReviewCommands } from './commands/review';
import { registerSecurityCommands } from './commands/security';
import { registerSkillCommands } from './commands/skills';
import { registerServerCommands } from './commands/server';
import { registerGitCommands } from './commands/git';

// ─── Redesigned Service Layer ────────────────────────────
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

export function activate(context: vscode.ExtensionContext) {
    const config = getConfig();

    // ── Core services ──────────────────────────────────────────
    const client = new GitPilotApiClient(config.serverUrl, config.githubToken);
    const statusBar = new StatusBarManager();
    const chatProvider = new ChatViewProvider(context.extensionUri, client);
    const sessionsTree = new SessionsTreeProvider(client);
    const skillsTree = new SkillsTreeProvider(client);
    const securityProvider = new SecurityDiagnosticsProvider(client);
    const codeLensProvider = new GitPilotCodeLensProvider(config.showInlineHints);

    // ── Connection state → UI sync ─────────────────────────────
    client.onStateChange((state) => {
        statusBar.update(state);
        chatProvider.updateConnectionState(state === 'connected');
        if (state === 'connected') {
            sessionsTree.refresh();
            skillsTree.refresh();
        }
    });

    // ── Register views ─────────────────────────────────────────
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(ChatViewProvider.viewType, chatProvider),
        vscode.window.registerTreeDataProvider('gitpilot.sessionsView', sessionsTree),
        vscode.window.registerTreeDataProvider('gitpilot.skillsView', skillsTree),
    );

    // ── Register providers ─────────────────────────────────────
    const codeLensDisposable = vscode.languages.registerCodeLensProvider(
        { scheme: 'file' },
        codeLensProvider,
    );
    const codeActionDisposable = vscode.languages.registerCodeActionsProvider(
        { scheme: 'file' },
        new GitPilotCodeActionProvider(),
        { providedCodeActionKinds: GitPilotCodeActionProvider.providedCodeActionKinds },
    );
    context.subscriptions.push(codeLensDisposable, codeActionDisposable);

    // ── Register all command groups ────────────────────────────
    registerChatCommands(context, client, chatProvider);
    registerReviewCommands(context, chatProvider);
    registerSecurityCommands(context, securityProvider);
    registerSkillCommands(context, client, chatProvider);
    registerServerCommands(context, client, chatProvider);
    registerGitCommands(context, client, chatProvider);

    // ── Additional commands ────────────────────────────────────
    context.subscriptions.push(
        vscode.commands.registerCommand('gitpilot.showAgentFlow', () => {
            AgentFlowPanel.show(client, context.extensionUri);
        }),

        vscode.commands.registerCommand('gitpilot.refreshSessions', () => {
            sessionsTree.refresh();
        }),

        vscode.commands.registerCommand('gitpilot.refreshSkills', () => {
            skillsTree.refresh();
        }),

        vscode.commands.registerCommand('gitpilot.runCommand', async () => {
            const command = await vscode.window.showInputBox({
                prompt: 'Enter command to run via GitPilot',
                placeHolder: 'e.g., npm test',
            });
            if (!command) { return; }
            chatProvider.sendMessageFromCommand(`Run this command: ${command}`);
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),
    );

    // ── Configuration change handling ──────────────────────────
    context.subscriptions.push(
        onConfigChange((newConfig) => {
            client.setServerUrl(newConfig.serverUrl);
            client.setToken(newConfig.githubToken);
            codeLensProvider.setEnabled(newConfig.showInlineHints);

            if (newConfig.scanOnSave) {
                securityProvider.enableScanOnSave();
            }
        }),
    );

    // ── Security scan on save (if enabled) ─────────────────────
    if (config.scanOnSave) {
        securityProvider.enableScanOnSave();
    }

    // ── Disposables ────────────────────────────────────────────
    context.subscriptions.push(statusBar, securityProvider, { dispose: () => client.dispose() });

    // ── Auto-connect ───────────────────────────────────────────
    if (config.autoConnect) {
        client.connect().then((connected) => {
            if (connected) {
                const channel = vscode.window.createOutputChannel('GitPilot');
                channel.appendLine(`Connected to GitPilot server at ${config.serverUrl}`);
            }
        });
    }

    // ── Git repo detection ─────────────────────────────────────
    // Check workspace for git repo; prompt to initialize if missing
    const wsCtx = getWorkspaceContext();
    if (wsCtx.workspaceRoot && !wsCtx.isGitRepo) {
        // Defer prompt slightly so VS Code is fully loaded
        setTimeout(() => { ensureGitRepo(); }, 3000);
    }
    if (wsCtx.workspaceRoot) {
        const channel = vscode.window.createOutputChannel('GitPilot');
        if (wsCtx.isGitRepo) {
            channel.appendLine(`Workspace: ${wsCtx.repoOwner}/${wsCtx.repoName} (branch: ${wsCtx.branch || 'HEAD'})`);
        } else {
            channel.appendLine(`Workspace: ${wsCtx.workspaceRoot} (no git repo detected)`);
        }
    }

    // ─── Redesigned Service Layer (Phase 1–4) ──────────────

    // Core state
    const stateStore = new StateStore();
    const events = new GitPilotEvents();
    context.subscriptions.push(stateStore, events);

    // Split API clients
    const statusClient = new StatusClient(client);
    const sessionClient = new SessionClient(client);
    const chatClientV2 = new ChatClient(client);
    const settingsClient = new SettingsClient(client);
    const repoClient = new RepoClient(client);

    // Domain services
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
    context.subscriptions.push(workspaceResolver);

    // Register redesigned webview panel
    const gitpilotPanel = new GitPilotPanel(
        context.extensionUri,
        stateStore,
        async (msg) => {
            // Handle webview messages
            switch (msg.type) {
                case "START_SESSION":
                    await sessionCoordinator.startSession(msg.payload.mode);
                    break;
                case "CHANGE_MODE":
                    stateStore.updateWorkspace({ mode: msg.payload.mode });
                    break;
                case "SEND_CHAT":
                    if (stateStore.state.session.sessionId) {
                        try {
                            const resp = await chatClientV2.sendMessage({
                                session_id: stateStore.state.session.sessionId,
                                message: msg.payload.text,
                            });
                            gitpilotPanel.postMessage({
                                type: "CHAT_RESPONSE",
                                payload: {
                                    id: resp.message_id || Date.now().toString(),
                                    role: "assistant",
                                    content: resp.answer,
                                    createdAt: new Date().toISOString(),
                                    plan: resp.plan,
                                },
                            });
                        } catch (err: any) {
                            gitpilotPanel.postMessage({
                                type: "ERROR",
                                payload: {
                                    code: "CHAT_ERROR",
                                    title: "Chat Error",
                                    message: errorTranslator.translate(err),
                                    recoverable: true,
                                },
                            });
                        }
                    }
                    break;
                case "RUN_QUICK_ACTION":
                    vscode.commands.executeCommand(
                        `gitpilot.${msg.payload.action}`
                    );
                    break;
                case "OPEN_SETTINGS":
                    vscode.commands.executeCommand("workbench.action.openFolder");
                    break;
                case "OPEN_ADMIN_UI":
                    vscode.commands.executeCommand("gitpilot.showServerInfo");
                    break;
                case "OPEN_PROVIDER_SETUP":
                    vscode.commands.executeCommand("gitpilot.selectProvider");
                    break;
                case "REFRESH_STATUS":
                    vscode.commands.executeCommand("gitpilot.refreshStatus");
                    break;
            }
        }
    );
    context.subscriptions.push(gitpilotPanel);

    // Register redesigned webview
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(
            "gitpilot.redesignedView",
            gitpilotPanel
        )
    );

    // Register redesigned commands
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

    // Sync state from existing client state changes
    client.onStateChange((connectionState: string) => {
        stateStore.updateServer({
            connected: connectionState === "connected",
        });
        if (connectionState === "connected") {
            vscode.commands.executeCommand("gitpilot.refreshStatus");
        }
    });

    // Initial state sync
    const wsInfo = workspaceResolver.resolve();
    stateStore.updateWorkspace({
        folderOpen: wsInfo.folderOpen,
        folderPath: wsInfo.folderPath,
        folderName: wsInfo.folderName,
    });

    // Workspace change listener
    context.subscriptions.push(
        workspaceResolver.onDidChange(async (info) => {
            stateStore.updateWorkspace({
                folderOpen: info.folderOpen,
                folderPath: info.folderPath,
                folderName: info.folderName,
            });
            if (info.folderPath) {
                const gitCtx = await gitContextService.detect(info.folderPath);
                stateStore.updateWorkspace({ git: gitCtx });
            }
            vscode.commands.executeCommand("gitpilot.refreshStatus");
        })
    );
}

export function deactivate() {}
