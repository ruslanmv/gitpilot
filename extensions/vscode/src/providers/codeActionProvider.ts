/**
 * Code Action Provider
 *
 * Adds "GitPilot: Explain / Review / Fix / Test" quick-fix actions
 * when text is selected or when hovering over diagnostics.
 */
import * as vscode from 'vscode';

export class GitPilotCodeActionProvider implements vscode.CodeActionProvider {
    static readonly providedCodeActionKinds = [
        vscode.CodeActionKind.QuickFix,
        vscode.CodeActionKind.RefactorRewrite,
    ];

    provideCodeActions(
        document: vscode.TextDocument,
        range: vscode.Range | vscode.Selection,
    ): vscode.CodeAction[] {
        const actions: vscode.CodeAction[] = [];

        // Only show when there's a selection with content
        if (range.isEmpty) { return actions; }

        const selectedText = document.getText(range);
        if (!selectedText.trim()) { return actions; }

        // Explain action
        const explain = new vscode.CodeAction(
            'GitPilot: Explain this code',
            vscode.CodeActionKind.QuickFix,
        );
        explain.command = {
            command: 'gitpilot.explainSelection',
            title: 'Explain Selection',
        };
        actions.push(explain);

        // Review action
        const review = new vscode.CodeAction(
            'GitPilot: Review this code',
            vscode.CodeActionKind.QuickFix,
        );
        review.command = {
            command: 'gitpilot.reviewSelection',
            title: 'Review Selection',
        };
        actions.push(review);

        // Fix action
        const fix = new vscode.CodeAction(
            'GitPilot: Fix this code',
            vscode.CodeActionKind.QuickFix,
        );
        fix.command = {
            command: 'gitpilot.fixSelection',
            title: 'Fix Selection',
        };
        actions.push(fix);

        // Test action
        const test = new vscode.CodeAction(
            'GitPilot: Generate tests',
            vscode.CodeActionKind.RefactorRewrite,
        );
        test.command = {
            command: 'gitpilot.testSelection',
            title: 'Generate Tests',
        };
        actions.push(test);

        return actions;
    }
}
