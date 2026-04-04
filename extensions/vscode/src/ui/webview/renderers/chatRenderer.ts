import type { GitPilotState } from "../../../core/types";

export function renderChat(state: GitPilotState): string {
  return `
  <section class="gp-section">
    <h2>Chat</h2>
    <div class="gp-summary">${state.activeTask.summary || "What would you like GitPilot to do?"}</div>
    <textarea id="gp-chat-input" placeholder="Describe the task..."></textarea>
    <div class="gp-actions-inline"><button data-action="SEND_CHAT">Send</button></div>
  </section>`;
}
