/**
 * Git operations command handlers.
 *
 * Enterprise patterns:
 * - Safety-first: confirmations for destructive ops (force push, hard reset)
 * - Progressive disclosure: basic → advanced commands
 * - Privacy-first: all Git ops run locally
 * - Dry-run preview before execution
 */
import * as vscode from 'vscode';
import { GitPilotApiClient } from '../api/client';
import { ChatViewProvider } from '../views/chatViewProvider';
import { getWorkspaceContext } from '../utils/context';

export function registerGitCommands(
    context: vscode.ExtensionContext,
    client: GitPilotApiClient,
    chatProvider: ChatViewProvider,
): void {
    context.subscriptions.push(
        // ── Basic Git Operations ───────────────────────────────
        vscode.commands.registerCommand('gitpilot.gitStatus', () => {
            chatProvider.sendMessageFromCommand('Show me the git status of this repository');
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),

        vscode.commands.registerCommand('gitpilot.gitDiffAnalysis', () => {
            chatProvider.sendMessageFromCommand(
                'Analyze the current git diff and summarize what changed, why it might have changed, and if there are any issues',
            );
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),

        vscode.commands.registerCommand('gitpilot.smartCommit', () => {
            chatProvider.sendMessageFromCommand(
                'Analyze the staged changes and generate a semantic commit message following conventional commits. Show me the plan before executing.',
            );
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),

        vscode.commands.registerCommand('gitpilot.createPR', async () => {
            const title = await vscode.window.showInputBox({
                prompt: 'PR title (leave empty for AI-generated)',
                placeHolder: 'feat: add user authentication',
            });

            const message = title
                ? `Create a pull request with title "${title}". Analyze all commits on this branch and generate a comprehensive PR description.`
                : 'Analyze all commits on this branch and create a pull request with an AI-generated title and description.';

            chatProvider.sendMessageFromCommand(message);
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),

        // ── Intermediate Git Operations ────────────────────────
        vscode.commands.registerCommand('gitpilot.branchManager', async () => {
            const action = await vscode.window.showQuickPick([
                { label: '$(git-branch) Create Branch', description: 'Create a new branch', action: 'create' },
                { label: '$(git-merge) Merge Branch', description: 'Merge a branch into current', action: 'merge' },
                { label: '$(git-compare) Compare Branches', description: 'Compare two branches', action: 'compare' },
                { label: '$(trash) Delete Branch', description: 'Delete a branch (with safety check)', action: 'delete' },
            ], { placeHolder: 'Select branch operation' });

            if (!action) { return; }

            const prompts: Record<string, string> = {
                create: 'List current branches and help me create a new branch. Ask me what the branch should be named.',
                merge: 'List current branches and help me merge a branch into the current one. Show the plan before executing.',
                compare: 'List current branches and compare the differences between two branches I specify.',
                delete: 'List current branches and help me safely delete a branch. Warn me if it has unmerged changes.',
            };

            chatProvider.sendMessageFromCommand(prompts[action.action]);
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),

        vscode.commands.registerCommand('gitpilot.conflictResolver', () => {
            chatProvider.sendMessageFromCommand(
                'Check for merge conflicts in the current repository. If any exist, explain each conflict, suggest resolutions, and show the plan before applying changes.',
            );
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),

        vscode.commands.registerCommand('gitpilot.stashManager', async () => {
            const action = await vscode.window.showQuickPick([
                { label: '$(archive) Stash Changes', description: 'Save current changes to stash', action: 'save' },
                { label: '$(cloud-download) Pop Stash', description: 'Apply and remove top stash', action: 'pop' },
                { label: '$(list-flat) List Stashes', description: 'Show all stashed changes', action: 'list' },
            ], { placeHolder: 'Select stash operation' });

            if (!action) { return; }

            const prompts: Record<string, string> = {
                save: 'Stash the current uncommitted changes with a descriptive message. Show the plan first.',
                pop: 'List stashes and apply the most recent one. Show what changes will be restored.',
                list: 'List all stashes with their descriptions and show what files each stash contains.',
            };

            chatProvider.sendMessageFromCommand(prompts[action.action]);
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),

        // ── Advanced Git Operations ────────────────────────────
        vscode.commands.registerCommand('gitpilot.repoHealthCheck', async () => {
            const ctx = getWorkspaceContext();
            chatProvider.sendMessageFromCommand(
                `Perform a comprehensive health check on this repository (${ctx.repoOwner}/${ctx.repoName}). Check for: large files, stale branches, missing .gitignore entries, untracked sensitive files, commit message quality, and branch protection status.`,
            );
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),

        vscode.commands.registerCommand('gitpilot.commitSearch', async () => {
            const query = await vscode.window.showInputBox({
                prompt: 'Describe what you are looking for in commit history',
                placeHolder: 'e.g., when was the login feature added?',
            });
            if (!query) { return; }

            chatProvider.sendMessageFromCommand(
                `Search the commit history for: "${query}". Show relevant commits with their messages, authors, dates, and changed files.`,
            );
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),

        vscode.commands.registerCommand('gitpilot.impactAnalysis', async () => {
            const editor = vscode.window.activeTextEditor;
            const fileName = editor
                ? vscode.workspace.asRelativePath(editor.document.uri)
                : undefined;

            const target = fileName || await vscode.window.showInputBox({
                prompt: 'Enter file or module path to analyze',
                placeHolder: 'src/auth/login.ts',
            });
            if (!target) { return; }

            chatProvider.sendMessageFromCommand(
                `Perform an impact analysis for "${target}". Show: which files depend on it, what would break if it changed, recent change frequency, and test coverage status.`,
            );
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),

        // ── Natural Language Git ───────────────────────────────
        vscode.commands.registerCommand('gitpilot.naturalLanguageGit', async () => {
            const command = await vscode.window.showInputBox({
                prompt: 'Describe what you want to do with Git in plain English',
                placeHolder: 'e.g., undo my last commit but keep the changes',
            });
            if (!command) { return; }

            chatProvider.sendMessageFromCommand(
                `I want to do this Git operation: "${command}". Generate the exact Git command(s), explain what each does, warn me about any risks, and show the plan before executing.`,
            );
            vscode.commands.executeCommand('gitpilot.chatView.focus');
        }),
    );
}
