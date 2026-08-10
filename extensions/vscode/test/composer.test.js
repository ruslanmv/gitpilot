/**
 * Chat composer and transcript tests.
 *
 * These cover the behaviours that separate an agent surface from a chat box:
 * context you can see before you send it, `@` and `/` completion inline in the
 * composer, tool activity that collapses into the conversation instead of
 * piling up beside it, and a transcript that is patched rather than rebuilt so
 * nothing the user opened closes underneath them.
 *
 * The template is loaded into jsdom and driven the way a user drives it, with
 * the extension host's side of the protocol played by hand.
 *
 * Run with:  npm test        (from extensions/vscode)
 *            make extension-test
 */
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM } = require("jsdom");

const EXT_ROOT = path.resolve(__dirname, "..");
const TEMPLATE = path.join(EXT_ROOT, "src/ui/webview/gitpilotWorkspaceTemplate.html");

const html = fs
  .readFileSync(TEMPLATE, "utf-8")
  .replace(/__CSP__/g, "")
  .replace(/__NONCE__/g, "n")
  .replace(/__CSS_URI__/g, "about:blank");

const sent = [];
const dom = new JSDOM(html, {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  beforeParse(window) {
    window.acquireVsCodeApi = () => ({ postMessage: (m) => sent.push(m) });
  },
});
const { window } = dom;
const doc = window.document;
const $ = (sel) => doc.querySelector(sel);
const $$ = (sel) => [...doc.querySelectorAll(sel)];

const toHost = (msg) => window.postMessage(msg, "*");
const flush = () => new Promise((r) => window.setTimeout(r, 0));
const lastOfType = (type) => [...sent].reverse().find((m) => m.type === type);

const key = (k, extra = {}) =>
  new window.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true, ...extra });

/** Type into the composer with the caret at the end, as a user would. */
function type(text) {
  const input = $("#chat-input");
  input.value = text;
  input.selectionStart = input.selectionEnd = text.length;
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
  return input;
}

/** A STATE_SYNC carrying a conversation. */
const stateWith = (messages, activeTask = { status: "idle" }) => ({
  type: "STATE_SYNC",
  payload: {
    chat: { messages },
    activeTask,
    provider: {},
    server: {},
    workflow: {},
    workspace: { folderOpen: true, git: {} },
    ui: {},
  },
});

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── Context chips ──

test("selecting code attaches a chip on its own", async () => {
  toHost({
    type: "EDITOR_CONTEXT",
    payload: { file: "src/api/user.ts", range: "42-58", text: "const x = 1;" },
  });
  await flush();

  const chips = $$("#context-chips .context-chip");
  assert.strictEqual(chips.length, 1, "naming the file you mean should not be manual");
  assert.match(chips[0].textContent, /src\/api\/user\.ts:42-58/);
  assert.ok(!$("#context-chips").classList.contains("hidden"));
});

test("clearing the selection removes the chip rather than stacking a second", async () => {
  toHost({ type: "EDITOR_CONTEXT", payload: { file: "a.ts", range: "1-2", text: "x" } });
  await flush();
  toHost({ type: "EDITOR_CONTEXT", payload: undefined });
  await flush();

  assert.strictEqual($$("#context-chips .context-chip").length, 0);
  assert.ok($("#context-chips").classList.contains("hidden"));
});

test("a chip can be taken back", async () => {
  toHost({ type: "EDITOR_CONTEXT", payload: { file: "a.ts", range: "3", text: "x" } });
  await flush();

  $("#context-chips .chip-remove").click();
  assert.strictEqual($$("#context-chips .context-chip").length, 0,
    "context you cannot take back is context you cannot trust");
});

test("attaching a file adds a chip, and adding it twice does not", async () => {
  toHost({ type: "ATTACH_CONTEXT_FILE", payload: { path: "src/db.ts" } });
  toHost({ type: "ATTACH_CONTEXT_FILE", payload: { path: "src/db.ts" } });
  await flush();

  const labels = $$("#context-chips .chip-label").map((n) => n.textContent);
  assert.deepStrictEqual(labels, ["src/db.ts"]);
});

test("the + button asks the host for a file", () => {
  $("#attach-btn").click();
  assert.ok(lastOfType("PICK_CONTEXT_FILE"));
});

// ── What actually gets sent ──

