import * as path from "path";
import * as vscode from "vscode";

export type WorkingSet = {
  currentFile?: string;
  languageId?: string;
  currentSelection?: string;
  openTabs: string[];
  recentFiles: string[];
  relatedFiles: string[];
};

export class WorkingSetService {
  build(workspaceRoot?: string): WorkingSet {
    const editor = vscode.window.activeTextEditor;
    const openTabs = vscode.window.visibleTextEditors
      .map((e) => e.document?.uri?.fsPath)
      .filter((v): v is string => Boolean(v))
      .map((v) => this.rel(workspaceRoot, v));

    const currentFile = editor?.document?.uri?.fsPath
      ? this.rel(workspaceRoot, editor.document.uri.fsPath)
      : undefined;

    const currentSelection = editor && !editor.selection.isEmpty
      ? editor.document.getText(editor.selection)
      : undefined;

    const relatedFiles = this.computeRelatedFiles(currentFile, openTabs);

    return {
      currentFile,
      languageId: editor?.document?.languageId,
      currentSelection,
      openTabs: Array.from(new Set(openTabs)).slice(0, 12),
      recentFiles: Array.from(new Set(openTabs)).slice(0, 12),
      relatedFiles,
    };
  }

  private rel(workspaceRoot: string | undefined, filePath: string): string {
    if (!workspaceRoot) return filePath;
    return path.relative(workspaceRoot, filePath).replace(/\\/g, "/") || filePath;
  }

  private computeRelatedFiles(currentFile: string | undefined, openTabs: string[]): string[] {
    if (!currentFile) return openTabs.slice(0, 8);
    const stem = currentFile.replace(/\.[^.]+$/, "");
    return Array.from(new Set(openTabs.filter((p) => p === currentFile || p.startsWith(stem) || stem.startsWith(p.replace(/\.[^.]+$/, ""))))).slice(0, 8);
  }
}
