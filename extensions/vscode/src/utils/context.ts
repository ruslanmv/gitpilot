/**
 * Workspace context detection.
 * Extracts repo owner/name from git remote, detects project type.
 */
import * as vscode from 'vscode';
import * as path from 'path';
import { execSync } from 'child_process';

export interface WorkspaceContext {
    workspaceRoot: string | undefined;
    repoOwner: string;
    repoName: string;
    branch: string;
    hasGitPilotConfig: boolean;
}

export function getWorkspaceContext(): WorkspaceContext {
    const folders = vscode.workspace.workspaceFolders;
    const root = folders?.[0]?.uri.fsPath;

    let owner = '';
    let name = '';
    let branch = '';

    if (root) {
        try {
            const remote = execSync('git remote get-url origin', { cwd: root, encoding: 'utf-8' }).trim();
            const match = remote.match(/[/:]([\w.-]+)\/([\w.-]+?)(?:\.git)?$/);
            if (match) {
                owner = match[1];
                name = match[2];
            }
        } catch { /* not a git repo */ }

        try {
            branch = execSync('git rev-parse --abbrev-ref HEAD', { cwd: root, encoding: 'utf-8' }).trim();
        } catch { /* ignore */ }
    }

    let hasConfig = false;
    if (root) {
        try {
            const configPath = path.join(root, '.gitpilot');
            const fs = require('fs');
            hasConfig = fs.existsSync(configPath);
        } catch { /* ignore */ }
    }

    return { workspaceRoot: root, repoOwner: owner, repoName: name, branch, hasGitPilotConfig: hasConfig };
}
