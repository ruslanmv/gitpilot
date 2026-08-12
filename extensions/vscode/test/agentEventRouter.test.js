/**
 * Every render path in the agent console, over a recorded event stream.
 *
 * Batch V4-E5. This mapping used to live inside the SSE reading loop —
 * ~120 lines of `else if` nested in a `for await` inside a `try` inside a command
 * handler — which meant testing "does a tool row show a duration?" required a
 * running server and a live webview. It required neither: the mapping is a pure
 * function, and it is now written as one.
 *
 * The stream below is what the engine actually emits for a small task: a couple
 * of tool calls, a TODO list, a delegation to an explorer, an approval that names
 * its command class, a verification nudge, and a finish.
 *
 * Run with:  npm test        (from extensions/vscode)
 */
const assert = require("assert");
const path = require("path");

const {
  ROUTED_TYPES,
  createRouterState,
  routeAgentEvent,
} = require(path.resolve(__dirname, "../out/agent/eventRouter.js"));

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

/** A clock that advances 100 ms per read, so durations are deterministic. */
function fakeClock(start = 1000, step = 100) {
  let now = start;
  return () => {
    const value = now;
    now += step;
    return value;
  };
}

/** Route a whole stream and collect every message, in order. */
function drive(events, now = fakeClock()) {
  const state = createRouterState(now);
  const messages = [];
  for (const event of events) {
    messages.push(...routeAgentEvent(event, state));
  }
  return messages;
}

const of = (messages, type) => messages.filter((m) => m.type === type);
const first = (messages, type) => of(messages, type)[0];

// ── The recorded stream ──────────────────────────────────────────────────

const STREAM = [
  { type: "status_change", status: "run_started", message: "running on gpt-4o" },
  {
    type: "todo_updated",
    source: "model",
    items: [
      { id: "t1", text: "Read the config", status: "in_progress" },
      { id: "t2", text: "Fix the parser", status: "pending" },
    ],
  },
  { type: "tool_start", id: "c1", name: "fs.read", arguments: { path: "app.py" } },
  { type: "tool_result", id: "c1", name: "fs.read", result: "x = 1", ok: true },
  { type: "agent_delegated", parent_call_id: "d1", agent: "explorer", title: "Repository Explorer" },
  {
    type: "tool_start", id: "s1", name: "fs.glob",
    arguments: { pattern: "**/*.py" },
    parent_call_id: "d1", subagent: "explorer",
  },
  {
    type: "tool_result", id: "s1", name: "fs.glob", result: "3 files",
    parent_call_id: "d1", subagent: "explorer",
  },
  { type: "agent_delegate_done", parent_call_id: "d1", agent: "explorer", status: "completed" },
  {
    type: "approval_needed",
    request_id: "a1",
    tool: "terminal.run",
    arguments: { command: "pip install requests" },
    reason: "this command changes files or installed packages (MUTATING)",
    risk: "approval",
    command_class: "MUTATING",
    sandbox: { backend: "subprocess", network: false, timeout_sec: 120 },
  },
  { type: "verification_required", cycle: 1, max_cycles: 2, command: "pytest -q" },
  { type: "test_result", framework: "pytest", passed: 12, failed: 0, skipped: 1, exit_code: 0 },
  { type: "text_delta", text: "Done — the parser now handles empty input." },
  { type: "status_change", status: "keepalive" },
  { type: "done", usage: { prompt_tokens: 900, completion_tokens: 120 } },
];

// ── Tool activity ────────────────────────────────────────────────────────

test("a tool call becomes a running row and then a completed one", () => {
  const messages = drive(STREAM);
  const rows = of(messages, "AGENT_TOOL_ACTIVITY").filter((m) => m.payload.id === "c1");

  assert.strictEqual(rows.length, 2);
  assert.strictEqual(rows[0].payload.status, "running");
  assert.strictEqual(rows[0].payload.name, "fs.read");
  assert.deepStrictEqual(rows[0].payload.args, { path: "app.py" });
  assert.strictEqual(rows[1].payload.status, "completed");
});

test("a completed row carries how long the call took", () => {
  // A list of tool names is not a trace; the timings are what make it one.
  const messages = drive(STREAM, fakeClock(0, 250));
  const done = of(messages, "AGENT_TOOL_ACTIVITY").find(
    (m) => m.payload.id === "c1" && m.payload.status === "completed"
  );

  assert.ok(done.payload.durationMs > 0, "no duration was reported");
});

