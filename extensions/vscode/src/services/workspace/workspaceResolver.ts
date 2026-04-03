/**
 * GitPilot Redesign — Workspace Resolver
 * Detects current VS Code folder and watches for changes.
 */

import * as vscode from "vscode";

export interface WorkspaceInfo {
  folderOpen: boolean;
  folderPath?: string;
  folderName?: string;
}

export class WorkspaceResolver implements vscode.Disposable {
  private _onDidChange = new vscode.EventEmitter<WorkspaceInfo>();
  readonly onDidChange = this._onDidChange.event;
  private _disposables: vscode.Disposable[] = [];

  constructor() {
    this._disposables.push(
      vscode.workspace.onDidChangeWorkspaceFolders(() => {
        this._onDidChange.fire(this.resolve());
      })
    );
  }

  resolve(): WorkspaceInfo {
    const folders = vscode.workspace.workspaceFolders;
    if (!folders || folders.length === 0) {
      return { folderOpen: false };
    }
    const folder = folders[0];
    return {
      folderOpen: true,
      folderPath: folder.uri.fsPath,
      folderName: folder.name,
    };
  }

  dispose(): void {
    this._onDidChange.dispose();
    this._disposables.forEach((d) => d.dispose());
  }
}
