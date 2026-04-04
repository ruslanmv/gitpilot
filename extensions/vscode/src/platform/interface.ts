/**
 * Platform abstraction interface.
 *
 * The agent layer depends ONLY on this interface. VS Code and web
 * each provide their own implementation. This enables the same agent
 * logic to run on VS Code (in-process), web (via API), or HF Spaces.
 */

export interface ShellEvent {
  type: "stdout" | "stderr" | "exit";
  data?: string;
  code?: number;
}

export interface DiagnosticEntry {
  file: string;
  line: number;
  column: number;
  severity: "error" | "warning" | "info" | "hint";
  message: string;
  source?: string;
}

export interface ApprovalRequest {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  summary: string;
  diffPreview?: string;
  riskLevel: "low" | "medium" | "high";
}

export type ApprovalResponse =
  | { approved: true; scope: "once" | "session" | "always" }
  | { approved: false; reason?: string };

export interface PlatformHost {
  // ── Filesystem ──
  readFile(path: string): Promise<string>;
  writeFile(path: string, content: string): Promise<void>;
  fileExists(path: string): Promise<boolean>;
  listDirectory(path: string, recursive?: boolean): Promise<string[]>;

  // ── Shell execution ──
  runCommand(
    command: string,
    options?: { cwd?: string; timeout?: number; signal?: AbortSignal }
  ): AsyncIterable<ShellEvent>;

  // ── Diagnostics ──
  getDiagnostics(path?: string): Promise<DiagnosticEntry[]>;

  // ── User interaction ──
  requestApproval(request: ApprovalRequest): Promise<ApprovalResponse>;

  // ── Context ──
  getWorkspaceRoot(): string;
  getOpenFiles(): string[];
  getActiveFile(): {
    path: string;
    language: string;
    selection?: string;
  } | null;
}