test("attached context travels with the message, and only once", async () => {
  toHost({ type: "EDITOR_CONTEXT", payload: { file: "src/api/user.ts", range: "42-58", text: "const x = 1;" } });
  toHost({ type: "ATTACH_CONTEXT_FILE", payload: { path: "src/db.ts" } });
  await flush();

  type("add error handling");
  $("#send-btn").click();
  await flush();

  const outbound = lastOfType("SEND_CHAT").payload.text;
  assert.match(outbound, /src\/api\/user\.ts:42-58/);
  assert.match(outbound, /const x = 1;/, "the code should travel, not just the coordinate");
  assert.match(outbound, /src\/db\.ts/);
  assert.ok(outbound.endsWith("add error handling"));

  // Pinned files are consumed by the send; leaving them attached silently
  // re-sends them with every later message.
  assert.deepStrictEqual(
    $$("#context-chips .chip-label").map((n) => n.textContent),
    ["src/api/user.ts:42-58"],
    "the live selection stays because it is still selected; pinned files do not"
  );
});

test("the transcript shows what you typed, not the preamble", async () => {
  const shown = $$("#chat-list .chat-item.user .chat-content").map((n) => n.textContent);
  assert.ok(shown.some((t) => t.trim() === "add error handling"),
    "the user should not have to read their own context block back");
});

// ── Inline completion ──

test("@ opens a file picker inline, not a modal", async () => {
  toHost({ type: "FILE_INDEX", payload: { files: ["src/api/user.ts", "src/api/auth.ts", "README.md"] } });
  await flush();

  type("look at @api");
  assert.ok(!$("#compose-popup").classList.contains("hidden"));
  const labels = $$("#compose-popup .popup-row .popup-label").map((n) => n.textContent);
  assert.deepStrictEqual(labels, ["src/api/user.ts", "src/api/auth.ts"]);
});

test("arrows move the selection and Enter accepts it", async () => {
  type("look at @api");
  const input = $("#chat-input");

  input.dispatchEvent(key("ArrowDown"));
  assert.strictEqual($$("#compose-popup .popup-row")[1].className, "popup-row active");

  input.dispatchEvent(key("Enter"));
  await flush();

  assert.ok($$("#context-chips .chip-label").some((n) => n.textContent === "src/api/auth.ts"),
    "an @ reference becomes a chip rather than inline noise in the sentence");
  assert.strictEqual(input.value, "look at ", "the trigger text is consumed");
  assert.ok($("#compose-popup").classList.contains("hidden"));
});

/** Drop every chip, so a test can assert on the message body alone. */
async function clearChips() {
  toHost({ type: "EDITOR_CONTEXT", payload: undefined });
  await flush();
  for (const btn of $$("#context-chips .chip-remove")) btn.click();
}

/** Return the panel to a state where a new message can start a run. */
async function goIdle() {
  toHost(stateWith([{ id: "u1", role: "user", content: "done" }], { status: "idle" }));
  await flush();
}

test("Enter sends when no picker is open", async () => {
  await goIdle();
  await clearChips();
  sent.length = 0;
  const input = type("just send this");
  input.dispatchEvent(key("Enter"));
  await flush();
  assert.strictEqual(lastOfType("SEND_CHAT").payload.text.trim(), "just send this");
});

test("typing while GitPilot works queues instead of interrupting", async () => {
  // The previous test left a run in flight, which is exactly the state this
  // is about.
  sent.length = 0;
  const input = type("and also update the docs");
  input.dispatchEvent(key("Enter"));
  await flush();

  assert.strictEqual(lastOfType("SEND_CHAT"), undefined,
    "a second request mid-run is not something the backend was asked for");
  assert.strictEqual(lastOfType("CANCEL_TASK"), undefined,
    "losing a task to a reflex press of Enter is not a trade anyone would choose");
  assert.strictEqual(input.value, "", "the text was taken, so the composer clears");
  assert.match($("#compose-status").textContent, /1 queued/);
});

test("the queue is sent when the run ends", async () => {
  await goIdle();
  const outbound = lastOfType("SEND_CHAT");
  assert.ok(outbound, "a message held during the run has to go somewhere");
  assert.ok(outbound.payload.text.endsWith("and also update the docs"));
});

test("stopping a run drops what was queued behind it", async () => {
  toHost(stateWith([{ id: "u1", role: "user", content: "go" }], { status: "generating" }));
  await flush();

  type("queued behind a run I am about to stop").dispatchEvent(key("Enter"));
  await flush();
  assert.match($("#compose-status").textContent, /1 queued/);

  sent.length = 0;
  $("#send-btn").click();
  await flush();

  assert.ok(lastOfType("CANCEL_TASK"));
  assert.strictEqual(lastOfType("SEND_CHAT"), undefined,
    "cancelling then firing the queue would restart the work you just stopped");
});

test("Escape closes the picker without sending", async () => {
  sent.length = 0;
  const input = type("look at @api");
  input.dispatchEvent(key("Escape"));
  assert.ok($("#compose-popup").classList.contains("hidden"));
  assert.strictEqual(lastOfType("SEND_CHAT"), undefined);
});

