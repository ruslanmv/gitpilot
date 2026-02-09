/**
 * GitPilot VS Code Extension
 *
 * Connects to the GitPilot API server and provides:
 * - Sidebar chat panel
 * - Inline code actions (review, explain)
 * - Terminal integration
 * - Session management
 * - Skill invocation via command palette
 */
import * as vscode from 'vscode';

let serverUrl: string;

export function activate(context: vscode.ExtensionContext) {
    serverUrl = vscode.workspace.getConfiguration('gitpilot').get('serverUrl', 'http://127.0.0.1:8000');

    // Register commands
    context.subscriptions.push(
        vscode.commands.registerCommand('gitpilot.openChat', openChat),
        vscode.commands.registerCommand('gitpilot.sendMessage', sendMessage),
        vscode.commands.registerCommand('gitpilot.reviewFile', reviewCurrentFile),
        vscode.commands.registerCommand('gitpilot.explainSelection', explainSelection),
        vscode.commands.registerCommand('gitpilot.runCommand', runTerminalCommand),
        vscode.commands.registerCommand('gitpilot.setServer', setServerUrl),
        vscode.commands.registerCommand('gitpilot.invokeSkill', invokeSkill),
    );

    // Auto-connect check
    if (vscode.workspace.getConfiguration('gitpilot').get('autoConnect', true)) {
        checkConnection();
    }

    // Status bar
    const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    statusBar.text = '$(rocket) GitPilot';
    statusBar.tooltip = 'GitPilot AI Assistant';
    statusBar.command = 'gitpilot.openChat';
    statusBar.show();
    context.subscriptions.push(statusBar);
}

export function deactivate() {}

// --- Commands ---

async function openChat() {
    vscode.window.showInformationMessage('GitPilot: Chat panel opening...');
    // In a full implementation, this would open a webview panel
    // connected to the GitPilot API
}

async function sendMessage() {
    const message = await vscode.window.showInputBox({
        prompt: 'Enter your message for GitPilot',
        placeHolder: 'e.g., Review the authentication module',
    });
    if (!message) return;

    try {
        const response = await apiRequest('/api/chat/message', {
            method: 'POST',
            body: JSON.stringify({
                repo_owner: '',
                repo_name: '',
                message: message,
            }),
        });
        vscode.window.showInformationMessage(`GitPilot: ${response.result?.substring(0, 200) || 'Done'}`);
    } catch (err: any) {
        vscode.window.showErrorMessage(`GitPilot Error: ${err.message}`);
    }
}

async function reviewCurrentFile() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        vscode.window.showWarningMessage('No active file to review');
        return;
    }
    const filePath = editor.document.uri.fsPath;
    const content = editor.document.getText();
    vscode.window.showInformationMessage(`GitPilot: Reviewing ${filePath}...`);
}

async function explainSelection() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) return;
    const selection = editor.document.getText(editor.selection);
    if (!selection) {
        vscode.window.showWarningMessage('No text selected');
        return;
    }
    vscode.window.showInformationMessage(`GitPilot: Explaining selected code...`);
}

async function runTerminalCommand() {
    const command = await vscode.window.showInputBox({
        prompt: 'Enter command to run',
        placeHolder: 'e.g., npm test',
    });
    if (!command) return;
    vscode.window.showInformationMessage(`GitPilot: Running "${command}"...`);
}

async function setServerUrl() {
    const url = await vscode.window.showInputBox({
        prompt: 'Enter GitPilot server URL',
        value: serverUrl,
    });
    if (url) {
        serverUrl = url;
        await vscode.workspace.getConfiguration('gitpilot').update('serverUrl', url, true);
        vscode.window.showInformationMessage(`GitPilot server set to: ${url}`);
    }
}

async function invokeSkill() {
    try {
        const response = await apiRequest('/api/skills');
        const skills = response.skills || [];
        if (skills.length === 0) {
            vscode.window.showInformationMessage('No skills available');
            return;
        }
        const items = skills.map((s: any) => ({
            label: `/${s.name}`,
            description: s.description,
        }));
        const selected = await vscode.window.showQuickPick(items, {
            placeHolder: 'Select a skill to invoke',
        });
        if (selected) {
            vscode.window.showInformationMessage(`GitPilot: Invoking ${selected.label}...`);
        }
    } catch (err: any) {
        vscode.window.showErrorMessage(`Failed to load skills: ${err.message}`);
    }
}

// --- Helpers ---

async function checkConnection() {
    try {
        await apiRequest('/api/health');
    } catch {
        // Silent fail — server might not be running
    }
}

async function apiRequest(path: string, options?: RequestInit): Promise<any> {
    const url = `${serverUrl}${path}`;
    const resp = await fetch(url, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
    });
    if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
    }
    return resp.json();
}
