/**
 * Chat-related command handlers.
 */
import * as vscode from 'vscode';
import { GitPilotApiClient } from '../api/client';
import { ChatViewProvider } from '../views/chatViewProvider';
import { getWorkspaceContext } from '../utils/context';

export function registerChatCommands(
    context: vscode.ExtensionContext,
    client: GitPilotApiClient,
    chatProvider: ChatViewProvider,
): void {
    context.subscriptions.push(
        vscode.commands.registerCommand('gitpilot.openChat', () => {
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),

        vscode.commands.registerCommand('gitpilot.sendMessage', async () => {
            const message = await vscode.window.showInputBox({
                prompt: 'Enter your message for GitPilot',
                placeHolder: 'e.g., Review the authentication module',
            });
            if (message) {
                chatProvider.sendMessageFromCommand(message);
                vscode.commands.executeCommand('gitpilot.chatView.focus');
            }
        }),

        vscode.commands.registerCommand('gitpilot.newSession', async () => {
            const ctx = getWorkspaceContext();
            try {
                const session = await client.createSession(ctx.repoOwner, ctx.repoName);
                vscode.window.showInformationMessage(`New session created: ${session.id}`);
                vscode.commands.executeCommand('gitpilot.refreshSessions');
            } catch (err: any) {
                vscode.window.showErrorMessage(`Failed to create session: ${err.message}`);
            }
        }),

        vscode.commands.registerCommand('gitpilot.loadSession', async (sessionId: string) => {
            await chatProvider.loadSessionIntoChat(sessionId);
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),

        vscode.commands.registerCommand('gitpilot.deleteSession', async (item: any) => {
            if (!item?.sessionId) { return; }
            const confirm = await vscode.window.showWarningMessage(
                `Delete session ${item.sessionId}?`,
                { modal: true },
                'Delete',
            );
            if (confirm === 'Delete') {
                try {
                    await client.deleteSession(item.sessionId);
                    vscode.commands.executeCommand('gitpilot.refreshSessions');
                } catch (err: any) {
                    vscode.window.showErrorMessage(`Failed to delete: ${err.message}`);
                }
            }
        }),
    );
}
