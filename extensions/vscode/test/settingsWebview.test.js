/**
 * Settings webview tests.
 *
 * Loads the real template into jsdom, plays the extension host's side of the
 * message protocol, and asserts the pages behave — two-level navigation, one
 * provider form at a time, masked keys, and the MCP tool switches.
 *
 * Run with:  npm test        (from extensions/vscode)
 *            make extension-test
 */
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM } = require("jsdom");

const EXT_ROOT = path.resolve(__dirname, "..");
const TEMPLATE = path.join(EXT_ROOT, "src/ui/webview/gitpilotSettingsTemplate.html");
const catalog = require(path.join(EXT_ROOT, "out/ui/webview/providerCatalog.js"));

let html = fs.readFileSync(TEMPLATE, "utf-8");
html = html
  .replace(/__CSP__/g, "")
  .replace(/__NONCE__/g, "n")
  .replace(/__VERSION__/g, require(path.join(EXT_ROOT, "package.json")).version)
  .replace(/__PROVIDER_CATALOG__/g, JSON.stringify(catalog.PROVIDER_CATALOG))
  .replace(/__PROVIDER_ORDER__/g, JSON.stringify(catalog.PROVIDER_ORDER))
  .replace(
    /__OLLABRIDGE_DEFAULTS__/g,
    JSON.stringify({
      cloud: catalog.OLLABRIDGE_CLOUD_URL,
      local: catalog.OLLABRIDGE_LOCAL_URL,
      link: catalog.OLLABRIDGE_LINK_URL,
    })
  );

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

const SETUP = {
  activeProvider: "claude",
  serverUrl: "http://127.0.0.1:8000",
  ollabridgeMode: "cloud",
  providers: [
    { name: "ollabridge", label: "OllaBridge Cloud", description: "Free hosted models", active: false, configured: true },
    { name: "ollama", label: "Ollama (Local)", description: "Run models locally", active: false, configured: true },
    { name: "claude", label: "Claude (Anthropic)", description: "Anthropic models", model: "claude-sonnet-4-5", active: true, configured: true },
    { name: "openai", label: "OpenAI", description: "GPT models", active: false, configured: false },
    { name: "watsonx", label: "IBM watsonx", description: "Granite models", active: false, configured: false },
    { name: "openwebui", label: "Open WebUI", description: "Your Open WebUI instance", active: false, configured: true },
    { name: "custom", label: "Custom endpoint", description: "Any OpenAI-compatible gateway", active: false, configured: false },
  ],
  configs: {
    claude: { model: "claude-sonnet-4-5", base_url: "", hasApiKey: true, apiKeyHint: "••••A7X2" },
    openai: { model: "gpt-4o", base_url: "", hasApiKey: false },
    watsonx: { model: "ibm/granite-3-8b-instruct", base_url: "", project_id: "", hasApiKey: false },
    ollama: { model: "llama3", base_url: "http://127.0.0.1:11434", hasApiKey: false },
    ollabridge: { model: "qwen2.5:1.5b", base_url: catalog.OLLABRIDGE_CLOUD_URL, hasApiKey: false },
    openwebui: { model: "", base_url: "http://localhost:3000", hasApiKey: false },
    custom: { model: "GLM-5.2", base_url: "https://inference.example.com/v1", hasApiKey: true, apiKeyHint: "••••K9F1", headers: { "x-user": "me@example.com" } },
  },
};

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("settings section starts on General and asks the host for settings", () => {
  assert.ok(sent.some((m) => m.type === "GET_SETTINGS"), "GET_SETTINGS not sent");
  assert.ok($("#page-general").classList.contains("active"));
});

test("opening AI Providers requests the overview", async () => {
  $$(".nav-item").find((b) => b.dataset.page === "provider").click();
  await flush();
  assert.ok($("#page-provider").classList.contains("active"));
  assert.ok(sent.some((m) => m.type === "LOAD_PROVIDER_OVERVIEW"));
});

test("offline server shows recovery actions, not provider forms", async () => {
  toHost({ type: "SERVER_STATE", state: "offline", serverUrl: "http://127.0.0.1:8000", canStartLocally: true, command: "gitpilot serve --no-open", detail: "Could not reach it." });
  await flush();
  assert.ok(!$("#server-offline").classList.contains("hidden"), "offline panel hidden");
  assert.ok($("#provider-lists").classList.contains("hidden"), "provider list still visible");
  assert.ok(!$("#btn-start-server").classList.contains("hidden"), "start button hidden");
  assert.strictEqual($("#server-badge-text").textContent, "Not connected");
  assert.match($("#offline-command").textContent, /gitpilot serve --no-open/);
  assert.strictEqual($("#offline-detail").textContent, "Could not reach it.");
});

