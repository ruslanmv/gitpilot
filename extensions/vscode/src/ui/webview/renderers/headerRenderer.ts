import type { PanelViewModel } from "../viewModel/panelViewModel";

export function renderHeader(vm: PanelViewModel): string {
  return `
  <section class="gp-section gp-header">
    <div class="gp-title-row">
      <div>
        <h1>${vm.title}</h1>
        <p>${vm.providerLine}</p>
        <p>${vm.repoLine}</p>
      </div>
      <div class="gp-header-actions">
        <button data-action="OPEN_SETUP_WIZARD">Setup</button>
        <button data-action="REFRESH_PROJECT_CONTEXT">↻</button>
      </div>
    </div>
  </section>`;
}
