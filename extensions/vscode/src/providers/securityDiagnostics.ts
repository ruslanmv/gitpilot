/**
 * Security Diagnostics Provider
 *
 * Integrates GitPilot's AI security scanner into VS Code's
 * Problems panel with real-time diagnostics on file save.
 */
import * as vscode from 'vscode';
import { GitPilotApiClient, SecurityFinding } from '../api/client';

const SEVERITY_MAP: Record<string, vscode.DiagnosticSeverity> = {
    critical: vscode.DiagnosticSeverity.Error,
    high: vscode.DiagnosticSeverity.Error,
    medium: vscode.DiagnosticSeverity.Warning,
    low: vscode.DiagnosticSeverity.Information,
    info: vscode.DiagnosticSeverity.Hint,
};

export class SecurityDiagnosticsProvider {
    private _collection: vscode.DiagnosticCollection;
    private _disposables: vscode.Disposable[] = [];

    constructor(private readonly _client: GitPilotApiClient) {
        this._collection = vscode.languages.createDiagnosticCollection('gitpilot-security');
    }

    enableScanOnSave(): void {
        const watcher = vscode.workspace.onDidSaveTextDocument(async (doc) => {
            if (doc.uri.scheme !== 'file') { return; }
            await this.scanDocument(doc);
        });
        this._disposables.push(watcher);
    }

    async scanDocument(doc: vscode.TextDocument): Promise<SecurityFinding[]> {
        if (!this._client.isConnected) { return []; }

        try {
            const result = await this._client.scanFile(
                vscode.workspace.asRelativePath(doc.uri),
                doc.getText(),
            );
            const findings = result.findings || [];
            this._applyDiagnostics(doc.uri, findings);
            return findings;
        } catch {
            return [];
        }
    }

    async scanWorkspace(): Promise<SecurityFinding[]> {
        if (!this._client.isConnected) { return []; }

        const folders = vscode.workspace.workspaceFolders;
        if (!folders) { return []; }

        try {
            const result = await this._client.scanDirectory(folders[0].uri.fsPath);
            const findings = result.findings || [];

            // Group findings by file
            const byFile = new Map<string, SecurityFinding[]>();
            for (const f of findings) {
                const existing = byFile.get(f.file) || [];
                existing.push(f);
                byFile.set(f.file, existing);
            }

            // Apply diagnostics per file
            for (const [file, fileFindings] of byFile) {
                const uri = vscode.Uri.joinPath(folders[0].uri, file);
                this._applyDiagnostics(uri, fileFindings);
            }

            return findings;
        } catch {
            return [];
        }
    }

    clear(): void {
        this._collection.clear();
    }

    private _applyDiagnostics(uri: vscode.Uri, findings: SecurityFinding[]): void {
        const diagnostics = findings.map(f => {
            const range = new vscode.Range(
                Math.max(0, f.line - 1), 0,
                Math.max(0, f.line - 1), Number.MAX_SAFE_INTEGER,
            );
            const diag = new vscode.Diagnostic(
                range,
                `[${f.category}] ${f.message}${f.cwe ? ` (${f.cwe})` : ''}`,
                SEVERITY_MAP[f.severity] ?? vscode.DiagnosticSeverity.Warning,
            );
            diag.source = 'GitPilot Security';
            diag.code = f.cwe || f.category;
            return diag;
        });
        this._collection.set(uri, diagnostics);
    }

    dispose(): void {
        this._collection.dispose();
        this._disposables.forEach(d => d.dispose());
    }
}
