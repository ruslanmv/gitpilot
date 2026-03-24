/**
 * Server connection and configuration command handlers.
 */
import * as vscode from 'vscode';
import { GitPilotApiClient } from '../api/client';
import { ChatViewProvider } from '../views/chatViewProvider';

export function registerServerCommands(
    context: vscode.ExtensionContext,
    client: GitPilotApiClient,
    chatProvider: ChatViewProvider,
): void {
    context.subscriptions.push(
        vscode.commands.registerCommand('gitpilot.setServer', async () => {
            const url = await vscode.window.showInputBox({
                prompt: 'Enter GitPilot server URL',
                value: client.serverUrl,
                validateInput: (val) => {
                    try {
                        new URL(val);
                        return null;
                    } catch {
                        return 'Please enter a valid URL';
                    }
                },
            });
            if (!url) { return; }

            client.setServerUrl(url);
            await vscode.workspace.getConfiguration('gitpilot').update('serverUrl', url, true);
            vscode.window.showInformationMessage(`Server URL set to: ${url}`);

            // Try to connect
            await vscode.window.withProgress(
                { location: vscode.ProgressLocation.Notification, title: 'Connecting to GitPilot...' },
                async () => {
                    const connected = await client.connect();
                    chatProvider.updateConnectionState(connected);
                    if (connected) {
                        vscode.window.showInformationMessage('Connected to GitPilot server');
                    } else {
                        vscode.window.showWarningMessage('Could not connect. Is the server running?');
                    }
                },
            );
        }),

        vscode.commands.registerCommand('gitpilot.reconnect', async () => {
            await vscode.window.withProgress(
                { location: vscode.ProgressLocation.Notification, title: 'Reconnecting to GitPilot...' },
                async () => {
                    const connected = await client.connect();
                    chatProvider.updateConnectionState(connected);
                    if (connected) {
                        vscode.window.showInformationMessage('Reconnected to GitPilot');
                    } else {
                        vscode.window.showWarningMessage('Could not reconnect. Is the server running?');
                    }
                },
            );
        }),

        vscode.commands.registerCommand('gitpilot.showServerInfo', async () => {
            if (!client.isConnected) {
                vscode.window.showWarningMessage('Not connected to GitPilot server');
                return;
            }

            try {
                const settings = await client.getSettings();
                const perms = await client.getPermissions();
                const info = [
                    `Server: ${client.serverUrl}`,
                    `Provider: ${settings.provider || 'unknown'}`,
                    `Permission Mode: ${perms.mode}`,
                ].join('\n');
                vscode.window.showInformationMessage(info, { modal: true });
            } catch (err: any) {
                vscode.window.showErrorMessage(`Failed to fetch server info: ${err.message}`);
            }
        }),

        vscode.commands.registerCommand('gitpilot.setPermissionMode', async () => {
            const modes: Array<{ label: string; description: string; mode: 'normal' | 'plan' | 'auto' }> = [
                { label: 'Normal', description: 'Ask before risky operations', mode: 'normal' },
                { label: 'Plan Only', description: 'Read-only — all writes blocked', mode: 'plan' },
                { label: 'Auto', description: 'Allow everything without confirmation', mode: 'auto' },
            ];
            const selected = await vscode.window.showQuickPick(modes, {
                placeHolder: 'Select permission mode',
            });
            if (!selected) { return; }

            if (selected.mode === 'auto') {
                const confirm = await vscode.window.showWarningMessage(
                    'Auto mode allows all operations without confirmation. Continue?',
                    { modal: true },
                    'Enable Auto Mode',
                );
                if (confirm !== 'Enable Auto Mode') { return; }
            }

            try {
                await client.setPermissionMode(selected.mode);
                vscode.window.showInformationMessage(`Permission mode set to: ${selected.label}`);
            } catch (err: any) {
                vscode.window.showErrorMessage(`Failed to set mode: ${err.message}`);
            }
        }),

        vscode.commands.registerCommand('gitpilot.selectTopology', async () => {
            if (!client.isConnected) {
                vscode.window.showWarningMessage('Not connected');
                return;
            }
            try {
                const topologies = await client.listTopologies();
                const items = topologies.map(t => ({
                    label: t.name,
                    description: `[${t.category}]`,
                    detail: t.description,
                    id: t.id,
                }));
                const selected = await vscode.window.showQuickPick(items, {
                    placeHolder: 'Select an agent topology',
                    matchOnDescription: true,
                    matchOnDetail: true,
                });
                if (selected) {
                    await client.setTopology(selected.id);
                    vscode.window.showInformationMessage(`Topology set to: ${selected.label}`);
                }
            } catch (err: any) {
                vscode.window.showErrorMessage(`Failed to load topologies: ${err.message}`);
            }
        }),
    );
}
