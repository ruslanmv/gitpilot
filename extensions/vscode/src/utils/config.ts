/**
 * Configuration utilities for GitPilot extension.
 * Centralized access to all settings with change detection.
 */
import * as vscode from 'vscode';

export interface GitPilotConfig {
    serverUrl: string;
    autoConnect: boolean;
    showInlineHints: boolean;
    githubToken?: string;
    permissionMode: 'normal' | 'plan' | 'auto';
    defaultTopology: string;
    showSecurityDiagnostics: boolean;
    scanOnSave: boolean;
    chatFontSize: number;
    maxChatHistory: number;
    liteMode: boolean;
}

const DEFAULTS: GitPilotConfig = {
    serverUrl: 'http://127.0.0.1:8000',
    autoConnect: true,
    showInlineHints: true,
    permissionMode: 'normal',
    defaultTopology: 'default',
    showSecurityDiagnostics: true,
    scanOnSave: false,
    chatFontSize: 13,
    maxChatHistory: 100,
    liteMode: false,
};

export function getConfig(): GitPilotConfig {
    const cfg = vscode.workspace.getConfiguration('gitpilot');
    return {
        serverUrl: cfg.get('serverUrl', DEFAULTS.serverUrl),
        autoConnect: cfg.get('autoConnect', DEFAULTS.autoConnect),
        showInlineHints: cfg.get('showInlineHints', DEFAULTS.showInlineHints),
        githubToken: cfg.get('githubToken') || process.env.GITPILOT_GITHUB_TOKEN || process.env.GITHUB_TOKEN,
        permissionMode: cfg.get('permissionMode', DEFAULTS.permissionMode),
        defaultTopology: cfg.get('defaultTopology', DEFAULTS.defaultTopology),
        showSecurityDiagnostics: cfg.get('showSecurityDiagnostics', DEFAULTS.showSecurityDiagnostics),
        scanOnSave: cfg.get('scanOnSave', DEFAULTS.scanOnSave),
        chatFontSize: cfg.get('chatFontSize', DEFAULTS.chatFontSize),
        maxChatHistory: cfg.get('maxChatHistory', DEFAULTS.maxChatHistory),
        liteMode: cfg.get('liteMode', DEFAULTS.liteMode),
    };
}

export function onConfigChange(callback: (config: GitPilotConfig) => void): vscode.Disposable {
    return vscode.workspace.onDidChangeConfiguration(e => {
        if (e.affectsConfiguration('gitpilot')) {
            callback(getConfig());
        }
    });
}
