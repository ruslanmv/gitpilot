/**
 * The in-extension agent tree stays deleted — Batch V4-H5.
 *
 * `src/local/` was a second agent implementation living inside the VS Code
 * extension: its own LLM client, its own tool engine, its own agent loop, its own
 * workspace scanner. The extension stopped driving it once the backend grew a real
 * engine, but it kept compiling, so it kept looking like a thing that worked. Six of
 * its eight modules had no importer at all; the two that did were dormant — a type
 * re-export and an adapter nobody constructed.
 *
 * Two implementations of the same idea is the state this whole programme exists to
 * leave, so the deletion is worth a test rather than a commit message: a file that
 * compiles is very easy to reintroduce, and much harder to notice.
 *
 * The event *types* survived, in `src/agent/agentEvents.ts` — the webview still
 * renders those shapes, and they were the only thing anything outside the tree used.
 *
 * Run with:  npm test        (from extensions/vscode)
 */
const assert = require("assert");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const SRC = path.join(ROOT, "src");

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

/** Every `.ts` file under `src/`, so the assertions cover the tree rather than a list. */
function sourceFiles(dir = SRC, found = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      sourceFiles(full, found);
    } else if (entry.name.endsWith(".ts")) {
      found.push(full);
    }
  }
  return found;
}

// ---------------------------------------------------------------------------

test("the src/local tree is gone", () => {
  assert.ok(
    !fs.existsSync(path.join(SRC, "local")),
    "src/local/ is back — it is a second agent implementation inside the extension",
  );
});

test("the adapter that wrapped it is gone", () => {
  assert.ok(
    !fs.existsSync(path.join(SRC, "platform", "vscodeAdapter.ts")),
    "vscodeAdapter.ts is back; it existed only to wrap src/local/fileOps",
  );
});

test("nothing imports from src/local", () => {
  const offenders = [];
  for (const file of sourceFiles()) {
    const text = fs.readFileSync(file, "utf8");
    // Matches `from "../local/x"`, `from "./local/x"`, and `require("…/local/x")`.
    if (/(from|require\()\s*["'][^"']*\/local\//.test(text)) {
      offenders.push(path.relative(ROOT, file));
    }
  }
  assert.deepStrictEqual(
    offenders,
    [],
    `these reach into the deleted tree: ${offenders.join(", ")}`,
  );
});

test("the event types survived, next to the bus that uses them", () => {
  const events = path.join(SRC, "agent", "agentEvents.ts");
  assert.ok(fs.existsSync(events), "src/agent/agentEvents.ts is missing");

  const text = fs.readFileSync(events, "utf8");
  for (const name of [
    "AgentTextEvent",
    "AgentToolStartEvent",
    "AgentToolResultEvent",
    "AgentFileWriteEvent",
    "AgentDoneEvent",
    "AgentErrorEvent",
  ]) {
    assert.ok(text.includes(`interface ${name}`), `${name} was lost in the move`);
  }
  assert.ok(text.includes("export type AgentEvent"), "the union was lost");
});

test("the event bus imports them from their new home", () => {
  const bus = fs.readFileSync(path.join(SRC, "agent", "agentEventBus.ts"), "utf8");
  assert.ok(
    bus.includes('from "./agentEvents"'),
    "agentEventBus.ts no longer imports the event union from agentEvents",
  );
});

test("the union is still re-exported, so its consumers did not have to change", () => {
  const bus = fs.readFileSync(path.join(SRC, "agent", "agentEventBus.ts"), "utf8");
  assert.ok(
    bus.includes("export type { AgentEvent }"),
    "agentEventBus.ts stopped re-exporting AgentEvent",
  );
});

// ---------------------------------------------------------------------------

let passed = 0;
let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed += 1;
  } catch (error) {
    console.log(`  ✗ ${name}`);
    console.log(`    ${error.message}`);
    failed += 1;
  }
}
console.log(`\n${passed}/${passed + failed} passed`);
process.exitCode = failed === 0 ? 0 : 1;
