import type { GitPilotState, PlanStepSummary } from "../../../core/types";

export interface PanelViewModel {
  title: string;
  providerLine: string;
  repoLine: string;
  activeTaskTitle: string;
  activeTaskStatus: string;
  activeTaskProgress: string;
  planSteps: PlanStepSummary[];
}

export function buildPanelViewModel(state: GitPilotState): PanelViewModel {
  const providerLine = [
    state.provider.providerName || "Provider unknown",
    state.server.connected ? "Connected" : "Disconnected",
    state.provider.model || "Model unset",
  ].join(" · ");
  const repoLine = [
    state.projectContextSummary.repoName || state.workspace.folderName || "No repo",
    state.projectContextSummary.branch ? `Branch: ${state.projectContextSummary.branch}` : undefined,
    `Workflow: ${state.workflow.selectedMode}`,
  ].filter(Boolean).join(" · ");
  const planSteps = state.activeTask.plan?.steps || [];
  return {
    title: "GitPilot Workspace",
    providerLine,
    repoLine,
    activeTaskTitle: state.activeTask.title || "No active task",
    activeTaskStatus: state.activeTask.status,
    activeTaskProgress: planSteps.length ? `Step ${Math.min(planSteps.filter((s) => s.status === "applied" || s.status === "ready").length + 1, planSteps.length)}/${planSteps.length}` : "Ready",
    planSteps,
  };
}
