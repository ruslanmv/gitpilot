import type { GitPilotState } from "../../../core/types";

export function renderFiles(state: GitPilotState): string {
  const items = state.activeTask.filesInScope.length
    ? state.activeTask.filesInScope.map((item) => `<li><span>${item.path}</span><div><button data-open-file="${item.path}">Open</button></div></li>`).join("")
    : "<li><span>No files in scope yet</span></li>";
  return `<section class="gp-section"><h2>Files in Scope</h2><ul class="gp-list">${items}</ul></section>`;
}
