/**
 * Settings panel host tests.
 *
 * Loads the compiled panel with a stubbed `vscode` module and drives its
 * message handler directly, asserting the things a UI test cannot see: that
 * secrets never cross into the webview, that saves reach the backend in the
 * right order, and that risky actions ask first.
 *
 * Run with:  npm test        (from extensions/vscode)
 *            make extension-test
 */
const Module = require("module");
const assert = require("assert");
const path = require("path");

const OUT = path.resolve(__dirname, "../out");

// ── Stub the `vscode` module ──
const posted = [];
const shown = { warnings: [], info: [], errors: [] };
let warningAnswer;
const vscodeStub = {
  version: "1.110.0",
  ViewColumn: { One: 1 },
  ProgressLocation: { Notification: 15, Window: 10 },
  ConfigurationTarget: { Global: 1 },
  Uri: { joinPath: (...p) => ({ fsPath: path.join(...p.map((x) => x.fsPath || x)) }), parse: (u) => ({ toString: () => u, url: u }) },
  window: {
    createWebviewPanel: () => ({
      webview: {
        cspSource: "vscode-webview:",
        set html(v) { this._html = v; },
        get html() { return this._html; },
        onDidReceiveMessage: (cb) => { vscodeStub.__onMessage = cb; },
        postMessage: (m) => { posted.push(m); return Promise.resolve(true); },
      },
      onDidDispose: () => {},
      reveal: () => {},
    }),
    showWarningMessage: (msg, ...rest) => {
      shown.warnings.push(msg);
      return Promise.resolve(warningAnswer);
    },
    showInformationMessage: (msg) => { shown.info.push(msg); return Promise.resolve(undefined); },
    showErrorMessage: (msg) => { shown.errors.push(msg); return Promise.resolve(undefined); },
    showInputBox: (opts) => {
      inputPrompts.push(opts || {});
      return Promise.resolve(inputAnswers.shift());
    },
    withProgress: (_opts, fn) => fn({ report() {} }, { isCancellationRequested: false }),
    createOutputChannel: () => ({ appendLine() {}, append() {}, dispose() {} }),
    setStatusBarMessage: () => ({ dispose() {} }),
  },
  workspace: {
    workspaceFolders: undefined,
    getConfiguration: () => ({ get: (_k, d) => d, update: () => Promise.resolve() }),
  },
  commands: { executeCommand: () => Promise.resolve() },
  env: { openExternal: (u) => { vscodeStub.__opened = u; return Promise.resolve(true); }, clipboard: { writeText: (t) => { vscodeStub.__clipboard = t; return Promise.resolve(); } } },
  EventEmitter: class { constructor() { this.event = () => ({ dispose() {} }); } fire() {} dispose() {} },
};

let inputAnswers = [];
const inputPrompts = [];

const origResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...args) {
  if (request === "vscode") return "vscode";
  return origResolve.call(this, request, ...args);
};
require.cache["vscode"] = { id: "vscode", filename: "vscode", loaded: true, exports: vscodeStub };

const { GitPilotSettingsPanel } = require(path.join(OUT, "ui/webview/GitPilotSettingsPanel.js"));

// ── Fake backend ──
const SETTINGS = {
  provider: "claude",
  providers: ["openai", "claude", "watsonx", "ollama", "ollabridge"],
  openai: { api_key: "sk-openai-secret-value-1234", model: "gpt-4o", base_url: "" },
  claude: { api_key: "sk-ant-super-secret-A7X2", model: "claude-sonnet-4-5", base_url: "" },
  watsonx: { api_key: "wx-secret-9999", project_id: "proj-1", model_id: "ibm/granite-3-8b-instruct", base_url: "https://us-south.ml.cloud.ibm.com" },
  ollama: { base_url: "http://127.0.0.1:11434", model: "llama3" },
  ollabridge: { base_url: "https://ruslanmv-ollabridge.hf.space", model: "qwen2.5:1.5b", api_key: "" },
  openwebui: { base_url: "http://localhost:3000", model: "", api_key: "owui-secret-key-4444" },
  custom: { base_url: "https://inference.example.com/v1", model: "GLM-5.2", api_key: "sk-custom-secret-K9F1", headers: { "x-user": "me@example.com" } },
};

const calls = [];
const settingsClient = {
  getSettings: async () => { calls.push(["getSettings"]); return JSON.parse(JSON.stringify(SETTINGS)); },
  updateProviderConfig: async (p, c) => { calls.push(["updateProviderConfig", p, c]); },
  setProvider: async (p) => { calls.push(["setProvider", p]); },
  testProvider: async (r) => { calls.push(["testProvider", r]); return { health: "ok", configured: true, name: r.provider, source: "settings", model: "claude-sonnet-4-5" }; },
  listModelsCached: async (p, f) => { calls.push(["listModels", p, f]); return { models: ["m1", "m2"] }; },
  listModels: async (p) => ({ models: [] }),
  clearModelCache: () => {},
  listTopologies: async () => { calls.push(["listTopologies"]); return TOPOLOGIES; },
  getTopologyPreference: async () => { calls.push(["getTopologyPreference"]); return savedTopology; },
  setTopologyPreference: async (t) => { calls.push(["setTopologyPreference", t]); savedTopology = t === "auto" ? null : t; },
};

