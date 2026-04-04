export function renderActions(): string {
  return `
  <section class="gp-section">
    <h2>Actions</h2>
    <div class="gp-actions-inline">
      <button data-action="APPLY_PROPOSED_CHANGES">Apply Patch</button>
      <button data-action="REFRESH_PROJECT_CONTEXT">Refresh Context</button>
      <button data-action="OPEN_SETUP_WIZARD">Setup Wizard</button>
    </div>
  </section>`;
}
