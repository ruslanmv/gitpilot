import type { ChangedFile, FileInScope, ProposedEdit } from "../../core/types";

export class TaskMapper {
  filesInScopeFromPaths(paths: string[]): FileInScope[] {
    const seen = new Set<string>();
    return paths
      .filter((path): path is string => Boolean(path))
      .filter((path) => {
        if (seen.has(path)) return false;
        seen.add(path);
        return true;
      })
      .map((path, index) => ({
        path,
        reason: index === 0 ? "Primary file for the current task" : "Related file in the current working set",
        confidence: index === 0 ? "high" : "medium",
      }));
  }

  changedFilesFromEdits(edits: ProposedEdit[]): ChangedFile[] {
    return edits.map((edit) => ({
      path: edit.file,
      kind: edit.kind === "create" ? "A" : "M",
      status: "proposed",
      summary: edit.summary || this.summarizeEdit(edit),
      reason: edit.kind === "create" ? "New file proposed by GitPilot" : "Existing file updated by GitPilot",
      hasDiff: Boolean(edit.diff || edit.content),
      diffPreview: edit.diff,
      contentPreview: edit.content,
    }));
  }

  private summarizeEdit(edit: ProposedEdit): string {
    if (edit.kind === "create") return "Create new file";
    if (edit.kind === "replace") return "Replace file contents";
    return "Patch existing file";
  }
}