let savedTopology = "feature_builder";
const TOPOLOGIES = [
  { id: "default", name: "Default", description: "Route per request", category: "system", agents_used: [], execution_style: "single_task" },
  { id: "feature_builder", name: "Feature Builder", description: "Full pipeline", category: "pipeline", agents_used: ["explorer", "planner", "developer", "reviewer", "git_agent"], execution_style: "crew_pipeline" },
];

let healthy = true;
const client = {
  serverUrl: "http://127.0.0.1:8000",
  state: "connected",
  isConnected: true,
  health: async () => healthy,
  connect: async () => healthy,
  setServerUrl(u) { this.serverUrl = u; },
  ollaBridgePair: async (base, code) => { calls.push(["pair", base, code]); return { success: true, token: "device-token-secret-Z9Q1" }; },
  request: async (path, options) => { requestCalls.push([path, options || {}]); return {}; },
};
const requestCalls = [];

// ── MCP doubles ──
const mcpCalls = [];
const MCP_SERVERS = [
  {
    id: "mcp-postgre-server", installed: true, enabled: true,
    endpoint: "http://localhost:8080/mcp", description: "PostgreSQL tools",
    // The env var names where the token lives; the value is on the host only.
    auth_token_env: "MCP_POSTGRE_SERVER_TOKEN",
    tags: ["database"], tool_count: 3, is_known: true, orphan: false, source: "manual",
    tools: [
      { name: "postgres.list_tables", risk: "low", enabled: true, enabled_default: true, destructive: false, mutation: false, used_by: ["explorer"] },
      { name: "postgres.safe_select", risk: "low", enabled: true, enabled_default: true, destructive: false, mutation: false, used_by: ["coder"] },
      { name: "postgres.drop_table", risk: "high", enabled: false, enabled_default: false, destructive: true, mutation: false, used_by: [] },
    ],
  },
  {
    id: "mcp-milvus-server", installed: true, enabled: false,
    endpoint: "http://localhost:8082/mcp", description: "Vector search",
    auth_token_env: "", tags: ["vector"], tool_count: 0, tools: [],
    is_known: true, orphan: false, source: "manual",
  },
];

const mcpClient = {
  listServers: async () => { mcpCalls.push(["listServers"]); return JSON.parse(JSON.stringify(MCP_SERVERS)); },
  listCatalog: async () => { mcpCalls.push(["listCatalog"]); return [{ id: "mcp-inspector-server", slug: "inspector", description: "Validate MCP servers", endpoint: "http://x/mcp", tags: [], installed: false }]; },
  status: async () => { mcpCalls.push(["status"]); return { gateway_url: "http://localhost:4444", gateway_reachable: true, gateway_detail: "", plugin_enabled: true, servers_installed: 2, servers_enabled: 1, tools_advertised: 3 }; },
  searchRegistry: async (q) => { mcpCalls.push(["searchRegistry", q]); return { items: [{ id: "mcp-timescale", description: "TimescaleDB", endpoint: "http://z/mcp", tags: [], installed: false }], registry_url: "https://api.matrixhub.io" }; },
  installFromCatalog: async (id) => { mcpCalls.push(["installFromCatalog", id]); },
  installFromRegistry: async (id, url) => { mcpCalls.push(["installFromRegistry", id, url]); },
  installCustom: async (params) => { mcpCalls.push(["installCustom", params]); },
  uninstall: async (id) => { mcpCalls.push(["uninstall", id]); },
  setServerEnabled: async (id, enabled) => { mcpCalls.push(["setServerEnabled", id, enabled]); },
  setToolEnabled: async (id, tool, enabled) => { mcpCalls.push(["setToolEnabled", id, tool, enabled]); },
  testServer: async (id) => { mcpCalls.push(["testServer", id]); return { ok: true, tools: ["a", "b", "c"] }; },
  syncGateway: async () => { mcpCalls.push(["syncGateway"]); return { added: ["a", "b"] }; },
};

let forgeResult = { ok: true, gatewayUrl: "http://localhost:4444", mode: "container" };
const forgeInstaller = {
  isRunning: false,
  isHealthy: async () => true,
  install: async (onProgress) => {
    onProgress({ stage: "starting", message: "Starting…" });
    return forgeResult;
  },
};
const serverController = {
  isLocalUrl: () => true,
  isManaged: false,
  commandLine: "gitpilot serve --no-open",
  start: async () => ({ ok: true, serverUrl: "http://127.0.0.1:8001" }),
};

GitPilotSettingsPanel.open({
  extensionUri: { fsPath: path.resolve(__dirname, "..") },
  client,
  settingsClient,
  serverController,
  mcpClient,
  forgeInstaller,
});
// The panel's listener is registered as `(msg) => void this._onMessage(msg)`,
// so the promise is dropped. Drain the microtask/macrotask queues instead.
const settle = async () => {
  for (let i = 0; i < 12; i++) {
    await new Promise((r) => setImmediate(r));
  }
};
const send = async (msg) => {
  vscodeStub.__onMessage(msg);
  await settle();
};
const lastOfType = (t) => [...posted].reverse().find((m) => m.type === t);

// Every plaintext secret the fake backend holds.
const SECRETS = [
  "sk-openai-secret-value-1234",
  "sk-ant-super-secret-A7X2",
  "wx-secret-9999",
  "device-token-secret-Z9Q1",
  "owui-secret-key-4444",
  "sk-custom-secret-K9F1",
  "super-secret-mcp-token",
];

const tests = [];
const test = (n, f) => tests.push([n, f]);

