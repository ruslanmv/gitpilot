# GitPilot VS Code Workspace Migration

This upgrade migrates the VS Code extension UI toward a right-side Copilot workspace layout.

## Target layout

1. Header / provider / repo state
2. Active task / status
3. Plan
4. Files in scope
5. Changes
6. Chat
7. Actions

## What changed

- Rebuilt the main VS Code webview UI in `extensions/vscode/src/ui/webview/GitPilotPanel.ts`.
- Added an idle workspace state with project context, quick actions, and recent files.
- Added a task-focused state with task progress, plan, scoped files, changed files, chat, and actions.
- Preserved the current extension message contract so the new UI works with the existing controller and backend wiring.
- Renamed the contributed VS Code view to **GitPilot Workspace**.

## Behavior

### Idle / ready state
Shows:
- provider/model health
- workflow / repo / branch
- project context summary
- quick actions
- recent files
- chat prompt

### Active task state
Shows:
- task title and status
- inferred step progress
- plan steps
- files in scope
- proposed changes with open/diff actions
- chat stream
- action buttons

## Instructions for the UI upgrade

To use the upgrade as a right-side copilot workspace inside VS Code:

1. Open the **GitPilot Workspace** view.
2. Right-click the view title and move it to the **Secondary Side Bar**.
3. Keep Explorer and Source Control on the left.
4. Keep the editor or diff view in the center.
5. Keep GitPilot Workspace visible on the right for chat, plans, and change review.

## Recommended next follow-up

- Create a dedicated `GitPilotWorkspaceViewModel` adapter so the webview does not consume raw `GitPilotState` directly.
- Add a true revert flow for proposed edits.
- Persist chat history in extension state instead of synthesizing it in the webview.
- Consider reusing the React web UI inside the VS Code webview for full design parity.