test("a result with no matching start reports no duration rather than zero", () => {
  // A row claiming 0 ms is worse than one that says nothing.
  const messages = drive([
    { type: "tool_result", id: "orphan", name: "fs.read", result: "x" },
  ]);

  assert.strictEqual(first(messages, "AGENT_TOOL_ACTIVITY").payload.durationMs, undefined);
});

test("a failed call is rendered as failed", () => {
  const messages = drive([
    { type: "tool_start", id: "c9", name: "fs.write" },
    { type: "tool_result", id: "c9", name: "fs.write", is_error: true, result: "denied" },
  ]);
  const rows = of(messages, "AGENT_TOOL_ACTIVITY");

  assert.strictEqual(rows[1].payload.status, "failed");
  assert.strictEqual(rows[1].payload.is_error, true);
});

test("a long result is truncated before it reaches the panel", () => {
  const messages = drive([
    { type: "tool_result", id: "c1", name: "fs.read", result: "y".repeat(5000) },
  ]);

  assert.strictEqual(first(messages, "AGENT_TOOL_ACTIVITY").payload.result.length, 500);
});

// ── Delegation nesting ───────────────────────────────────────────────────

test("a subagent's activity is tagged with the call that spawned it", () => {
  // Without this the child's reads look like the parent's own work, and the
  // parent appears to stall for however long the child takes.
  const messages = drive(STREAM);
  const nested = of(messages, "AGENT_TOOL_ACTIVITY").filter(
    (m) => m.payload.parentCallId === "d1"
  );

  assert.strictEqual(nested.length, 2);
  assert.strictEqual(nested[0].payload.subagent, "explorer");
});

test("a delegation opens and closes as a block", () => {
  const messages = drive(STREAM);
  const blocks = of(messages, "AGENT_DELEGATION");

  assert.strictEqual(blocks.length, 2);
  assert.strictEqual(blocks[0].payload.status, "running");
  assert.strictEqual(blocks[0].payload.title, "Repository Explorer");
  assert.strictEqual(blocks[1].payload.status, "completed");
});

test("a partial subagent is not shown as completed", () => {
  const messages = drive([
    { type: "agent_delegate_done", parent_call_id: "d1", agent: "reviewer", status: "partial" },
  ]);

  assert.strictEqual(first(messages, "AGENT_DELEGATION").payload.status, "partial");
});

test("an unrecognised subagent status settles on completed", () => {
  const messages = drive([
    { type: "agent_delegate_done", parent_call_id: "d1", agent: "x", status: "??" },
  ]);

  assert.strictEqual(first(messages, "AGENT_DELEGATION").payload.status, "completed");
});

// ── The TODO checklist ───────────────────────────────────────────────────

test("the checklist arrives with its statuses", () => {
  const messages = drive(STREAM);
  const todo = first(messages, "AGENT_TODO_UPDATE");

  assert.deepStrictEqual(todo.payload.items, [
    { id: "t1", text: "Read the config", status: "in_progress" },
    { id: "t2", text: "Fix the parser", status: "pending" },
  ]);
  assert.strictEqual(todo.payload.source, "model");
});

test("an engine-maintained checklist is indistinguishable except for its source", () => {
  // A small model has no todo.write; the engine keeps the list so the panel looks
  // the same either way.
  const messages = drive([
    {
      type: "todo_updated", source: "engine",
      items: [{ id: "t1", text: "Read the relevant files", status: "in_progress" }],
    },
  ]);
  const todo = first(messages, "AGENT_TODO_UPDATE");

  assert.strictEqual(todo.payload.source, "engine");
  assert.strictEqual(todo.payload.items[0].status, "in_progress");
});

test("a malformed checklist item does not break the update", () => {
  const messages = drive([
    { type: "todo_updated", items: [{ text: "no status" }, "not an object", null] },
  ]);
  const items = first(messages, "AGENT_TODO_UPDATE").payload.items;

  assert.strictEqual(items.length, 3);
  assert.strictEqual(items[0].status, "pending");
});

// ── Approval cards ───────────────────────────────────────────────────────

test("an approval card says what kind of thing it is approving", () => {
  // "Run a shell command?" left the user to read the string themselves.
  const messages = drive(STREAM);
  const card = first(messages, "TOOL_APPROVAL_REQUEST");

  assert.strictEqual(card.payload.commandClass, "MUTATING");
  assert.ok(card.payload.summary.includes("MUTATING"));
  assert.ok(card.payload.summary.includes("installed packages"));
});

