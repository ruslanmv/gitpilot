// extensions/vscode/src/agent/eventRouter.ts
//
// Turning agent events into webview messages — Batch V4-E5.
//
// Extracted from the SSE reading loop in `extension.ts`, where it was ~120 lines
// of `else if` inside a `for await` inside a `try` inside a command handler.
// Nothing about the mapping needs a live server, a webview or a VS Code host, so
// none of those belong in the way of testing it: this is a pure function from one
// event to zero or more messages, and the loop's job is now to call it.
//
// The engine's events are the reason this was worth doing. A tool row needs a
// duration, an approval card needs the command class, a subagent's activity has
// to nest under the call that spawned it, and a checklist has to update in place.
// Growing four more branches in that loop would have made the render paths
// untestable at exactly the point they became worth testing.

import type { ExtensionToWebviewMessage } from "../core/types";

/**
 * Event types this module handles.
 *
 * Exported so the reading loop can ask rather than restate: a list repeated at
 * the call site would drift the first time a case is added here.
 */
export const ROUTED_TYPES: ReadonlySet<string> = new Set([
  "text_delta", "tool_start", "tool_result", "approval_needed", "todo_updated",
  "agent_delegated", "agent_delegate_done", "verification_required",
  "terminal_output", "terminal_exit", "test_result", "diagnostics", "plan_step",
]);

/** One event off the wire. Keys are whatever the server sent. */
export type AgentEvent = Record<string, unknown>;

/** Timings, kept between events so a result row can report a duration. */
export interface RouterState {
  /** When each tool call started, by call id. */
  startedAt: Map<string, number>;
  /** Monotonic-ish clock, injectable so a test is not timing-dependent. */
  now: () => number;
}

export function createRouterState(now: () => number = () => Date.now()): RouterState {
  return { startedAt: new Map(), now };
}

const str = (value: unknown, fallback = ""): string =>
  value === undefined || value === null ? fallback : String(value);

const num = (value: unknown, fallback = 0): number => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

const args = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" ? (value as Record<string, unknown>) : {};

/** The tool call id, however the event spelled it. */
const callId = (event: AgentEvent): string =>
  str(event.tool_id || event.id || event.call_id);

/**
 * Map one event to the messages the webview should receive.
 *
 * Returns an array because some events produce more than one (a subagent's
 * `tool_start` is both a tool row and a delegation marker) and some produce none
 * (a keepalive). Returning `[]` rather than `null` keeps every caller's loop the
 * same shape.
 */
