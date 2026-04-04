/**
 * VS Code implementation of PlatformHost.
 *
 * Wraps existing FileOps, GitLocal, and VS Code APIs.
 * Does NOT replace them — delegates to them.
 */
import * as vscode from "vscode";
import {
  PlatformHost,
  ShellEvent,
  DiagnosticEntry,
  ApprovalRequest,
  ApprovalResponse,
} from "./interface";
import { FileOps } from "../local/fileOps";

export class VSCodeAdapter implements PlatformHost {
  constructor(
    private readonly _root: string,
    private readonly _fileOps: FileOps,
    private readonly _onApproval: (
      req: ApprovalRequest
    ) => Promise<ApprovalResponse>
  ) {}

  readFile(path: string): Promise<string> {
    return this._fileOps.readFile(path);
  }

  writeFile(path: string, content: string): Promise<void> {
    return this._fileOps.writeFile(path, content);
  }

  fileExists(path: string): Promise<boolean> {
    return this._fileOps.exists(path);
  }

  listDirectory(path: string, recursive?: boolean): Promise<string[]> {
    return this._fileOps.listDirectory(path, recursive);
  }

  async *runCommand(
    command: string,
    options?: { cwd?: string; timeout?: number; signal?: AbortSignal }
  ): AsyncIterable<ShellEvent> {
    const cp = await import("child_process");
    const proc = cp.spawn(command, {
      shell: true,
      cwd: options?.cwd ?? this._root,
      env: { ...process.env, FORCE_COLOR: "0" },
    });

    const timeout = options?.timeout ?? 30_000;
    const timer = setTimeout(() => proc.kill("SIGTERM"), timeout);

    options?.signal?.addEventListener("abort", () => proc.kill("SIGTERM"), {
      once: true,
    });

    if (proc.stdout) {
      for await (const chunk of proc.stdout) {
        yield { type: "stdout", data: chunk.toString() };
      }
    }
    if (proc.stderr) {
      for await (const chunk of proc.stderr) {
        yield { type: "stderr", data: chunk.toString() };
      }
    }

    const code = await new Promise<number>((resolve) => {
      proc.on("close", (c) => resolve(c ?? 1));
    });

    clearTimeout(timer);
    yield { type: "exit", code };
  }

  async getDiagnostics(path?: string): Promise<DiagnosticEntry[]> {
    const allDiagnostics = path
      ? [
          [
            vscode.Uri.file(`${this._root}/${path}`),
            vscode.languages.getDiagnostics(
              vscode.Uri.file(`${this._root}/${path}`)
            ),
          ] as [vscode.Uri, vscode.Diagnostic[]],
        ]
      : vscode.languages.getDiagnostics();

    const entries: DiagnosticEntry[] = [];
    for (const [uri, diagnostics] of allDiagnostics) {
      for (const d of diagnostics) {
        const sevMap = ["error", "warning", "info", "hint"] as const;
        entries.push({
          file: vscode.workspace.asRelativePath(uri),
          line: d.range.start.line + 1,
          column: d.range.start.character + 1,
          severity: sevMap[d.severity] ?? "info",
          message: d.message,
          source: d.source,
        });
      }
    }
    return entries;
  }

  requestApproval(request: ApprovalRequest): Promise<ApprovalResponse> {
    return this._onApproval(request);
  }

  getWorkspaceRoot(): string {
    return this._root;
  }

  getOpenFiles(): string[] {
    const files: string[] = [];
    for (const group of vscode.window.tabGroups.all) {
      for (const tab of group.tabs) {
        if (tab.input instanceof vscode.TabInputText) {
          files.push(vscode.workspace.asRelativePath(tab.input.uri));
        }
      }
    }
    return files;
  }

  getActiveFile() {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      return null;
    }
    const sel = editor.selection;
    const text = sel.isEmpty ? undefined : editor.document.getText(sel);
    return {
      path: vscode.workspace.asRelativePath(editor.document.uri),
      language: editor.document.languageId,
      selection: text,
    };
  }
}
