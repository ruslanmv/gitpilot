/**
 * Code review, explain, fix, and test command handlers.
 *
 * Safety-first: review actions show a plan before execution
 * (inspired by InflixOP/GitPilot's dry-run pattern).
 */
import * as vscode from 'vscode';
import { ChatViewProvider } from '../views/chatViewProvider';

export function registerReviewCommands(
    context: vscode.ExtensionContext,
    chatProvider: ChatViewProvider,
): void {
    context.subscriptions.push(
        vscode.commands.registerCommand('gitpilot.reviewFile', async () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
                vscode.window.showWarningMessage('No active file to review');
                return;
            }
            const fileName = vscode.workspace.asRelativePath(editor.document.uri);
            const content = editor.document.getText();
            chatProvider.sendCodeContext(content, 'review');
            vscode.commands.executeCommand('gitpilot.openChatTab');
            vscode.window.showInformationMessage(`Reviewing ${fileName}...`);
        }),

        vscode.commands.registerCommand('gitpilot.explainSelection', () => {
            const code = getSelectedText();
            if (!code) { return; }
            chatProvider.sendCodeContext(code, 'explain');
            vscode.commands.executeCommand('gitpilot.openChatTab');
        }),

        vscode.commands.registerCommand('gitpilot.reviewSelection', () => {
            const code = getSelectedText();
            if (!code) { return; }
            chatProvider.sendCodeContext(code, 'review');
            vscode.commands.executeCommand('gitpilot.openChatTab');
        }),

        vscode.commands.registerCommand('gitpilot.fixSelection', () => {
            const code = getSelectedText();
            if (!code) { return; }
            chatProvider.sendCodeContext(code, 'fix');
            vscode.commands.executeCommand('gitpilot.openChatTab');
        }),

        vscode.commands.registerCommand('gitpilot.testSelection', () => {
            const code = getSelectedText();
            if (!code) { return; }
            chatProvider.sendCodeContext(code, 'test');
            vscode.commands.executeCommand('gitpilot.openChatTab');
        }),

        // CodeLens commands for symbols
        vscode.commands.registerCommand('gitpilot.explainSymbol',
            async (uri: vscode.Uri, range: vscode.Range, name: string) => {
                const doc = await vscode.workspace.openTextDocument(uri);
                // Get the function/class body (up to 100 lines)
                const endLine = Math.min(range.start.line + 100, doc.lineCount - 1);
                const bodyRange = new vscode.Range(range.start.line, 0, endLine, 0);
                const code = doc.getText(bodyRange);
                chatProvider.sendCodeContext(code, 'explain');
                vscode.commands.executeCommand('gitpilot.openChatTab');
            },
        ),

        vscode.commands.registerCommand('gitpilot.reviewSymbol',
            async (uri: vscode.Uri, range: vscode.Range, name: string) => {
                const doc = await vscode.workspace.openTextDocument(uri);
                const endLine = Math.min(range.start.line + 100, doc.lineCount - 1);
                const bodyRange = new vscode.Range(range.start.line, 0, endLine, 0);
                const code = doc.getText(bodyRange);
                chatProvider.sendCodeContext(code, 'review');
                vscode.commands.executeCommand('gitpilot.openChatTab');
            },
        ),
    );
}

function getSelectedText(): string | undefined {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        vscode.window.showWarningMessage('No active editor');
        return undefined;
    }
    const selection = editor.document.getText(editor.selection);
    if (!selection) {
        vscode.window.showWarningMessage('No text selected');
        return undefined;
    }
    return selection;
}
