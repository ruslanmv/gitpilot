import type { ChatMessagePayload, GitPilotState, PlanStepSummary } from "../../../core/types";
import { TaskStatusMapper } from "../../../services/task/taskStatusMapper";

export interface PanelViewModel {
  title: string;
  providerLine: string;
  repoLine: string;
  activeTaskTitle: string;
  activeTaskStatus: string;
  activeTaskProgress: string;
  activeTaskPercent: number;
  planSteps: PlanStepSummary[];
  recentFiles: string[];
  chatMessages: ChatMessagePayload[];
  isIdle: boolean;
}

export function buildPanelViewModel(state: GitPilotState): PanelViewModel {
  const statusMapper = new TaskStatusMapper();
  const providerLine = [
    state.provider.providerName || "Provider unknown",
    state.server.connected ? (state.provider.health === "error" ? "Degraded" : "Connected") : "Disconnected",
    state.provider.model || "Model unset",
  ].join(" · ");

  const repoLine = [
    `Workflow: ${state.workflow.selectedMode}`,
    `Repo: ${state.projectContextSummary.repoName || state.workspace.folderName || "No repo"}`,
    `Branch: ${state.projectContextSummary.branch || state.workspace.git.branch || "No branch"}`,
  ].join(" · ");

  const planSteps = state.activeTask.plan?.steps || [];
  const completedSteps = planSteps.filter((s) => s.status === "applied" || s.status === "ready").length;
  const progress = statusMapper.inferProgress(state.activeTask.status, planSteps.length, completedSteps);
  const percent = progress.total > 0 ? Math.round(((progress.current - 1) / progress.total) * 100) : 0;
  const isIdle = state.ui.mode === "idle" || state.activeTask.status === "idle";

  return {
    title: "GitPilot Workspace",
    providerLine,
    repoLine,
    activeTaskTitle: state.activeTask.title || "No active task",
    activeTaskStatus: statusMapper.toUserLabel(state.activeTask.status),
    activeTaskProgress: progress.total > 0 ? `Step ${progress.current}/${progress.total}` : "Ready",
    activeTaskPercent: Math.max(0, Math.min(100, percent)),
    planSteps,
    recentFiles: state.projectContextSummary.recentFiles || state.projectContextSummary.keyFiles || [],
    chatMessages: state.chat.messages,
    isIdle,
  };
}
