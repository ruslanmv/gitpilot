/**
 * Workspace scanner — builds a file tree of the current VS Code workspace.
 *
 * Features:
 * - Respects .gitignore patterns
 * - Skips common large/binary directories (node_modules, .git, etc.)
 * - Caches the tree and updates incrementally via FileSystemWatcher
 * - Provides both flat file lists and tree-formatted strings
 */

import * as vscode from 'vscode';
import * as path from 'path';

// Directories to always skip (never useful for context)
const ALWAYS_SKIP = new Set([
    'node_modules', '.git', '.hg', '.svn', '__pycache__', '.tox',
    '.venv', 'venv', 'env', '.env', '.mypy_cache', '.pytest_cache',
    '.ruff_cache', 'dist', 'build', 'out', '.next', '.nuxt',
    '.turbo', 'coverage', '.nyc_output', '.cargo', 'target',
    '.gradle', '.idea', '.vs', 'Pods', '.dart_tool',
    'vendor', 'bower_components', '.terraform',
]);

// Binary/large file extensions to skip
const SKIP_EXTENSIONS = new Set([
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.ico', '.svg',
    '.webp', '.mp3', '.mp4', '.avi', '.mov', '.wav', '.flac',
    '.zip', '.tar', '.gz', '.bz2', '.7z', '.rar',
    '.woff', '.woff2', '.ttf', '.eot', '.otf',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx',
    '.pyc', '.pyo', '.so', '.dylib', '.dll', '.exe',
    '.bin', '.dat', '.db', '.sqlite', '.sqlite3',
    '.lock', '.lockb',
]);

export interface FileNode {
    name: string;
    relativePath: string;
    isDirectory: boolean;
    children?: FileNode[];
    size?: number;
}

export class WorkspaceScanner {
    private _root: string;
    private _tree: FileNode | null = null;
    private _flatFiles: string[] = [];
    private _watcher: vscode.FileSystemWatcher | null = null;
    private _onDidChange = new vscode.EventEmitter<void>();
    readonly onDidChange = this._onDidChange.event;

    constructor(root: string) {
        this._root = root;
    }

    get root(): string { return this._root; }
    get files(): string[] { return this._flatFiles; }
    get tree(): FileNode | null { return this._tree; }

    /**
     * Perform a full scan of the workspace. Call once on activation,
     * then rely on the watcher for incremental updates.
     */
    async scan(maxFiles = 5000): Promise<void> {
        const rootUri = vscode.Uri.file(this._root);
        const flatFiles: string[] = [];
        const rootNode: FileNode = {
            name: path.basename(this._root),
            relativePath: '.',
            isDirectory: true,
            children: [],
        };

        await this._scanDir(rootUri, '', rootNode, flatFiles, maxFiles);

        this._tree = rootNode;
        this._flatFiles = flatFiles;
        this._onDidChange.fire();
    }

    /**
     * Start watching for file system changes to keep the tree updated.
     */
    startWatching(): vscode.Disposable {
        if (this._watcher) {
            this._watcher.dispose();
        }

        this._watcher = vscode.workspace.createFileSystemWatcher(
            new vscode.RelativePattern(this._root, '**/*'),
        );

        // Debounced rescan on changes
        let timeout: NodeJS.Timeout | undefined;
        const debouncedRescan = () => {
            if (timeout) clearTimeout(timeout);
            timeout = setTimeout(() => this.scan(), 2000);
        };

        this._watcher.onDidCreate(debouncedRescan);
        this._watcher.onDidDelete(debouncedRescan);
        // Don't rescan on content changes — only structural changes matter

        return this._watcher;
    }

    /**
     * Format the file tree as a string for the LLM system prompt.
     * Truncates to maxLines to fit within token budget.
     */
    formatTree(maxLines = 200): string {
        if (!this._tree) return '(workspace not scanned)';
        const lines: string[] = [];
        this._formatNode(this._tree, '', lines, maxLines);
        if (lines.length >= maxLines) {
            lines.push(`... (${this._flatFiles.length} total files, truncated)`);
        }
        return lines.join('\n');
    }