test("remote server offers no local start button", async () => {
  toHost({ type: "SERVER_STATE", state: "offline", serverUrl: "https://gitpilot.example.com", canStartLocally: false });
  await flush();
  assert.ok($("#btn-start-server").classList.contains("hidden"), "start button shown for remote");
});

test("start / reconnect / change-url buttons message the host", async () => {
  toHost({ type: "SERVER_STATE", state: "offline", serverUrl: "http://127.0.0.1:8000", canStartLocally: true });
  await flush();
  $("#btn-start-server").click();
  $("#btn-offline-reconnect").click();
  $("#btn-offline-change").click();
  $("#btn-copy-diag").click();
  assert.ok(lastOfType("START_LOCAL_SERVER"));
  assert.ok(lastOfType("RECONNECT_SERVER"));
  assert.ok(lastOfType("CHANGE_SERVER_URL"));
  assert.ok(lastOfType("COPY_DIAGNOSTICS"));
});

test("online overview lists the active provider apart from the rest", async () => {
  toHost({ type: "SERVER_STATE", state: "online", serverUrl: "http://127.0.0.1:8000", canStartLocally: true });
  toHost({ type: "PROVIDER_SETUP_DATA", data: SETUP });
  await flush();

  assert.ok($("#server-offline").classList.contains("hidden"));
  assert.strictEqual($("#server-badge-text").textContent, "Connected");

  const active = $$("#active-provider-list .provider-row");
  const available = $$("#available-provider-list .provider-row");
  assert.strictEqual(active.length, 1, "expected exactly one active provider");
  assert.match(active[0].textContent, /Claude \(Anthropic\)/);
  assert.match(active[0].textContent, /claude-sonnet-4-5/);
  assert.match(active[0].textContent, /Active/);

  const labels = available.map((r) => r.querySelector(".provider-name").textContent);
  assert.deepStrictEqual(
    labels,
    ["OllaBridge Cloud", "Ollama (Local)", "OpenAI", "IBM watsonx", "Open WebUI", "Custom endpoint"],
    "every provider must stay reachable, including OpenAI and watsonx");
});

test("overview shows no provider form", () => {
  assert.strictEqual($("#provider-overview input[type=password]"), null, "API key input on overview");
  assert.strictEqual($("#provider-overview select"), null, "model dropdown on overview");
  assert.ok($("#provider-detail").classList.contains("hidden"));
});

test("clicking Claude opens only the Claude page", async () => {
  $$("#active-provider-list .provider-row")[0].click();
  await flush();

  assert.ok($("#provider-overview").classList.contains("hidden"), "overview still visible");
  const detail = $("#provider-detail");
  assert.ok(!detail.classList.contains("hidden"));
  assert.strictEqual(detail.querySelector(".detail-title").textContent, "Claude (Anthropic)");
  // Exactly one provider form, and it is Claude's.
  assert.strictEqual(detail.querySelectorAll(".detail-title").length, 1);
  assert.ok(!/OllaBridge|Ollama|watsonx/.test(detail.querySelector(".detail-title").textContent));
});

test("Claude page masks the stored key and never receives it", () => {
  const detail = $("#provider-detail");
  const hint = [...detail.querySelectorAll(".form-hint")].map((n) => n.textContent).join(" ");
  assert.match(hint, /API key configured: ••••A7X2/);
  assert.match(hint, /Leave the field empty to keep it/);
  assert.match(hint, /subscription does not include API access/,
    "must not imply a Claude.ai subscription grants API access");

  const input = detail.querySelector("input[type=password]");
  assert.strictEqual(input.value, "", "stored key must never be prefilled");
  assert.match(input.placeholder, /keep the current key/);
});

test("model discovery is requested for the open provider only", async () => {
  await flush();
  const loads = sent.filter((m) => m.type === "LOAD_PROVIDER_MODELS");
  assert.ok(loads.length >= 1);
  assert.ok(loads.every((m) => m.provider === "claude"),
    "discovery leaked to other providers: " + loads.map((m) => m.provider).join(","));
});

test("Show button reveals only what the user typed", () => {
  const detail = $("#provider-detail");
  const input = detail.querySelector("input[type=password]");
  const show = [...detail.querySelectorAll("button")].find((b) => b.textContent === "Show");
  input.value = "typed-key";
  show.click();
  assert.strictEqual(detail.querySelector(".input-group input").type, "text");
  assert.strictEqual(detail.querySelector(".input-group input").value, "typed-key");
  show.click();
});

test("Save and activate sends the typed key and the chosen model", async () => {
  const detail = $("#provider-detail");
  const loadReq = sent.filter((m) => m.type === "LOAD_PROVIDER_MODELS").pop();
  toHost({ type: "PROVIDER_MODELS_RESULT", requestId: loadReq.requestId, provider: "claude", models: ["claude-opus-4-6", "claude-sonnet-4-5"] });
  await flush();

  detail.querySelector("select").value = "claude-opus-4-6";
  detail.querySelector("input[type=password]").value = "sk-ant-typed";

  [...detail.querySelectorAll("button")].find((b) => b.textContent === "Save and activate").click();
  const save = lastOfType("SAVE_AND_ACTIVATE_PROVIDER");
  assert.strictEqual(save.provider, "claude");
  assert.strictEqual(save.config.model, "claude-opus-4-6");
  assert.strictEqual(save.config.api_key, "sk-ant-typed");
});