test("overview load reports online and sends sanitized data", async () => {
  posted.length = 0;
  await send({ type: "LOAD_PROVIDER_OVERVIEW" });
  const state = posted.find((m) => m.type === "SERVER_STATE" && m.state === "online");
  assert.ok(state, "no online SERVER_STATE");
  assert.strictEqual(state.canStartLocally, true);
  assert.strictEqual(state.command, "gitpilot serve --no-open");

  const data = lastOfType("PROVIDER_SETUP_DATA").data;
  assert.strictEqual(data.activeProvider, "claude");
  assert.strictEqual(data.providers.length, 7, "every provider must be listed");
  assert.deepStrictEqual(
    data.providers.map((p) => p.name),
    ["ollabridge", "ollama", "claude", "openai", "watsonx", "openwebui", "custom"]
  );
});

test("no API key reaches the webview, in any message", () => {
  const serialized = JSON.stringify(posted);
  for (const secret of SECRETS) {
    assert.ok(!serialized.includes(secret), `secret leaked to webview: ${secret}`);
  }
});

test("keys are reported as a boolean plus a four-character hint", () => {
  const { configs } = lastOfType("PROVIDER_SETUP_DATA").data;
  assert.strictEqual(configs.claude.hasApiKey, true);
  assert.strictEqual(configs.claude.apiKeyHint, "••••A7X2");
  assert.strictEqual(configs.claude.api_key, undefined, "raw api_key field present");
  assert.strictEqual(configs.ollabridge.hasApiKey, false);
  assert.strictEqual(configs.ollabridge.apiKeyHint, undefined);
  assert.strictEqual(configs.watsonx.project_id, "proj-1", "non-secret fields must survive");
});

test("configured flags follow each provider's real requirements", () => {
  const by = Object.fromEntries(lastOfType("PROVIDER_SETUP_DATA").data.providers.map((p) => [p.name, p]));
  assert.strictEqual(by.ollabridge.configured, true, "OllaBridge must not require an API key");
  assert.strictEqual(by.ollama.configured, true, "Ollama must not require an API key");
  assert.strictEqual(by.claude.configured, true);
  assert.strictEqual(by.openai.configured, true);
  assert.strictEqual(by.watsonx.configured, true);
});

test("watsonx without a project ID counts as unconfigured", async () => {
  const saved = SETTINGS.watsonx.project_id;
  SETTINGS.watsonx.project_id = "";
  posted.length = 0;
  await send({ type: "LOAD_PROVIDER_OVERVIEW" });
  const by = Object.fromEntries(lastOfType("PROVIDER_SETUP_DATA").data.providers.map((p) => [p.name, p]));
  assert.strictEqual(by.watsonx.configured, false);
  SETTINGS.watsonx.project_id = saved;
});

test("saving writes the config before activating the provider", async () => {
  calls.length = 0;
  posted.length = 0;
  await send({ type: "SAVE_AND_ACTIVATE_PROVIDER", provider: "claude", requestId: 7, config: { model: "claude-opus-4-6", base_url: "", api_key: "sk-ant-new-key" } });

  const names = calls.map((c) => c[0]);
  assert.deepStrictEqual(names, ["updateProviderConfig", "setProvider", "getSettings"],
    "config must be written before the provider is activated: " + names);
  assert.deepStrictEqual(calls[0][2], { model: "claude-opus-4-6", base_url: "", api_key: "sk-ant-new-key" });
  assert.strictEqual(calls[1][1], "claude");

  const reply = lastOfType("PROVIDER_SAVE_RESULT");
  assert.strictEqual(reply.requestId, 7);
  assert.strictEqual(reply.ok, true);
  assert.ok(reply.data, "save must return refreshed data");
});

test("a blank key field keeps the stored key instead of clearing it", async () => {
  calls.length = 0;
  await send({ type: "SAVE_AND_ACTIVATE_PROVIDER", provider: "claude", requestId: 8, config: { model: "claude-sonnet-4-5", base_url: "", api_key: "   " } });
  const update = calls.find((c) => c[0] === "updateProviderConfig");
  assert.ok(!("api_key" in update[2]), "blank input would have cleared the stored key");
});

test("watsonx model goes to model_id, not model", async () => {
  calls.length = 0;
  await send({ type: "SAVE_AND_ACTIVATE_PROVIDER", provider: "watsonx", requestId: 9, config: { model: "ibm/granite-3-8b-instruct", project_id: "p2", base_url: "" } });
  const update = calls.find((c) => c[0] === "updateProviderConfig");
  assert.strictEqual(update[2].model_id, "ibm/granite-3-8b-instruct");
  assert.strictEqual(update[2].model, undefined);
  assert.strictEqual(update[2].project_id, "p2");
});

test("removing a key asks first and then clears it explicitly", async () => {
  calls.length = 0;
  warningAnswer = undefined; // user dismisses
  await send({ type: "REMOVE_PROVIDER_KEY", provider: "claude", requestId: 10 });
  assert.strictEqual(calls.length, 0, "key removed without confirmation");

  warningAnswer = "Remove key";
  await send({ type: "REMOVE_PROVIDER_KEY", provider: "claude", requestId: 11 });
  const update = calls.find((c) => c[0] === "updateProviderConfig");
  assert.strictEqual(update[2].api_key, "", "removal must send an empty key");
});

test("model discovery is lazy, per-provider, and forwards the request id", async () => {
  calls.length = 0;
  posted.length = 0;
  await send({ type: "LOAD_PROVIDER_MODELS", provider: "ollama", requestId: 12, force: true });
  assert.deepStrictEqual(calls[0], ["listModels", "ollama", true]);
  const reply = lastOfType("PROVIDER_MODELS_RESULT");
  assert.strictEqual(reply.requestId, 12);
  assert.deepStrictEqual(reply.models, ["m1", "m2"]);
});

