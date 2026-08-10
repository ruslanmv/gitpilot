/**
 * Chat-related command handlers.
 */
import * as vscode from 'vscode';
import { GitPilotApiClient } from '../api/client';
import { ChatViewProvider } from '../views/chatViewProvider';
import { StateStore } from '../core/stateStore';
import { SessionCoordinator } from '../services/workspace/sessionCoordinator';
import { ModeResolver } from '../services/workspace/modeResolver';
import { ChatMessagePayload } from '../core/types';

/**
 * Collaborators owned by the workspace panel — the view the user actually
 * sees. Session commands used to talk only to the legacy provider, which is
 * no longer registered as a view, so "New Chat" created a session on the
 * backend while the visible conversation stayed exactly as it was.
 */
export interface ChatSessionDeps {
    stateStore: StateStore;
    sessionCoordinator: SessionCoordinator;
    modeResolver: ModeResolver;
    /**
     * Tell the panel to drop what the previous task left behind.
     *
     * Clearing the store is not enough. Tool-activity blocks live in the
     * transcript's DOM and carry no message key, so they survive a state
     * change and keep the transcript on screen; the composer's queue, pinned
     * files and half-typed text are the panel's own memory too.
     */
    resetPanel?: () => void;
}

export function registerChatCommands(
    context: vscode.ExtensionContext,
    client: GitPilotApiClient,
    chatProvider: ChatViewProvider,
    deps: ChatSessionDeps,
): void {
    /**
     * Wipe everything belonging to the previous conversation.
     *
     * A session change has to clear the task as well as the transcript;
     * leaving a stale plan, diff or file list behind makes the new chat look
     * like a continuation of the old one.
     */
    const clearConversation = (): void => {
        deps.stateStore.setChatMessages([]);
        deps.stateStore.clearTaskState();
        deps.resetPanel?.();
    };

    /**
     * Bring the conversation into view.
     *
     * There is one GitPilot tab and it lives in the editor, so this is the
     * only place a conversation can be revealed. The sidebar view it used to
     * focus no longer exists.
     */
    const revealChat = async (): Promise<void> => {
        await vscode.commands.executeCommand('gitpilot.openChatTab');
    };
    context.subscriptions.push(
        vscode.commands.registerCommand('gitpilot.openChat', () => revealChat()),

        vscode.commands.registerCommand('gitpilot.sendMessage', async () => {
            const message = await vscode.window.showInputBox({
                prompt: 'Enter your message for GitPilot',
                placeHolder: 'e.g., Review the authentication module',
            });
            if (message) {
                chatProvider.sendMessageFromCommand(message);
                await revealChat();
            }
        }),

        /**
         * New Chat.
         *
         * Clears the visible conversation first, so the panel goes empty the
         * moment it is asked to — waiting for the round-trip made the button
         * look like it had done nothing.
         */
        vscode.commands.registerCommand('gitpilot.newSession', async () => {
            clearConversation();
            await revealChat();

            const state = deps.stateStore.state;
            const mode = deps.modeResolver.resolve({
                folderOpen: state.workspace.folderOpen,
                git: state.workspace.git,
                github: state.github,
                preferredMode: state.workspace.mode,
            });

            await deps.sessionCoordinator.startSession(mode);
            await vscode.commands.executeCommand('gitpilot.refreshSessions');
        }),

        /**
         * Resume a session.
         *
         * A GitPilot session is a task, not a transcript, so this restores the
         * session's own state and then replays its messages. Task state from
         * the previous conversation is cleared rather than carried over.
         */
        vscode.commands.registerCommand('gitpilot.loadSession', async (sessionId: string) => {
            if (!sessionId) { return; }

            clearConversation();
            await revealChat();
            await deps.sessionCoordinator.resumeSession(sessionId);

            try {
                const session = await client.getSession(sessionId);
                const messages: ChatMessagePayload[] = (session.messages || []).map(
                    (m, index) => ({
                        id: `${sessionId}-${index}`,
                        role: (m.role === 'user' || m.role === 'assistant' ? m.role : 'system'),
                        content: m.content ?? '',
                        createdAt: new Date().toISOString(),
                    }),
                );
                deps.stateStore.setChatMessages(messages);
            } catch (err: any) {
                // The session is restored either way; only its history is
                // missing, which is not worth blocking the user over.
                vscode.window.showWarningMessage(
                    `GitPilot restored the session but could not load its history: ${err.message}`,
                );
            }
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