test("a successful save re-renders and keeps the confirmation visible", async () => {
  const save = lastOfType("SAVE_AND_ACTIVATE_PROVIDER");
  toHost({ type: "PROVIDER_SAVE_RESULT", requestId: save.requestId, provider: "claude", ok: true, message: "Claude (Anthropic) is now the active provider.", data: SETUP });
  await flush();
  const result = $("#provider-result");
  assert.ok(result, "result area missing after re-render");
  assert.strictEqual(result.textContent, "Claude (Anthropic) is now the active provider.");
  assert.ok(result.classList.contains("ok"));
});

test("a failed test shows the provider's error", async () => {
  const detail = $("#provider-detail");
  [...detail.querySelectorAll("button")].find((b) => b.textContent === "Test connection").click();
  const req = lastOfType("TEST_PROVIDER");
  assert.ok($("#provider-result").classList.contains("info"), "no busy state while testing");
  toHost({ type: "PROVIDER_TEST_RESULT", requestId: req.requestId, provider: "claude", ok: false, message: "Claude (Anthropic) rejected the credentials." });
  await flush();
  assert.strictEqual($("#provider-result").textContent, "Claude (Anthropic) rejected the credentials.");
  assert.ok($("#provider-result").classList.contains("bad"));
});

test("Back returns to the overview inside the same tab", async () => {
  $(".detail-back").click();
  await flush();
  assert.ok(!$("#provider-overview").classList.contains("hidden"));
  assert.ok($("#provider-detail").classList.contains("hidden"));
  assert.ok($("#page-provider").classList.contains("active"), "left the settings tab");
});

test("clicking Ollama opens only the Ollama page", async () => {
  $$("#available-provider-list .provider-row")[1].click();
  await flush();
  const detail = $("#provider-detail");
  assert.strictEqual(detail.querySelector(".detail-title").textContent, "Ollama (Local)");
  assert.strictEqual(detail.querySelector(".tabs"), null, "Ollama must not show OllaBridge tabs");
  const urlInput = detail.querySelector("input[type=text]");
  assert.strictEqual(urlInput.value, "http://127.0.0.1:11434");
});

test("Ollama surfaces a friendly message when it is not running", async () => {
  const req = sent.filter((m) => m.type === "LOAD_PROVIDER_MODELS" && m.provider === "ollama").pop();
  toHost({ type: "PROVIDER_MODELS_RESULT", requestId: req.requestId, provider: "ollama", models: [], error: "connection refused" });
  await flush();
  const notice = [...$("#provider-detail").querySelectorAll(".notice")].find((n) => /not running/.test(n.textContent));
  assert.ok(notice && !notice.classList.contains("hidden"), "Ollama-down notice not shown");
  assert.match(notice.textContent, /Retry/);
});

test("clicking OllaBridge opens three connection tabs", async () => {
  $(".detail-back").click();
  await flush();
  $$("#available-provider-list .provider-row")[0].click();
  await flush();

  const detail = $("#provider-detail");
  assert.strictEqual(detail.querySelector(".detail-title").textContent, "OllaBridge Cloud");
  const tabs = [...detail.querySelectorAll(".tab")].map((t) => t.textContent);
  assert.deepStrictEqual(tabs, ["Cloud Login", "API Key", "Local Gateway"]);
  const activePanels = detail.querySelectorAll(".tab-panel.active");
  assert.strictEqual(activePanels.length, 1, "exactly one tab panel may be active");
});

test("OllaBridge cloud tab offers browser sign-in and pairing", () => {
  const panel = $("#provider-detail .tab-panel.active");
  const signIn = [...panel.querySelectorAll("button")].find((b) => b.textContent === "Sign in with browser");
  assert.ok(signIn, "no browser sign-in button");
  signIn.click();
  assert.strictEqual(lastOfType("START_OLLABRIDGE_LOGIN").baseUrl, catalog.OLLABRIDGE_CLOUD_URL);
  assert.ok(!/password/i.test(panel.textContent.replace(/never asks for your password/i, "")),
    "cloud tab must not collect a password");

  const code = panel.querySelector("input[type=text]");
  code.value = "ABCD-1234";
  [...panel.querySelectorAll("button")].find((b) => b.textContent === "Pair device").click();
  assert.strictEqual(lastOfType("PAIR_OLLABRIDGE").code, "ABCD-1234");
});

