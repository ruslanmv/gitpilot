import * as vscode from "vscode";
import type { ProposedEdit } from "../../core/types";
import { PatchValidationService } from "./patchValidationService";

export class DiffService {
  constructor(private readonly validator = new PatchValidationService()) {}

  async openFile(workspaceRoot: string | undefined, relativePath: string): Promise<void> {
    if (!workspaceRoot) return;
    const targetPath = this.validator.resolveSafePath(workspaceRoot, relativePath);
    if (!targetPath) throw new Error("Path escapes the workspace");
    const uri = vscode.Uri.file(targetPath);
    const doc = await vscode.workspace.openTextDocument(uri);
    await vscode.window.showTextDocument(doc, { preview: false });
  }

  async revealFile(workspaceRoot: string | undefined, relativePath: string): Promise<void> {
    if (!workspaceRoot) return;
    const targetPath = this.validator.resolveSafePath(workspaceRoot, relativePath);
    if (!targetPath) throw new Error("Path escapes the workspace");
    const uri = vscode.Uri.file(targetPath);
    await vscode.commands.executeCommand("revealInExplorer", uri);
  }

  async openDiff(workspaceRoot: string | undefined, edit: ProposedEdit): Promise<void> {
    if (!workspaceRoot) return;
    const targetPath = this.validator.resolveSafePath(workspaceRoot, edit.file);
    if (!targetPath) throw new Error("Path escapes the workspace");
    const left = vscode.Uri.file(targetPath);
    const right = vscode.Uri.parse(`untitled:${targetPath}.gitpilot-preview`);
    const content = edit.content || edit.diff || "No preview available.";
    const doc = await vscode.workspace.openTextDocument(right);
    const editor = await vscode.window.showTextDocument(doc, { preview: false });
    const fullRange = new vscode.Range(
      doc.positionAt(0),
      doc.positionAt(doc.getText().length)
    );
    await editor.edit((builder) => {
      builder.replace(fullRange, content);
    });
    await vscode.commands.executeCommand("vscode.diff", left, right, `GitPilot Preview · ${edit.file}`);
  }
}