test("an approval card carries the sandbox facts", () => {
  const card = first(drive(STREAM), "TOOL_APPROVAL_REQUEST");

  assert.deepStrictEqual(card.payload.sandbox, {
    backend: "subprocess",
    network: false,
    timeoutSec: 120,
    workspace: undefined,
  });
});

test("the engine's risk vocabulary maps onto the panel's", () => {
  const risk = (value) =>
    first(drive([{ type: "approval_needed", request_id: "a", tool: "t", risk: value }]),
      "TOOL_APPROVAL_REQUEST").payload.riskLevel;

  assert.strictEqual(risk("high_risk"), "high");
  assert.strictEqual(risk("approval"), "medium");
  assert.strictEqual(risk("safe"), "low");
  assert.strictEqual(risk("nonsense"), "medium");
});

test("an approval with no sandbox facts omits the section", () => {
  const card = first(
    drive([{ type: "approval_needed", request_id: "a", tool: "fs.write" }]),
    "TOOL_APPROVAL_REQUEST"
  );

  assert.strictEqual(card.payload.sandbox, undefined);
});

// ── Verification ─────────────────────────────────────────────────────────

test("a verification nudge says which attempt this is", () => {
  const nudge = first(drive(STREAM), "AGENT_VERIFICATION");

  assert.strictEqual(nudge.payload.cycle, 1);
  assert.strictEqual(nudge.payload.maxCycles, 2);
  assert.strictEqual(nudge.payload.command, "pytest -q");
});

// ── Text, tests, and the things that route nowhere ───────────────────────

test("text deltas become chat chunks", () => {
  const chunks = of(drive(STREAM), "CHAT_STREAM_CHUNK");

  assert.strictEqual(chunks.length, 1);
  assert.ok(chunks[0].payload.content.startsWith("Done —"));
});

test("an empty text delta produces nothing", () => {
  assert.deepStrictEqual(drive([{ type: "text_delta", text: "" }]), []);
});

test("a test result carries its numbers", () => {
  const result = first(drive(STREAM), "TEST_RESULT");

  assert.strictEqual(result.payload.framework, "pytest");
  assert.strictEqual(result.payload.passed, 12);
  assert.strictEqual(result.payload.skipped, 1);
});

test("a terminal exit reads as a line, not a raw code", () => {
  const messages = drive([{ type: "terminal_exit", exit_code: 2 }]);
  const payload = first(messages, "TERMINAL_OUTPUT").payload;

  assert.strictEqual(payload.stream, "exit");
  assert.strictEqual(payload.exitCode, 2);
  assert.ok(payload.text.includes("code 2"));
});

test("a keepalive routes nowhere", () => {
  // It is a transport heartbeat; rendering it would show the user nothing
  // happening as though something had.
  assert.deepStrictEqual(drive([{ type: "status_change", status: "keepalive" }]), []);
});

test("an event type we do not know is ignored rather than guessed at", () => {
  assert.deepStrictEqual(drive([{ type: "something_new_in_phase_f" }]), []);
});

test("the routed-type set matches what the switch handles", () => {
  // The reading loop asks this set rather than restating the list, so a case
  // added here without the set would silently double-handle.
  const handled = new Set();
  for (const type of ROUTED_TYPES) {
    const messages = routeAgentEvent(
      { type, items: [], entries: [] }, createRouterState(fakeClock())
    );
    if (messages.length || type === "text_delta") handled.add(type);
  }
  assert.strictEqual(handled.size, ROUTED_TYPES.size);
});

test("the whole recorded stream routes without throwing", () => {
  const messages = drive(STREAM);

  // Every render path the console has, exercised once.
  for (const type of [
    "AGENT_TODO_UPDATE", "AGENT_TOOL_ACTIVITY", "AGENT_DELEGATION",
    "TOOL_APPROVAL_REQUEST", "AGENT_VERIFICATION", "TEST_RESULT",
    "CHAT_STREAM_CHUNK",
  ]) {
    assert.ok(of(messages, type).length > 0, `${type} was never produced`);
  }
});

// ── Runner ───────────────────────────────────────────────────────────────

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
  } catch (err) {
    failed++;
    console.error(`  ✗ ${name}`);
    console.error(`    ${err.message}`);
  }
}

if (failed) {
  console.error(`\n${failed} of ${tests.length} agent-console tests failed.`);
  process.exit(1);
}
console.log(`\n${tests.length} agent-console tests passed.`);