test("local gateway tab defaults to 11435, never GitPilot's own port", async () => {
  const detail = $("#provider-detail");
  [...detail.querySelectorAll(".tab")].find((t) => t.textContent === "Local Gateway").click();
  await flush();
  const panel = detail.querySelector(".tab-panel.active");
  const url = panel.querySelector("input[type=text]");
  assert.strictEqual(url.value, "http://127.0.0.1:11435");
  assert.ok(!url.value.includes(":8000"), "local gateway defaulted to GitPilot's own port");
});

test("switching tabs reads the visible tab's fields, not a hidden one's", async () => {
  const detail = $("#provider-detail");
  const panels = [...detail.querySelectorAll(".tab-panel")];
  // Type a distinct value into the hidden API Key tab.
  panels[1].querySelector("input[type=text]").value = "https://hidden.example.com";
  const localPanel = detail.querySelector(".tab-panel.active");
  localPanel.querySelector("input[type=text]").value = "http://127.0.0.1:9999";

  [...localPanel.querySelectorAll("button")].find((b) => b.textContent === "Save and activate").click();
  const save = lastOfType("SAVE_AND_ACTIVATE_PROVIDER");
  assert.strictEqual(save.config.base_url, "http://127.0.0.1:9999",
    "read the wrong tab's field");
  assert.strictEqual(save.config.mode, "local");
});

test("watsonx page keeps its project ID field", async () => {
  $(".detail-back").click();
  await flush();
  $$("#available-provider-list .provider-row")[3].click();
  await flush();
  const detail = $("#provider-detail");
  assert.strictEqual(detail.querySelector(".detail-title").textContent, "IBM watsonx");
  const labels = [...detail.querySelectorAll(".form-label")].map((n) => n.textContent);
  assert.ok(labels.includes("Project ID"), "watsonx lost its project ID field: " + labels);
  assert.ok(labels.includes("API Key"));
});

test("OpenAI page is reachable and complete", async () => {
  $(".detail-back").click();
  await flush();
  $$("#available-provider-list .provider-row")[2].click();
  await flush();
  const detail = $("#provider-detail");
  assert.strictEqual(detail.querySelector(".detail-title").textContent, "OpenAI");
  const labels = [...detail.querySelectorAll(".form-label")].map((n) => n.textContent);
  assert.deepStrictEqual(labels, ["API Key", "Model", "Base URL (optional)"]);
});

test("going offline drops back to the overview", async () => {
  toHost({ type: "SERVER_STATE", state: "offline", serverUrl: "http://127.0.0.1:8000", canStartLocally: true });
  await flush();
  assert.ok(!$("#provider-overview").classList.contains("hidden"));
  assert.ok($("#provider-detail").classList.contains("hidden"));
  assert.ok(!$("#server-offline").classList.contains("hidden"));
});


test("Open WebUI page asks for an instance URL, not an API path", async () => {
  // The previous test left the page offline; bring the server back first.
  toHost({ type: "SERVER_STATE", state: "online", serverUrl: "http://127.0.0.1:8000", canStartLocally: true });
  toHost({ type: "PROVIDER_SETUP_DATA", data: SETUP });
  await flush();
  $$("#available-provider-list .provider-row")[4].click();
  await flush();
  const detail = $("#provider-detail");
  assert.strictEqual(detail.querySelector(".detail-title").textContent, "Open WebUI");
  const labels = [...detail.querySelectorAll(".form-label")].map((n) => n.textContent);
  assert.deepStrictEqual(labels, ["Open WebUI URL", "API key (optional)", "Model"]);
  assert.strictEqual(detail.querySelector("input[type=text]").value, "http://localhost:3000");
  assert.match(detail.textContent, /paste the root, not \/api or \/v1/);
});

test("Open WebUI reports an unreachable instance", async () => {
  const req = sent.filter((m) => m.type === "LOAD_PROVIDER_MODELS" && m.provider === "openwebui").pop();
  toHost({ type: "PROVIDER_MODELS_RESULT", requestId: req.requestId, provider: "openwebui", models: [], error: "connection refused" });
  await flush();
  const notice = [...$("#provider-detail").querySelectorAll(".notice")].find((n) => /did not answer/.test(n.textContent));
  assert.ok(notice && !notice.classList.contains("hidden"));
});

test("Open WebUI saves URL, key and model together", async () => {
  const detail = $("#provider-detail");
  detail.querySelector("input[type=text]").value = "https://webui.example.com";
  detail.querySelector("input[type=password]").value = "owui-key";
  // The page answers each request id once, so ask for a fresh listing.
  [...detail.querySelectorAll("button")].find((b) => b.textContent === "Refresh").click();
  const req = sent.filter((m) => m.type === "LOAD_PROVIDER_MODELS" && m.provider === "openwebui").pop();
  toHost({ type: "PROVIDER_MODELS_RESULT", requestId: req.requestId, provider: "openwebui", models: ["llama3.2:latest"] });
  await flush();
  detail.querySelector("select").value = "llama3.2:latest";
  [...detail.querySelectorAll("button")].find((b) => b.textContent === "Save and activate").click();
  const save = lastOfType("SAVE_AND_ACTIVATE_PROVIDER");
  assert.strictEqual(save.provider, "openwebui");
  assert.strictEqual(save.config.base_url, "https://webui.example.com");
  assert.strictEqual(save.config.api_key, "owui-key");
  assert.strictEqual(save.config.model, "llama3.2:latest");
});

