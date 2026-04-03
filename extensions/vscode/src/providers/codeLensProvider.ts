/**
 * CodeLens Provider
 *
 * Adds inline "GitPilot: Review | Explain | Fix" lenses
 * above functions and classes detected in the editor.
 */
import * as vscode from 'vscode';

const FUNCTION_PATTERNS: RegExp[] = [
    // JavaScript/TypeScript
    /^(?:export\s+)?(?:async\s+)?function\s+(\w+)/,
    /^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|[^=])\s*=>/,
    /^(?:export\s+)?class\s+(\w+)/,
    // Python
    /^(?:async\s+)?def\s+(\w+)/,
    /^class\s+(\w+)/,
    // Rust/Go/Java
    /^(?:pub\s+)?fn\s+(\w+)/,
    /^func\s+(?:\([^)]+\)\s+)?(\w+)/,
    /^(?:public|private|protected)\s+(?:static\s+)?(?:\w+\s+)+(\w+)\s*\(/,
];

export class GitPilotCodeLensProvider implements vscode.CodeLensProvider {
    private _onDidChangeCodeLenses = new vscode.EventEmitter<void>();
    readonly onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;

    private _enabled: boolean;

    constructor(enabled: boolean) {
        this._enabled = enabled;
    }

    setEnabled(enabled: boolean): void {
        this._enabled = enabled;
        this._onDidChangeCodeLenses.fire();
    }

    provideCodeLenses(document: vscode.TextDocument): vscode.CodeLens[] {
        if (!this._enabled) { return []; }

        const lenses: vscode.CodeLens[] = [];
        const text = document.getText();
        const lines = text.split('\n');

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i].trimStart();
            for (const pattern of FUNCTION_PATTERNS) {
                const match = line.match(pattern);
                if (match) {
                    const range = new vscode.Range(i, 0, i, lines[i].length);
                    const name = match[1] || 'this';

                    lenses.push(new vscode.CodeLens(range, {
                        title: '$(rocket) Explain',
                        command: 'gitpilot.explainSymbol',
                        arguments: [document.uri, range, name],
                        tooltip: `Ask GitPilot to explain ${name}`,
                    }));

                    lenses.push(new vscode.CodeLens(range, {
                        title: '$(shield) Review',
                        command: 'gitpilot.reviewSymbol',
                        arguments: [document.uri, range, name],
                        tooltip: `Ask GitPilot to review ${name}`,
                    }));

                    break; // one match per line
                }
            }
        }

        return lenses;
    }
}
