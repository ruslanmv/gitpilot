# GitPilot VS Code Workspace R2

This R2 migration upgrades the VS Code extension toward a right-side GitPilot Workspace experience.

## Included upgrades

- Unified workspace state with `chat` and `ui` sub-state
- Idle, working, and diff-focused panel modes
- Richer task plan generation and status mapping
- Files in scope with open and reveal actions
- Changes section with open and diff actions
- Apply and revert flows for proposed edits
- Recent files and README detection in the idle state
- Improved Setup Wizard with a right-sidebar placement step
- Updated extension metadata for the GitPilot Workspace positioning

## Key files updated

- `extensions/vscode/package.json`
- `extensions/vscode/src/core/types.ts`
- `extensions/vscode/src/core/stateStore.ts`
- `extensions/vscode/src/controllers/panelController.ts`
- `extensions/vscode/src/controllers/chatOrchestrator.ts`
- `extensions/vscode/src/controllers/taskExecutionController.ts`
- `extensions/vscode/src/controllers/workspaceLifecycleController.ts`
- `extensions/vscode/src/services/task/taskPlanner.ts`
- `extensions/vscode/src/services/task/taskMapper.ts`
- `extensions/vscode/src/services/task/taskStatusMapper.ts`
- `extensions/vscode/src/services/context/projectContextService.ts`
- `extensions/vscode/src/services/context/workingSetService.ts`
- `extensions/vscode/src/services/patch/diffService.ts`
- `extensions/vscode/src/services/patch/patchApplier.ts`
- `extensions/vscode/src/services/patch/patchModel.ts`
- `extensions/vscode/src/ui/webview/viewModel/panelViewModel.ts`
- `extensions/vscode/src/ui/webview/gitpilotWorkspaceTemplate.html`
- `extensions/vscode/src/extension.ts`

## Build validation

TypeScript compilation was validated with:

```bash
cd extensions/vscode
node node_modules/typescript/bin/tsc -p ./
```