test("a model listing failure answers rather than hanging", async () => {
  const orig = settingsClient.listModelsCached;
  settingsClient.listModelsCached = async () => { throw new Error("Request to /api/settings/models timed out after 15000ms"); };
  posted.length = 0;
  await send({ type: "LOAD_PROVIDER_MODELS", provider: "ollama", requestId: 13 });
  const reply = lastOfType("PROVIDER_MODELS_RESULT");
  assert.strictEqual(reply.requestId, 13);
  assert.deepStrictEqual(reply.models, []);
  assert.match(reply.error, /timed out/);
  settingsClient.listModelsCached = orig;
});

test("provider errors are translated into actionable text", async () => {
  const orig = settingsClient.testProvider;
  const err = new Error("HTTP 401: invalid x-api-key");
  err.status = 401;
  settingsClient.testProvider = async () => { throw err; };
  posted.length = 0;
  await send({ type: "TEST_PROVIDER", provider: "claude", requestId: 14, config: {} });
  const reply = lastOfType("PROVIDER_TEST_RESULT");
  assert.strictEqual(reply.ok, false);
  assert.match(reply.message, /rejected the credentials/);
  assert.match(reply.message, /Claude \(Anthropic\)/);
  settingsClient.testProvider = orig;
});

test("error text never echoes a key back to the webview", async () => {
  const orig = settingsClient.updateProviderConfig;
  settingsClient.updateProviderConfig = async () => {
    throw new Error('HTTP 400: {"api_key":"sk-ant-super-secret-A7X2"} rejected');
  };
  posted.length = 0;
  await send({ type: "SAVE_AND_ACTIVATE_PROVIDER", provider: "claude", requestId: 15, config: { api_key: "sk-ant-super-secret-A7X2" } });
  const reply = lastOfType("PROVIDER_SAVE_RESULT");
  assert.strictEqual(reply.ok, false);
  assert.ok(!reply.message.includes("sk-ant-super-secret-A7X2"), "secret echoed in error: " + reply.message);
  assert.match(reply.message, /redacted/);
  settingsClient.updateProviderConfig = orig;
});

test("a stale connected flag does not produce a false offline page", async () => {
  client.isConnected = true;
  healthy = true;
  posted.length = 0;
  await send({ type: "LOAD_PROVIDER_OVERVIEW" });
  assert.ok(lastOfType("PROVIDER_SETUP_DATA"), "healthy server reported as offline");
});

test("a genuinely down server yields an offline page with recovery info", async () => {
  healthy = false;
  client.isConnected = true;
  posted.length = 0;
  await send({ type: "LOAD_PROVIDER_OVERVIEW" });
  const state = lastOfType("SERVER_STATE");
  assert.strictEqual(state.state, "offline");
  assert.strictEqual(state.canStartLocally, true);
  assert.match(state.detail, /Could not reach/);
  assert.ok(!lastOfType("PROVIDER_SETUP_DATA"), "provider data sent while offline");
  healthy = true;
  client.isConnected = true;
});

test("starting the local server follows the port it actually bound", async () => {
  healthy = false;
  posted.length = 0;
  const startPromise = send({ type: "START_LOCAL_SERVER" });
  healthy = true;
  await startPromise;
  assert.ok(posted.some((m) => m.type === "SERVER_STATE" && m.state === "starting"), "no starting state");
  assert.ok(lastOfType("PROVIDER_SETUP_DATA"), "did not reload after starting");
});

test("pairing stores the token and activates OllaBridge", async () => {
  calls.length = 0;
  posted.length = 0;
  await send({ type: "PAIR_OLLABRIDGE", requestId: 16, code: "ABCD-1234", baseUrl: "https://ruslanmv-ollabridge.hf.space" });
  assert.deepStrictEqual(calls[0], ["pair", "https://ruslanmv-ollabridge.hf.space", "ABCD-1234"]);
  const update = calls.find((c) => c[0] === "updateProviderConfig");
  assert.strictEqual(update[2].api_key, "device-token-secret-Z9Q1");
  assert.ok(calls.some((c) => c[0] === "setProvider" && c[1] === "ollabridge"));

  const reply = lastOfType("OLLABRIDGE_PAIR_RESULT");
  assert.strictEqual(reply.ok, true);
  assert.ok(!JSON.stringify(posted).includes("device-token-secret-Z9Q1"), "device token leaked to webview");
});

test("an empty pairing code is rejected without a network call", async () => {
  calls.length = 0;
  posted.length = 0;
  await send({ type: "PAIR_OLLABRIDGE", requestId: 17, code: "  " });
  assert.strictEqual(calls.length, 0);
  assert.strictEqual(lastOfType("OLLABRIDGE_PAIR_RESULT").ok, false);
});