test("custom endpoint page offers URL, key, model and headers", async () => {
  $(".detail-back").click();
  await flush();
  $$("#available-provider-list .provider-row")[5].click();
  await flush();
  const detail = $("#provider-detail");
  assert.strictEqual(detail.querySelector(".detail-title").textContent, "Custom endpoint");
  const labels = [...detail.querySelectorAll(".form-label")].map((n) => n.textContent);
  assert.deepStrictEqual(labels, ["Endpoint URL", "API key", "Model", "Request headers (optional)"]);
});

test("stored headers are shown for editing", () => {
  const rows = [...$("#provider-detail").querySelectorAll(".form-row")].pop()
    .querySelectorAll(".input-group");
  assert.strictEqual(rows.length, 1);
  const inputs = rows[0].querySelectorAll("input");
  assert.strictEqual(inputs[0].value, "x-user");
  assert.strictEqual(inputs[1].value, "me@example.com");
});

test("headers can be added and removed, and only live rows are saved", async () => {
  const detail = $("#provider-detail");
  const headerRow = [...detail.querySelectorAll(".form-row")].pop();
  [...headerRow.querySelectorAll("button")].find((b) => b.textContent === "Add header").click();
  [...headerRow.querySelectorAll("button")].find((b) => b.textContent === "Add header").click();

  const groups = [...headerRow.querySelectorAll(".input-group")];
  assert.strictEqual(groups.length, 3);
  groups[1].querySelectorAll("input")[0].value = "x-team";
  groups[1].querySelectorAll("input")[1].value = "blue";
  // Third row stays blank and must be ignored; remove the first entirely.
  [...groups[0].querySelectorAll("button")].find((b) => b.textContent === "Remove").click();

  detail.querySelector("input[type=text]").value = "https://inference.example.com/v1/chat/completions";
  detail.querySelector("input[type=password]").value = "sk-custom";
  [...detail.querySelectorAll("button")].find((b) => b.textContent === "Save and activate").click();

  const save = lastOfType("SAVE_AND_ACTIVATE_PROVIDER");
  assert.strictEqual(save.provider, "custom");
  assert.strictEqual(save.config.base_url, "https://inference.example.com/v1/chat/completions");
  assert.strictEqual(save.config.api_key, "sk-custom");
  assert.deepStrictEqual({ ...save.config.headers }, { "x-team": "blue" },
    "deleted and blank header rows must not be saved");
});

test("custom endpoint page never prefills the stored key", () => {
  const input = $("#provider-detail input[type=password]");
  const hint = [...$("#provider-detail").querySelectorAll(".form-hint")].map((n) => n.textContent).join(" ");
  assert.match(hint, /API key configured: ••••K9F1/);
  assert.ok(!hint.includes("sk-"), "hint leaked key material");
});


const TOPOLOGIES = [
  { id: "default", name: "Default (CrewAI Routing)", description: "Route per request", category: "system", icon: "", agents_used: [], execution_style: "single_task" },
  { id: "feature_builder", name: "Feature Builder", description: "Full pipeline", category: "pipeline", icon: "🚀", agents_used: ["explorer", "planner", "developer", "reviewer", "git_agent"], execution_style: "crew_pipeline" },
  { id: "code_inspector", name: "Code Inspector", description: "Review only", category: "pipeline", icon: "🔍", agents_used: ["explorer", "reviewer"], execution_style: "crew_pipeline" },
];

test("opening Agent asks the host for topologies", async () => {
  $$(".nav-item").find((b) => b.dataset.page === "agent").click();
  await flush();
  assert.ok($("#page-agent").classList.contains("active"));
  assert.ok(lastOfType("LOAD_TOPOLOGIES"), "LOAD_TOPOLOGIES not sent");
});

test("topologies render as cards with their agent sequence", async () => {
  toHost({ type: "TOPOLOGY_DATA", offline: false, topologies: TOPOLOGIES, selected: "feature_builder" });
  await flush();

  assert.ok($("#topology-offline").classList.contains("hidden"));
  const cards = $$("#topology-list .provider-row");
  const names = cards.map((c) => c.querySelector(".provider-name").textContent);
  assert.deepStrictEqual(names, [
    "Automatic (recommended)",
    "🚀 Feature Builder",
    "🔍 Code Inspector",
    "Default (CrewAI Routing)",
  ], "pipelines should be grouped before system topologies");

  const featureBuilder = cards[1];
  assert.match(featureBuilder.textContent, /Explorer\s+→\s+Planner\s+→\s+Coder\s+→\s+Reviewer\s+→\s+PR Manager/);
  assert.match(featureBuilder.textContent, /Active/, "saved preference is not marked active");
});

