/**
 * Connection procedure tests.
 *
 * The reported failure: `gitpilot serve` was running and answering
 * `GET /api/health` with 200 — in 10.03s, because the Python side probed local
 * providers inline. The extension's whole connect procedure was a single probe
 * with a 3s deadline, so it gave up at 3s and rendered
 * "Offline — Could not reach http://127.0.0.1:8000".
 *
 * Clicking Reconnect repeated exactly the same 3s probe against exactly the
 * same slow server, which is why the button appeared to do nothing.
 *
 * These drive the real client against a real HTTP server, because the bug was
 * entirely in timing and a mocked fetch would have proved nothing.
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
  fire(value) { for (const l of [...this.listeners]) l(value); }
  dispose() { this.listeners = []; }
}

const vscodeStub = {
  EventEmitter: Emitter,
  workspace: { getConfiguration: () => ({ get: (_k, d) => d }) },
  window: { showErrorMessage() {}, showWarningMessage() {}, showInformationMessage() {} },
};

const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...args) {
  if (request === "vscode") return "vscode";
  return origResolve.call(this, request, ...args);
};
require.cache["vscode"] = { id: "vscode", filename: "vscode", loaded: true, exports: vscodeStub };

const {
  GitPilotApiClient,
  CONNECT_DEADLINES,
  AUTO_RECONNECT_MIN_MS,
} = require(path.join(OUT, "api/client.js"));

// ── A server whose answer speed we control ───────────────────────────────

/**
 * @param {() => number} delayMs how long /api/health takes to answer
 */
function startServer(delayMs) {
  const state = { hits: 0, delayMs };
  const server = http.createServer((req, res) => {
    state.hits++;
    setTimeout(() => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "healthy" }));
    }, state.delayMs);
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      resolve({
        url: `http://127.0.0.1:${server.address().port}`,
        state,
        close: () => new Promise((r) => server.close(r)),
      });
    });
  });
}

