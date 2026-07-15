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

    const preparedEdits = edits.map((edit) => ({
      edit,
      targetPath: this.validator.resolveSafePath(workspaceRoot, edit.file),
    }));

    const unsafeEdit = preparedEdits.find((item) => !item.targetPath);
    if (unsafeEdit) {
      return {
        success: false,
        appliedFiles: [],
        failedFiles: [{ file: unsafeEdit.edit.file, reason: "Unsafe path" }],
      };
    }

    for (const { edit, targetPath } of preparedEdits) {
      const fileUri = vscode.Uri.file(targetPath!);
      try {
        const original = await this.captureOriginalFile(edit.file, fileUri);
        this.lastOriginals.set(edit.file, original);
      } catch (error) {
        this.lastOriginals.clear();
        return {
          success: false,
          appliedFiles: [],
          failedFiles: [{ file: edit.file, reason: String(error) }],
        };
      }
    }

    for (const { edit, targetPath } of preparedEdits) {
      const fileUri = vscode.Uri.file(targetPath!);
      try {
        await vscode.workspace.fs.createDirectory(vscode.Uri.file(path.dirname(targetPath!)));
        const bytes = Buffer.from(edit.content || "", "utf8");
        await vscode.workspace.fs.writeFile(fileUri, bytes);
        result.appliedFiles.push(edit.file);
      } catch (error) {
        result.success = false;
        result.failedFiles.push({ file: edit.file, reason: String(error) });
        await this.rollbackAppliedFiles(workspaceRoot, result.appliedFiles);
        break;
      }
    }

    if (!result.success) {
      this.lastOriginals.clear();
      result.appliedFiles = [];
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
        const targetPath = this.validator.resolveSafePath(workspaceRoot, original.file);
        if (!targetPath) {
          throw new Error("Unsafe path");
        }

        const fileUri = vscode.Uri.file(targetPath);
        if (original.existed && original.content) {
          await vscode.workspace.fs.writeFile(fileUri, original.content);
        } else {
          await vscode.workspace.fs.delete(fileUri, { recursive: false, useTrash: true });
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

  private async captureOriginalFile(
    file: string,
    fileUri: vscode.Uri
  ): Promise<StoredOriginalFile> {
    try {
      return { file, existed: true, content: await vscode.workspace.fs.readFile(fileUri) };
    } catch {
      return { file, existed: false };
    }
  }

  private async rollbackAppliedFiles(
    workspaceRoot: string,
    appliedFiles: string[]
  ): Promise<void> {
    for (const file of [...appliedFiles].reverse()) {
      const original = this.lastOriginals.get(file);
      const targetPath = this.validator.resolveSafePath(workspaceRoot, file);
      if (!original || !targetPath) {
        continue;
      }

      const fileUri = vscode.Uri.file(targetPath);
      try {
        if (original.existed && original.content) {
          await vscode.workspace.fs.writeFile(fileUri, original.content);
        } else {
          await vscode.workspace.fs.delete(fileUri, { recursive: false, useTrash: true });
        }
      } catch {
        // Best-effort rollback. The original apply failure remains the
        // user-visible error while subsequent revert can address leftovers.
      }
    }
  }
}