test("routed topologies say agents are chosen per request", () => {
  const cards = $$("#topology-list .provider-row");
  assert.match(cards[0].textContent, /Agents chosen per request/);
  assert.match(cards[3].textContent, /Agents chosen per request/);
});

test("no free-text topology box remains", () => {
  assert.strictEqual($("#defaultTopology"), null,
    "the topology field must be a picker, not a text box you have to guess at");
});

test("choosing a topology sends it to the host", async () => {
  $$("#topology-list .provider-row")[2].click();
  await flush();
  const req = lastOfType("SET_TOPOLOGY");
  assert.strictEqual(req.topology, "code_inspector");
  assert.match($("#topology-result").textContent, /Saving/);

  toHost({ type: "TOPOLOGY_SAVED", requestId: req.requestId, ok: true, topology: "code_inspector" });
  await flush();
  assert.match($("#topology-result").textContent, /Code Inspector is now the default topology/);
  assert.ok($("#topology-result").classList.contains("ok"));
  assert.ok(lastOfType("LOAD_TOPOLOGIES"), "should reload to refresh the active badge");
});

test("Automatic is offered as a choice and sends 'auto'", async () => {
  $$("#topology-list .provider-row")[0].click();
  await flush();
  assert.strictEqual(lastOfType("SET_TOPOLOGY").topology, "auto");
});

test("a rejected topology save shows the reason", async () => {
  const req = lastOfType("SET_TOPOLOGY");
  toHost({ type: "TOPOLOGY_SAVED", requestId: req.requestId, ok: false, message: "Unknown topology 'x'." });
  await flush();
  assert.strictEqual($("#topology-result").textContent, "Unknown topology 'x'.");
  assert.ok($("#topology-result").classList.contains("bad"));
});

test("an offline server explains why topologies are unavailable", async () => {
  toHost({ type: "TOPOLOGY_DATA", offline: true, serverUrl: "http://127.0.0.1:8000", detail: "Could not reach it." });
  await flush();
  assert.ok(!$("#topology-offline").classList.contains("hidden"));
  assert.strictEqual($("#topology-offline-detail").textContent, "Could not reach it.");
  assert.strictEqual($$("#topology-list .provider-row").length, 0, "stale topology list left on screen");
});


// ── MCP servers ──

const MCP_DATA = {
  type: "MCP_DATA",
  offline: false,
  forgeRunning: true,
  forgeInstalling: false,
  status: {
    gateway_url: "http://localhost:4444",
    gateway_reachable: true,
    gateway_detail: "",
    servers_installed: 2,
    servers_enabled: 1,
    tools_advertised: 6,
  },
  servers: [
    {
      id: "mcp-postgre-server", installed: true, enabled: true,
      endpoint: "http://localhost:8080/mcp", description: "PostgreSQL schema discovery",
      auth_token_env: "MCP_POSTGRE_SERVER_TOKEN", tags: ["database"],
      tool_count: 3, enabled_tool_count: 2,
      tools: [
        { name: "postgres.list_tables", risk: "low", enabled: true, destructive: false, mutation: false, used_by: ["explorer"] },
        { name: "postgres.safe_select", risk: "low", enabled: true, destructive: false, mutation: false, used_by: ["coder"] },
        { name: "postgres.drop_table", risk: "high", enabled: false, destructive: true, mutation: false, used_by: [] },
      ],
    },
    {
      id: "mcp-milvus-server", installed: true, enabled: false,
      endpoint: "http://localhost:8082/mcp", description: "Vector search",
      auth_token_env: "", tags: ["vector"], tool_count: 2, enabled_tool_count: 0, tools: [],
    },
  ],
  catalog: [
    { id: "mcp-inspector-server", description: "Validate other MCP servers", endpoint: "http://x/mcp", tags: ["mcp"], installed: false },
    { id: "mcp-postgre-server", description: "already attached", endpoint: "http://y/mcp", tags: [], installed: true },
  ],
};

const openMcp = async () => {
  $$(".nav-item").find((b) => b.dataset.page === "mcp").click();
  await flush();
};

test("opening MCP Servers asks the host for the overview", async () => {
  await openMcp();
  assert.ok($("#page-mcp").classList.contains("active"));
  assert.ok(lastOfType("LOAD_MCP_OVERVIEW"), "LOAD_MCP_OVERVIEW not sent");
});