test("/ completes commands, but only at the start of a message", () => {
  type("/rev");
  const labels = $$("#compose-popup .popup-row .popup-label").map((n) => n.textContent);
  assert.deepStrictEqual(labels, ["/review"]);

  type("what about http://x/rev");
  assert.ok($("#compose-popup").classList.contains("hidden"),
    "a slash mid-sentence is a path, not a command");
});

test("an @ inside a word is not a trigger", () => {
  type("mail me at someone@example.com");
  assert.ok($("#compose-popup").classList.contains("hidden"));
});

test("a picked slash command is inserted as text", async () => {
  const input = type("/rev");
  input.dispatchEvent(key("Tab"));
  await flush();
  assert.strictEqual(input.value, "/review ");
});

// ── The mode picker ──

test("the menu is closed until asked for, and opens on the trigger", () => {
  const menu = $("#mode-selector");
  assert.ok(menu.classList.contains("hidden"), "a menu open by default is not a menu");

  $("#mode-trigger").click();
  assert.ok(!menu.classList.contains("hidden"));
  assert.strictEqual($("#mode-trigger").getAttribute("aria-expanded"), "true",
    "toggle() reports the hidden state, which is easy to invert by accident");
});

test("picking a mode updates the trigger immediately", async () => {
  sent.length = 0;
  $$("#mode-selector .mode-btn")[1].click();
  await flush();

  assert.strictEqual($("#mode-label").textContent, "Plan",
    "the trigger is the only thing on screen naming the mode; it must not wait " +
    "for the backend to echo");
  assert.strictEqual(lastOfType("SET_EXECUTION_MODE").payload.mode, "plan");
  assert.ok($("#mode-selector").classList.contains("hidden"), "picking closes the menu");
});

test("clicking away closes the menu", () => {
  $("#mode-trigger").click();
  assert.ok(!$("#mode-selector").classList.contains("hidden"));

  doc.body.click();
  assert.ok($("#mode-selector").classList.contains("hidden"));
  assert.strictEqual($("#mode-trigger").getAttribute("aria-expanded"), "false");
});

test("the @ and / buttons type the trigger the keyboard uses", async () => {
  await goIdle();
  await clearChips();

  const input = type("look at ");
  $("#mention-btn").click();
  assert.strictEqual(input.value, "look at @");
  assert.ok(!$("#compose-popup").classList.contains("hidden"),
    "the button should open the same picker the keystroke does");

  input.value = "";
  input.selectionStart = input.selectionEnd = 0;
  $("#slash-btn").click();
  assert.strictEqual(input.value, "/");

  // Leave the picker closed: Escape belongs to whichever popup is open, so a
  // stray one here would swallow the interrupt the next test presses.
  type("");
  assert.ok($("#compose-popup").classList.contains("hidden"));
});

// ── Tool activity lives in the conversation ──

test("tool calls collapse into one line in the transcript", async () => {
  toHost(stateWith([{ id: "u1", role: "user", content: "find the bug" }]));
  await flush();

  for (const [id, name, target] of [
    ["t1", "read_file", "src/a.ts"],
    ["t2", "read_file", "src/b.ts"],
    ["t3", "grep", "handleAuth"],
  ]) {
    toHost({ type: "AGENT_TOOL_ACTIVITY", payload: { id, name, target, status: "completed" } });
  }
  await flush();

  const blocks = $$("#chat-list .activity-block");
  assert.strictEqual(blocks.length, 1, "consecutive calls are one step, not three entries");
  assert.match(blocks[0].querySelector(".activity-summary-text").textContent, /Investigated 3 steps/);
  assert.ok(!blocks[0].classList.contains("open"), "the summary is the answer; the calls are the detail");
});

test("the block expands to the individual calls, named as verbs", () => {
  const block = $("#chat-list .activity-block");
  block.querySelector(".activity-summary").click();

  assert.ok(block.classList.contains("open"));
  assert.strictEqual(block.querySelector(".activity-summary").getAttribute("aria-expanded"), "true");

  const lines = [...block.querySelectorAll(".activity-name")].map((n) => n.textContent);
  assert.deepStrictEqual(lines, ["Read src/a.ts", "Read src/b.ts", "Searched handleAuth"],
    "`read_file · completed` is a log; `Read src/a.ts` is an explanation");
});

test("a failed call is called out in the summary", async () => {
  toHost({ type: "AGENT_TOOL_ACTIVITY", payload: { id: "t4", name: "run", target: "npm test", status: "failed" } });
  await flush();

  const block = $("#chat-list .activity-block");
  assert.match(block.querySelector(".activity-summary-text").textContent, /1 of 4 steps failed/);
  assert.ok(block.classList.contains("failed"));
});

