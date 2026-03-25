/**
 * Local git operations — async wrapper around git CLI.
 *
 * Uses child_process.execFile (async, non-blocking) instead of execSync.
 * Falls back gracefully if not in a git repository.
 */

import { execFile } from 'child_process';
import { promisify } from 'util';

const execFileAsync = promisify(execFile);

export interface GitStatus {
    branch: string;
    isRepo: boolean;
    staged: string[];
    modified: string[];
    untracked: string[];
    ahead: number;
    behind: number;
    clean: boolean;
}

export interface GitLogEntry {
    hash: string;
    shortHash: string;
    author: string;
    date: string;
    message: string;
}

export class GitLocal {
    private _cwd: string;
    private _isRepo: boolean | null = null;

    constructor(cwd: string) {
        this._cwd = cwd;
    }

    private async _git(...args: string[]): Promise<string> {
        try {
            const { stdout } = await execFileAsync('git', args, {
                cwd: this._cwd,
                maxBuffer: 10 * 1024 * 1024, // 10 MB
                timeout: 30000,
            });
            return stdout.trim();
        } catch (err: any) {
            throw new Error(`git ${args[0]} failed: ${err.stderr || err.message}`);
        }
    }

    /** Check if the workspace is a git repository. */
    async isGitRepo(): Promise<boolean> {
        if (this._isRepo !== null) return this._isRepo;
        try {
            await this._git('rev-parse', '--git-dir');
            this._isRepo = true;
        } catch {
            this._isRepo = false;
        }
        return this._isRepo;
    }

    /** Get current branch name. */
    async branch(): Promise<string> {
        try {
            return await this._git('rev-parse', '--abbrev-ref', 'HEAD');
        } catch {
            return 'unknown';
        }
    }

    /** Get comprehensive status. */
    async status(): Promise<GitStatus> {
        if (!await this.isGitRepo()) {
            return { branch: '', isRepo: false, staged: [], modified: [], untracked: [], ahead: 0, behind: 0, clean: true };
        }

        const branchName = await this.branch();
        const porcelain = await this._git('status', '--porcelain=v1');

        const staged: string[] = [];
        const modified: string[] = [];
        const untracked: string[] = [];

        for (const line of porcelain.split('\n').filter(Boolean)) {
            const x = line[0]; // index status
            const y = line[1]; // worktree status
            const file = line.slice(3);

            if (x === '?' && y === '?') {
                untracked.push(file);
            } else {
                if (x !== ' ' && x !== '?') staged.push(file);
                if (y !== ' ' && y !== '?') modified.push(file);
            }
        }

        // Ahead/behind
        let ahead = 0, behind = 0;
        try {
            const ab = await this._git('rev-list', '--left-right', '--count', `HEAD...@{upstream}`);
            const [a, b] = ab.split('\t').map(Number);
            ahead = a || 0;
            behind = b || 0;
        } catch {
            // No upstream configured
        }

        return {
            branch: branchName,
            isRepo: true,
            staged, modified, untracked,
            ahead, behind,
            clean: staged.length === 0 && modified.length === 0 && untracked.length === 0,
        };
    }

    /** Get diff of working tree changes. */
    async diff(staged = false): Promise<string> {
        const args = staged ? ['diff', '--cached'] : ['diff'];
        return this._git(...args);
    }

    /** Get recent log entries. */
    async log(count = 10): Promise<GitLogEntry[]> {
        const format = '%H%n%h%n%an%n%ai%n%s';
        const raw = await this._git('log', `-${count}`, `--format=${format}`);

        const lines = raw.split('\n');
        const entries: GitLogEntry[] = [];

        for (let i = 0; i + 4 < lines.length; i += 5) {
            entries.push({
                hash: lines[i],
                shortHash: lines[i + 1],
                author: lines[i + 2],
                date: lines[i + 3],
                message: lines[i + 4],
            });
        }

        return entries;
    }

    /** Stage files. */
    async add(...files: string[]): Promise<void> {
        await this._git('add', ...files);
    }

    /** Commit with message. */
    async commit(message: string): Promise<string> {
        return this._git('commit', '-m', message);
    }

    /** Create a new branch. */
    async createBranch(name: string, checkout = true): Promise<void> {
        if (checkout) {
            await this._git('checkout', '-b', name);
        } else {
            await this._git('branch', name);
        }
    }

    /** Checkout a branch. */
    async checkout(name: string): Promise<void> {
        await this._git('checkout', name);
    }

    /** List branches. */
    async listBranches(): Promise<string[]> {
        const raw = await this._git('branch', '--format=%(refname:short)');
        return raw.split('\n').filter(Boolean);
    }

    /** Search commits by message. */
    async searchCommits(query: string, count = 20): Promise<GitLogEntry[]> {
        const format = '%H%n%h%n%an%n%ai%n%s';
        const raw = await this._git('log', `--grep=${query}`, '-i', `-${count}`, `--format=${format}`);

        const lines = raw.split('\n');
        const entries: GitLogEntry[] = [];

        for (let i = 0; i + 4 < lines.length; i += 5) {
            entries.push({
                hash: lines[i],
                shortHash: lines[i + 1],
                author: lines[i + 2],
                date: lines[i + 3],
                message: lines[i + 4],
            });
        }

        return entries;
    }

    /** Get a formatted status string suitable for LLM context. */
    async statusSummary(): Promise<string> {
        const s = await this.status();
        if (!s.isRepo) return 'Not a git repository.';

        const lines = [`Branch: ${s.branch}`];
        if (s.staged.length) lines.push(`Staged (${s.staged.length}): ${s.staged.slice(0, 10).join(', ')}`);
        if (s.modified.length) lines.push(`Modified (${s.modified.length}): ${s.modified.slice(0, 10).join(', ')}`);
        if (s.untracked.length) lines.push(`Untracked (${s.untracked.length}): ${s.untracked.slice(0, 10).join(', ')}`);
        if (s.ahead) lines.push(`Ahead of remote by ${s.ahead} commit(s)`);
        if (s.behind) lines.push(`Behind remote by ${s.behind} commit(s)`);
        if (s.clean) lines.push('Working tree clean.');

        return lines.join('\n');
    }
}
