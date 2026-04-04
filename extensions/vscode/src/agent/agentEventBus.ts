/**
 * Agent event bus — typed event emitter for agent → UI communication.
 *
 * The agent executor emits events; the stream controller subscribes
 * and forwards them to the webview. Platform-agnostic (no vscode import).
 *
 * Extended event types beyond what the existing agentLoop yields.
 * These are ADDITIVE — the existing AgentEvent union from
 * providers/interface.ts is preserved and re-exported.
 */
import type { AgentEvent } from "../local/providers/interface";

// Re-export the base events for convenience
export type { AgentEvent };

// ── Extended events for the new features ──

export interface ApprovalRequestedEvent {
  type: "approval_requested";
  id: string;
  tool: string;
  args: Record<string, unknown>;
  summary: string;
  diffPreview?: string;
  riskLevel: "low" | "medium" | "high";
}

export interface ApprovalResolvedEvent {
  type: "approval_resolved";
  id: string;
  approved: boolean;
  scope?: "once" | "session" | "always";
}

export interface PlanStepEvent {
  type: "plan_step_started" | "plan_step_completed" | "plan_step_failed";
  stepIndex: number;
  stepTitle: string;
  action: string;
}

export interface TerminalOutputEvent {
  type: "terminal_output";
  stream: "stdout" | "stderr";
  text: string;
}

export interface TerminalExitEvent {
  type: "terminal_exit";
  command: string;
  exitCode: number;
  durationMs?: number;
}

export interface DiagnosticsEvent {
  type: "diagnostics_result";
  file?: string;
  errors: number;
  warnings: number;
  entries: Array<{
    file: string;
    line: number;
    severity: string;
    message: string;
  }>;
}

export interface TestResultEvent {
  type: "test_result";
  framework: string;
  passed: number;
  failed: number;
  skipped: number;
  exitCode: number;
  output?: string;
}

export type ExtendedAgentEvent =
  | AgentEvent
  | ApprovalRequestedEvent
  | ApprovalResolvedEvent
  | PlanStepEvent
  | TerminalOutputEvent
  | TerminalExitEvent
  | DiagnosticsEvent
  | TestResultEvent;

type Listener = (event: ExtendedAgentEvent) => void;

/**
 * Simple typed event emitter for agent events.
 * The stream controller subscribes; the executor emits.
 */
export class AgentEventBus {
  private _listeners: Listener[] = [];

  on(listener: Listener): () => void {
    this._listeners.push(listener);
    return () => {
      this._listeners = this._listeners.filter((l) => l !== listener);
    };
  }

  emit(event: ExtendedAgentEvent): void {
    for (const listener of this._listeners) {
      try {
        listener(event);
      } catch {
        // swallow listener errors
      }
    }
  }

  removeAll(): void {
    this._listeners = [];
  }
}