test("the block survives a re-render with its open state intact", async () => {
  const before = $("#chat-list .activity-block");
  assert.ok(before.classList.contains("open"), "precondition: the user opened it");

  toHost(stateWith([
    { id: "u1", role: "user", content: "find the bug" },
    { id: "a1", role: "assistant", content: "It is a missing null check." },
  ]));
  await flush();

  const after = $("#chat-list .activity-block");
  assert.ok(after, "rebuilding the transcript used to delete everything it did not own");
  assert.ok(after.classList.contains("open"), "a block the user opened must not close underneath them");
  assert.strictEqual(after, before, "the node was patched, not replaced");
});

test("the transcript still shows both turns after the re-render", () => {
  const roles = $$("#chat-list .chat-item[data-key]").map((n) => n.className.split(" ")[1]);
  assert.ok(roles.includes("user") && roles.includes("assistant"));
});

// ── A new task is a new task ──

test("a new task clears the transcript, the steps and the composer", async () => {
  // Build up everything a finished task leaves behind.
  toHost(stateWith([
    { id: "u1", role: "user", content: "fix the auth error" },
    { id: "a1", role: "assistant", content: "Done." },
  ]));
  await flush();
  toHost({ type: "AGENT_TOOL_ACTIVITY", payload: { id: "z1", name: "read_file", target: "a.ts", status: "completed" } });
  toHost({ type: "ATTACH_CONTEXT_FILE", payload: { path: "src/db.ts" } });
  await flush();
  type("half a sentence I never sent");

  assert.ok($$("#chat-list .chat-item[data-key]").length > 0, "precondition: a transcript");
  assert.ok($("#chat-list .activity-block"), "precondition: tool activity");
  assert.ok($$("#context-chips .context-chip").length > 0, "precondition: a pinned file");

  toHost({ type: "SESSION_RESET" });
  await flush();

  assert.strictEqual($$("#chat-list .chat-item[data-key]").length, 0);
  assert.strictEqual($("#chat-list .activity-block"), null,
    "activity blocks carry no message key, so clearing state alone leaves them");
  assert.strictEqual($$("#context-chips .context-chip").length, 0,
    "a pinned file belongs to the task being replaced");
  assert.strictEqual($("#chat-input").value, "");
});

test("a new task lands on the landing page, not an empty transcript", async () => {
  assert.ok(!$("#chat-empty-state").classList.contains("hidden"),
    "'What are we building?' is what a new task should open on");
  assert.ok(!$("#home-actions").classList.contains("hidden"));
  assert.ok($("#chat-list").classList.contains("hidden"));
});

test("a state sync after the reset still renders", async () => {
  // The render guard compares state signatures; the reset changes only the
  // DOM, so without clearing the guard the next paint is skipped.
  toHost(stateWith([{ id: "n1", role: "user", content: "the new task" }]));
  await flush();

  assert.match($("#chat-list").textContent, /the new task/);
  assert.ok($("#chat-empty-state").classList.contains("hidden"));
});

test("a queued message does not survive into the new task", async () => {
  toHost(stateWith([{ id: "u1", role: "user", content: "go" }], { status: "generating" }));
  await flush();
  type("queued behind the old task").dispatchEvent(key("Enter"));
  await flush();
  assert.match($("#compose-status").textContent, /1 queued/);

  sent.length = 0;
  toHost({ type: "SESSION_RESET" });
  await flush();
  toHost(stateWith([], { status: "idle" }));
  await flush();

  assert.strictEqual(lastOfType("SEND_CHAT"), undefined,
    "it was meant for the conversation that has just been replaced");
});

// ── Escape stops a run ──

test("Escape interrupts a run in progress", async () => {
  toHost(stateWith([{ id: "u1", role: "user", content: "go" }], { status: "generating" }));
  await flush();

  sent.length = 0;
  $("#chat-input").dispatchEvent(key("Escape"));
  await flush();
  assert.ok(lastOfType("CANCEL_TASK"),
    "reaching for the mouse to stop a runaway agent is friction worth removing");
});

// ── The diff is summarised before it is listed ──

test("changes are stated as one line before the file rows", async () => {
  toHost(stateWith([{ id: "u1", role: "user", content: "fix it" }], {
    status: "ready_to_apply",
    changedFiles: [
      {
        path: "src/api/user.ts",
        status: "applied",
        hasDiff: true,
        diffPreview: "--- a\n+++ b\n+one\n+two\n-old\n",
      },
    ],
  }));
  await flush();

  assert.strictEqual($("#changes-label").textContent, "✓ Updated user.ts");
  assert.strictEqual($("#changes-stat").textContent, "+2 −1",
    "a +++/--- header is not an edit");
  assert.strictEqual($$("#changes-list .row").length, 1, "the rows are still there");
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
