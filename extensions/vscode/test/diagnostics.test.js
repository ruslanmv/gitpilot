/**
 * Diagnostics tests.
 *
 * Every hard-to-diagnose report in this project has had the same shape: the
 * interface said one thing and the truth was somewhere the developer could not
 * see from inside VS Code. The worst of them was a version mismatch — an
 * extension rebuilt from a fresh checkout talking to a backend still installed
 * from an old one. Everything looked connected; one feature failed for reasons
 * nothing on screen explained, and it cost several rounds to find.
 *
 * So these assert the two jobs: notice a mismatch without being asked, and be
 * able to print what actually happened.
 *
 * Run with:  npm test        (from extensions/vscode)
 *            make extension-test
 */
const assert = require("assert");
const http = require("http");
const Module = require("module");
const path = require("path");

const OUT = path.resolve(__dirname, "../out");

// ── vscode stub ──────────────────────────────────────────────────────────

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

const warnings = [];
let warningChoice;

const vscodeStub = {
  EventEmitter: Emitter,
  Uri: { parse: (s) => ({ toString: () => s }) },
  env: { openExternal: async () => true },
  commands: { executeCommand: async () => undefined },
  workspace: { getConfiguration: () => ({ get: (_k, d) => d }) },
  window: {
    showWarningMessage: (message, ...actions) => {
      warnings.push({ message, actions });
      return Promise.resolve(warningChoice);
    },
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

const { GitPilotApiClient } = require(path.join(OUT, "api/client.js"));
const { DiagnosticsService } = require(path.join(OUT, "services/diagnostics/DiagnosticsService.js"));

// ── A backend we can make say anything ───────────────────────────────────

function startBackend({ version, diagnostics, healthDelayMs = 0 } = {}) {
  const server = http.createServer((req, res) => {
    const send = (payload) => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify(payload));
    };

    if (req.url === "/api/health") {
      setTimeout(() => send(version === null ? { status: "healthy" } : { status: "healthy", version }), healthDelayMs);
      return;
    }
    if (req.url === "/api/diagnostics" && diagnostics) {
      send(diagnostics);
      return;
    }
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end("{}");
  });

  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () =>
      resolve({
        url: `http://127.0.0.1:${server.address().port}`,
        close: () => new Promise((r) => server.close(r)),
      })
    );
  });
}

