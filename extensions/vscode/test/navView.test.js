/**
 * Navigation sidebar tests.
 *
 * Loads the real nav template into jsdom and asserts the thing the redesign
 * is actually about: the sidebar navigates and nothing more. No message list,
 * no composer, no provider form — those live in the chat tab and the settings
 * tab respectively, and duplicating them here is the bug this replaces.
 *
 * Run with:  npm test        (from extensions/vscode)
 *            make extension-test
 */
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM } = require("jsdom");

const EXT_ROOT = path.resolve(__dirname, "..");
const TEMPLATE = path.join(EXT_ROOT, "src/ui/webview/gitpilotNavTemplate.html");

const html = fs
  .readFileSync(TEMPLATE, "utf-8")
  .replace(/__CSP__/g, "")
  .replace(/__NONCE__/g, "n");

const sent = [];
const dom = new JSDOM(html, {
  runScripts: "dangerously",
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

const now = new Date();
const iso = (d) => d.toISOString();
const yesterday = new Date(now);
yesterday.setDate(now.getDate() - 1);

const STATE = {
  connected: true,
  connecting: false,
  canStartLocally: true,
  model: "llama3:8b",
  repo: "gitpilot",
  branch: "develop/ai-provider",
  activeSessionId: "s1",
  sessions: [
    { id: "s1", name: "Add error handling to API routes", created_at: iso(now), message_count: 6, status: "active" },
    { id: "s2", name: "Refactor auth middleware", created_at: iso(yesterday), message_count: 3, status: "idle" },
    { id: "s3", name: "Optimize database queries", created_at: iso(new Date(now.getTime() - 6 * 864e5)), status: "idle" },
  ],
};

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── The core claim of the redesign ──

test("the sidebar signals it is ready", () => {
  assert.ok(lastOfType("NAV_READY"), "NAV_READY not sent");
});

test("the sidebar contains no chat", () => {
  assert.strictEqual($("textarea"), null, "a composer in the sidebar duplicates the chat tab");
  assert.strictEqual($("#messages"), null, "a message list in the sidebar duplicates the chat tab");
  assert.strictEqual($("#chat-list"), null);
  assert.strictEqual($("input[type=password]"), null, "credentials belong in Settings");
});

test("the sidebar contains no provider form", () => {
  assert.strictEqual($("select"), null, "a model dropdown here duplicates Settings");
  // The model is a button that delegates, not an editor.
  assert.ok($("#btn-model"), "the model should still be reachable");
});

// ── Status ──

test("connected state reads Ready with the model beside it", async () => {
  toHost({ type: "NAV_STATE", state: STATE });
  await flush();
  assert.strictEqual($("#status-text").textContent, "Ready");
  assert.ok($("#status-dot").classList.contains("ok"));
  assert.strictEqual($("#model-name").textContent, "llama3:8b");
  assert.ok($("#status-reason").classList.contains("hidden"),
    "Ready needs no explanation");
});

test("connecting is the one state that animates", async () => {
  toHost({ type: "NAV_STATE", state: { ...STATE, connected: false, connecting: true } });
  await flush();
  assert.strictEqual($("#status-text").textContent, "Connecting…");
  assert.ok($("#status-dot").classList.contains("pending"));
  // No offline recovery while a connection is still in flight.
  assert.ok($("#recovery").classList.contains("hidden"));
});

test("offline gives one status and one reason", async () => {
  toHost({
    type: "NAV_STATE",
    state: { ...STATE, connected: false, connecting: false, sessions: [], detail: "Could not reach http://127.0.0.1:8000" },
  });
  await flush();

  assert.strictEqual($("#status-text").textContent, "Offline");
  assert.strictEqual($("#status-reason").textContent, "Could not reach http://127.0.0.1:8000");
  assert.ok(!$("#recovery").classList.contains("hidden"));
  assert.ok(!$("#btn-start-server").classList.contains("hidden"));
});

test("reachable but unconfigured is Needs setup, not Ready", async () => {
  toHost({ type: "NAV_STATE", state: { ...STATE, model: undefined } });
  await flush();

  assert.strictEqual($("#status-text").textContent, "Needs setup",
    "a server you can reach but cannot use is not ready for work");
  assert.strictEqual($("#status-reason").textContent, "No provider configured");
  assert.ok($("#status-dot").classList.contains("warn"));
  assert.ok($("#btn-model").classList.contains("hidden"),
    "there is no model to change");
});

test("the sidebar never contradicts itself", async () => {
  toHost({ type: "NAV_STATE", state: { ...STATE, connected: false, connecting: false } });
  await flush();

  // "Disconnected" beside "Ready" beside "Provider not set" was four status
  // concepts arguing with each other. There is exactly one now.
  // Rendered text only: the script that decides the wording naturally
  // mentions every state.
  const rendered = [...doc.querySelectorAll("body *")]
    .filter((n) => !["SCRIPT", "STYLE", "SVG"].includes(n.tagName))
    .map((n) => n.textContent)
    .join(" ");
  const claims = ["Ready", "Connecting", "Needs setup", "Offline"]
    .filter((word) => rendered.includes(word));
  assert.deepStrictEqual(claims, ["Offline"]);
});

test("a slow server is not offered a Start button", async () => {
  // It is already running; Start would fail on a port that is in use.
  toHost({
    type: "NAV_STATE",
    state: { ...STATE, connected: false, connecting: false, retrying: true,
             detail: "http://127.0.0.1:8000 is not answering yet — still trying" },
  });
  await flush();

  assert.ok($("#btn-start-server").classList.contains("hidden"));
  assert.ok(!$("#retrying").classList.contains("hidden"),
    "a dead-looking panel made Reconnect feel like the only way forward");
});

test("the panel says it is retrying, so Reconnect is a choice not a chore", async () => {
  toHost({ type: "NAV_STATE", state: { ...STATE, connected: false, connecting: false, retrying: true } });
  await flush();
  assert.match($("#retrying").textContent, /Retrying automatically/);

  toHost({ type: "NAV_STATE", state: { ...STATE, connected: false, connecting: false, retrying: false } });
  await flush();
  assert.ok($("#retrying").classList.contains("hidden"));
});

test("a remote server offers no local start button", async () => {
  toHost({ type: "NAV_STATE", state: { ...STATE, connected: false, connecting: false, canStartLocally: false } });
  await flush();
  assert.ok($("#btn-start-server").classList.contains("hidden"));
});

test("no folder open says so rather than showing a blank line", async () => {
  toHost({ type: "NAV_STATE", state: { ...STATE, repo: undefined, branch: undefined } });
  await flush();
  assert.strictEqual($("#repo-line").textContent, "No folder open");
});

// ── Sessions ──

test("recent tasks are what the sidebar is for", async () => {
  toHost({ type: "NAV_STATE", state: STATE });
  await flush();
  const labels = $$(".group-label").map((n) => n.textContent.trim());
  assert.ok(labels.some((l) => l.startsWith("Recent Tasks")));
});

test("sessions are rows with a title and a subtle timestamp", () => {
  const rows = $$("#session-list .row");
  assert.strictEqual(rows.length, 3);
  assert.strictEqual(rows[0].querySelector(".row-title").textContent, "Add error handling to API routes");
  assert.match(rows[0].querySelector(".row-sub").textContent, /6 msg/);
  assert.strictEqual(rows[1].querySelector(".row-sub").textContent.split(" · ")[0], "Yesterday");
});

test("the active session is marked, and only that one", () => {
  const rows = $$("#session-list .row");
  assert.ok(rows[0].classList.contains("current"));
  assert.ok(!rows[1].classList.contains("current"));
  assert.ok(!rows[2].classList.contains("current"));
});

test("clicking a session opens it — one action, not select-then-open", () => {
  $$("#session-list .row")[1].click();
  const req = lastOfType("OPEN_SESSION");
  assert.strictEqual(req.sessionId, "s2");
});

test("the overflow menu is hidden until hover, and does not switch session", () => {
  const row = $$("#session-list .row")[2];
  const more = row.querySelector(".row-more");
  assert.ok(more, "no overflow menu on the row");
  assert.strictEqual(window.getComputedStyle(more).opacity, "0",
    "permanently visible per-row controls are what make a list cluttered");

  sent.length = 0;
  more.click();
  assert.strictEqual(lastOfType("SESSION_MENU").sessionId, "s3");
  assert.strictEqual(lastOfType("OPEN_SESSION"), undefined,
    "reaching for the menu must not also switch session");
});

test("an empty session list explains what to do", async () => {
  toHost({ type: "NAV_STATE", state: { ...STATE, sessions: [] } });
  await flush();
  assert.strictEqual($$("#session-list .row").length, 0);
  assert.ok(!$("#session-empty").classList.contains("hidden"));
  assert.match($("#session-empty").textContent, /New Task/);
});

test("a long list is capped, with a way to see the rest", async () => {
  const many = Array.from({ length: 20 }, (_, i) => ({
    id: `x${i}`, name: `Task ${i}`, created_at: iso(now), status: "idle",
  }));
  toHost({ type: "NAV_STATE", state: { ...STATE, sessions: many } });
  await flush();
  assert.strictEqual($$("#session-list .row").length, 8, "the sidebar is not a session browser");
  assert.ok(!$("#link-all-sessions").classList.contains("hidden"));
});

// ── The sidebar is navigation, and only navigation ──

test("quick actions are here, not in the conversation", async () => {
  toHost({ type: "NAV_STATE", state: STATE });
  await flush();

  const labels = $$("#quick-actions .row").map((r) => r.querySelector(".row-title").textContent);
  assert.deepStrictEqual(labels, [
    "Explain project",
    "Review current file",
    "Fix selection",
    "Write tests",
    "Security scan",
  ]);
});

test("a quick action sends its id", () => {
  $$("#quick-actions .row")[0].click();
  assert.strictEqual(lastOfType("QUICK_ACTION").action, "explain_project");
});

test("recent tasks come before quick actions", async () => {
  toHost({ type: "NAV_STATE", state: STATE });
  await flush();
  const labels = $$(".group-label").map((n) => n.textContent.replace(/\s+/g, " ").trim());
  assert.deepStrictEqual(labels, ["Recent Tasks View all", "Quick Actions"],
    "resuming work is more frequent than starting a canned action");
});

// ── Header and footer ──

test("every header and footer control reaches the host", async () => {
  toHost({ type: "NAV_STATE", state: STATE });
  await flush();

  $("#btn-new-chat").click();
  assert.ok(lastOfType("NEW_TASK"),
    "and the host turns that into a real new session, not just a tab reveal");
  $("#btn-model").click();
  assert.ok(lastOfType("SELECT_MODEL"));
  $("#btn-docs").click();
  assert.ok(lastOfType("OPEN_DOCS"));

  sent.length = 0;
  $("#btn-settings-footer").click();
  assert.ok(lastOfType("OPEN_SETTINGS"),
    "the footer is Settings now — Open Chat pointed at a surface that opens itself");
});

test("offline recovery buttons reach the host", async () => {
  toHost({ type: "NAV_STATE", state: { ...STATE, connected: false, connecting: false } });
  await flush();
  $("#btn-start-server").click();
  assert.ok(lastOfType("START_SERVER"));
  $("#btn-reconnect").click();
  assert.ok(lastOfType("RECONNECT"));
});

test("the sidebar names itself once", () => {
  // VS Code draws "GITPILOT" above this webview. A brand row of our own put
  // the same word on screen twice, one under the other.
  assert.strictEqual($(".nav-header"), null);
  assert.strictEqual($(".brand"), null);
});

test("there is one settings entry point in the webview", () => {
  const settingsish = $$("button").filter((b) =>
    /setting/i.test(b.getAttribute("title") || ""));
  assert.strictEqual(settingsish.length, 1,
    "the gear in the view title bar is VS Code's; ours is the footer row");

  sent.length = 0;
  settingsish[0].click();
  assert.ok(lastOfType("OPEN_SETTINGS"));
});

test("reduced motion is respected", () => {
  const style = doc.querySelector("style").textContent;
  assert.match(style, /prefers-reduced-motion/,
    "animations must yield to the platform preference");
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
