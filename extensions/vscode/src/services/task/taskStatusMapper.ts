import type { TaskStatus } from "../../core/types";

export class TaskStatusMapper {
  toUserLabel(status: TaskStatus): string {
    return {
      idle: "Idle",
      planning: "Planning",
      generating: "Editing files",
      reviewing: "Reviewing proposal",
      ready_to_apply: "Ready to apply",
      applying: "Applying changes",
      done: "Done",
      failed: "Failed",
    }[status];
  }

  fromPlanPresence(hasPlan: boolean): TaskStatus {
    return hasPlan ? "reviewing" : "done";
  }

  inferProgress(status: TaskStatus, totalSteps: number, completedSteps: number): { current: number; total: number } {
    if (totalSteps <= 0) return { current: 0, total: 0 };
    if (status === "done") return { current: totalSteps, total: totalSteps };
    if (status === "failed") return { current: Math.min(completedSteps + 1, totalSteps), total: totalSteps };
    return { current: Math.min(completedSteps + 1, totalSteps), total: totalSteps };
  }
}
