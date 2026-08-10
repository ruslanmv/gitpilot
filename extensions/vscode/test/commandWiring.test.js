/**
 * Command wiring tests.
 *
 * The Revert button sat in the chat panel for months posting a message that
 * reached `vscode.commands.executeCommand("gitpilot.revertProposedChanges")`
 * — a command nobody had registered. Clicking it did nothing, silently, and
 * no test noticed because every piece in isolation was fine.
 *
 * So this suite checks the joins: every command the extension dispatches to
 * itself exists, and every command it advertises in package.json exists too.
 *
 * Run with:  npm test        (from extensions/vscode)
 *            make extension-test
 */
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const EXT_ROOT = path.resolve(__dirname, "..");
const SRC = path.join(EXT_ROOT, "src");

/** Every .ts file under src/, so nothing is missed by listing directories. */
function sources(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return sources(full);
    return entry.name.endsWith(".ts") ? [full] : [];
  });
}

const files = sources(SRC);
const code = files.map((f) => fs.readFileSync(f, "utf-8")).join("\n");
const pkg = JSON.parse(fs.readFileSync(path.join(EXT_ROOT, "package.json"), "utf-8"));

const matchAll = (re) => [...code.matchAll(re)].map((m) => m[1]);

/** Both registration paths: vscode.commands.registerCommand and the local helper. */
const registered = new Set(
  matchAll(/registerCommand\(\s*["'`](gitpilot\.[\w.]+)["'`]/g)
);

/** Commands the extension asks VS Code to run. */
const dispatched = new Set(
  matchAll(/executeCommand\(\s*["'`](gitpilot\.[\w.]+)["'`]/g)
);

const declared = new Set((pkg.contributes?.commands || []).map((c) => c.command));

/**
 * Commands VS Code provides for free. A webview view registered as
 * `gitpilot.chatView` gets `gitpilot.chatView.focus` without anyone
 * registering it, and the tree views get `.focus` and `.refresh` likewise.
 */
const BUILT_IN = new Set([
  "gitpilot.navView.focus",
  "gitpilot.sessionsView.focus",
  "gitpilot.skillsView.focus",
]);

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("every command the extension dispatches is registered", () => {
  const missing = [...dispatched].filter(
    (id) => !registered.has(id) && !BUILT_IN.has(id)
  );
  assert.deepStrictEqual(missing, [],
    "a command that is dispatched but never registered fails silently — the " +
    "button looks fine and does nothing");
});

test("every command declared in package.json is registered", () => {
  const missing = [...declared].filter((id) => !registered.has(id));
  assert.deepStrictEqual(missing, [],
    "a command in the palette that throws when picked is worse than one that " +
    "is not there");
});

// ── The two that were broken ──

test("Revert is wired", () => {
  assert.ok(registered.has("gitpilot.revertProposedChanges"),
    "the chat panel has had a Revert button all along with nothing behind it");
  assert.ok(declared.has("gitpilot.revertProposedChanges"),
    "reachable from the palette, not only from the button");
});

test("Rewind is wired, declared, and reachable from the panel", () => {
  assert.ok(registered.has("gitpilot.rewind"));
  assert.ok(declared.has("gitpilot.rewind"));

  const template = fs.readFileSync(
    path.join(SRC, "ui/webview/gitpilotWorkspaceTemplate.html"), "utf-8"
  );
  assert.match(template, /id="rewind-btn"/,
    "an undo nobody can find is not an undo");
  assert.match(template, /type: "REWIND"/);
});

test("the panel's REWIND message reaches the command", () => {
  const ext = fs.readFileSync(path.join(SRC, "extension.ts"), "utf-8");
  assert.match(ext, /case "REWIND":[\s\S]{0,160}gitpilot\.rewind/,
    "the webview message has to be handled, not just sent");
});

test("New Task starts a session, it does not only open the tab", () => {
  const nav = fs.readFileSync(path.join(SRC, "ui/webview/GitPilotNavView.ts"), "utf-8");
  const block = nav.slice(nav.indexOf('case "NEW_TASK"'), nav.indexOf('case "NEW_TASK"') + 220);

  assert.match(block, /gitpilot\.newSession/,
    "it dispatched gitpilot.openHome, which reveals the tab and nothing more — " +
    "so the control named for starting fresh landed you in the last conversation");
});

test("the panel reset reaches the webview", () => {
  const ext = fs.readFileSync(path.join(SRC, "extension.ts"), "utf-8");
  assert.match(ext, /resetPanel:[\s\S]{0,400}SESSION_RESET/);
  assert.match(ext, /resetPanel:[\s\S]{0,400}activeStreamAbort/,
    "a run still streaming would carry on writing into the new conversation");

  const template = fs.readFileSync(
    path.join(SRC, "ui/webview/gitpilotWorkspaceTemplate.html"), "utf-8"
  );
  assert.match(template, /msg\.type === "SESSION_RESET"/);
});

// ── The checkpoint client matches the backend ──

test("the checkpoint client calls the routes the backend serves", () => {
  const client = fs.readFileSync(path.join(SRC, "api/checkpointClient.ts"), "utf-8");
  for (const route of [
    "/api/sessions/${sessionId}/checkpoints",
    "/api/sessions/${sessionId}/checkpoint",
    "/api/sessions/${sessionId}/rewind",
  ]) {
    assert.ok(client.includes(route), `missing call to ${route}`);
  }
});

test("a checkpoint without files never offers to restore them", () => {
  const ext = fs.readFileSync(path.join(SRC, "extension.ts"), "utf-8");
  assert.match(ext, /has_files/,
    "a workspace too large to snapshot can only rewind the conversation, and " +
    "the confirmation has to say so");
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
