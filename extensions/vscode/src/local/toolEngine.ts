/**
 * Tool engine — defines all available tools and executes them.
 *
 * Tools are the bridge between the LLM and the workspace.
 * Each tool has a definition (sent to the LLM) and an executor function.
 */

import { execFile } from 'child_process';
import { promisify } from 'util';
import type { ToolDefinition } from './providers/interface';
import { FileOps } from './fileOps';
import { GitLocal } from './gitLocal';
import { WorkspaceScanner } from './workspaceScanner';

const execFileAsync = promisify(execFile);

// ---------------------------------------------------------------------------
// Tool definitions (sent to the LLM so it knows what it can call)
// ---------------------------------------------------------------------------

export const TOOL_DEFINITIONS: ToolDefinition[] = [
    {
        name: 'read_file',
        description: 'Read the full contents of a file in the workspace. Use this to understand existing code before making changes.',
        parameters: {
            type: 'object',
            properties: {
                path: { type: 'string', description: 'Relative path from workspace root (e.g., "src/index.ts")' },
            },
            required: ['path'],
        },
    },
    {
        name: 'write_file',
        description: 'Create a new file or completely overwrite an existing file. Use for new files or full rewrites. For partial edits, prefer edit_file.',
        parameters: {
            type: 'object',
            properties: {
                path: { type: 'string', description: 'Relative path from workspace root' },
                content: { type: 'string', description: 'The complete file content to write' },
            },
            required: ['path', 'content'],
        },
    },
    {
        name: 'edit_file',
        description: 'Make a targeted edit to an existing file by replacing a specific text section. The old_text must match exactly.',
        parameters: {
            type: 'object',
            properties: {
                path: { type: 'string', description: 'Relative path from workspace root' },
                old_text: { type: 'string', description: 'The exact text to find and replace' },
                new_text: { type: 'string', description: 'The replacement text' },
            },
            required: ['path', 'old_text', 'new_text'],
        },
    },
    {
        name: 'list_files',
        description: 'List files and directories at a given path. Use to explore the project structure.',
        parameters: {
            type: 'object',
            properties: {
                path: { type: 'string', description: 'Relative directory path (empty string for workspace root)' },
                recursive: { type: 'string', description: '"true" to list recursively, "false" for one level', enum: ['true', 'false'] },
            },
            required: ['path'],
        },
    },
    {
        name: 'search_files',
        description: 'Search for a text pattern across files in the workspace using grep. Returns matching lines with file paths and line numbers.',
        parameters: {
            type: 'object',
            properties: {
                pattern: { type: 'string', description: 'Search pattern (supports regex)' },
                path: { type: 'string', description: 'Subdirectory to search in (empty for workspace root)' },
                file_pattern: { type: 'string', description: 'Glob pattern to filter files (e.g., "*.ts", "*.py")' },
            },
            required: ['pattern'],
        },
    },
    {
        name: 'run_command',
        description: 'Execute a shell command in the workspace directory. Use for running tests, build commands, linters, etc. Commands have a 30s timeout.',
        parameters: {
            type: 'object',
            properties: {
                command: { type: 'string', description: 'The shell command to execute' },
            },
            required: ['command'],
        },
    },
    {
        name: 'git_status',
        description: 'Get the current git status: branch name, staged/modified/untracked files, ahead/behind counts.',
        parameters: {
            type: 'object',
            properties: {},
        },
    },
    {
        name: 'git_diff',
        description: 'Get the git diff of current working tree changes.',
        parameters: {
            type: 'object',
            properties: {
                staged: { type: 'string', description: '"true" for staged changes only, "false" for unstaged', enum: ['true', 'false'] },
            },
        },
    },
    {
        name: 'git_log',
        description: 'Get recent git commit history.',
        parameters: {
            type: 'object',
            properties: {
                count: { type: 'string', description: 'Number of commits to show (default: 10)' },
            },
        },
    },
    {
        name: 'git_commit',
        description: 'Stage all changes and create a git commit with the given message.',
        parameters: {
            type: 'object',
            properties: {
                message: { type: 'string', description: 'Commit message' },
                files: { type: 'string', description: 'Comma-separated file paths to stage (empty to stage all modified files)' },
            },
            required: ['message'],
        },
    },
];

// ---------------------------------------------------------------------------
// Tools that require user confirmation before execution
// ---------------------------------------------------------------------------

export const CONFIRMATION_REQUIRED = new Set([
    'write_file',
    'edit_file',
    'run_command',
    'git_commit',
]);

// ---------------------------------------------------------------------------
// Tool executor
// ---------------------------------------------------------------------------

export class ToolExecutor {
    private _fileOps: FileOps;
    private _git: GitLocal;
    private _scanner: WorkspaceScanner;
    private _root: string;

    constructor(root: string, fileOps: FileOps, git: GitLocal, scanner: WorkspaceScanner) {
        this._root = root;
        this._fileOps = fileOps;
        this._git = git;
        this._scanner = scanner;
    }

