/**
 * Workspace context detection.
 *
 * Detects git repository info from the workspace and passes it to the
 * GitPilot server. If no git repo is found, offers to initialize one
 * so the server can track version history.
 */
import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { execSync } from 'child_process';

export interface WorkspaceContext {
    workspaceRoot: string | undefined;
    repoOwner: string;
    repoName: string;
    branch: string;
    remoteUrl: string;
    isGitRepo: boolean;
    hasGitPilotConfig: boolean;
}

export function getWorkspaceContext(): WorkspaceContext {
    const folders = vscode.workspace.workspaceFolders;
    const root = folders?.[0]?.uri.fsPath;

    let owner = '';
    let name = '';
    let branch = '';
    let remoteUrl = '';
    let isGitRepo = false;

    if (root) {
        // Check if this is a git repo
        try {
            execSync('git rev-parse --git-dir', { cwd: root, encoding: 'utf-8', stdio: 'pipe' });
            isGitRepo = true;
        } catch { /* not a git repo */ }

        if (isGitRepo) {
            // Extract remote URL for owner/name
            try {
                remoteUrl = execSync('git remote get-url origin', { cwd: root, encoding: 'utf-8', stdio: 'pipe' }).trim();
                const match = remoteUrl.match(/[/:]([\w.-]+)\/([\w.-]+?)(?:\.git)?$/);
                if (match) {
                    owner = match[1];
                    name = match[2];
                }
            } catch {
                // Repo exists but has no remote — use folder name
                name = path.basename(root);
                owner = 'local';
            }

            // Get current branch
            try {
                branch = execSync('git rev-parse --abbrev-ref HEAD', { cwd: root, encoding: 'utf-8', stdio: 'pipe' }).trim();
            } catch { /* ignore */ }
        } else {
            // No git repo — use workspace folder name as project identifier
            name = path.basename(root);
            owner = 'local';
        }
    }

    let hasConfig = false;
    if (root) {
        hasConfig = fs.existsSync(path.join(root, '.gitpilot'));
    }

    return { workspaceRoot: root, repoOwner: owner, repoName: name, branch, remoteUrl, isGitRepo, hasGitPilotConfig: hasConfig };
}

/**
 * Initialize a git repo in the workspace if one doesn't exist.
 * Returns true if a repo was created or already exists.
 */
export async function ensureGitRepo(): Promise<boolean> {
    const ctx = getWorkspaceContext();
    if (ctx.isGitRepo) { return true; }
    if (!ctx.workspaceRoot) {
        vscode.window.showWarningMessage('No workspace folder open.');
        return false;
    }

    const choice = await vscode.window.showInformationMessage(
        'This workspace is not a git repository. GitPilot works best with version control. Initialize a git repo?',
        'Initialize Git Repo',
        'Continue Without Git',
    );

    if (choice === 'Initialize Git Repo') {
        try {
            execSync('git init', { cwd: ctx.workspaceRoot, encoding: 'utf-8', stdio: 'pipe' });
            execSync('git add -A', { cwd: ctx.workspaceRoot, encoding: 'utf-8', stdio: 'pipe' });
            execSync('git commit -m "Initial commit (GitPilot)" --allow-empty', { cwd: ctx.workspaceRoot, encoding: 'utf-8', stdio: 'pipe' });
            vscode.window.showInformationMessage('Git repository initialized with initial commit.');
            return true;
        } catch (err: any) {
            vscode.window.showErrorMessage(`Failed to initialize git: ${err.message}`);
            return false;
        }
    }

    // User chose to continue without git — still works, just no version control
    return false;
}
