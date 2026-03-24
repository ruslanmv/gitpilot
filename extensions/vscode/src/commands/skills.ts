/**
 * Skill and plugin management command handlers.
 */
import * as vscode from 'vscode';
import { GitPilotApiClient } from '../api/client';
import { ChatViewProvider } from '../views/chatViewProvider';

export function registerSkillCommands(
    context: vscode.ExtensionContext,
    client: GitPilotApiClient,
    chatProvider: ChatViewProvider,
): void {
    context.subscriptions.push(
        vscode.commands.registerCommand('gitpilot.invokeSkill', async (skillName?: string) => {
            if (!client.isConnected) {
                vscode.window.showErrorMessage('GitPilot server not connected');
                return;
            }

            if (!skillName) {
                try {
                    const skills = await client.listSkills();
                    if (skills.length === 0) {
                        vscode.window.showInformationMessage('No skills available');
                        return;
                    }
                    const items = skills.map(s => ({
                        label: `/${s.name}`,
                        description: s.description,
                        detail: s.source,
                        name: s.name,
                    }));
                    const selected = await vscode.window.showQuickPick(items, {
                        placeHolder: 'Select a skill to invoke',
                        matchOnDescription: true,
                    });
                    if (!selected) { return; }
                    skillName = selected.name;
                } catch (err: any) {
                    vscode.window.showErrorMessage(`Failed to load skills: ${err.message}`);
                    return;
                }
            }

            chatProvider.sendMessageFromCommand(`/${skillName}`);
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),

        vscode.commands.registerCommand('gitpilot.installPlugin', async () => {
            const source = await vscode.window.showInputBox({
                prompt: 'Enter plugin Git URL or local path',
                placeHolder: 'https://github.com/user/my-gitpilot-plugin.git',
            });
            if (!source) { return; }

            await vscode.window.withProgress(
                { location: vscode.ProgressLocation.Notification, title: 'Installing plugin...' },
                async () => {
                    try {
                        const result = await client.installPlugin(source);
                        vscode.window.showInformationMessage(`Plugin "${result.name}" installed successfully`);
                        vscode.commands.executeCommand('gitpilot.refreshSkills');
                    } catch (err: any) {
                        vscode.window.showErrorMessage(`Failed to install plugin: ${err.message}`);
                    }
                },
            );
        }),

        vscode.commands.registerCommand('gitpilot.uninstallPlugin', async (item: any) => {
            const name = item?.plugin?.name;
            if (!name) { return; }
            const confirm = await vscode.window.showWarningMessage(
                `Uninstall plugin "${name}"?`,
                { modal: true },
                'Uninstall',
            );
            if (confirm === 'Uninstall') {
                try {
                    await client.uninstallPlugin(name);
                    vscode.window.showInformationMessage(`Plugin "${name}" uninstalled`);
                    vscode.commands.executeCommand('gitpilot.refreshSkills');
                } catch (err: any) {
                    vscode.window.showErrorMessage(`Failed to uninstall: ${err.message}`);
                }
            }
        }),
    );
}
