/**
 * GitPilot VS Code Extension — Main Entry Point
 *
 * Enterprise-grade agentic AI assistant for GitHub repositories.
 *
 * Architecture:
 *   api/           → HTTP client with retry, auth, health monitoring
 *   views/         → Webview providers (Chat sidebar)
 *   tree/          → TreeView providers (Sessions, Skills/Plugins)
 *   providers/     → CodeLens, CodeActions, Security Diagnostics
 *   commands/      → All command handlers (chat, review, security, skills, server)
 *   panels/        → Full webview panels (Agent Flow Viewer)
 *   utils/         → Config, context detection, status bar
 */
import * as vscode from 'vscode';

import { GitPilotApiClient } from './api/client';
import { getConfig, onConfigChange } from './utils/config';
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
}

export function deactivate() {}
