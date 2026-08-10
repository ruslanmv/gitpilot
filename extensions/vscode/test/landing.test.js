/**
 * The landing page — GitPilot Home.
 *
 * Home is not a separate tab any more. It is the empty state of the one
 * GitPilot editor tab, so the landing page and the conversation are the same
 * surface at two moments and there is never a question about which composer
 * is the real one.
 *
 * These assertions are about that: the landing is there when there is nothing
 * to read, it gets out of the way the moment there is, and it states status
 * once rather than competing with the sidebar.
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
const CSS = path.join(EXT_ROOT, "src/ui/webview/gitpilotWorkspace.css");

const source = fs.readFileSync(TEMPLATE, "utf-8");
const css = fs.readFileSync(CSS, "utf-8");
const html = source
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

const stateWith = (messages, extra = {}) => ({
  type: "STATE_SYNC",
  payload: {
    chat: { messages },
    activeTask: { status: "idle" },
    provider: { model: "llama3:8b" },
    server: { connected: true },
    workflow: {},
    workspace: { folderOpen: true, folderName: "gitpilot", git: {} },
    projectContextSummary: { repoName: "gitpilot" },
    executionMode: "ask",
    ui: {},
    ...extra,
  },
});

/** The declaration block for a selector, so assertions read like the file. */
function ruleFor(selector) {
  const match = css.match(
    new RegExp(`(^|\\n)${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`)
  );
  return match ? match[2] : "";
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── One surface, two moments ──

test("an empty GitPilot shows the landing page", async () => {
  toHost(stateWith([]));
  await flush();

  assert.ok(!$("#chat-empty-state").classList.contains("hidden"));
  assert.strictEqual($("#home-headline").textContent, "What are we building?");
  assert.strictEqual($(".home-name").textContent, "GitPilot");
});

test("there is exactly one composer on the whole surface", () => {
  assert.strictEqual($$("textarea").length, 1,
    "two composers is the question 'which one is real?' made visible");
  assert.ok($("#chat-input"));
});

test("the landing page has no composer of its own", () => {
  assert.strictEqual($("#chat-empty-state textarea"), null,
    "the landing shares the one composer below it rather than owning a second");
});

test("the first message replaces the landing with the conversation", async () => {
  toHost(stateWith([{ id: "u1", role: "user", content: "fix the auth error" }]));
  await flush();

  assert.ok($("#chat-empty-state").classList.contains("hidden"),
    "the landing is the empty state; it has nothing to say once there is a transcript");
  assert.ok(!$("#chat-list").classList.contains("hidden"));
  assert.match($("#chat-list").textContent, /fix the auth error/);
});

test("clearing the conversation brings the landing back", async () => {
  toHost(stateWith([]));
  await flush();
  assert.ok(!$("#chat-empty-state").classList.contains("hidden"));
});

test("the composer sits directly under the question, shortcuts below it", () => {
  const order = [...$(".chat-shell").children].map((n) => n.id || n.className);
  const hero = order.indexOf("chat-empty-state");
  const compose = order.findIndex((c) => String(c).includes("chat-compose"));
  const actions = order.indexOf("home-actions");

  assert.ok(hero >= 0 && compose >= 0 && actions >= 0);
  assert.ok(hero < compose,
    "the question is asked, then the box to answer it in");
  assert.ok(compose < actions,
    "shortcuts above the composer pushed the one thing to do off the fold");
});

test("the shortcuts hide with the landing", async () => {
  toHost(stateWith([{ id: "u1", role: "user", content: "go" }]));
  await flush();
  assert.ok($("#home-actions").classList.contains("hidden"));

  toHost(stateWith([]));
  await flush();
  assert.ok(!$("#home-actions").classList.contains("hidden"));
});

test("the landing carries no Quick Actions box", () => {
  assert.strictEqual($("#overview-section"), null,
    "a collapsible Quick Actions panel at the top of the tab is chrome above " +
    "the question; those shortcuts belong in the sidebar");
  assert.strictEqual($("#advanced-tools"), null);
});

// ── The suggestions ──

test("the four ways to start work are here, not in the sidebar", () => {
  const labels = $$(".empty-suggestions .suggestion-chip").map((b) => b.textContent.trim());
  assert.deepStrictEqual(labels, ["Review code", "Find bugs", "Write tests", "Explain project"]);
});

test("a suggestion sends a prompt rather than filling the box", async () => {
  toHost(stateWith([]));
  await flush();

  sent.length = 0;
  $$(".empty-suggestions .suggestion-chip")[0].click();
  await flush();

  assert.match(lastOfType("SEND_CHAT").payload.text, /Review the current file/,
    "one click, one result");
});

test("only the send button carries the accent", () => {
  // Four accent-coloured buttons compete with the one action the page exists
  // for, and turn a landing page into a toolbar.
  const chip = ruleFor(".suggestion-chip");
  assert.match(chip, /background:\s*none/);
  assert.ok(!/var\(--accent\)/.test(chip));
});

// ── The trust line ──

test("the landing states readiness, model, repository and permission", async () => {
  toHost(stateWith([]));
  await flush();

  const text = $("#home-trust").textContent;
  assert.match(text, /Ready/);
  assert.match(text, /llama3:8b/);
  assert.match(text, /gitpilot repository/);
  assert.match(text, /Changes require approval/);
});

test("the permission line follows the mode", async () => {
  toHost(stateWith([], { executionMode: "plan" }));
  await flush();
  assert.match($("#home-trust").textContent, /nothing is written/i);

  toHost(stateWith([], { executionMode: "auto" }));
  await flush();
  assert.match($("#home-trust").textContent, /applied automatically/i);
});

test("an unreachable server is stated once, not four times", async () => {
  toHost(stateWith([], { server: { connected: false } }));
  await flush();

  const text = $("#home-trust").textContent;
  assert.match(text, /Offline/);
  assert.ok(!/Ready/.test(text), "Offline and Ready must never appear together");
  assert.ok($("#home-trust .home-dot").classList.contains("bad"));
});

test("connecting is not offline", async () => {
  toHost(stateWith([], { server: { connected: false, connecting: true } }));
  await flush();
  assert.match($("#home-trust").textContent, /Connecting/);
  assert.ok($("#home-trust .home-dot").classList.contains("pending"));
});

// ── Motion is kept ──

test("the landing has an entrance, and every panel animation survives", () => {
  assert.match(css, /@keyframes homeIn\b/);
  for (const name of [
    "spin", "thinkingPulse", "messageIn", "thinkingSweep",
    "thinkingCollapse", "successFlash", "errorShake", "cursorBlink",
    "activityFadeIn",
  ]) {
    assert.match(css, new RegExp(`@keyframes ${name}\\b`), `${name} was removed`);
  }
});

test("the connecting dot pulses while it is working", () => {
  assert.match(ruleFor(".home-dot.pending"), /animation:\s*thinkingPulse/,
    "the one state expected to change on its own is the one worth animating");
});

test("motion stays a preference", () => {
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
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
