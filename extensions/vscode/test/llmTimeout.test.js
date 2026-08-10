/**
 * How long the extension waits for a model, and what it does when it stops.
 *
 * The report these come from: the web app answered every question and VS
 * Code answered none, against the same backend, at the same moment. The
 * server log settled it —
 *
 *     POST /api/chat/send took 21.26s (status=200)
 *
 * — while VS Code showed "timed out after 20000ms". Nothing was broken.
 * Inference had been given the deadline meant for an ordinary request,
 * and the web app had always allowed five minutes for the same call.
 *
 * The retry budget made it worse rather than better. A timeout carries no
 * HTTP status, so it slipped past the non-retryable-status guard and the
 * default two retries fired: three runs of a call the server was still
 * executing, queued behind each other on a single-threaded Ollama. That is
 * the 21s / 23s / 35s progression in the report — each attempt slower than
 * the one it was sent to rescue. And `/api/chat/send` appends to the
 * session before returning, so every duplicate that lands writes the
 * exchange to history twice.
 *
 * Run with:  npm test        (from extensions/vscode)
 */
const assert = require("assert");
const Module = require("module");
const path = require("path");

const OUT = path.resolve(__dirname, "../out");

// ── vscode stub ──────────────────────────────────────────────────────────

let configuredTimeout; // undefined => setting unset, use the default

class Emitter {
  constructor() { this.listeners = []; }
  get event() {
    return (fn) => {
      this.listeners.push(fn);
      return { dispose: () => { this.listeners = this.listeners.filter((l) => l !== fn); } };
    };
  }
  fire(v) { for (const l of [...this.listeners]) l(v); }
  dispose() { this.listeners = []; }
}

const vscodeStub = {
  EventEmitter: Emitter,
  Uri: { parse: (s) => ({ toString: () => s }) },
  env: { openExternal: async () => true },
  commands: { executeCommand: async () => undefined },
  workspace: {
    getConfiguration: () => ({
      get: (key, fallback) =>
        key === "llmTimeoutSeconds" && configuredTimeout !== undefined
          ? configuredTimeout
          : fallback,
    }),
  },
  window: {
    showWarningMessage: () => Promise.resolve(undefined),
    showErrorMessage: () => Promise.resolve(undefined),
    showInformationMessage: () => Promise.resolve(undefined),
  },
};

const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...args) {
  if (request === "vscode") return "vscode";
  return origResolve.call(this, request, ...args);
};
require.cache["vscode"] = { id: "vscode", filename: "vscode", loaded: true, exports: vscodeStub };

const {
  REQUEST_TIMEOUTS,
  MIN_LLM_TIMEOUT_SECONDS,
  llmTimeoutMs,
  llmRequestOptions,
  timeoutMessage,
} = require(path.join(OUT, "api", "client.js"));

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── the deadline ─────────────────────────────────────────────────────────

test("inference gets far longer than an ordinary request", () => {
  assert.ok(
    REQUEST_TIMEOUTS.llm > REQUEST_TIMEOUTS.default,
    "the default deadline is for a request; this one waits on a model"
  );
  assert.ok(
    REQUEST_TIMEOUTS.llm >= 300000,
    "the web app allows five minutes for the same call — anything less and " +
      "the two front ends disagree about whether the backend works"
  );
});

test("a 21s answer would have survived the new deadline", () => {
  // The exact number from the report, which the old 20s deadline missed by
  // a little over a second.
  configuredTimeout = undefined;
  assert.ok(21260 < llmTimeoutMs());
  assert.ok(21260 > REQUEST_TIMEOUTS.default, "and would have failed the old one");
});

test("the deadline is configurable", () => {
  configuredTimeout = 900;
  assert.strictEqual(llmTimeoutMs(), 900000, "a big model on CPU can need it");
  configuredTimeout = undefined;
  assert.strictEqual(llmTimeoutMs(), REQUEST_TIMEOUTS.llm, "unset falls back to the default");
});

test("a nonsensical setting cannot make things worse", () => {
  for (const bad of [0, -5, Number.NaN, undefined]) {
    configuredTimeout = bad;
    assert.strictEqual(
      llmTimeoutMs(),
      REQUEST_TIMEOUTS.llm,
      `${String(bad)} should fall back, not disable the wait`
    );
  }
  configuredTimeout = 1;
  assert.strictEqual(
    llmTimeoutMs(),
    MIN_LLM_TIMEOUT_SECONDS * 1000,
    "a one-second ceiling times out every model there is; clamp it"
  );
  configuredTimeout = undefined;
});

// ── the retry budget ─────────────────────────────────────────────────────

test("inference is never retried", () => {
  assert.strictEqual(
    llmRequestOptions().retries,
    0,
    "a timeout has no HTTP status, so it escapes the no-retry list and the " +
      "default budget re-sends a call the server is still running"
  );
});

test("the options carry the configured deadline, not the constant", () => {
  configuredTimeout = 600;
  assert.strictEqual(llmRequestOptions().timeoutMs, 600000);
  configuredTimeout = undefined;
});

// ── what the user is told ────────────────────────────────────────────────

test("an inference timeout explains itself and names the setting", () => {
  const msg = timeoutMessage("/api/chat/send", 300000);

  assert.match(msg, /300s|model/, "say what was being waited on");
  assert.match(msg, /gitpilot\.llmTimeoutSeconds/, "and the knob that changes it");
  assert.match(msg, /not retried/, "and that the answer is gone, not duplicated");
  assert.doesNotMatch(msg, /^Request to/, "the bare form describes only our own impatience");
});

for (const p of ["/api/chat/plan", "/api/chat/execute", "/api/chat/message"]) {
  test(`${p} gets the same explanation`, () => {
    assert.match(timeoutMessage(p, 300000), /gitpilot\.llmTimeoutSeconds/);
  });
}

test("a query-string does not hide the path", () => {
  assert.match(
    timeoutMessage("/api/chat/plan?x=1", 300000),
    /gitpilot\.llmTimeoutSeconds/,
    "matching has to tolerate a real URL"
  );
});

test("an ordinary endpoint keeps the plain message", () => {
  const msg = timeoutMessage("/api/settings", 10000);

  assert.match(msg, /timed out after 10000ms/);
  assert.doesNotMatch(msg, /llmTimeoutSeconds/, "settings are not slow because a model is thinking");
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