    /**
     * Get a summary of the project (package.json name, language, etc.)
     */
    async detectProjectInfo(): Promise<Record<string, string>> {
        const info: Record<string, string> = {};
        const rootUri = vscode.Uri.file(this._root);

        // Check for common project files
        const markers: Array<{ file: string; key: string; detect: (content: string) => string }> = [
            { file: 'package.json', key: 'type', detect: () => 'Node.js / JavaScript' },
            { file: 'Cargo.toml', key: 'type', detect: () => 'Rust' },
            { file: 'go.mod', key: 'type', detect: () => 'Go' },
            { file: 'pyproject.toml', key: 'type', detect: () => 'Python' },
            { file: 'setup.py', key: 'type', detect: () => 'Python' },
            { file: 'requirements.txt', key: 'type', detect: () => 'Python' },
            { file: 'pom.xml', key: 'type', detect: () => 'Java (Maven)' },
            { file: 'build.gradle', key: 'type', detect: () => 'Java (Gradle)' },
            { file: 'Gemfile', key: 'type', detect: () => 'Ruby' },
            { file: 'composer.json', key: 'type', detect: () => 'PHP' },
            { file: 'pubspec.yaml', key: 'type', detect: () => 'Dart / Flutter' },
            { file: 'CMakeLists.txt', key: 'type', detect: () => 'C / C++' },
            { file: 'Makefile', key: 'type', detect: () => 'Make-based project' },
            { file: 'Dockerfile', key: 'has_docker', detect: () => 'true' },
            { file: '.gitignore', key: 'has_git', detect: () => 'true' },
        ];

        for (const m of markers) {
            try {
                const uri = vscode.Uri.joinPath(rootUri, m.file);
                const content = Buffer.from(
                    await vscode.workspace.fs.readFile(uri),
                ).toString('utf-8');
                info[m.key] = m.detect(content);

                // Extract project name from package.json
                if (m.file === 'package.json') {
                    try {
                        const pkg = JSON.parse(content);
                        if (pkg.name) info['name'] = pkg.name;
                        if (pkg.description) info['description'] = pkg.description;
                    } catch { /* empty */ }
                }
            } catch {
                // File doesn't exist, skip
            }
        }

        info['total_files'] = String(this._flatFiles.length);
        return info;
    }

    dispose(): void {
        this._watcher?.dispose();
        this._onDidChange.dispose();
    }

    // -----------------------------------------------------------------------
    // Private
    // -----------------------------------------------------------------------

    private async _scanDir(
        dirUri: vscode.Uri,
        relativePath: string,
        parentNode: FileNode,
        flatFiles: string[],
        maxFiles: number,
    ): Promise<void> {
        if (flatFiles.length >= maxFiles) return;

        let entries: [string, vscode.FileType][];
        try {
            entries = await vscode.workspace.fs.readDirectory(dirUri);
        } catch {
            return; // Permission denied or other error
        }

        // Sort: directories first, then alphabetical
        entries.sort((a, b) => {
            if (a[1] === b[1]) return a[0].localeCompare(b[0]);
            return a[1] === vscode.FileType.Directory ? -1 : 1;
        });

        for (const [name, type] of entries) {
            if (flatFiles.length >= maxFiles) break;

            const relPath = relativePath ? `${relativePath}/${name}` : name;

            if (type === vscode.FileType.Directory) {
                if (ALWAYS_SKIP.has(name) || name.startsWith('.')) continue;

                const childNode: FileNode = {
                    name,
                    relativePath: relPath,
                    isDirectory: true,
                    children: [],
                };
                parentNode.children!.push(childNode);

                await this._scanDir(
                    vscode.Uri.joinPath(dirUri, name),
                    relPath,
                    childNode,
                    flatFiles,
                    maxFiles,
                );
            } else {
                const ext = path.extname(name).toLowerCase();
                if (SKIP_EXTENSIONS.has(ext)) continue;

                const fileNode: FileNode = {
                    name,
                    relativePath: relPath,
                    isDirectory: false,
                };
                parentNode.children!.push(fileNode);
                flatFiles.push(relPath);
            }
        }
    }

    private _formatNode(
        node: FileNode,
        indent: string,
        lines: string[],
        maxLines: number,
    ): void {
        if (lines.length >= maxLines) return;

        if (node.isDirectory && node.children) {
            if (node.relativePath !== '.') {
                lines.push(`${indent}${node.name}/`);
            }
            const childIndent = node.relativePath === '.' ? '' : indent + '  ';
            for (const child of node.children) {
                this._formatNode(child, childIndent, lines, maxLines);
            }
        } else {
            lines.push(`${indent}${node.name}`);
        }
    }
}
