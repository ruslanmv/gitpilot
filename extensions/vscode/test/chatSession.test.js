/**
 * Session command tests.
 *
 * These cover the bug behind "New Chat does not produce a new chat": the
 * session commands used to talk only to the legacy chat provider, which is no
 * longer registered as a view. A new session was created on the backend while
 * the conversation the user could actually see never changed.
 *
 * The panel renders its transcript from `state.chat.messages`, so these
 * assertions are about that state — clearing it is what makes the panel go
 * empty.
 *
 * Run with:  npm test        (from extensions/vscode)
 *            make extension-test
 */
const Module = require("module");
const assert = require("assert");
const path = require("path");

const OUT = path.resolve(__dirname, "../out");

// ── vscode stub ──
const commands = new Map();
const executed = [];
const shown = { info: [], warnings: [], errors: [] };

const vscodeStub = {
  commands: {
    registerCommand: (id, fn) => {
      commands.set(id, fn);
      return { dispose() {} };
    },
    executeCommand: async (id, ...args) => {
      executed.push(id);
      // Only the commands under test are dispatched; the rest are recorded.
      const fn = commands.get(id);
      if (fn && id.startsWith("gitpilot.") && UNDER_TEST.has(id)) {
        return fn(...args);
      }
      return undefined;
    },
  },
  window: {
    showInformationMessage: (m) => { shown.info.push(m); return Promise.resolve(undefined); },
    showWarningMessage: (m) => { shown.warnings.push(m); return Promise.resolve(undefined); },
    showErrorMessage: (m) => { shown.errors.push(m); return Promise.resolve(undefined); },
    showInputBox: () => Promise.resolve(undefined),
  },
  workspace: { workspaceFolders: undefined, getConfiguration: () => ({ get: (_k, d) => d }) },
};

const UNDER_TEST = new Set();

const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...args) {
  if (request === "vscode") return "vscode";
  return origResolve.call(this, request, ...args);
};
require.cache["vscode"] = { id: "vscode", filename: "vscode", loaded: true, exports: vscodeStub };

const { registerChatCommands } = require(path.join(OUT, "commands/chat.js"));

// ── Doubles ──

/** Mirrors the slice of StateStore these commands touch. */
let panelResets = 0;

function makeStateStore() {
  const state = {
    chat: { messages: [{ id: "old-1", role: "user", content: "previous conversation" }] },
    activeTask: { status: "complete", plan: { steps: [1] }, changedFiles: ["a.ts"] },
    ui: { mode: "diff" },
    workspace: { folderOpen: true, mode: "local_git", git: { isGitRepo: true }, folderPath: "/w" },
    github: { connected: false },
    session: { active: true, sessionId: "old-session" },
  };
  return {
    state,
    calls: [],
    setChatMessages(messages) {
      this.calls.push(["setChatMessages", messages.length]);
      state.chat.messages = messages;
    },
    clearTaskState() {
      this.calls.push(["clearTaskState"]);
      state.activeTask = { status: "idle" };
      state.ui = { mode: "idle" };
    },
  };
}

let store;
let coordinatorCalls;
let sessionMessages;
let getSessionShouldFail;

const coordinator = {
  startSession: async (mode) => { coordinatorCalls.push(["startSession", mode]); },
  resumeSession: async (id) => { coordinatorCalls.push(["resumeSession", id]); },
};

const modeResolver = {
  resolve: (input) => {
    coordinatorCalls.push(["resolve", input.preferredMode]);
    return input.git.isGitRepo ? "local_git" : "folder";
  },
};

const client = {
  createSession: async () => { coordinatorCalls.push(["legacyCreateSession"]); return { id: "legacy" }; },
  getSession: async (id) => {
    coordinatorCalls.push(["getSession", id]);
    if (getSessionShouldFail) throw new Error("history unavailable");
    return { id, messages: sessionMessages };
  },
  deleteSession: async () => {},
};

const chatProvider = {
  loadSessionIntoChat: async () => { coordinatorCalls.push(["legacyLoadSessionIntoChat"]); },
  sendMessageFromCommand: () => {},
};

function setup() {
  commands.clear();
  executed.length = 0;
  coordinatorCalls = [];
  sessionMessages = [
    { role: "user", content: "add error handling" },
    { role: "assistant", content: "I'll handle the missing-user case." },
  ];
  getSessionShouldFail = false;
  store = makeStateStore();

  panelResets = 0;
  registerChatCommands({ subscriptions: [] }, client, chatProvider, {
    stateStore: store,
    sessionCoordinator: coordinator,
    modeResolver,
    resetPanel: () => { panelResets++; },
  });
  UNDER_TEST.clear();
  ["gitpilot.newSession", "gitpilot.loadSession"].forEach((id) => UNDER_TEST.add(id));
}

const run = (id, ...args) => commands.get(id)(...args);

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── New Chat ──

test("New Chat empties the conversation the panel renders", async () => {
  setup();
  assert.strictEqual(store.state.chat.messages.length, 1, "precondition: a previous conversation");

  await run("gitpilot.newSession");

  assert.strictEqual(store.state.chat.messages.length, 0,
    "the panel renders state.chat.messages — not clearing it is why New Chat looked like it did nothing");
});

test("New Chat also clears the previous task", async () => {
  setup();
  await run("gitpilot.newSession");

  assert.ok(store.calls.some((c) => c[0] === "clearTaskState"),
    "a stale plan or diff makes the new chat look like a continuation of the old one");
  assert.strictEqual(store.state.activeTask.status, "idle");
  assert.strictEqual(store.state.ui.mode, "idle");
});

