/**
 * The v2 stream, projected onto the chat panel's live state — Batch V4-H4.
 *
 * The legacy ``/ws/sessions`` protocol fed StreamingMessage a list of
 * ``agent_message`` / ``tool_use`` / ``tool_result`` events and had the panel
 * consolidate them into one assistant message on ``status_change("waiting")``.
 * The v2 stream speaks a richer vocabulary (``text_delta``, ``tool_start``,
 * ``approval_needed``, ``done``, ...). This module is the one place that
 * vocabulary is translated, so StreamingMessage and the consolidation render
 * exactly what they rendered before; ``tests/streamState.test.js`` pins the
 * parity.
 *
 * Pure functions, no React and no DOM: the same events always give the same
 * state, and an unknown event leaves the state untouched.
 */

export function initialStreamState() {
  return {
    /** What StreamingMessage renders — the legacy event shapes. */
    events: [],
    /** The assistant's text so far. */
    answer: "",
    /** The last status_change message, for a progress line. */
    status: "",
    /** Plan steps announced by the run: {index, title, status}. */
    planSteps: [],
    /** {files, additions, deletions} once the run wrote something. */
    diff: null,
    /** approval_needed events still waiting for an answer. */
    approvals: [],
    done: false,
    error: null,
    usage: null,
    summary: "",
  };
}

function formatArgs(args) {
  if (args == null) return "";
  if (typeof args === "string") return args;
  if (typeof args !== "object") return String(args);
  // The legacy socket showed a shell line; a tool call reads best the same way,
  // one ``key=value`` per argument, long values clipped.
  return Object.entries(args)
    .map(([key, value]) => {
      const text = typeof value === "string" ? value : JSON.stringify(value);
      return `${key}=${text.length > 80 ? `${text.slice(0, 77)}...` : text}`;
    })
    .join(" ");
}

function clone(state) {
  return {
    ...state,
    events: state.events.slice(),
    planSteps: state.planSteps.slice(),
    approvals: state.approvals.slice(),
  };
}

/**
 * Fold one v2 event into the state. Returns the same object when the event
 * changes nothing, so callers can skip a render.
 */
export function applyStreamEvent(state, event) {
  if (!event || typeof event !== "object" || !event.type) return state;

  switch (event.type) {
    case "text_delta": {
      const text = String(event.text ?? "");
      if (!text) return state;
      const next = clone(state);
      next.answer = state.answer + text;
      const last = next.events[next.events.length - 1];
      if (last && last.type === "agent_message") {
        // Tokens arrive one at a time; one growing block, not one block per token.
        next.events[next.events.length - 1] = { ...last, content: last.content + text };
      } else {
        next.events.push({ type: "agent_message", content: text });
      }
      return next;
    }

    case "tool_start": {
      const next = clone(state);
      next.events.push({
        type: "tool_use",
        tool: event.name || event.tool || "tool",
        input: formatArgs(event.args ?? event.input),
        tool_id: event.tool_id,
      });
      return next;
    }

    case "tool_result": {
      const next = clone(state);
      next.events.push({
        type: "tool_result",
        tool: event.name || event.tool || "tool",
        output: String(event.result ?? event.output ?? ""),
        is_error: Boolean(event.is_error),
        tool_id: event.tool_id,
      });
      return next;
    }

    case "terminal_output": {
      const text = String(event.text ?? event.data ?? "");
      if (!text) return state;
      const next = clone(state);
      const last = next.events[next.events.length - 1];
      if (last && last.type === "tool_result" && last.tool === "terminal" && last.streaming) {
        next.events[next.events.length - 1] = { ...last, output: last.output + text };
      } else {
        next.events.push({ type: "tool_result", tool: "terminal", output: text, streaming: true });
      }
      return next;
    }

    case "terminal_exit": {
      const next = clone(state);
      const last = next.events[next.events.length - 1];
      if (last && last.type === "tool_result" && last.tool === "terminal" && last.streaming) {
        const newline = last.output.endsWith("\n") ? "" : "\n";
        next.events[next.events.length - 1] = {
          ...last,
          streaming: false,
          output: `${last.output}${newline}(exit ${event.exit_code ?? "?"})`,
        };
      }
      return next;
    }

    case "plan_step": {
      const next = clone(state);
      const index = Number(event.step_index ?? event.index ?? next.planSteps.length);
      const step = { index, title: event.title || `Step ${index + 1}`, status: event.status || "pending" };
      const at = next.planSteps.findIndex((s) => s.index === index);
      if (at >= 0) next.planSteps[at] = step;
      else next.planSteps.push(step);
      return next;
    }

    case "file_write": {
      const next = clone(state);
      const diff = state.diff || { files: 0, additions: 0, deletions: 0 };
      next.diff = {
        files: diff.files + 1,
        additions: diff.additions + Number(event.additions || 0),
        deletions: diff.deletions + Number(event.deletions || 0),
      };
      return next;
    }

    case "diff_update": {
      const next = clone(state);
      next.diff = event.stats || {
        files: event.files,
        additions: event.additions,
        deletions: event.deletions,
      };
      return next;
    }

    case "status_change": {
      const next = clone(state);
      next.status = String(event.message || event.status || "");
      return next;
    }

    case "approval_needed": {
      if (!event.request_id) return state;
      const next = clone(state);
      next.approvals.push({
        request_id: event.request_id,
        tool: event.tool || "tool",
        summary: event.summary || "",
        risk: event.risk || "",
        diff_preview: event.diff_preview || null,
        args: event.args || {},
      });
      return next;
    }

    case "approval_resolved": {
      const next = clone(state);
      next.approvals = next.approvals.filter((a) => a.request_id !== event.request_id);
      return next;
    }

    case "done": {
      const next = clone(state);
      next.done = true;
      next.usage = event.usage || null;
      next.summary = String(event.summary || "");
      next.status = "";
      if (!next.answer && event.answer) next.answer = String(event.answer);
      return next;
    }

    case "error": {
      const next = clone(state);
      next.error = String(event.error || event.message || "error");
      next.done = true;
      next.status = "";
      return next;
    }

    default:
      return state;
  }
}

/**
 * The assistant message a finished run leaves in the transcript, in the shape
 * the legacy consolidation produced — or null when there is nothing to say.
 */
export function consolidateMessage(state) {
  const text = state.answer || state.summary || "";
  if (!text.trim()) return null;
  return { from: "ai", role: "assistant", answer: text, content: text };
}

/** Fold a whole event list; convenient for tests and replays. */
export function reduceStream(events, state = initialStreamState()) {
  return events.reduce(applyStreamEvent, state);
}
