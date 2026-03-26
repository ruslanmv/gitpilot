/**
 * GitPilot Redesign — Chat Commands (new)
 */

import * as vscode from "vscode";
import { StateStore } from "../core/stateStore";
import { ChatClient } from "../api/chatClient";

export function registerChatCommandsV2(
  context: vscode.ExtensionContext,
  stateStore: StateStore,
  chatClient: ChatClient
): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("gitpilot.sendChatMessage", async () => {
      const state = stateStore.state;

      if (!state.readiness.canChat) {
        vscode.window.showWarningMessage(
          "Chat is not available. " +
            (state.readiness.blockers[0] || "Complete setup first.")
        );
        return;
      }

      const text = await vscode.window.showInputBox({
        prompt: "Ask GitPilot...",
        placeHolder: "e.g., Explain this project",
      });

      if (!text) {
        return;
      }

      if (!state.session.sessionId) {
        vscode.window.showWarningMessage("No active session. Start a session first.");
        return;
      }

      try {
        const response = await chatClient.sendMessage({
          session_id: state.session.sessionId,
          message: text,
        });
        vscode.window.showInformationMessage(
          response.answer.substring(0, 200) + (response.answer.length > 200 ? "..." : "")
        );
      } catch (err: any) {
        vscode.window.showErrorMessage(`Chat error: ${err.message || err}`);
      }
    })
  );
}