test("New Chat clears before awaiting the backend", async () => {
  setup();
  await run("gitpilot.newSession");

  const clearedAt = store.calls.findIndex((c) => c[0] === "setChatMessages");
  const startedAt = coordinatorCalls.findIndex((c) => c[0] === "startSession");
  assert.ok(clearedAt >= 0 && startedAt >= 0);
  // The panel must go empty on click, not after a round-trip.
  assert.ok(clearedAt < startedAt, "the clear must not wait on the network");
});

test("New Chat starts a session the panel understands", async () => {
  setup();
  await run("gitpilot.newSession");

  assert.ok(coordinatorCalls.some((c) => c[0] === "startSession" && c[1] === "local_git"),
    "the session must be started through the coordinator the panel reads");
  assert.ok(!coordinatorCalls.some((c) => c[0] === "legacyCreateSession"),
    "the legacy repo-scoped endpoint creates a session the panel never sees");
});

test("New Chat reveals the chat tab", async () => {
  setup();
  await run("gitpilot.newSession");
  assert.ok(executed.includes("gitpilot.openChatTab"),
    "the conversation lives in an editor tab; focusing the sidebar view no longer shows it");
});

test("New Chat refreshes the session list", async () => {
  setup();
  await run("gitpilot.newSession");
  assert.ok(executed.includes("gitpilot.refreshSessions"),
    "the sidebar should show the conversation that was just created");
});

test("the resolved mode respects the workspace", async () => {
  setup();
  store.state.workspace.git.isGitRepo = false;
  await run("gitpilot.newSession");
  assert.ok(coordinatorCalls.some((c) => c[0] === "startSession" && c[1] === "folder"));
});

test("New Chat tells the panel to drop what it is still holding", async () => {
  setup();
  await run("gitpilot.newSession");

  assert.strictEqual(panelResets, 1,
    "tool-activity blocks and the composer's queue live in the panel, not the " +
    "store, so clearing state alone leaves the previous task on screen");
});

// ── Resume ──

test("resuming replays the session's messages", async () => {
  setup();
  await run("gitpilot.loadSession", "s2");

  assert.deepStrictEqual(
    store.state.chat.messages.map((m) => [m.role, m.content]),
    [["user", "add error handling"], ["assistant", "I'll handle the missing-user case."]]
  );
});

test("resuming resets the panel before replaying the history", async () => {
  setup();
  await run("gitpilot.loadSession", "s2");

  assert.strictEqual(panelResets, 1,
    "otherwise the resumed transcript appears under the previous task's steps");
});

test("resuming clears the previous conversation first", async () => {
  setup();
  await run("gitpilot.loadSession", "s2");

  const clears = store.calls.filter((c) => c[0] === "setChatMessages");
  assert.strictEqual(clears[0][1], 0, "the old transcript must go before the new one arrives");
  assert.ok(store.calls.some((c) => c[0] === "clearTaskState"));
});

test("resuming restores the session, not just the transcript", async () => {
  setup();
  await run("gitpilot.loadSession", "s2");

  assert.ok(coordinatorCalls.some((c) => c[0] === "resumeSession" && c[1] === "s2"),
    "a GitPilot session is a task, so its own state has to be restored too");
  assert.ok(!coordinatorCalls.some((c) => c[0] === "legacyLoadSessionIntoChat"),
    "the legacy provider is not registered as a view — calling it does nothing visible");
});

test("resuming reveals the chat tab", async () => {
  setup();
  await run("gitpilot.loadSession", "s2");
  assert.ok(executed.includes("gitpilot.openChatTab"),
    "the conversation lives in an editor tab; focusing the sidebar view no longer shows it");
});

test("message roles are normalised", async () => {
  setup();
  sessionMessages = [{ role: "tool", content: "ran tests" }, { role: "assistant", content: "done" }];
  await run("gitpilot.loadSession", "s3");

  const roles = store.state.chat.messages.map((m) => m.role);
  assert.deepStrictEqual(roles, ["system", "assistant"],
    "an unrecognised role must not reach the renderer as-is");
});

test("a session with no history still resumes", async () => {
  setup();
  sessionMessages = [];
  await run("gitpilot.loadSession", "s4");

  assert.strictEqual(store.state.chat.messages.length, 0);
  assert.ok(coordinatorCalls.some((c) => c[0] === "resumeSession"));
  assert.strictEqual(shown.warnings.length, 0, "an empty history is not a failure");
});

test("unreadable history warns but leaves the session restored", async () => {
  setup();
  getSessionShouldFail = true;
  shown.warnings.length = 0;
  await run("gitpilot.loadSession", "s5");

  assert.ok(coordinatorCalls.some((c) => c[0] === "resumeSession"),
    "the session is usable even when its transcript is not");
  assert.ok(shown.warnings.some((w) => /could not load its history/i.test(w)));
  assert.strictEqual(shown.errors.length, 0, "this is a warning, not an error");
});

test("resuming with no id does nothing", async () => {
  setup();
  await run("gitpilot.loadSession", undefined);

  assert.strictEqual(store.calls.length, 0);
  assert.strictEqual(coordinatorCalls.length, 0);
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log(`  ✓ ${name}`);
    } catch (err) {
      failed++;
      console.log(`  ✗ ${name}\n      ${err.message}`);
    }
  }
  console.log(`\n${tests.length - failed}/${tests.length} passed`);
  process.exitCode = failed ? 1 : 0;
})();
