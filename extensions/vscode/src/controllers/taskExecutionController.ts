import { StateStore } from "../core/stateStore";
import type { ProposedEdit } from "../core/types";

export class TaskExecutionController {
  constructor(private readonly stateStore: StateStore) {}

  beginTask(title: string, intent: string): void {
    this.stateStore.updateActiveTask({
      title,
      intent,
      status: "planning",
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      changedFiles: [],
      filesInScope: [],
      edits: [],
      error: undefined,
    });
  }

  setEdits(edits: ProposedEdit[]): void {
    this.stateStore.setProposedEdits(edits);
  }
}