test("an offline backend explains itself and shows no server list", async () => {
  toHost({ type: "MCP_DATA", offline: true, detail: "Could not reach it." });
  await flush();
  assert.ok(!$("#mcp-offline").classList.contains("hidden"));
  assert.ok($("#mcp-body").classList.contains("hidden"));
  assert.strictEqual($("#mcp-offline-detail").textContent, "Could not reach it.");
});

test("gateway status is summarised, not just reachable/unreachable", async () => {
  toHost(MCP_DATA);
  await flush();
  assert.ok($("#mcp-offline").classList.contains("hidden"));
  assert.strictEqual($("#mcp-gateway-text").textContent, "Connected");
  assert.strictEqual($("#mcp-gateway-url").textContent, "http://localhost:4444");
  assert.match($("#mcp-gateway-detail").textContent, /1 of 2 server\(s\) enabled/);
  assert.match($("#mcp-gateway-detail").textContent, /6 tool\(s\) available to the agents/);
});

test("the install button is hidden while a gateway is reachable", () => {
  assert.ok($("#btn-install-forge").classList.contains("hidden"),
    "offering to install a gateway that is already running is noise");
});

test("the install button appears when there is no gateway", async () => {
  toHost({ ...MCP_DATA, forgeRunning: false,
    status: { ...MCP_DATA.status, gateway_reachable: false, gateway_detail: "connection refused" } });
  await flush();
  assert.ok(!$("#btn-install-forge").classList.contains("hidden"));
  assert.strictEqual($("#mcp-gateway-text").textContent, "Not running");

  $("#btn-install-forge").click();
  assert.ok(lastOfType("INSTALL_MCP_FORGE"));
  assert.strictEqual($("#btn-install-forge").disabled, true, "double-clicking must not start two installs");
});

test("forge install progress is shown and re-enables the button when done", async () => {
  toHost({ type: "MCP_FORGE_PROGRESS", stage: "starting", message: "Starting…" });
  await flush();
  assert.ok(!$("#mcp-forge-progress").classList.contains("hidden"));
  assert.strictEqual($("#mcp-forge-progress").textContent, "Starting…");

  toHost({ type: "MCP_FORGE_PROGRESS", stage: "done", message: "Running at http://localhost:4444." });
  await flush();
  assert.ok($("#mcp-forge-progress").classList.contains("ok"));
  assert.strictEqual($("#btn-install-forge").disabled, false);
});

test("a failed install says so and allows a retry", async () => {
  toHost({ type: "MCP_FORGE_PROGRESS", stage: "failed", message: "Docker is not running." });
  await flush();
  assert.ok($("#mcp-forge-progress").classList.contains("bad"));
  assert.strictEqual($("#btn-install-forge").disabled, false);
});

test("attached servers list with their enabled tool counts", async () => {
  toHost(MCP_DATA);
  await flush();
  const rows = $$("#mcp-server-list .provider-row");
  assert.strictEqual(rows.length, 2);
  assert.match(rows[0].textContent, /mcp-postgre-server/);
  assert.match(rows[0].textContent, /2 of 3 tool\(s\) available to the agents/);
  assert.match(rows[0].textContent, /Enabled/);
  assert.match(rows[1].textContent, /Disabled/);
});

test("the catalogue omits servers already attached", () => {
  const rows = $$("#mcp-catalog-list .provider-row");
  const ids = rows.map((r) => r.querySelector(".provider-name").textContent);
  assert.deepStrictEqual(ids, ["mcp-inspector-server"],
    "an attached server must not be offered again");
});

test("attaching sends the source so the host knows where to look", async () => {
  const attach = [...$$("#mcp-catalog-list button")].find((b) => b.textContent === "Attach");
  attach.click();
  const req = lastOfType("INSTALL_MCP_SERVER");
  assert.strictEqual(req.entryId, "mcp-inspector-server");
  assert.strictEqual(req.source, "bundled");
  assert.strictEqual(attach.disabled, true);

  toHost({ type: "MCP_ACTION_RESULT", requestId: req.requestId, ok: true, message: "Attached, and left disabled." });
  await flush();
  assert.match($("#mcp-result").textContent, /left disabled/);
});

test("a registry search is sent and its results are listed separately", async () => {
  $("#mcp-search").value = "postgres";
  $("#btn-search-registry").click();
  const req = lastOfType("SEARCH_MCP_REGISTRY");
  assert.strictEqual(req.query, "postgres");

  toHost({
    type: "MCP_REGISTRY_RESULT", requestId: req.requestId,
    registryUrl: "https://api.matrixhub.io",
    items: [{ id: "mcp-timescale", description: "TimescaleDB tools", endpoint: "http://z/mcp", tags: ["timeseries"], installed: false }],
  });
  await flush();

  assert.match($("#mcp-registry-status").textContent, /1 result\(s\) from https:\/\/api\.matrixhub\.io/);
  const ids = $$("#mcp-catalog-list .provider-row").map((r) => r.querySelector(".provider-name").textContent);
  assert.ok(ids.includes("mcp-timescale"));
});