/** A port with nothing on it, so connections are refused rather than slow. */
async function deadUrl() {
  const s = await startServer(0);
  const url = s.url;
  await s.close();
  return url;
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

// ── The reported failure ─────────────────────────────────────────────────

test("a server that answers in 10s connects, instead of reading as offline", async () => {
  const srv = await startServer(10000);
  try {
    const client = new GitPilotApiClient(srv.url);
    const connected = await client.connect();

    assert.strictEqual(connected, true,
      "the server answered 200 — giving up at 3s reported a working backend as down");
    assert.strictEqual(client.state, "connected");
    client.disconnect();
  } finally {
    await srv.close();
  }
});

test("the first attempt is short, so a real outage still renders promptly", async () => {
  assert.strictEqual(CONNECT_DEADLINES[0], 3000,
    "offline has to appear quickly when it is true");
  assert.ok(CONNECT_DEADLINES[CONNECT_DEADLINES.length - 1] >= 20000,
    "and a warming-up server has to be given real time");
});

test("a refused connection fails fast rather than waiting out every deadline", async () => {
  const url = await deadUrl();
  const client = new GitPilotApiClient(url);

  const started = Date.now();
  const connected = await client.connect();
  const elapsed = Date.now() - started;

  assert.strictEqual(connected, false);
  assert.ok(elapsed < 3000,
    `waited ${elapsed}ms; no amount of waiting turns a refused connection into an answer`);
  client.disconnect();
});

// ── The failure says which remedy applies ────────────────────────────────

test("a slow server is reported as a timeout, not as unreachable", async () => {
  const srv = await startServer(10000);
  try {
    const client = new GitPilotApiClient(srv.url);
    const probe = await client.probe(200);

    assert.strictEqual(probe.ok, false);
    assert.strictEqual(probe.outcome, "timeout",
      "it was reached; 'Could not reach' sends the user to fix the working half");
    client.disconnect();
  } finally {
    await srv.close();
  }
});

test("nothing listening is reported as unreachable", async () => {
  const client = new GitPilotApiClient(await deadUrl());
  const probe = await client.probe(2000);

  assert.strictEqual(probe.ok, false);
  assert.strictEqual(probe.outcome, "unreachable");
  client.disconnect();
});

test("a healthy server reports how long it took", async () => {
  const srv = await startServer(0);
  try {
    const client = new GitPilotApiClient(srv.url);
    const probe = await client.probe();

    assert.strictEqual(probe.ok, true);
    assert.strictEqual(probe.outcome, "reachable");
    assert.ok(typeof probe.elapsedMs === "number");
    client.disconnect();
  } finally {
    await srv.close();
  }
});

test("the reason survives the failed connect, for the sidebar to explain", async () => {
  const client = new GitPilotApiClient(await deadUrl());
  await client.connect();

  assert.ok(client.lastProbe, "the UI cannot explain a failure it was not told about");
  assert.strictEqual(client.lastProbe.outcome, "unreachable");
  client.disconnect();
});

// ── Starting the server should be enough on its own ──────────────────────

test("the watcher connects by itself once the server appears", async () => {
  const srv = await startServer(0);
  const url = srv.url;
  await srv.close();

  const client = new GitPilotApiClient(url);
  assert.strictEqual(await client.connect(), false, "precondition: nothing there yet");

  client.startAutoReconnect();
  assert.strictEqual(client.isRetrying, true);

  // The server comes up, the way it does when someone runs `gitpilot serve`.
  const restarted = await new Promise((resolve) => {
    const s = http.createServer((_req, res) => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "healthy" }));
    });
    s.listen(Number(new URL(url).port), "127.0.0.1", () =>
      resolve({ close: () => new Promise((r) => s.close(r)) })
    );
  });

  try {
    const connected = await new Promise((resolve) => {
      const timer = setTimeout(() => resolve(false), 10000);
      client.onStateChange((state) => {
        if (state === "connected") { clearTimeout(timer); resolve(true); }
      });
    });

    assert.strictEqual(connected, true,
      "coming back to the sidebar to click Reconnect is a step the extension can take itself");
    assert.strictEqual(client.isRetrying, false, "the watcher stands down once connected");
  } finally {
    client.disconnect();
    await restarted.close();
  }
});

test("the watcher is armed once, however many times it is asked", async () => {
  const client = new GitPilotApiClient(await deadUrl());
  client.startAutoReconnect();
  client.startAutoReconnect();
  client.startAutoReconnect();

  assert.strictEqual(client.isRetrying, true);
  client.stopAutoReconnect();
  assert.strictEqual(client.isRetrying, false,
    "three starts must not leave two timers running");
  client.disconnect();
});

test("the first retry is soon, not a minute away", () => {
  assert.ok(AUTO_RECONNECT_MIN_MS <= 3000,
    "someone who just started the server should not wait long");
});

test("changing the server URL drops the watcher and the stale reason", async () => {
  const client = new GitPilotApiClient(await deadUrl());
  await client.connect();
  client.startAutoReconnect();

  client.setServerUrl("http://127.0.0.1:65530");

  assert.strictEqual(client.isRetrying, false, "it would keep probing the old address");
  assert.strictEqual(client.lastProbe, undefined,
    "a reason from the previous URL explains nothing about this one");
  client.disconnect();
});

// ── A live server that blinks must not blink the whole UI ────────────────

test("the periodic check tolerates a slow moment", async () => {
  const srv = await startServer(0);
  try {
    const client = new GitPilotApiClient(srv.url);
    await client.connect();

    // Busy for longer than the 3s connect probe allows, but well inside the
    // deadline the periodic check uses.
    srv.state.delayMs = 4000;
    const probe = await client.probe(10000);

    assert.strictEqual(probe.ok, true,
      "a live server that is briefly busy is not a dead one");
    client.disconnect();
  } finally {
    await srv.close();
  }
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
