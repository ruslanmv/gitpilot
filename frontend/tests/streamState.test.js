// frontend/tests/streamState.test.js — Batch V4-H4
//
// The chat panel used to render whatever the legacy /ws/sessions socket pushed
// and consolidate it on status_change("waiting"). These tests pin that the v2
// stream, folded through streamState.js, renders and consolidates the same.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  applyStreamEvent,
  consolidateMessage,
  initialStreamState,
  reduceStream,
} from "../utils/streamState.js";

// What ChatPanel did with the legacy protocol, verbatim: text events joined,
// one assistant message, tool events rendered as they came.
function legacyConsolidate(events) {
  const textParts = events.filter((e) => e.type === "agent_message").map((e) => e.content);
  if (textParts.length === 0) return null;
  return { from: "ai", role: "assistant", answer: textParts.join(""), content: textParts.join("") };
}

function renderedText(events) {
  return events.filter((e) => e.type === "agent_message").map((e) => e.content).join("");
}

function renderedTools(events) {
  return events
    .filter((e) => e.type === "tool_use" || e.type === "tool_result")
    .map((e) => (e.type === "tool_use" ? `$ ${e.input}` : e.output));
}

test("a v2 run renders and consolidates like the legacy protocol did", () => {
  const legacy = [
    { type: "agent_message", content: "Reading " },
    { type: "tool_use", tool: "fs.read", input: "path=README.md" },
    { type: "tool_result", tool: "fs.read", output: "# Demo" },
    { type: "agent_message", content: "the README." },
    { type: "status_change", status: "waiting" },
  ];
  const v2 = [
    { type: "text_delta", text: "Read" },
    { type: "text_delta", text: "ing " },
    { type: "tool_start", tool_id: "t1", name: "fs.read", args: { path: "README.md" } },
    { type: "tool_result", tool_id: "t1", name: "fs.read", result: "# Demo" },
    { type: "text_delta", text: "the README." },
    { type: "done", usage: { prompt_tokens: 10 }, summary: "" },
  ];

  const state = reduceStream(v2);

  assert.deepEqual(consolidateMessage(state), legacyConsolidate(legacy));
  assert.equal(renderedText(state.events), renderedText(legacy));
  assert.deepEqual(renderedTools(state.events), renderedTools(legacy));
  assert.equal(state.done, true);
});

test("text deltas grow one block instead of one block per token", () => {
  const state = reduceStream([
    { type: "text_delta", text: "a" },
    { type: "text_delta", text: "b" },
    { type: "text_delta", text: "c" },
  ]);

  assert.deepEqual(state.events, [{ type: "agent_message", content: "abc" }]);
  assert.equal(state.answer, "abc");
});

test("tool events keep the shapes StreamingMessage renders", () => {
  const state = reduceStream([
    { type: "tool_start", tool_id: "t1", name: "terminal.run", args: { command: "pytest -q", timeout: 30 } },
    { type: "tool_result", tool_id: "t1", name: "terminal.run", result: "3 passed", is_error: false },
  ]);

  assert.equal(state.events[0].type, "tool_use");
  assert.equal(state.events[0].tool, "terminal.run");
  assert.equal(state.events[0].input, "command=pytest -q timeout=30");
  assert.equal(state.events[1].type, "tool_result");
  assert.equal(state.events[1].output, "3 passed");
});

test("terminal output streams into one block and closes with the exit code", () => {
  const state = reduceStream([
    { type: "terminal_output", stream: "stdout", text: "collecting...\n" },
    { type: "terminal_output", stream: "stdout", text: "3 passed\n" },
    { type: "terminal_exit", command: "pytest", exit_code: 0, duration_ms: 12 },
  ]);

  assert.equal(state.events.length, 1);
  assert.equal(state.events[0].output, "collecting...\n3 passed\n(exit 0)");
  assert.equal(state.events[0].streaming, false);
});

test("an approval waits until it is resolved", () => {
  let state = applyStreamEvent(initialStreamState(), {
    type: "approval_needed",
    request_id: "approval-1",
    tool: "fs.write",
    args: { path: "src/app.py" },
    summary: "Write src/app.py",
    risk: "medium",
    diff_preview: "+hello",
  });
  assert.equal(state.approvals.length, 1);
  assert.equal(state.approvals[0].tool, "fs.write");
  assert.equal(state.approvals[0].diff_preview, "+hello");

  state = applyStreamEvent(state, { type: "approval_resolved", request_id: "approval-1", approved: true });
  assert.deepEqual(state.approvals, []);
});

test("done without text falls back to the summary; error keeps the partial text", () => {
  const planned = reduceStream([{ type: "plan_step", step_index: 0, title: "Look", status: "pending" }, { type: "done", summary: "Plan generated." }]);
  assert.deepEqual(consolidateMessage(planned), {
    from: "ai", role: "assistant", answer: "Plan generated.", content: "Plan generated.",
  });
  assert.deepEqual(planned.planSteps, [{ index: 0, title: "Look", status: "pending" }]);

  const failed = reduceStream([{ type: "text_delta", text: "Half an ans" }, { type: "error", error: "model timed out" }]);
  assert.equal(failed.error, "model timed out");
  assert.equal(failed.done, true);
  assert.equal(consolidateMessage(failed).content, "Half an ans");

  assert.equal(consolidateMessage(reduceStream([{ type: "done", summary: "" }])), null);
});

test("file writes accumulate into diff stats the DiffStats badge understands", () => {
  const state = reduceStream([
    { type: "file_write", path: "a.py", additions: 3, deletions: 1 },
    { type: "file_write", path: "b.py", additions: 2, deletions: 0 },
  ]);
  assert.deepEqual(state.diff, { files: 2, additions: 5, deletions: 1 });
});

test("unknown and malformed events leave the state object untouched", () => {
  const state = initialStreamState();
  assert.equal(applyStreamEvent(state, { type: "compaction", dropped: 3 }), state);
  assert.equal(applyStreamEvent(state, { type: "text_delta", text: "" }), state);
  assert.equal(applyStreamEvent(state, null), state);
  assert.equal(applyStreamEvent(state, "nonsense"), state);
});
