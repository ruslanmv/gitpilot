import type { ChangedFile, FileInScope, ProposedEdit } from "../../core/types";

export class TaskMapper {
  filesInScopeFromPaths(paths: string[]): FileInScope[] {
    return paths.map((path) => ({ path, confidence: "medium" }));
  }

  changedFilesFromEdits(edits: ProposedEdit[]): ChangedFile[] {
    return edits.map((edit) => ({
      path: edit.file,
      status: "proposed",
      summary: edit.summary,
      hasDiff: Boolean(edit.diff || edit.content),
      diff: edit.diff,
      content: edit.content,
    }));
  }
}
