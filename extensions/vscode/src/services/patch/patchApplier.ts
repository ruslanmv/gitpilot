import * as vscode from "vscode";
import * as path from "path";
import type { ProposedEdit } from "../../core/types";
import type { PatchApplyResult } from "./patchModel";
import { PatchValidationService } from "./patchValidationService";

export class PatchApplier {
  constructor(private readonly validator = new PatchValidationService()) {}

  async apply(workspaceRoot: string | undefined, edits: ProposedEdit[]): Promise<PatchApplyResult> {
    const result: PatchApplyResult = { success: true, appliedFiles: [], failedFiles: [] };
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
}
