/**
 * Agent stream controller — bridges AgentEventBus events to webview messages.
 *
 * This controller subscribes to the AgentEventBus and translates each
 * extended event into webview-compatible messages. It also handles
 * approval request/response flow between the webview and the agent.
 *
 * Works identically whether events come from:
 *   - Local agent loop (in-process)
 *   - Backend SSE stream (/api/v2/chat/stream)
 *
 * The webview never knows the difference — same message types either way.
 */
import { StateStore } from "../core/stateStore";
import {
  AgentEventBus,
  ExtendedAgentEvent,
} from "../agent/agentEventBus";
import type { ApprovalResponse } from "../platform/interface";

/** Shape of the postMessage callback (decoupled from VS Code). */
type PostMessage = (msg: { type: string; payload?: unknown }) => void;

export class AgentStreamController {
  private _unsubscribe: (() => void) | null = null;
  private _pendingApprovals = new Map<
    string,
    { resolve: (response: ApprovalResponse) => void }
  >();

  constructor(
    private readonly _bus: AgentEventBus,
    private readonly _stateStore: StateStore,
    private readonly _postMessage: PostMessage
  ) {
    this._unsubscribe = this._bus.on((event) => this._handleEvent(event));
  }

  /**
   * Called when the webview sends TOOL_APPROVAL_RESPONSE.
   */
  resolveApproval(
    id: string,
    approved: boolean,
    scope?: string
  ): void {
    const pending = this._pendingApprovals.get(id);
    if (!pending) {
      return;
    }
    this._pendingApprovals.delete(id);
    pending.resolve(
      approved
        ? { approved: true, scope: (scope as "once" | "session" | "always") ?? "once" }
        : { approved: false }
    );
  }

  /**
   * Creates an approval callback for the PlatformHost.
   */
  createApprovalHandler(): (
    req: {
      id: string;
      tool: string;
      args: Record<string, unknown>;
      summary: string;
      diffPreview?: string;
      riskLevel: "low" | "medium" | "high";
    }
  ) => Promise<ApprovalResponse> {
    return (req) => {
      return new Promise<ApprovalResponse>((resolve) => {
        this._pendingApprovals.set(req.id, { resolve });
        this._postMessage({
          type: "TOOL_APPROVAL_REQUEST",
          payload: {
            id: req.id,
            tool: req.tool,
            args: req.args,
            summary: req.summary,
            diffPreview: req.diffPreview,
            riskLevel: req.riskLevel,
          },
        });
      });
    };
  }

  dispose(): void {
    this._unsubscribe?.();
    this._unsubscribe = null;
    // Deny all pending approvals
    for (const [, pending] of this._pendingApprovals) {
      pending.resolve({ approved: false });
    }
    this._pendingApprovals.clear();
  }

  private _handleEvent(event: ExtendedAgentEvent): void {
    switch (event.type) {
      // ── Streaming text (reuses existing webview handler) ──
      case "agent_text":
        this._postMessage({
          type: "CHAT_STREAM_CHUNK",
          payload: { content: event.text },
        });
        break;

      // ── Tool lifecycle ──
      case "agent_tool_start":
        this._postMessage({
          type: "AGENT_TOOL_ACTIVITY",
          payload: {
            id: event.id,
            name: event.name,
            status: "running",
            args: event.arguments,
          },
        });
        break;

      case "agent_tool_result":
        this._postMessage({
          type: "AGENT_TOOL_ACTIVITY",
          payload: {
            id: event.id,
            name: event.name,
            status: event.is_error ? "failed" : "completed",
            result: typeof event.result === "string"
              ? event.result.slice(0, 500)
              : undefined,
            is_error: event.is_error,
          },
        });
        break;

      // ── Plan step progress ──
      case "plan_step_started":
      case "plan_step_completed":
      case "plan_step_failed": {
        const status = event.type.replace("plan_step_", "");
        this._postMessage({
          type: "PLAN_STEP_UPDATE",
          payload: {
            stepIndex: event.stepIndex,
            stepTitle: event.stepTitle,
            action: event.action,
            status,
          },
        });
        this._updatePlanStepInState(
          event.stepIndex,
          status === "completed" ? "applied" : status === "failed" ? "failed" : "pending"
        );
        break;
      }

      // ── Terminal output ──
      case "terminal_output":
        this._postMessage({
          type: "TERMINAL_OUTPUT",
          payload: { stream: event.stream, text: event.text },
        });
        break;

      case "terminal_exit":
        this._postMessage({
          type: "TERMINAL_OUTPUT",
          payload: {
            stream: "exit",
            text: `\n[Process exited with code ${event.exitCode}]`,
            exitCode: event.exitCode,
          },
        });
        break;

      // ── Diagnostics ──
      case "diagnostics_result":
        this._postMessage({
          type: "DIAGNOSTICS_RESULT",
          payload: {
            file: event.file,
            errors: event.errors,
            warnings: event.warnings,
            entries: event.entries,
          },
        });
        break;

      // ── Tests ──
      case "test_result":
        this._postMessage({
          type: "TEST_RESULT",
          payload: {
            framework: event.framework,
            passed: event.passed,
            failed: event.failed,
            skipped: event.skipped,
            exitCode: event.exitCode,
          },
        });
        break;

      // ── Approval (creates pending promise) ──
      case "approval_requested":
        // Handled via createApprovalHandler
        break;

      // ── Done ──
      case "agent_done":
        this._postMessage({
          type: "CHAT_STREAM_END",
          payload: { usage: event.usage },
        });
        break;

      // ── Error ──
      case "agent_error":
        this._postMessage({
          type: "ERROR",
          payload: {
            code: "AGENT_ERROR",
            title: "Agent Error",
            message: event.error,
            recoverable: true,
          },
        });
        break;
    }
  }

  private _updatePlanStepInState(index: number, status: string): void {
    const plan = this._stateStore.state.activeTask.plan;
    if (!plan?.steps?.[index]) {
      return;
    }
    const steps = [...plan.steps];
    steps[index] = { ...steps[index], status: status as "pending" | "ready" | "applied" | "failed" };
    this._stateStore.setTaskPlan({ ...plan, steps });
  }
}
