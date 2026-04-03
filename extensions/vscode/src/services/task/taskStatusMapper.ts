import type { TaskStatus } from "../../core/types";

export class TaskStatusMapper {
  fromPlanPresence(hasPlan: boolean): TaskStatus {
    return hasPlan ? "reviewing" : "done";
  }
}
