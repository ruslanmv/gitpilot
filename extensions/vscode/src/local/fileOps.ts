/**
 * File operations module — read, write, edit, create, delete files
 * in the VS Code workspace using the workspace.fs API.
 */

import * as vscode from 'vscode';
import * as path from 'path';

export class FileOps {
    private _root: string;

    constructor(root: string) {
        this._root = root;
    }

    /** Resolve a relative path to an absolute URI within the workspace. */
    private _resolve(relativePath: string): vscode.Uri {
        // Security: prevent path traversal
        const normalized = path.normalize(relativePath).replace(/^(\.\.[/\\])+/, '');
        return vscode.Uri.file(path.join(this._root, normalized));
    }

    /** Read a file and return its text content. */
    async readFile(relativePath: string): Promise<string> {
        const uri = this._resolve(relativePath);
        const bytes = await vscode.workspace.fs.readFile(uri);
        return Buffer.from(bytes).toString('utf-8');
    }

    /** Write (create or overwrite) a file. Creates parent directories if needed. */
    async writeFile(relativePath: string, content: string): Promise<void> {
        const uri = this._resolve(relativePath);
        const bytes = Buffer.from(content, 'utf-8');
        await vscode.workspace.fs.writeFile(uri, bytes);
    }

    /**
     * Edit a file by replacing old_text with new_text.
     * Returns true if the replacement was made, false if old_text was not found.
     */
    async editFile(
        relativePath: string,
        oldText: string,
        newText: string,
    ): Promise<{ success: boolean; content: string }> {
        const current = await this.readFile(relativePath);
        if (!current.includes(oldText)) {
            return { success: false, content: current };
        }
        const updated = current.replace(oldText, newText);
        await this.writeFile(relativePath, updated);
        return { success: true, content: updated };
    }

    /** Delete a file or directory. */
    async delete(relativePath: string): Promise<void> {
        const uri = this._resolve(relativePath);
        await vscode.workspace.fs.delete(uri, { recursive: true });
    }

    /** Check if a file or directory exists. */
    async exists(relativePath: string): Promise<boolean> {
        try {
            const uri = this._resolve(relativePath);
            await vscode.workspace.fs.stat(uri);
            return true;
        } catch {
            return false;
        }
    }

    /** List directory contents. */
    async listDirectory(relativePath: string, recursive = false): Promise<string[]> {
        const uri = this._resolve(relativePath || '.');
        const results: string[] = [];

        const entries = await vscode.workspace.fs.readDirectory(uri);
        for (const [name, type] of entries) {
            const relPath = relativePath ? `${relativePath}/${name}` : name;
            if (type === vscode.FileType.Directory) {
                results.push(relPath + '/');
                if (recursive) {
                    const subEntries = await this.listDirectory(relPath, true);
                    results.push(...subEntries);
                }
            } else {
                results.push(relPath);
            }
        }

        return results;
    }

    /**
     * Create a unified diff string between original and proposed content.
     * Simple line-based diff for display in the webview.
     */
    static createDiff(
        filePath: string,
        original: string,
        proposed: string,
    ): string {
        const origLines = original.split('\n');
        const newLines = proposed.split('\n');
        const diff: string[] = [`--- a/${filePath}`, `+++ b/${filePath}`];

        let i = 0;
        let j = 0;
        while (i < origLines.length || j < newLines.length) {
            if (i < origLines.length && j < newLines.length && origLines[i] === newLines[j]) {
                diff.push(` ${origLines[i]}`);
                i++; j++;
            } else if (j < newLines.length && (i >= origLines.length || origLines[i] !== newLines[j])) {
                diff.push(`+${newLines[j]}`);
                j++;
            } else {
                diff.push(`-${origLines[i]}`);
                i++;
            }
        }

        return diff.join('\n');
    }

    /**
     * Open a file in the VS Code editor.
     */
    async openInEditor(relativePath: string, options?: { preview?: boolean }): Promise<void> {
        const uri = this._resolve(relativePath);
        await vscode.window.showTextDocument(uri, { preview: options?.preview ?? true });
    }

    /**
     * Show a diff between original and modified content in VS Code's diff editor.
     */
    async showDiff(relativePath: string, originalContent: string, modifiedContent: string): Promise<void> {
        const originalUri = vscode.Uri.parse(`gitpilot-original:${relativePath}`);
        const modifiedUri = vscode.Uri.parse(`gitpilot-modified:${relativePath}`);

        // Register content providers temporarily
        const disposables: vscode.Disposable[] = [];

        disposables.push(
            vscode.workspace.registerTextDocumentContentProvider('gitpilot-original', {
                provideTextDocumentContent: () => originalContent,
            }),
        );
        disposables.push(
            vscode.workspace.registerTextDocumentContentProvider('gitpilot-modified', {
                provideTextDocumentContent: () => modifiedContent,
            }),
        );

        await vscode.commands.executeCommand(
            'vscode.diff',
            originalUri,
            modifiedUri,
            `GitPilot: ${relativePath} (proposed changes)`,
        );

        // Clean up after a delay (keep providers alive while diff is open)
        setTimeout(() => disposables.forEach(d => d.dispose()), 60000);
    }
}
