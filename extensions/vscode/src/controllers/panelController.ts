import * as vscode from "vscode";
import type { WebviewToExtensionMessage } from "../core/types";
import { StateStore } from "../core/stateStore";
import { DiffService } from "../services/patch/diffService";
import { PatchApplier } from "../services/patch/patchApplier";
import { WorkspaceLifecycleController } from "./workspaceLifecycleController";
import { TaskPlanner } from "../services/task/taskPlanner";

export class PanelController {
  constructor(
    private readonly stateStore: StateStore,
    private readonly onSendChat: (text: string) => Promise<void>,
    private readonly lifecycle: WorkspaceLifecycleController,
    private readonly diffService = new DiffService(),
    private readonly patchApplier = new PatchApplier(),
    private readonly taskPlanner = new TaskPlanner(),
  ) {}

  async handle(msg: WebviewToExtensionMessage): Promise<void> {
    switch (msg.type) {
      case "INIT":
        return;
      case "SEND_CHAT":
        await this.onSendChat(msg.payload.text);
        return;
      case "RUN_QUICK_ACTION":
        await this.onSendChat(msg.payload.action.replace(/_/g, " "));
        return;
      case "OPEN_SETUP_WIZARD":
        await this.lifecycle.runSetupWizard();
        return;
      case "REFRESH_PROJECT_CONTEXT":
        await vscode.commands.executeCommand("gitpilot.refreshProjectContext");
        return;
      case "OPEN_CHANGED_FILE":
        this.stateStore.updateUi({ focusedDiffPath: undefined, mode: this.stateStore.state.activeTask.status === "idle" ? "idle" : "working" });
        await this.diffService.openFile(this.stateStore.state.workspace.folderPath, msg.payload.path);
        return;
      case "REVEAL_FILE":
        await this.diffService.revealFile(this.stateStore.state.workspace.folderPath, msg.payload.path);
        return;
      case "OPEN_CHANGED_DIFF": {
        const edit = this.stateStore.state.activeTask.edits.find((item) => item.file === msg.payload.path);
        if (edit) {
          this.stateStore.updateUi({ focusedDiffPath: msg.payload.path, mode: "diff" });
          await this.diffService.openDiff(this.stateStore.state.workspace.folderPath, edit);
        }
        return;
      }
      case "APPLY_PROPOSED_CHANGES": {
        this.stateStore.setTaskStatus("applying");
        const result = await this.patchApplier.apply(this.stateStore.state.workspace.folderPath, this.stateStore.state.activeTask.edits);
        this.stateStore.setChangedFiles(
          this.stateStore.state.activeTask.changedFiles.map((item) => ({
            ...item,
            status: result.appliedFiles.includes(item.path) ? "applied" : item.status,
          }))
        );
        this.stateStore.setTaskStatus(result.success ? "done" : "failed", result.success ? undefined : `Failed files: ${result.failedFiles.map((f) => f.file).join(", ")}`);
        vscode.window.showInformationMessage(result.success ? `GitPilot applied ${result.appliedFiles.length} change(s).` : `GitPilot applied ${result.appliedFiles.length} change(s) with ${result.failedFiles.length} failure(s).`);
        return;
      }
      case "REVERT_PROPOSED_CHANGES": {
        const result = await this.patchApplier.revert(this.stateStore.state.workspace.folderPath);
        this.stateStore.setChangedFiles(
          this.stateStore.state.activeTask.changedFiles.map((item) => ({
            ...item,
            status: result.appliedFiles.includes(item.path) ? "proposed" : item.status,
          }))
        );
        this.stateStore.setTaskStatus(result.success ? "ready_to_apply" : "failed", result.success ? undefined : `Revert failed for ${result.failedFiles.length} file(s).`);
        vscode.window.showInformationMessage(result.success ? `GitPilot reverted ${result.appliedFiles.length} file(s).` : `GitPilot reverted ${result.appliedFiles.length} file(s) with ${result.failedFiles.length} failure(s).`);
        return;
      }
      case "REGENERATE_TASK_PLAN": {
        const task = this.stateStore.state.activeTask;
        const prompt = task.title || task.summary || "Workspace task";
        this.stateStore.setTaskPlan(this.taskPlanner.refreshPlan(task.plan, prompt));
        this.stateStore.updateUi({ notice: "Task plan regenerated from the current task context." });
        return;
      }
      case "OPEN_SETTINGS":
        await vscode.commands.executeCommand("workbench.action.openSettings", "@ext:ruslanmv.gitpilot-vscode");
        return;
      case "OPEN_PROVIDER_SETUP":
        await vscode.commands.executeCommand("gitpilot.selectProviderV2");
        return;
      case "OPEN_MODEL_SETUP":
        await vscode.commands.executeCommand("gitpilot.selectModelV2");
        return;
      case "OPEN_LLM_SETTINGS":
        await vscode.commands.executeCommand("gitpilot.openLlmSettings");
        return;
      case "OPEN_WORKSPACE":
        await vscode.commands.executeCommand("workbench.action.files.openFolder");
        return;
      case "REFRESH_STATUS":
        await vscode.commands.executeCommand("gitpilot.refreshStatus");
        return;
      default:
        return;
    }
  }
}