    /**
     * Execute a tool and return its result as a string.
     * Throws on error (caller wraps in tool_result with is_error=true).
     */
    async execute(
        name: string,
        args: Record<string, unknown>,
    ): Promise<string> {
        switch (name) {
            case 'read_file':
                return this._readFile(args);
            case 'write_file':
                return this._writeFile(args);
            case 'edit_file':
                return this._editFile(args);
            case 'list_files':
                return this._listFiles(args);
            case 'search_files':
                return this._searchFiles(args);
            case 'run_command':
                return this._runCommand(args);
            case 'git_status':
                return this._git.statusSummary();
            case 'git_diff':
                return this._git.diff(args.staged === 'true');
            case 'git_log':
                return this._gitLog(args);
            case 'git_commit':
                return this._gitCommit(args);
            default:
                throw new Error(`Unknown tool: ${name}`);
        }
    }

    private async _readFile(args: Record<string, unknown>): Promise<string> {
        const filePath = String(args.path || '');
        if (!filePath) throw new Error('path is required');
        const content = await this._fileOps.readFile(filePath);
        // Truncate very large files
        if (content.length > 100_000) {
            return content.slice(0, 100_000) + `\n\n... [truncated, file is ${content.length} bytes]`;
        }
        return content;
    }

    private async _writeFile(args: Record<string, unknown>): Promise<string> {
        const filePath = String(args.path || '');
        const content = String(args.content || '');
        if (!filePath) throw new Error('path is required');
        await this._fileOps.writeFile(filePath, content);
        return `File written: ${filePath} (${content.length} bytes)`;
    }

    private async _editFile(args: Record<string, unknown>): Promise<string> {
        const filePath = String(args.path || '');
        const oldText = String(args.old_text || '');
        const newText = String(args.new_text || '');
        if (!filePath || !oldText) throw new Error('path and old_text are required');

        const result = await this._fileOps.editFile(filePath, oldText, newText);
        if (!result.success) {
            throw new Error(`old_text not found in ${filePath}. Read the file first to see its current content.`);
        }
        return `File edited: ${filePath}`;
    }

    private async _listFiles(args: Record<string, unknown>): Promise<string> {
        const dirPath = String(args.path || '');
        const recursive = args.recursive === 'true';
        const files = await this._fileOps.listDirectory(dirPath, recursive);
        if (files.length === 0) return 'Directory is empty.';
        if (files.length > 500) {
            return files.slice(0, 500).join('\n') + `\n... (${files.length} total entries, truncated)`;
        }
        return files.join('\n');
    }

    private async _searchFiles(args: Record<string, unknown>): Promise<string> {
        const pattern = String(args.pattern || '');
        if (!pattern) throw new Error('pattern is required');

        const searchPath = args.path ? String(args.path) : '.';
        const grepArgs = ['-rn', '--color=never', '-I']; // recursive, line numbers, skip binary

        if (args.file_pattern) {
            grepArgs.push('--include', String(args.file_pattern));
        }

        // Exclude common noise directories
        for (const dir of ['node_modules', '.git', 'dist', 'build', '__pycache__', '.venv']) {
            grepArgs.push('--exclude-dir', dir);
        }

        grepArgs.push(pattern, searchPath);

        try {
            const { stdout } = await execFileAsync('grep', grepArgs, {
                cwd: this._root,
                maxBuffer: 1024 * 1024,
                timeout: 15000,
            });
            const lines = stdout.trim().split('\n');
            if (lines.length > 100) {
                return lines.slice(0, 100).join('\n') + `\n... (${lines.length} total matches, truncated)`;
            }
            return stdout.trim() || 'No matches found.';
        } catch (err: any) {
            if (err.code === 1) return 'No matches found.';
            throw new Error(`Search failed: ${err.message}`);
        }
    }

    private async _runCommand(args: Record<string, unknown>): Promise<string> {
        const command = String(args.command || '');
        if (!command) throw new Error('command is required');

        // Security: block obviously dangerous commands
        const dangerous = ['rm -rf /', 'mkfs', ':(){:|:&};:', 'dd if=/dev/zero'];
        if (dangerous.some(d => command.includes(d))) {
            throw new Error('Command blocked for safety reasons.');
        }

        try {
            const { stdout, stderr } = await execFileAsync('sh', ['-c', command], {
                cwd: this._root,
                maxBuffer: 5 * 1024 * 1024,
                timeout: 30000,
            });
            let result = '';
            if (stdout.trim()) result += stdout.trim();
            if (stderr.trim()) result += (result ? '\n\nSTDERR:\n' : 'STDERR:\n') + stderr.trim();
            return result || '(command completed with no output)';
        } catch (err: any) {
            const output = (err.stdout || '') + (err.stderr ? '\nSTDERR: ' + err.stderr : '');
            return `Command failed (exit ${err.code}):\n${output || err.message}`;
        }
    }

    private async _gitLog(args: Record<string, unknown>): Promise<string> {
        const count = Number(args.count) || 10;
        const entries = await this._git.log(count);
        if (entries.length === 0) return 'No commits found.';
        return entries
            .map(e => `${e.shortHash} ${e.date.split(' ')[0]} ${e.author}: ${e.message}`)
            .join('\n');
    }

    private async _gitCommit(args: Record<string, unknown>): Promise<string> {
        const message = String(args.message || '');
        if (!message) throw new Error('commit message is required');

        const files = args.files ? String(args.files).split(',').map(f => f.trim()) : ['.'];
        await this._git.add(...files);
        const result = await this._git.commit(message);
        return result;
    }
}
