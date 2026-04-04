import type { GitPilotState } from "../../../core/types";
import type { PanelViewModel } from "../viewModel/panelViewModel";

export function renderTask(vm: PanelViewModel, state: GitPilotState): string {
  return `
  <section class="gp-section">
    <h2>Active Task</h2>
    <div class="gp-task-title">${vm.activeTaskTitle}</div>
    <div class="gp-meta">Status: ${vm.activeTaskStatus}</div>
    <div class="gp-meta">${vm.activeTaskProgress}</div>
    <div class="gp-summary">${state.activeTask.summary || "GitPilot is ready for the next workspace task."}</div>
  </section>`;
}
