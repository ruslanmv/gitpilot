/**
 * Chat panel presentation tests.
 *
 * These pin down visual decisions that are easy to undo by accident: the
 * conversation reads as text rather than a stack of cards, the mode selector
 * states a permission model, and the transcript uses the height it has.
 *
 * They read the shipped template and stylesheet directly — the panel's own
 * behaviour is covered by the session-command suite.
 *
 * Run with:  npm test        (from extensions/vscode)
 *            make extension-test
 */
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM } = require("jsdom");

const EXT_ROOT = path.resolve(__dirname, "..");
const template = fs.readFileSync(
  path.join(EXT_ROOT, "src/ui/webview/gitpilotWorkspaceTemplate.html"),
  "utf-8"
);
const css = fs.readFileSync(
  path.join(EXT_ROOT, "src/ui/webview/gitpilotWorkspace.css"),
  "utf-8"
);

const dom = new JSDOM(template);
const doc = dom.window.document;
const $$ = (sel) => [...doc.querySelectorAll(sel)];

/** The declaration block for a selector, so assertions read like the file. */
function ruleFor(selector) {
  const match = css.match(
    new RegExp(`(^|\\n)${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`)
  );
  return match ? match[2] : "";
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── The conversation is text, not a stack of cards ──

test("a message is not a card", () => {
  const rule = ruleFor(".chat-item");
  assert.match(rule, /background:\s*transparent/,
    "boxing every turn makes a long conversation visually exhausting");
  assert.match(rule, /border:\s*none/);
  assert.ok(!/border-radius:\s*var\(--radius-md\)/.test(rule),
    "rounded card chrome belongs to interactive blocks, not prose");
});

test("the role is carried by a rule, not a fill", () => {
  assert.match(ruleFor(".chat-item"), /border-left:\s*2px solid transparent/);
  assert.match(ruleFor(".chat-item.user"), /border-left-color:\s*var\(--accent\)/);
});

test("cards remain for things you can act on", () => {
  // The approval card is the canonical interactive object; it must keep its
  // own chrome now that ordinary messages have shed theirs.
  const approval = ruleFor(".approval-card");
  assert.ok(
    /background|border/.test(approval),
    "an approval must still stand out from the prose around it"
  );
});

test("the message-in animation survives the restyle", () => {
  assert.match(ruleFor(".chat-item"), /animation:\s*messageIn/);
  assert.match(css, /@keyframes messageIn/);
});

test("every animation the panel had is still present", () => {
  // The redesign moves and restyles; it does not delete motion.
  for (const name of [
    "spin", "thinkingPulse", "messageIn", "thinkingSweep",
    "thinkingCollapse", "successFlash", "errorShake", "cursorBlink",
    "activityFadeIn",
  ]) {
    assert.match(css, new RegExp(`@keyframes ${name}\\b`), `${name} was removed`);
  }
});

// ── The transcript uses the height it has ──

test("the transcript is not capped at a fixed pixel height", () => {
  const rule = ruleFor(".chat-list");
  assert.ok(!/max-height:\s*380px/.test(rule),
    "a fixed cap wastes a tall panel and cramps a short one");
  assert.match(rule, /max-height:\s*min\(/);
  assert.match(rule, /overflow:\s*auto/, "the conversation scrolls, the composer does not");
});

// ── The mode selector states a permission model ──

test("modes read Ask, Plan, Agent in that order", () => {
  const labels = $$("#mode-selector .mode-btn .mode-name").map((b) => b.textContent.trim());
  assert.deepStrictEqual(labels, ["Ask", "Plan", "Agent"],
    "least to most permission, so the model is obvious from the order");
});

test("the mode menu says what each mode will and will not do", () => {
  const details = $$("#mode-selector .mode-btn .mode-detail").map((b) => b.textContent.trim());
  assert.match(details[0], /approve each change/i);
  assert.match(details[1], /nothing is written/i);
  assert.match(details[2], /without prompting/i);
});

test("the trigger shows the mode without opening the menu", () => {
  assert.ok(doc.getElementById("mode-trigger"), "the picker must be readable at a glance");
  assert.strictEqual(doc.getElementById("mode-label").textContent, "Ask");
  assert.ok(doc.getElementById("mode-selector").classList.contains("hidden"),
    "a menu that is open by default is not a menu");
});

test("the composer row carries @ and / beside +", () => {
  for (const id of ["attach-btn", "mention-btn", "slash-btn"]) {
    assert.ok(doc.getElementById(id), `${id} missing from the composer row`);
  }
});

test("Send is one small accent button, not a bar", () => {
  const send = doc.getElementById("send-btn");
  assert.ok(send.querySelector("svg"), "an arrow, not the word Send shouting across the row");
  assert.strictEqual(doc.getElementById("new-chat-btn"), null,
    "New Task lives in the sidebar; a second one here is the duplication again");
});

test("Agent is a relabelling, not a new value", () => {
  const values = $$("#mode-selector .mode-btn").map((b) => b.dataset.mode);
  assert.deepStrictEqual(values, ["ask", "plan", "auto"],
    "the stored value stays `auto` so nothing downstream changes");
});

test("each mode says what it will and will not do", () => {
  const byMode = Object.fromEntries(
    $$("#mode-selector .mode-btn").map((b) => [b.dataset.mode, b.getAttribute("title") || ""])
  );
  assert.match(byMode.ask, /approve each change/i);
  assert.match(byMode.plan, /nothing is written/i);
  assert.match(byMode.auto, /without prompting/i);
});

test("Ask is still the default", () => {
  const active = $$("#mode-selector .mode-btn.active").map((b) => b.dataset.mode);
  assert.deepStrictEqual(active, ["ask"],
    "the safe mode is the one you get without choosing");
});

test("the mode buttons stay a radiogroup", () => {
  assert.strictEqual(doc.getElementById("mode-selector").getAttribute("role"), "radiogroup");
  for (const btn of $$("#mode-selector .mode-btn")) {
    assert.strictEqual(btn.getAttribute("role"), "radio");
    assert.ok(btn.hasAttribute("aria-checked"));
  }
});

// ── The panel keeps its job, and only its job ──

test("the chat panel does not list sessions", () => {
  assert.strictEqual(doc.getElementById("session-list"), null,
    "sessions belong in the sidebar; duplicating them here is the noise this replaces");
});

test("the composer and its empty state are intact", () => {
  assert.ok(doc.getElementById("chat-input"), "the composer must survive the restyle");
  assert.ok(doc.getElementById("chat-empty-state"), "a new chat needs something to show");
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
