import * as vscode from "vscode";
import * as path from "path";
import type { ProposedEdit } from "../../core/types";
import type { PatchApplyResult, StoredOriginalFile } from "./patchModel";
import { PatchValidationService } from "./patchValidationService";

export class PatchApplier {
  private lastOriginals = new Map<string, StoredOriginalFile>();

  constructor(private readonly validator = new PatchValidationService()) {}

  async apply(workspaceRoot: string | undefined, edits: ProposedEdit[]): Promise<PatchApplyResult> {
    const result: PatchApplyResult = { success: true, appliedFiles: [], failedFiles: [] };
    this.lastOriginals.clear();

    if (!workspaceRoot) {
      return { success: false, appliedFiles: [], failedFiles: edits.map((e) => ({ file: e.file, reason: "No workspace root" })) };
    }

    for (const edit of edits) {
      if (!this.validator.validateFilePath(workspaceRoot, edit.file)) {
        result.success = false;
        result.failedFiles.push({ file: edit.file, reason: "Unsafe path" });
        continue;
      }
      try {
        const fileUri = vscode.Uri.file(path.join(workspaceRoot, edit.file));
        let original: StoredOriginalFile = { file: edit.file, existed: false };
        try {
          original = { file: edit.file, existed: true, content: await vscode.workspace.fs.readFile(fileUri) };
        } catch {
          original = { file: edit.file, existed: false };
        }
        this.lastOriginals.set(edit.file, original);
        const bytes = Buffer.from(edit.content || "", "utf8");
        await vscode.workspace.fs.writeFile(fileUri, bytes);
        result.appliedFiles.push(edit.file);
      } catch (error) {
        result.success = false;
        result.failedFiles.push({ file: edit.file, reason: String(error) });
      }
    }

    return result;
  }

  async revert(workspaceRoot: string | undefined): Promise<PatchApplyResult> {
    const originals = Array.from(this.lastOriginals.values());
    const result: PatchApplyResult = { success: true, appliedFiles: [], failedFiles: [] };

    if (!workspaceRoot) {
      return { success: false, appliedFiles: [], failedFiles: originals.map((e) => ({ file: e.file, reason: "No workspace root" })) };
    }

    for (const original of originals) {
      try {
        const fileUri = vscode.Uri.file(path.join(workspaceRoot, original.file));
        if (original.existed && original.content) {
          await vscode.workspace.fs.writeFile(fileUri, original.content);
        } else {
          await vscode.workspace.fs.delete(fileUri, { recursive: false, useTrash: false });
        }
        result.appliedFiles.push(original.file);
      } catch (error) {
        result.success = false;
        result.failedFiles.push({ file: original.file, reason: String(error) });
      }
    }

    if (result.success) this.lastOriginals.clear();
    return result;
  }
}