function makeOutput() {
  const lines = [];
  return { lines, appendLine: (l) => lines.push(l), append() {}, show() {}, dispose() {} };
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── The mismatch that cost the most time ─────────────────────────────────

test("a backend from a different build is called out, unprompted", async () => {
  const backend = await startBackend({ version: "0.2.7" });
  warnings.length = 0;
  try {
    const client = new GitPilotApiClient(backend.url);
    const output = makeOutput();
    const diag = new DiagnosticsService(client, output, "0.2.8");

    await diag.checkVersion();

    assert.strictEqual(diag.backendVersion, "0.2.7");
    assert.ok(output.lines.some((l) => l.includes("mismatch")),
      "the log has to record it even if the notification is dismissed");
    assert.strictEqual(warnings.length, 1);
    assert.match(warnings[0].message, /backend is 0\.2\.7.*extension is 0\.2\.8/s);
    diag.dispose();
    client.disconnect();
  } finally {
    await backend.close();
  }
});

test("matching versions say nothing", async () => {
  const backend = await startBackend({ version: "0.2.8" });
  warnings.length = 0;
  try {
    const client = new GitPilotApiClient(backend.url);
    const diag = new DiagnosticsService(client, makeOutput(), "0.2.8");

    await diag.checkVersion();

    assert.strictEqual(warnings.length, 0, "a warning that always fires is ignored");
    diag.dispose();
    client.disconnect();
  } finally {
    await backend.close();
  }
});

test("a backend too old to report a version is itself the answer", async () => {
  const backend = await startBackend({ version: null });
  warnings.length = 0;
  try {
    const client = new GitPilotApiClient(backend.url);
    const output = makeOutput();
    const diag = new DiagnosticsService(client, output, "0.2.8");

    await diag.checkVersion();

    assert.ok(output.lines.some((l) => l.includes("predates 0.2.8")));
    assert.strictEqual(warnings.length, 1);
    diag.dispose();
    client.disconnect();
  } finally {
    await backend.close();
  }
});

test("the mismatch is reported once, not on every reconnect", async () => {
  const backend = await startBackend({ version: "0.2.7" });
  warnings.length = 0;
  try {
    const client = new GitPilotApiClient(backend.url);
    const diag = new DiagnosticsService(client, makeOutput(), "0.2.8");

    await diag.checkVersion();
    await diag.checkVersion();
    await diag.checkVersion();

    assert.strictEqual(warnings.length, 1,
      "a modal on every reconnect would be worse than the problem it reports");
    diag.dispose();
    client.disconnect();
  } finally {
    await backend.close();
  }
});

test("an unreachable backend is the connection layer's story, not a mismatch", async () => {
  const backend = await startBackend({ version: "0.2.8" });
  const url = backend.url;
  await backend.close();

  warnings.length = 0;
  const client = new GitPilotApiClient(url);
  const diag = new DiagnosticsService(client, makeOutput(), "0.2.8");

  await diag.checkVersion();

  assert.strictEqual(warnings.length, 0,
    "'the backend is version unknown' is not useful when nothing is listening");
  diag.dispose();
  client.disconnect();
});

// ── Every request is on the record ───────────────────────────────────────

test("requests are logged with their timing", async () => {
  const backend = await startBackend({ version: "0.2.8" });
  try {
    const client = new GitPilotApiClient(backend.url);
    const output = makeOutput();
    const diag = new DiagnosticsService(client, output, "0.2.8");

    await client.probe();

    assert.strictEqual(diag.history.length, 1);
    assert.strictEqual(diag.history[0].path, "/api/health");
    assert.strictEqual(diag.history[0].status, 200);
    assert.ok(output.lines.some((l) => l.includes("[api]") && l.includes("/api/health")),
      "the Output channel used to carry almost nothing about what was happening");
    diag.dispose();
    client.disconnect();
  } finally {
    await backend.close();
  }
});

test("a failed request is recorded with its reason", async () => {
  const backend = await startBackend({ version: "0.2.8" });
  const url = backend.url;
  await backend.close();

  const client = new GitPilotApiClient(url);
  const output = makeOutput();
  const diag = new DiagnosticsService(client, output, "0.2.8");

  await client.probe(1000);

  assert.strictEqual(diag.history.length, 1);
  assert.ok(diag.history[0].error, "a failure with no reason recorded is a failure you cannot debug");
  assert.ok(output.lines.some((l) => l.includes("✗")));
  diag.dispose();
  client.disconnect();
});

test("a slow request is marked as slow", async () => {
  const backend = await startBackend({ version: "0.2.8", healthDelayMs: 3200 });
  try {
    const client = new GitPilotApiClient(backend.url);
    const output = makeOutput();
    const diag = new DiagnosticsService(client, output, "0.2.8");

    await client.probe(10000);

    assert.ok(output.lines.some((l) => l.includes("🐢")),
      "the 10s stalls were only ever visible in the server's terminal");
    diag.dispose();
    client.disconnect();
  } finally {
    await backend.close();
  }
});

test("the history is bounded", async () => {
  const backend = await startBackend({ version: "0.2.8" });
  try {
    const client = new GitPilotApiClient(backend.url);
    const diag = new DiagnosticsService(client, makeOutput(), "0.2.8");

    for (let i = 0; i < 120; i++) {
      await client.probe();
    }

    assert.ok(diag.history.length <= 100, "a log that grows forever is a leak");
    diag.dispose();
    client.disconnect();
  } finally {
    await backend.close();
  }
});

// ── The report ───────────────────────────────────────────────────────────

test("the report answers the questions that took rounds to answer", async () => {
  const backend = await startBackend({
    version: "0.2.7",
    diagnostics: {
      version: "0.2.7",
      python: { version: "3.11.9", executable: "/home/x/.venv/bin/python" },
      package_path: "/home/x/.venv/lib/python3.11/site-packages/gitpilot",
      chat: { path: "crewai", ready: false, detail: "needs the agent runtime" },
      provider: { name: "ollama", model: "llama3:8b", endpoint: "http://localhost:11434/v1", configured: true },
      runtimes: { crewai: false, litellm: false, httpx: true },
    },
  });
  warnings.length = 0;
  try {
    const client = new GitPilotApiClient(backend.url);
    const diag = new DiagnosticsService(client, makeOutput(), "0.2.8");
    await client.probe();

    const report = await diag.report();

    assert.match(report, /extension\s+0\.2\.8/, "which extension");
    assert.match(report, /backend\s+0\.2\.7/, "which backend");
    assert.match(report, /MISMATCH/, "and that they disagree");
    assert.match(report, /pip install -e \./, "with the command that fixes it");
    assert.match(report, /can answer\s+no/, "whether a message can be answered at all");
    assert.match(report, /site-packages/,
      "where the backend is imported from — the reason a git pull changed nothing");
    assert.match(report, /crewai=no/, "which runtimes are actually present");
    assert.match(report, /\/api\/health/, "and what it has been doing");

    diag.dispose();
    client.disconnect();
  } finally {
    await backend.close();
  }
});

test("the report works when the backend cannot answer", async () => {
  const backend = await startBackend({ version: "0.2.8" });
  const url = backend.url;
  await backend.close();

  const client = new GitPilotApiClient(url);
  const diag = new DiagnosticsService(client, makeOutput(), "0.2.8");
  await client.connect();

  const report = await diag.report();

  assert.match(report, /did not answer/,
    "a diagnostics report that fails when things are broken is the one thing it must not do");
  assert.match(report, /unreachable/, "and it still carries the probe result");
  diag.dispose();
  client.disconnect();
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
