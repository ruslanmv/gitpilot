/**
 * Context builder — assembles the system prompt for the LLM.
 *
 * Includes workspace file tree, project info, git status, currently open
 * files, and instructions. Manages token budget to avoid exceeding limits.
 */

import * as vscode from 'vscode';
import { WorkspaceScanner } from './workspaceScanner';
import { GitLocal } from './gitLocal';

const MAX_SYSTEM_PROMPT_CHARS = 12000; // ~3000 tokens, leaves room for conversation

export class ContextBuilder {
    private _scanner: WorkspaceScanner;
    private _git: GitLocal;

    constructor(scanner: WorkspaceScanner, git: GitLocal) {
        this._scanner = scanner;
        this._git = git;
    }

    /**
     * Build the full system prompt with workspace context.
     */
    async buildSystemPrompt(): Promise<string> {
        const parts: string[] = [];

        // Core identity
        parts.push(`You are GitPilot, an expert AI coding assistant running inside VS Code.
You have direct access to the user's workspace through tools. You can read files, write files, edit code, run commands, and manage git — all locally on the user's machine.

## Your Capabilities
- **Read & understand** the entire project structure and codebase
- **Write & create** new files, generate entire projects, scaffold code
- **Edit** existing files with precise targeted replacements
- **Search** across the codebase for patterns, functions, references
- **Run** shell commands (tests, builds, linters, package managers)
- **Git** operations: status, diff, log, commit, branch management

## Guidelines
- Always read relevant files before modifying them to understand context
- For edits, use edit_file with exact text matching (read first!)
- For new files, use write_file with complete content
- Explain what you're doing and why before making changes
- After writing code, offer to run tests or linters if applicable
- When generating a project, create all necessary files including config
- Use the project's existing code style, patterns, and conventions
- If something is unclear, ask the user rather than guessing`);

        // Project info
        const projectInfo = await this._scanner.detectProjectInfo();
        if (Object.keys(projectInfo).length > 0) {
            parts.push('\n## Project Info');
            for (const [key, value] of Object.entries(projectInfo)) {
                parts.push(`- ${key}: ${value}`);
            }
        }

        // Git status
        try {
            const gitSummary = await this._git.statusSummary();
            if (gitSummary && gitSummary !== 'Not a git repository.') {
                parts.push(`\n## Git Status\n${gitSummary}`);
            }
        } catch {
            // Not a git repo, skip
        }

        // File tree (truncated to fit budget)
        const currentLength = parts.join('\n').length;
        const treebudget = MAX_SYSTEM_PROMPT_CHARS - currentLength - 200;
        const maxTreeLines = Math.max(50, Math.floor(treebudget / 30));
        const tree = this._scanner.formatTree(maxTreeLines);

        parts.push(`\n## Workspace File Tree\n\`\`\`\n${tree}\n\`\`\``);

        // Currently open files
        const openFiles = this._getOpenFiles();
        if (openFiles.length > 0) {
            parts.push(`\n## Currently Open Files\n${openFiles.join('\n')}`);
        }

        // Active file context
        const activeFile = this._getActiveFileContext();
        if (activeFile) {
            parts.push(`\n## Active Editor\nFile: ${activeFile.path}\nLanguage: ${activeFile.language}`);
            if (activeFile.selection) {
                parts.push(`Selected text (lines ${activeFile.selection.startLine}-${activeFile.selection.endLine}):\n\`\`\`\n${activeFile.selection.text}\n\`\`\``);
            }
        }

        return parts.join('\n');
    }

    /**
     * Get the list of currently open editor files.
     */
    private _getOpenFiles(): string[] {
        const root = this._scanner.root;
        const files: string[] = [];

        for (const group of vscode.window.tabGroups.all) {
            for (const tab of group.tabs) {
                if (tab.input instanceof vscode.TabInputText) {
                    const uri = tab.input.uri;
                    if (uri.scheme === 'file' && uri.fsPath.startsWith(root)) {
                        const rel = uri.fsPath.slice(root.length + 1);
                        files.push(`- ${rel}${tab.isActive ? ' (active)' : ''}`);
                    }
                }
            }
        }

        return files.slice(0, 20); // Cap at 20
    }

    /**
     * Get context about the currently active editor.
     */
    private _getActiveFileContext(): {
        path: string;
        language: string;
        selection?: { text: string; startLine: number; endLine: number };
    } | null {
        const editor = vscode.window.activeTextEditor;
        if (!editor) return null;

        const uri = editor.document.uri;
        if (uri.scheme !== 'file') return null;

        const root = this._scanner.root;
        if (!uri.fsPath.startsWith(root)) return null;

        const relPath = uri.fsPath.slice(root.length + 1);
        const result: any = {
            path: relPath,
            language: editor.document.languageId,
        };

        // Include selected text if any
        const sel = editor.selection;
        if (!sel.isEmpty) {
            const text = editor.document.getText(sel);
            if (text.length < 5000) {
                result.selection = {
                    text,
                    startLine: sel.start.line + 1,
                    endLine: sel.end.line + 1,
                };
            }
        }

        return result;
    }
}
