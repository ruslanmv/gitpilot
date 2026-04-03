import * as vscode from "vscode";
import * as path from "path";
import type { ProposedEdit } from "../../core/types";

export class DiffService {
  async openFile(workspaceRoot: string | undefined, relativePath: string): Promise<void> {
    if (!workspaceRoot) return;
    const uri = vscode.Uri.file(path.join(workspaceRoot, relativePath));
    const doc = await vscode.workspace.openTextDocument(uri);
    await vscode.window.showTextDocument(doc, { preview: false });
  }

  async openDiff(workspaceRoot: string | undefined, edit: ProposedEdit): Promise<void> {
    if (!workspaceRoot) return;
    const left = vscode.Uri.file(path.join(workspaceRoot, edit.file));
    const right = vscode.Uri.parse(`untitled:${path.join(workspaceRoot, edit.file)}.gitpilot-preview`);
    const content = edit.content || edit.diff || "No preview available.";
    const doc = await vscode.workspace.openTextDocument(right);
    const editor = await vscode.window.showTextDocument(doc, { preview: false });
    await editor.edit((builder) => {
      builder.insert(new vscode.Position(0, 0), content);
    });
    await vscode.commands.executeCommand("vscode.diff", left, right, `GitPilot Preview · ${edit.file}`);
  }
}
