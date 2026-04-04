import { StateStore } from "../core/stateStore";
import type { PlanSummary, ProposedEdit } from "../core/types";

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
    this.stateStore.updateUi({ mode: "working", focusedDiffPath: undefined, notice: undefined });
  }

  setPlan(plan: PlanSummary | undefined): void {
    this.stateStore.setTaskPlan(plan);
  }

  setEdits(edits: ProposedEdit[]): void {
    this.stateStore.setProposedEdits(edits);
  }

  markDiffFocused(path: string | undefined): void {
    this.stateStore.updateUi({ mode: path ? "diff" : "working", focusedDiffPath: path });
  }

  complete(summary?: string): void {
    this.stateStore.updateActiveTask({ status: "done", summary: summary ?? this.stateStore.state.activeTask.summary });
    this.stateStore.updateUi({ mode: "working", focusedDiffPath: undefined });
  }

  fail(message: string): void {
    this.stateStore.setTaskStatus("failed", message);
    this.stateStore.updateUi({ mode: "working", notice: message });
  }
}
