import type { PanelViewModel } from "../viewModel/panelViewModel";

export function renderPlan(vm: PanelViewModel): string {
  const items = vm.planSteps.length
    ? vm.planSteps.map((step) => `<li><strong>${step.title}</strong><span>${step.description}</span></li>`).join("")
    : "<li><strong>No plan yet</strong><span>Start by asking GitPilot to inspect or change the workspace.</span></li>";
  return `<section class="gp-section"><h2>Plan</h2><ol class="gp-plan">${items}</ol></section>`;
}
