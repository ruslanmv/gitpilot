/**
 * Security scanning command handlers.
 */
import * as vscode from 'vscode';
import { SecurityDiagnosticsProvider } from '../providers/securityDiagnostics';

export function registerSecurityCommands(
    context: vscode.ExtensionContext,
    securityProvider: SecurityDiagnosticsProvider,
): void {
    context.subscriptions.push(
        vscode.commands.registerCommand('gitpilot.scanFile', async () => {
            const editor = vscode.window.activeTextEditor;
            if (!editor) {
                vscode.window.showWarningMessage('No active file to scan');
                return;
            }
            const fileName = vscode.workspace.asRelativePath(editor.document.uri);
            await vscode.window.withProgress(
                { location: vscode.ProgressLocation.Notification, title: `Scanning ${fileName}...` },
                async () => {
                    const findings = await securityProvider.scanDocument(editor.document);
                    if (findings.length === 0) {
                        vscode.window.showInformationMessage(`No security issues found in ${fileName}`);
                    } else {
                        const critical = findings.filter(f => f.severity === 'critical' || f.severity === 'high').length;
                        vscode.window.showWarningMessage(
                            `Found ${findings.length} issue(s) in ${fileName} (${critical} critical/high). Check Problems panel.`,
                        );
                    }
                },
            );
        }),

        vscode.commands.registerCommand('gitpilot.scanWorkspace', async () => {
            await vscode.window.withProgress(
                { location: vscode.ProgressLocation.Notification, title: 'Scanning workspace for security issues...' },
                async () => {
                    const findings = await securityProvider.scanWorkspace();
                    if (findings.length === 0) {
                        vscode.window.showInformationMessage('No security issues found in workspace');
                    } else {
                        const critical = findings.filter(f => f.severity === 'critical' || f.severity === 'high').length;
                        vscode.window.showWarningMessage(
                            `Found ${findings.length} issue(s) across workspace (${critical} critical/high). Check Problems panel.`,
                        );
                    }
                },
            );
        }),

        vscode.commands.registerCommand('gitpilot.clearSecurityDiagnostics', () => {
            securityProvider.clear();
            vscode.window.showInformationMessage('Security diagnostics cleared');
        }),
    );
}