test("browser sign-in opens the link page, never a password prompt", async () => {
  await send({ type: "START_OLLABRIDGE_LOGIN", baseUrl: "https://ruslanmv-ollabridge.hf.space" });
  assert.match(vscodeStub.__opened.url, /^https:\/\//);
  assert.strictEqual(lastOfType("OLLABRIDGE_LOGIN_OPENED").url, "https://app.ollabridge.com/link");
});

test("sending a key to a remote plain-http server asks first", async () => {
  client.serverUrl = "http://gitpilot.example.com:8000";
  calls.length = 0;
  warningAnswer = undefined; // user declines
  await send({ type: "SAVE_AND_ACTIVATE_PROVIDER", provider: "claude", requestId: 18, config: { api_key: "sk-ant-x" } });
  assert.strictEqual(calls.length, 0, "key sent over plain http without consent");
  assert.ok(shown.warnings.some((w) => /unencrypted/.test(w)));
  assert.strictEqual(lastOfType("PROVIDER_SAVE_RESULT").ok, false);

  warningAnswer = "Send anyway";
  await send({ type: "SAVE_AND_ACTIVATE_PROVIDER", provider: "claude", requestId: 19, config: { api_key: "sk-ant-x" } });
  assert.ok(calls.some((c) => c[0] === "updateProviderConfig"));
  client.serverUrl = "http://127.0.0.1:8000";
});

test("a local http server does not trigger the warning", async () => {
  shown.warnings.length = 0;
  calls.length = 0;
  await send({ type: "SAVE_AND_ACTIVATE_PROVIDER", provider: "claude", requestId: 20, config: { api_key: "sk-ant-local" } });
  assert.strictEqual(shown.warnings.length, 0, "warned about a loopback address");
  assert.ok(calls.some((c) => c[0] === "updateProviderConfig"));
});

test("diagnostics carry no secrets", async () => {
  await send({ type: "COPY_DIAGNOSTICS" });
  for (const secret of SECRETS) {
    assert.ok(!vscodeStub.__clipboard.includes(secret));
  }
  assert.match(vscodeStub.__clipboard, /GitPilot server URL/);
});


test("Open WebUI counts as configured without an API key", async () => {
  const savedKey = SETTINGS.openwebui.api_key;
  SETTINGS.openwebui.api_key = "";
  posted.length = 0;
  await send({ type: "LOAD_PROVIDER_OVERVIEW" });
  const by = Object.fromEntries(lastOfType("PROVIDER_SETUP_DATA").data.providers.map((p) => [p.name, p]));
  assert.strictEqual(by.openwebui.configured, true, "a URL is enough for Open WebUI");
  SETTINGS.openwebui.api_key = savedKey;
});

test("Open WebUI without a URL is unconfigured", async () => {
  const saved = SETTINGS.openwebui.base_url;
  SETTINGS.openwebui.base_url = "";
  posted.length = 0;
  await send({ type: "LOAD_PROVIDER_OVERVIEW" });
  const by = Object.fromEntries(lastOfType("PROVIDER_SETUP_DATA").data.providers.map((p) => [p.name, p]));
  assert.strictEqual(by.openwebui.configured, false);
  SETTINGS.openwebui.base_url = saved;
});

test("custom endpoint needs both a URL and a model", async () => {
  posted.length = 0;
  await send({ type: "LOAD_PROVIDER_OVERVIEW" });
  let by = Object.fromEntries(lastOfType("PROVIDER_SETUP_DATA").data.providers.map((p) => [p.name, p]));
  assert.strictEqual(by.custom.configured, true);

  const saved = SETTINGS.custom.model;
  SETTINGS.custom.model = "";
  posted.length = 0;
  await send({ type: "LOAD_PROVIDER_OVERVIEW" });
  by = Object.fromEntries(lastOfType("PROVIDER_SETUP_DATA").data.providers.map((p) => [p.name, p]));
  assert.strictEqual(by.custom.configured, false, "a model id is required");
  SETTINGS.custom.model = saved;
});

test("custom headers reach the webview, keys do not", async () => {
  posted.length = 0;
  await send({ type: "LOAD_PROVIDER_OVERVIEW" });
  const { configs } = lastOfType("PROVIDER_SETUP_DATA").data;
  assert.deepStrictEqual(configs.custom.headers, { "x-user": "me@example.com" });
  assert.strictEqual(configs.custom.hasApiKey, true);
  assert.strictEqual(configs.custom.apiKeyHint, "••••K9F1");
  assert.strictEqual(configs.custom.api_key, undefined);
  assert.strictEqual(configs.openwebui.apiKeyHint, "••••4444");
  assert.ok(!JSON.stringify(posted).includes("sk-custom-secret-K9F1"));
  assert.ok(!JSON.stringify(posted).includes("owui-secret-key-4444"));
});

test("saving a custom endpoint replaces the header set wholesale", async () => {
  calls.length = 0;
  await send({ type: "SAVE_AND_ACTIVATE_PROVIDER", provider: "custom", requestId: 30, config: {
    model: "GLM-5.2",
    base_url: "https://inference.example.com/v1/chat/completions",
    api_key: "sk-new",
    headers: { "x-team": "blue", "  ": "ignored", "x-blank": "" },
  }});
  const update = calls.find((c) => c[0] === "updateProviderConfig");
  assert.strictEqual(update[1], "custom");
  assert.deepStrictEqual(update[2].headers, { "x-team": "blue", "x-blank": "" },
    "blank-named headers must be dropped; blank values are legitimate");
  assert.strictEqual(update[2].base_url, "https://inference.example.com/v1/chat/completions");
  assert.ok(calls.some((c) => c[0] === "setProvider" && c[1] === "custom"));
});

test("headers are left untouched when the page does not send them", async () => {
  calls.length = 0;
  await send({ type: "SAVE_AND_ACTIVATE_PROVIDER", provider: "custom", requestId: 31, config: { model: "GLM-5.2" } });
  const update = calls.find((c) => c[0] === "updateProviderConfig");
  assert.ok(!("headers" in update[2]), "an absent header set must not clear the stored one");
});

test("Open WebUI saves without a key and keeps the stored one", async () => {
  calls.length = 0;
  await send({ type: "SAVE_AND_ACTIVATE_PROVIDER", provider: "openwebui", requestId: 32, config: {
    model: "llama3.2:latest", base_url: "https://webui.example.com", api_key: "",
  }});
  const update = calls.find((c) => c[0] === "updateProviderConfig");
  assert.strictEqual(update[2].model, "llama3.2:latest");
  assert.strictEqual(update[2].base_url, "https://webui.example.com");
  assert.ok(!("api_key" in update[2]));
  assert.ok(calls.some((c) => c[0] === "setProvider" && c[1] === "openwebui"));
});


test("topology load returns the presets and the saved choice", async () => {
  posted.length = 0;
  calls.length = 0;
  await send({ type: "LOAD_TOPOLOGIES" });
  const data = lastOfType("TOPOLOGY_DATA");
  assert.strictEqual(data.offline, false);
  assert.strictEqual(data.selected, "feature_builder");
  assert.strictEqual(data.topologies.length, 2);
  assert.ok(calls.some((c) => c[0] === "listTopologies"));
  assert.ok(calls.some((c) => c[0] === "getTopologyPreference"));
});

test("topologies are unavailable, not stale, when the server is down", async () => {
  healthy = false;
  posted.length = 0;
  await send({ type: "LOAD_TOPOLOGIES" });
  const data = lastOfType("TOPOLOGY_DATA");
  assert.strictEqual(data.offline, true);
  assert.strictEqual(data.topologies, undefined, "sent a topology list while offline");
  healthy = true;
});

test("selecting a topology persists to the server, not just VS Code", async () => {
  calls.length = 0;
  posted.length = 0;
  await send({ type: "SET_TOPOLOGY", topology: "code_inspector", requestId: 40 });
  assert.deepStrictEqual(calls[0], ["setTopologyPreference", "code_inspector"],
    "the preference must reach the backend, which is what actually routes work");
  const reply = lastOfType("TOPOLOGY_SAVED");
  assert.strictEqual(reply.requestId, 40);
  assert.strictEqual(reply.ok, true);
});

test("'auto' clears the saved preference", async () => {
  await send({ type: "SET_TOPOLOGY", topology: "auto", requestId: 41 });
  assert.strictEqual(savedTopology, null);
  posted.length = 0;
  await send({ type: "LOAD_TOPOLOGIES" });
  assert.strictEqual(lastOfType("TOPOLOGY_DATA").selected, null);
});

test("a rejected topology is reported, not swallowed", async () => {
  const orig = settingsClient.setTopologyPreference;
  settingsClient.setTopologyPreference = async () => {
    throw new Error("HTTP 400: Unknown topology 'nope'.");
  };
  posted.length = 0;
  await send({ type: "SET_TOPOLOGY", topology: "nope", requestId: 42 });
  const reply = lastOfType("TOPOLOGY_SAVED");
  assert.strictEqual(reply.ok, false);
  assert.match(reply.message, /Unknown topology/);
  settingsClient.setTopologyPreference = orig;
});


// ── MCP servers ──

test("the MCP overview gathers servers, catalogue and status together", async () => {
  posted.length = 0;
  mcpCalls.length = 0;
  await send({ type: "LOAD_MCP_OVERVIEW" });

  const data = lastOfType("MCP_DATA");
  assert.strictEqual(data.offline, false);
  assert.strictEqual(data.servers.length, 2);
  assert.strictEqual(data.catalog.length, 1);
  assert.strictEqual(data.status.gateway_reachable, true);
  assert.deepStrictEqual(
    mcpCalls.map((c) => c[0]).sort(),
    ["listCatalog", "listServers", "status"]
  );
});

test("a failing status does not hide the server list", async () => {
  const orig = mcpClient.status;
  mcpClient.status = async () => { throw new Error("gateway probe timed out"); };
  posted.length = 0;
  await send({ type: "LOAD_MCP_OVERVIEW" });

  const data = lastOfType("MCP_DATA");
  assert.strictEqual(data.offline, false, "a slow gateway probe must not blank the page");
  assert.strictEqual(data.servers.length, 2);
  assert.strictEqual(data.status, undefined);
  mcpClient.status = orig;
});

test("an offline backend yields no stale server list", async () => {
  healthy = false;
  posted.length = 0;
  await send({ type: "LOAD_MCP_OVERVIEW" });

  const data = lastOfType("MCP_DATA");
  assert.strictEqual(data.offline, true);
  assert.strictEqual(data.servers, undefined);
  healthy = true;
});

test("server summaries carry tools but not full schemas", async () => {
  posted.length = 0;
  await send({ type: "LOAD_MCP_OVERVIEW" });
  const server = lastOfType("MCP_DATA").servers[0];

  assert.strictEqual(server.id, "mcp-postgre-server");
  assert.strictEqual(server.enabled_tool_count, 2, "the count the overview renders");
  assert.strictEqual(server.tools.length, 3);
  for (const tool of server.tools) {
    assert.deepStrictEqual(
      Object.keys(tool).sort(),
      ["destructive", "enabled", "mutation", "name", "risk", "used_by"]
    );
  }
});

test("the token env var is forwarded, and no token value exists to leak", async () => {
  posted.length = 0;
  await send({ type: "LOAD_MCP_OVERVIEW" });
  const server = lastOfType("MCP_DATA").servers[0];
  assert.strictEqual(server.auth_token_env, "MCP_POSTGRE_SERVER_TOKEN");
  assert.ok(!JSON.stringify(posted).includes("super-secret-mcp-token"));
});

test("enabling a server persists and reloads", async () => {
  mcpCalls.length = 0;
  posted.length = 0;
  await send({ type: "SET_MCP_SERVER_ENABLED", serverId: "mcp-milvus-server", enabled: true, requestId: 50 });

  assert.deepStrictEqual(mcpCalls[0], ["setServerEnabled", "mcp-milvus-server", true]);
  const reply = lastOfType("MCP_ACTION_RESULT");
  assert.strictEqual(reply.requestId, 50);
  assert.strictEqual(reply.ok, true);
  assert.match(reply.message, /available to the agents/);
  assert.ok(lastOfType("MCP_DATA"), "the page must be refreshed from the backend");
});

test("a low-risk tool toggles without a prompt", async () => {
  mcpCalls.length = 0;
  shown.warnings.length = 0;
  await send({ type: "SET_MCP_TOOL_ENABLED", serverId: "mcp-postgre-server", tool: "postgres.list_tables", enabled: true, requestId: 51 });

  assert.deepStrictEqual(mcpCalls[0], ["setToolEnabled", "mcp-postgre-server", "postgres.list_tables", true]);
  assert.strictEqual(shown.warnings.length, 0);
});

test("enabling a destructive tool asks first, and a refusal leaves it off", async () => {
  mcpCalls.length = 0;
  posted.length = 0;
  warningAnswer = undefined; // user declines
  await send({ type: "SET_MCP_TOOL_ENABLED", serverId: "mcp-postgre-server", tool: "postgres.drop_table", enabled: true, requestId: 52 });

  assert.ok(!mcpCalls.some((c) => c[0] === "setToolEnabled"), "a destructive tool was enabled without consent");
  assert.ok(shown.warnings.some((w) => /destroy data/.test(w)));
  assert.strictEqual(lastOfType("MCP_ACTION_RESULT").ok, false);
});

test("enabling a destructive tool proceeds once confirmed", async () => {
  mcpCalls.length = 0;
  warningAnswer = "Enable anyway";
  await send({ type: "SET_MCP_TOOL_ENABLED", serverId: "mcp-postgre-server", tool: "postgres.drop_table", enabled: true, requestId: 53 });
  assert.deepStrictEqual(mcpCalls[0], ["setToolEnabled", "mcp-postgre-server", "postgres.drop_table", true]);
});

test("disabling a destructive tool never prompts", async () => {
  mcpCalls.length = 0;
  shown.warnings.length = 0;
  await send({ type: "SET_MCP_TOOL_ENABLED", serverId: "mcp-postgre-server", tool: "postgres.drop_table", enabled: false, requestId: 54 });

  assert.strictEqual(shown.warnings.length, 0, "removing a capability is never risky");
  assert.deepStrictEqual(mcpCalls[0], ["setToolEnabled", "mcp-postgre-server", "postgres.drop_table", false]);
});

test("destructive naming is recognised across separators", async () => {
  for (const tool of ["db.delete_rows", "truncate-table", "storage.destroy", "removeIndex"]) {
    mcpCalls.length = 0;
    warningAnswer = undefined;
    shown.warnings.length = 0;
    await send({ type: "SET_MCP_TOOL_ENABLED", serverId: "s", tool, enabled: true, requestId: 55 });
    const prompted = shown.warnings.length > 0;
    // "removeIndex" has no separator, so it is not expected to match; the
    // others must.
    if (tool === "removeIndex") {
      continue;
    }
    assert.ok(prompted, `${tool} should have prompted`);
  }
});

test("attaching from the bundled catalogue leaves the server disabled", async () => {
  mcpCalls.length = 0;
  posted.length = 0;
  await send({ type: "INSTALL_MCP_SERVER", entryId: "mcp-inspector-server", source: "bundled", requestId: 56 });

  assert.deepStrictEqual(mcpCalls[0], ["installFromCatalog", "mcp-inspector-server"]);
  const reply = lastOfType("MCP_ACTION_RESULT");
  assert.strictEqual(reply.ok, true);
  assert.match(reply.message, /left disabled/,
    "gaining a capability must not be a side effect of attaching");
});

test("attaching from a registry uses the registry route", async () => {
  mcpCalls.length = 0;
  await send({ type: "INSTALL_MCP_SERVER", entryId: "mcp-timescale", source: "registry", requestId: 57 });
  assert.deepStrictEqual(mcpCalls[0], ["installFromRegistry", "mcp-timescale", undefined]);
});

test("a failed attach reports rather than silently doing nothing", async () => {
  const orig = mcpClient.installFromCatalog;
  mcpClient.installFromCatalog = async () => { throw new Error("HTTP 404: not in catalog"); };
  posted.length = 0;
  await send({ type: "INSTALL_MCP_SERVER", entryId: "nope", source: "bundled", requestId: 58 });

  const reply = lastOfType("MCP_ACTION_RESULT");
  assert.strictEqual(reply.ok, false);
  assert.match(reply.message, /not in catalog/);
  mcpClient.installFromCatalog = orig;
});

test("registry search forwards results and the registry it used", async () => {
  posted.length = 0;
  await send({ type: "SEARCH_MCP_REGISTRY", query: "postgres", requestId: 59 });

  const reply = lastOfType("MCP_REGISTRY_RESULT");
  assert.strictEqual(reply.requestId, 59);
  assert.strictEqual(reply.items.length, 1);
  assert.strictEqual(reply.registryUrl, "https://api.matrixhub.io");
});

test("a registry outage answers rather than throwing", async () => {
  const orig = mcpClient.searchRegistry;
  mcpClient.searchRegistry = async () => { throw new Error("Request to /api/mcp/registry/search timed out after 20000ms"); };
  posted.length = 0;
  await send({ type: "SEARCH_MCP_REGISTRY", query: "x", requestId: 60 });

  const reply = lastOfType("MCP_REGISTRY_RESULT");
  assert.deepStrictEqual(reply.items, []);
  assert.match(reply.error, /timed out/);
  mcpClient.searchRegistry = orig;
});

test("detaching asks first and a refusal leaves the server attached", async () => {
  mcpCalls.length = 0;
  warningAnswer = undefined;
  await send({ type: "UNINSTALL_MCP_SERVER", serverId: "mcp-postgre-server", requestId: 61 });
  assert.strictEqual(mcpCalls.length, 0, "detached without confirmation");
});

test("detaching proceeds once confirmed", async () => {
  mcpCalls.length = 0;
  posted.length = 0;
  warningAnswer = "Detach";
  await send({ type: "UNINSTALL_MCP_SERVER", serverId: "mcp-postgre-server", requestId: 62 });

  assert.deepStrictEqual(mcpCalls[0], ["uninstall", "mcp-postgre-server"]);
  assert.strictEqual(lastOfType("MCP_ACTION_RESULT").ok, true);
  assert.ok(lastOfType("MCP_DATA"));
});

test("testing a server reports tool count on success", async () => {
  posted.length = 0;
  await send({ type: "TEST_MCP_SERVER", serverId: "mcp-postgre-server", requestId: 63 });

  const reply = lastOfType("MCP_ACTION_RESULT");
  assert.strictEqual(reply.ok, true);
  assert.match(reply.message, /3 tool\(s\)/);
});

test("a server that does not respond reports the backend's reason", async () => {
  const orig = mcpClient.testServer;
  mcpClient.testServer = async () => ({ ok: false, detail: "connection refused" });
  posted.length = 0;
  await send({ type: "TEST_MCP_SERVER", serverId: "mcp-milvus-server", requestId: 64 });

  const reply = lastOfType("MCP_ACTION_RESULT");
  assert.strictEqual(reply.ok, false);
  assert.strictEqual(reply.message, "connection refused");
  mcpClient.testServer = orig;
});

test("adding a custom server never asks for a token value", async () => {
  mcpCalls.length = 0;
  inputAnswers = ["my-server", "http://localhost:9000/mcp", "MY_SERVER_TOKEN"];
  await send({ type: "ADD_CUSTOM_MCP_SERVER", requestId: 65 });

  const call = mcpCalls.find((c) => c[0] === "installCustom");
  assert.ok(call, "custom install not attempted");
  assert.deepStrictEqual(call[1], {
    id: "my-server",
    endpoint: "http://localhost:9000/mcp",
    authTokenEnv: "MY_SERVER_TOKEN",
  });
  // Every prompt must name the variable, never request the secret itself.
  assert.ok(inputPrompts.some((p) => /do not paste the token here/i.test(p.prompt || "")));
});

test("cancelling the custom-server wizard installs nothing", async () => {
  mcpCalls.length = 0;
  inputAnswers = ["my-server", undefined];
  await send({ type: "ADD_CUSTOM_MCP_SERVER", requestId: 66 });
  assert.ok(!mcpCalls.some((c) => c[0] === "installCustom"));
});

test("installing Forge points GitPilot at it and refreshes", async () => {
  forgeResult = { ok: true, gatewayUrl: "http://localhost:4444", mode: "container" };
  posted.length = 0;
  requestCalls.length = 0;
  await send({ type: "INSTALL_MCP_FORGE" });

  const saved = requestCalls.find((c) => c[0] === "/api/mcp/gateway");
  assert.ok(saved, "the gateway URL must be saved or the agents keep using the old one");
  assert.deepStrictEqual(JSON.parse(saved[1].body), { url: "http://localhost:4444", auth_mode: "auto" });

  const progress = [...posted].reverse().find((m) => m.type === "MCP_FORGE_PROGRESS");
  assert.strictEqual(progress.stage, "done");
  assert.ok(lastOfType("MCP_DATA"));
});

test("a failed Forge install reports the reason and does not save a URL", async () => {
  forgeResult = { ok: false, reason: "Docker is not running.", hint: "https://docs.docker.com/get-docker/" };
  posted.length = 0;
  requestCalls.length = 0;
  await send({ type: "INSTALL_MCP_FORGE" });

  assert.ok(!requestCalls.some((c) => c[0] === "/api/mcp/gateway"));
  const progress = [...posted].reverse().find((m) => m.type === "MCP_FORGE_PROGRESS");
  assert.strictEqual(progress.stage, "failed");
  assert.match(progress.message, /Docker is not running/);
});

test("gateway sync reports how many servers it added", async () => {
  posted.length = 0;
  await send({ type: "SYNC_MCP_GATEWAY", requestId: 67 });
  const reply = lastOfType("MCP_ACTION_RESULT");
  assert.strictEqual(reply.ok, true);
  assert.match(reply.message, /2 server\(s\) added/);
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