test("a registry outage is reported without breaking the page", async () => {
  $("#btn-search-registry").click();
  const req = lastOfType("SEARCH_MCP_REGISTRY");
  toHost({ type: "MCP_REGISTRY_RESULT", requestId: req.requestId, items: [], error: "Cannot reach https://api.matrixhub.io" });
  await flush();
  assert.match($("#mcp-registry-status").textContent, /Registry unavailable/);
  // The bundled catalogue is still there.
  assert.ok($$("#mcp-catalog-list .provider-row").length >= 1);
});

test("clicking a server opens only its tools page", async () => {
  toHost(MCP_DATA);
  await flush();
  $$("#mcp-server-list .provider-row")[0].click();
  await flush();

  assert.ok($("#mcp-overview").classList.contains("hidden"));
  const detail = $("#mcp-detail");
  assert.strictEqual(detail.querySelector(".detail-title").textContent, "mcp-postgre-server");
  assert.strictEqual(detail.querySelectorAll(".detail-title").length, 1);
});

test("the tools page shows risk and which agents call each tool", () => {
  const detail = $("#mcp-detail");
  const labels = [...detail.querySelectorAll(".field-label")].map((n) => n.textContent);
  assert.ok(labels.some((l) => /postgres\.list_tables/.test(l)));
  assert.ok(labels.some((l) => /postgres\.drop_table\s*high/.test(l)), "high-risk tools must be badged");
  assert.match(detail.textContent, /Used by explorer/);
  assert.match(detail.textContent, /Used by coder/);
});

test("the token env var is named but never its value", () => {
  const detail = $("#mcp-detail");
  assert.match(detail.textContent, /MCP_POSTGRE_SERVER_TOKEN/);
  assert.match(detail.textContent, /never reaches this editor/);
  assert.strictEqual(detail.querySelector("input[type=password]"), null);
});

test("toggling a tool sends the intended new state", async () => {
  const detail = $("#mcp-detail");
  const toggles = [...detail.querySelectorAll(".toggle")];
  // First toggle is the server master switch; the rest are tools.
  toggles[3].click();
  const req = lastOfType("SET_MCP_TOOL_ENABLED");
  assert.strictEqual(req.serverId, "mcp-postgre-server");
  assert.strictEqual(req.tool, "postgres.drop_table");
  assert.strictEqual(req.enabled, true, "clicking a disabled tool asks to enable it");
});

test("the master switch toggles the whole server", () => {
  const toggles = [...$("#mcp-detail").querySelectorAll(".toggle")];
  toggles[0].click();
  const req = lastOfType("SET_MCP_SERVER_ENABLED");
  assert.strictEqual(req.serverId, "mcp-postgre-server");
  assert.strictEqual(req.enabled, false, "the server was enabled, so the click disables it");
});

test("testing a server reports the outcome inline", async () => {
  const detail = $("#mcp-detail");
  [...detail.querySelectorAll("button")].find((b) => b.textContent === "Test connection").click();
  const req = lastOfType("TEST_MCP_SERVER");
  assert.ok($("#mcp-detail-result").classList.contains("info"));

  toHost({ type: "MCP_ACTION_RESULT", requestId: req.requestId, ok: false, message: "The server did not respond." });
  await flush();
  assert.strictEqual($("#mcp-detail-result").textContent, "The server did not respond.");
  assert.ok($("#mcp-detail-result").classList.contains("bad"));
});

test("back returns to the MCP overview inside the same tab", async () => {
  $("#mcp-detail .detail-back").click();
  await flush();
  assert.ok(!$("#mcp-overview").classList.contains("hidden"));
  assert.ok($("#mcp-detail").classList.contains("hidden"));
  assert.ok($("#page-mcp").classList.contains("active"));
});

test("detaching returns to the overview", async () => {
  $$("#mcp-server-list .provider-row")[0].click();
  await flush();
  [...$("#mcp-detail").querySelectorAll("button")].find((b) => b.textContent === "Detach server").click();
  const req = lastOfType("UNINSTALL_MCP_SERVER");
  assert.strictEqual(req.serverId, "mcp-postgre-server");

  toHost({ type: "MCP_ACTION_RESULT", requestId: req.requestId, ok: true, message: "Detached." });
  await flush();
  assert.ok(!$("#mcp-overview").classList.contains("hidden"));
});

test("gateway configure and sync reach the host", () => {
  $("#btn-configure-gateway").click();
  assert.ok(lastOfType("CONFIGURE_MCP_GATEWAY"));
  $("#btn-sync-gateway").click();
  assert.ok(lastOfType("SYNC_MCP_GATEWAY"));
  $("#btn-add-custom").click();
  assert.ok(lastOfType("ADD_CUSTOM_MCP_SERVER"));
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
