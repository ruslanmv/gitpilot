import type { GitPilotState } from "../../../core/types";

export function renderChanges(state: GitPilotState): string {
  const items = state.activeTask.changedFiles.length
    ? state.activeTask.changedFiles.map((item) => `<li><span>${item.status.toUpperCase()} ${item.path}</span><div><button data-open-file="${item.path}">Open</button><button data-open-diff="${item.path}">Diff</button></div></li>`).join("")
    : "<li><span>No proposed changes yet</span></li>";
  return `<section class="gp-section"><h2>Changes</h2><ul class="gp-list">${items}</ul></section>`;
}