export function routeAgentEvent(
  event: AgentEvent,
  state: RouterState,
): ExtensionToWebviewMessage[] {
  const type = str(event.type);
  const out: ExtensionToWebviewMessage[] = [];

  switch (type) {
    case "text_delta": {
      const text = str(event.text);
      if (!text) return [];
      return [{ type: "CHAT_STREAM_CHUNK", payload: { content: text } }];
    }

    case "tool_start": {
      const id = callId(event);
      state.startedAt.set(id, state.now());
      out.push({
        type: "AGENT_TOOL_ACTIVITY",
        payload: {
          id,
          name: str(event.name),
          status: "running",
          args: args(event.arguments || event.args),
          parentCallId: str(event.parent_call_id) || undefined,
          subagent: str(event.subagent) || undefined,
        },
      });
      return out;
    }

    case "tool_result": {
      const id = callId(event);
      const started = state.startedAt.get(id);
      state.startedAt.delete(id);
      out.push({
        type: "AGENT_TOOL_ACTIVITY",
        payload: {
          id,
          name: str(event.name),
          status: event.is_error ? "failed" : "completed",
          result: str(event.result).slice(0, 500),
          is_error: Boolean(event.is_error),
          // Absent rather than zero when the start was never seen: a row
          // claiming 0 ms is worse than one that says nothing.
          durationMs: started === undefined ? undefined : state.now() - started,
          parentCallId: str(event.parent_call_id) || undefined,
          subagent: str(event.subagent) || undefined,
        },
      });
      return out;
    }

    case "approval_needed": {
      const command = str(args(event.arguments).command);
      return [{
        type: "TOOL_APPROVAL_REQUEST",
        payload: {
          id: str(event.request_id),
          tool: str(event.tool),
          args: args(event.arguments),
          // The engine sends a `reason` that already names the command class;
          // prefer it over a generic summary, because that sentence is the whole
          // point of classifying the command.
          summary: str(event.reason) || str(event.summary) || command,
          diffPreview: event.diff_preview ? str(event.diff_preview) : undefined,
          riskLevel: riskOf(event),
          commandClass: str(event.command_class) || undefined,
          sandbox: sandboxOf(event),
        },
      }];
    }

    case "todo_updated": {
      const items = Array.isArray(event.items) ? event.items : [];
      return [{
        type: "AGENT_TODO_UPDATE",
        payload: {
          items: items.map((raw) => {
            const item = args(raw);
            return {
              id: str(item.id),
              text: str(item.text),
              status: str(item.status, "pending"),
            };
          }),
          source: str(event.source) || undefined,
        },
      }];
    }

    case "agent_delegated":
      return [{
        type: "AGENT_DELEGATION",
        payload: {
          parentCallId: str(event.parent_call_id),
          agent: str(event.agent),
          title: str(event.title) || undefined,
          status: "running",
        },
      }];

    case "agent_delegate_done":
      return [{
        type: "AGENT_DELEGATION",
        payload: {
          parentCallId: str(event.parent_call_id),
          agent: str(event.agent),
          status: delegationStatus(str(event.status)),
        },
      }];

    case "verification_required":
      return [{
        type: "AGENT_VERIFICATION",
        payload: {
          cycle: num(event.cycle, 1),
          maxCycles: num(event.max_cycles, 1),
          command: str(event.command),
        },
      }];

    case "terminal_output":
      return [{
        type: "TERMINAL_OUTPUT",
        payload: {
          stream: (str(event.stream, "stdout") as "stdout" | "stderr" | "exit"),
          text: str(event.text),
        },
      }];

    case "terminal_exit":
      return [{
        type: "TERMINAL_OUTPUT",
        payload: {
          stream: "exit",
          text: `\n[Process exited with code ${num(event.exit_code, -1)}]`,
          exitCode: num(event.exit_code, -1),
        },
      }];

    case "test_result":
      return [{
        type: "TEST_RESULT",
        payload: {
          framework: str(event.framework, "unknown"),
          passed: num(event.passed),
          failed: num(event.failed),
          skipped: num(event.skipped),
          exitCode: num(event.exit_code),
        },
      }];

    case "diagnostics":
      return [{
        type: "DIAGNOSTICS_RESULT",
        payload: {
          errors: num(event.errors),
          warnings: num(event.warnings),
          entries: Array.isArray(event.entries)
            ? (event.entries as Array<{ file: string; line: number; severity: string; message: string }>)
            : [],
        },
      }];

    case "plan_step":
      return [{
        type: "PLAN_STEP_UPDATE",
        payload: {
          stepIndex: num(event.step_index),
          stepTitle: str(event.title),
          action: str(event.action),
          status: str(event.status, "pending"),
        },
      }];

    default:
      return [];
  }
}

/**
 * Approval risk, from the engine's own label.
 *
 * The engine speaks `safe` / `approval` / `high_risk`; the panel speaks
 * low/medium/high. A DESTRUCTIVE or PRIVILEGED command never reaches here — the
 * policy engine refuses those without prompting — so there is no fourth level to
 * render.
 */
function riskOf(event: AgentEvent): "low" | "medium" | "high" {
  const label = str(event.risk || event.risk_level, "medium").toLowerCase();
  if (label === "high_risk" || label === "high") return "high";
  if (label === "safe" || label === "low") return "low";
  return "medium";
}

/** Sandbox facts for the card, when the event carries them. */
function sandboxOf(event: AgentEvent): {
  backend?: string; network?: boolean; timeoutSec?: number; workspace?: string;
} | undefined {
  const sandbox = args(event.sandbox);
  if (!Object.keys(sandbox).length) return undefined;
  return {
    backend: str(sandbox.backend) || undefined,
    network: typeof sandbox.network === "boolean" ? sandbox.network : undefined,
    timeoutSec: sandbox.timeout_sec === undefined ? undefined : num(sandbox.timeout_sec),
    workspace: str(sandbox.workspace) || undefined,
  };
}

function delegationStatus(
  status: string,
): "running" | "completed" | "partial" | "failed" {
  if (status === "partial" || status === "failed" || status === "completed") {
    return status;
  }
  return "completed";
}
