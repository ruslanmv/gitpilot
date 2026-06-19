import React, { useEffect, useState } from "react";
import { apiUrl } from "../utils/api.js";

const SANDBOX_BACKENDS = [
  {
    id: "subprocess",
    label: "Local",
    sub: "Host subprocess with a workspace jail. Default — best for trying simple snippets.",
  },
  {
    id: "matrixlab",
    label: "MatrixLab",
    sub: "Containerised, ephemeral sandboxes from a MatrixLab Runner. Recommended for enterprise.",
  },
  {
    id: "off",
    label: "Pass-through",
    sub: "Run on the host with no jail. Local development only.",
  },
];

export default function SettingsModal({ onClose }) {
  const [settings, setSettings] = useState(null);
  const [models, setModels] = useState([]);
  const [modelsError, setModelsError] = useState(null);
  const [loadingModels, setLoadingModels] = useState(false);
  const [testResult, setTestResult] = useState(null); // { ok: bool, message: string }
  const [testing, setTesting] = useState(false);
  // Sandbox runtime state. ``sandbox`` is the persisted block from the
  // settings response; ``sandboxStatus`` is the live probe result
  // (ok / error). Both are independent of LLM settings so a failed
  // MatrixLab probe doesn't block provider switching.
  const [sandbox, setSandbox] = useState(null);
  const [sandboxStatus, setSandboxStatus] = useState(null);
  const [sandboxTokenInput, setSandboxTokenInput] = useState("");
  const [sandboxBusy, setSandboxBusy] = useState(false);
  // MatrixLab lifecycle state — separate from the sandbox runtime state
  // because the lifecycle endpoints can run for many seconds (docker
  // pulls) and we don't want to block the "switch backend" buttons on
  // a running install.
  const [lifecycle, setLifecycle] = useState(null);
  const [lifecycleBusy, setLifecycleBusy] = useState(null); // "install" | "start" | "stop" | null
  const [lifecycleLog, setLifecycleLog] = useState([]);
  const [showLifecycleLog, setShowLifecycleLog] = useState(false);

  const loadSettings = async () => {
    const res = await fetch(apiUrl("/api/settings"));
    const data = await res.json();
    setSettings(data);
    if (data?.sandbox) setSandbox(data.sandbox);
  };

  const loadSandboxStatus = async () => {
    try {
      const res = await fetch(apiUrl("/api/sandbox/status"));
      const data = await res.json();
      setSandboxStatus({ ok: data.ok, error: data.error, remote: data.remote });
      // /status returns the same shape as the persisted block, so refresh
      // the form state from it — the env vars may override settings.json
      // and we want the UI to show what's actually live.
      setSandbox((prev) => ({
        ...(prev || {}),
        backend: data.backend,
        matrixlab_url: data.matrixlab_url,
        matrixlab_image: data.matrixlab_image,
        allow_network: data.allow_network,
        timeout_sec: data.timeout_sec,
        has_token: data.has_token,
      }));
    } catch (err) {
      setSandboxStatus({ ok: false, error: err.message || "status probe failed" });
    }
  };

  const updateSandbox = async (patch) => {
    setSandboxBusy(true);
    try {
      const res = await fetch(apiUrl("/api/sandbox/config"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      const data = await res.json();
      if (!res.ok) {
        setSandboxStatus({ ok: false, error: data.detail || "update failed" });
        return;
      }
      setSandbox((prev) => ({
        ...(prev || {}),
        backend: data.backend,
        matrixlab_url: data.matrixlab_url,
        matrixlab_image: data.matrixlab_image,
        allow_network: data.allow_network,
        timeout_sec: data.timeout_sec,
        has_token: data.has_token,
      }));
      setSandboxStatus({ ok: data.ok, error: data.error, remote: data.remote });
      // Always clear the local token input after a save so a stale value
      // doesn't sit in the DOM. The backend stores it; we don't need to
      // hold it client-side.
      if ("matrixlab_token" in patch) setSandboxTokenInput("");
    } finally {
      setSandboxBusy(false);
    }
  };

  const loadLifecycle = async () => {
    try {
      const res = await fetch(apiUrl("/api/sandbox/matrixlab/lifecycle"));
      const data = await res.json();
      setLifecycle(data);
      if (Array.isArray(data.steps) && data.steps.length) {
        setLifecycleLog(data.steps);
      }
    } catch (err) {
      setLifecycle({
        docker_available: false,
        installed: false,
        running: false,
        lifecycle_enabled: false,
        error: err.message || "lifecycle probe failed",
      });
    }
  };

  const runLifecycle = async (action) => {
    if (!["install", "start", "stop"].includes(action)) return;
    setLifecycleBusy(action);
    setShowLifecycleLog(true);
    try {
      const res = await fetch(apiUrl(`/api/sandbox/matrixlab/${action}`), { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setLifecycle((prev) => ({ ...(prev || {}), error: data.detail || `HTTP ${res.status}` }));
        return;
      }
      setLifecycle(data);
      setLifecycleLog(data.steps || []);
      // Refresh the runtime status — a successful start should flip
      // sandboxStatus.ok to true.
      loadSandboxStatus();
    } finally {
      setLifecycleBusy(null);
    }
  };

  useEffect(() => {
    loadSettings();
    loadSandboxStatus();
    loadLifecycle();
  }, []);

  const changeProvider = async (provider) => {
    const res = await fetch(apiUrl("/api/settings/provider"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    const data = await res.json();
    setSettings(data);

    // Reset models state when provider changes
    setModels([]);
    setModelsError(null);
  };

  const loadModels = async () => {
    if (!settings) return;
    setLoadingModels(true);
    setModelsError(null);
    try {
      const res = await fetch(
        `/api/settings/models?provider=${settings.provider}`
      );
      const data = await res.json();
      if (data.error) {
        setModelsError(data.error);
        setModels([]);
      } else {
        setModels(data.models || []);
      }
    } catch (err) {
      console.error(err);
      setModelsError("Failed to load models");
      setModels([]);
    } finally {
      setLoadingModels(false);
    }
  };

  const currentModelForActiveProvider = () => {
    if (!settings) return "";
    const p = settings.provider;
    if (p === "openai") return settings.openai?.model || "";
    if (p === "claude") return settings.claude?.model || "";
    if (p === "watsonx") return settings.watsonx?.model_id || "";
    if (p === "ollama") return settings.ollama?.model || "";
    return "";
  };

  const changeModel = async (model) => {
    if (!settings) return;
    const provider = settings.provider;

    let payload = {};
    if (provider === "openai") {
      payload = {
        openai: {
          ...settings.openai,
          model,
        },
      };
    } else if (provider === "claude") {
      payload = {
        claude: {
          ...settings.claude,
          model,
        },
      };
    } else if (provider === "watsonx") {
      payload = {
        watsonx: {
          ...settings.watsonx,
          model_id: model,
        },
      };
    } else if (provider === "ollama") {
      payload = {
        ollama: {
          ...settings.ollama,
          model,
        },
      };
    }

    const res = await fetch(apiUrl("/api/settings/llm"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    setSettings(data);
  };

  const testConnection = async () => {
    if (!settings) return;
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetch(apiUrl(`/api/settings/test?provider=${settings.provider}`));
      const data = await res.json();
      if (!res.ok || data.error) {
        setTestResult({ ok: false, message: data.error || data.detail || "Connection failed" });
      } else {
        setTestResult({ ok: true, message: data.message || "Connection successful" });
      }
    } catch (err) {
      setTestResult({ ok: false, message: err.message || "Connection test failed" });
    } finally {
      setTesting(false);
    }
  };

  const toggleLiteMode = async () => {
    if (!settings) return;
    const newValue = !settings.lite_mode;
    try {
      const res = await fetch(apiUrl("/api/settings/lite-mode"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lite_mode: newValue }),
      });
      if (res.ok) {
        setSettings((prev) => ({ ...prev, lite_mode: newValue }));
      }
    } catch (err) {
      console.error("Failed to toggle lite mode:", err);
    }
  };

  if (!settings) return null;

  const activeModel = currentModelForActiveProvider();

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">Settings</div>
          <button className="modal-close" type="button" onClick={onClose}>
            ✕
          </button>
        </div>

        <div style={{ fontSize: 13, color: "#c3c5dd" }}>
          Select which LLM provider GitPilot should use for planning and chat.
        </div>

        <div className="provider-list">
          {settings.providers.map((p) => (
            <div
              key={p}
              className={
                "provider-item" + (settings.provider === p ? " active" : "")
              }
            >
              <div className="provider-name">{p}</div>
              <button
                type="button"
                className="chat-btn secondary"
                style={{ padding: "4px 8px", fontSize: 11 }}
                onClick={() => changeProvider(p)}
                disabled={settings.provider === p}
              >
                {settings.provider === p ? "Active" : "Use"}
              </button>
            </div>
          ))}
        </div>

        {/* Models section */}
        <div
          style={{
            marginTop: 16,
            paddingTop: 12,
            borderTop: "1px solid #2c2d46",
            fontSize: 13,
          }}
        >
          <div style={{ marginBottom: 6, color: "#c3c5dd" }}>
            Active provider: <strong>{settings.provider}</strong>
          </div>

          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <button
              type="button"
              className="chat-btn secondary"
              style={{ padding: "4px 8px", fontSize: 11 }}
              onClick={testConnection}
              disabled={testing}
            >
              {testing ? "Testing…" : "Test Connection"}
            </button>
            <button
              type="button"
              className="chat-btn secondary"
              style={{ padding: "4px 8px", fontSize: 11 }}
              onClick={loadModels}
              disabled={loadingModels}
            >
              {loadingModels ? "Loading…" : "Display models"}
            </button>

            {activeModel && (
              <span style={{ fontSize: 12, color: "#9092b5" }}>
                Current model: <code>{activeModel}</code>
              </span>
            )}
          </div>

          {modelsError && (
            <div style={{ marginTop: 8, color: "#ff8080", fontSize: 12 }}>
              {modelsError}
            </div>
          )}

          {testResult && (
            <div style={{
              marginTop: 8,
              padding: "6px 10px",
              borderRadius: 6,
              background: testResult.ok ? "#0d3320" : "#3d1111",
              border: `1px solid ${testResult.ok ? "#166534" : "#7f1d1d"}`,
              color: testResult.ok ? "#86efac" : "#fca5a5",
              fontSize: 12,
            }}>
              {testResult.ok ? "✓ " : "✗ "}{testResult.message}
            </div>
          )}

          {models.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <label
                style={{
                  display: "block",
                  marginBottom: 4,
                  fontSize: 12,
                  color: "#c3c5dd",
                }}
              >
                Select model for {settings.provider}:
              </label>
              <select
                style={{
                  width: "100%",
                  fontSize: 12,
                  padding: "4px 6px",
                  background: "#14152a",
                  color: "#e6e8ff",
                  border: "1px solid #2c2d46",
                  borderRadius: 4,
                }}
                value={activeModel}
                onChange={(e) => changeModel(e.target.value)}
              >
                <option value="">-- select a model --</option>
                {models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        {/* Lite Mode section */}
        <div
          style={{
            marginTop: 16,
            paddingTop: 12,
            borderTop: "1px solid #2c2d46",
            fontSize: 13,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 6,
            }}
          >
            <div style={{ color: "#c3c5dd", fontWeight: 600 }}>
              Lite Mode
            </div>
            <button
              type="button"
              onClick={toggleLiteMode}
              style={{
                padding: "4px 14px",
                fontSize: 11,
                fontWeight: 600,
                borderRadius: 12,
                border: "none",
                cursor: "pointer",
                background: settings.lite_mode ? "#166534" : "#2c2d46",
                color: settings.lite_mode ? "#86efac" : "#9092b5",
                transition: "background 0.2s, color 0.2s",
              }}
            >
              {settings.lite_mode ? "ON" : "OFF"}
            </button>
          </div>
          <div style={{ fontSize: 11, color: "#9092b5", lineHeight: 1.5 }}>
            Optimized for small models (under 7B parameters).
            Uses simplified prompts and single-agent execution instead
            of multi-agent pipelines. Recommended for: qwen2.5:1.5b,
            phi-3-mini, gemma-2b, tinyllama, etc.
          </div>
        </div>

        {/* Sandbox Runtime section — controls the Run button on chat
            code blocks. Local subprocess is the default so users can
            try simple snippets immediately; MatrixLab is the enterprise
            opt-in for containerised isolation. */}
        {sandbox && (
          <div
            style={{
              marginTop: 16,
              paddingTop: 12,
              borderTop: "1px solid #2c2d46",
              fontSize: 13,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
              <div style={{ color: "#c3c5dd", fontWeight: 600 }}>Sandbox runtime</div>
              {sandboxStatus && (
                <span style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  fontSize: 11,
                  fontWeight: 600,
                  padding: "2px 8px",
                  borderRadius: 10,
                  background: sandboxStatus.ok ? "#0d3320" : "#3d1111",
                  border: `1px solid ${sandboxStatus.ok ? "#166534" : "#7f1d1d"}`,
                  color: sandboxStatus.ok ? "#86efac" : "#fca5a5",
                }}>
                  <span style={{
                    width: 6, height: 6, borderRadius: "50%",
                    background: sandboxStatus.ok ? "#10B981" : "#ef4444",
                  }} />
                  {sandboxStatus.ok ? "Reachable" : "Unreachable"}
                </span>
              )}
            </div>
            <div style={{ fontSize: 11, color: "#9092b5", lineHeight: 1.5, marginBottom: 10 }}>
              Where the Run button on generated code blocks executes. Choose Local
              for a quick try, or install MatrixLab and switch to it for isolated
              enterprise sandboxes.
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {SANDBOX_BACKENDS.map((b) => (
                <label
                  key={b.id}
                  style={{
                    display: "flex",
                    gap: 10,
                    padding: "8px 10px",
                    borderRadius: 6,
                    border: `1px solid ${sandbox.backend === b.id ? "#4f46e5" : "#2c2d46"}`,
                    background: sandbox.backend === b.id ? "#1a1a3a" : "transparent",
                    cursor: sandboxBusy ? "not-allowed" : "pointer",
                    opacity: sandboxBusy ? 0.6 : 1,
                  }}
                >
                  <input
                    type="radio"
                    name="sandbox-backend"
                    value={b.id}
                    checked={sandbox.backend === b.id}
                    disabled={sandboxBusy}
                    onChange={() => updateSandbox({ backend: b.id })}
                    style={{ marginTop: 2 }}
                  />
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 13, fontWeight: 500, color: "#e6e8ff" }}>{b.label}</div>
                    <div style={{ fontSize: 11, color: "#9092b5", marginTop: 2 }}>{b.sub}</div>
                  </div>
                </label>
              ))}
            </div>

            {sandbox.backend === "matrixlab" && (
              <div style={{ marginTop: 10, padding: 10, background: "#0e0f24", borderRadius: 6, border: "1px solid #2c2d46" }}>
                <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: 8, alignItems: "center" }}>
                  <label style={{ fontSize: 12, color: "#c3c5dd" }}>Runner URL</label>
                  <input
                    type="text"
                    value={sandbox.matrixlab_url || ""}
                    onChange={(e) => setSandbox({ ...sandbox, matrixlab_url: e.target.value })}
                    onBlur={() => updateSandbox({ matrixlab_url: sandbox.matrixlab_url })}
                    placeholder="http://localhost:8765"
                    style={{
                      fontSize: 12, padding: "4px 6px",
                      background: "#14152a", color: "#e6e8ff",
                      border: "1px solid #2c2d46", borderRadius: 4,
                    }}
                  />
                  <label style={{ fontSize: 12, color: "#c3c5dd" }}>Bearer token</label>
                  <div style={{ display: "flex", gap: 6 }}>
                    <input
                      type="password"
                      value={sandboxTokenInput}
                      onChange={(e) => setSandboxTokenInput(e.target.value)}
                      placeholder={sandbox.has_token ? "•••••••• (saved)" : "Optional"}
                      style={{
                        flex: 1,
                        fontSize: 12, padding: "4px 6px",
                        background: "#14152a", color: "#e6e8ff",
                        border: "1px solid #2c2d46", borderRadius: 4,
                      }}
                    />
                    <button
                      type="button"
                      className="chat-btn secondary"
                      style={{ padding: "2px 8px", fontSize: 11 }}
                      disabled={sandboxBusy}
                      onClick={() => updateSandbox({ matrixlab_token: sandboxTokenInput })}
                    >
                      Save token
                    </button>
                    {sandbox.has_token && (
                      <button
                        type="button"
                        className="chat-btn secondary"
                        style={{ padding: "2px 8px", fontSize: 11 }}
                        disabled={sandboxBusy}
                        onClick={() => updateSandbox({ matrixlab_token: "" })}
                        title="Clear the saved token"
                      >
                        Clear
                      </button>
                    )}
                  </div>
                  <label style={{ fontSize: 12, color: "#c3c5dd" }}>Default image</label>
                  <input
                    type="text"
                    value={sandbox.matrixlab_image || ""}
                    onChange={(e) => setSandbox({ ...sandbox, matrixlab_image: e.target.value })}
                    onBlur={() => updateSandbox({ matrixlab_image: sandbox.matrixlab_image })}
                    placeholder="matrixlab-python (let runner pick)"
                    style={{
                      fontSize: 12, padding: "4px 6px",
                      background: "#14152a", color: "#e6e8ff",
                      border: "1px solid #2c2d46", borderRadius: 4,
                    }}
                  />
                </div>
              </div>
            )}

            {/* MatrixLab lifecycle card — only shown when MatrixLab is
                the selected backend. The button label tracks the
                detected state: Install → Start → Running. When the
                operator hasn't enabled GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE
                the actions are disabled and an inline hint explains how
                to flip the env flag. */}
            {sandbox.backend === "matrixlab" && lifecycle && (
              <div style={{
                marginTop: 10, padding: 10,
                background: "#0e0f24", borderRadius: 6,
                border: "1px solid #2c2d46",
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: "#c3c5dd" }}>
                    MatrixLab lifecycle
                  </div>
                  <span style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 10,
                    background: lifecycle.running ? "#0d3320"
                      : lifecycle.installed ? "#3d2d11"
                      : "#3d1111",
                    border: `1px solid ${lifecycle.running ? "#166534"
                      : lifecycle.installed ? "#854d0e"
                      : "#7f1d1d"}`,
                    color: lifecycle.running ? "#86efac"
                      : lifecycle.installed ? "#fde68a"
                      : "#fca5a5",
                  }}>
                    <span style={{
                      width: 6, height: 6, borderRadius: "50%",
                      background: lifecycle.running ? "#10B981"
                        : lifecycle.installed ? "#f59e0b"
                        : "#ef4444",
                    }} />
                    {lifecycle.running ? "Running"
                      : lifecycle.installed ? "Installed · stopped"
                      : "Not installed"}
                  </span>
                  {sandbox.env_override && (
                    <span style={{
                      marginLeft: 8, fontSize: 10, fontWeight: 600,
                      padding: "1px 6px", borderRadius: 9,
                      background: "rgba(217, 119, 6, 0.12)",
                      color: "#f59e0b",
                      border: "1px solid rgba(217, 119, 6, 0.35)",
                    }}
                    title={`Env var ${sandbox.env_override} is overriding the persisted setting`}>
                    env override
                    </span>
                  )}
                </div>

                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
                  {/* Running > Installed > Not-installed.  Checking
                      ``running`` first matters when the operator
                      brought MatrixLab up from source (e.g. `make run`
                      inside a checkout) so the image tag doesn't match
                      our canonical ``ruslanmv/matrixlab-runner:latest``
                      — the URL still answers, just don't offer to
                      install on top of a healthy runner. */}
                  {lifecycle.running ? (
                    <button
                      type="button"
                      className="chat-btn secondary"
                      style={{ padding: "4px 10px", fontSize: 11 }}
                      disabled={!lifecycle.lifecycle_enabled || lifecycleBusy != null}
                      onClick={() => runLifecycle("stop")}
                      title="docker stop the GitPilot-managed runner container"
                    >
                      {lifecycleBusy === "stop" ? "Stopping…" : "Stop"}
                    </button>
                  ) : lifecycle.installed ? (
                    <button
                      type="button"
                      className="chat-btn"
                      style={{ padding: "4px 10px", fontSize: 11, fontWeight: 600 }}
                      disabled={!lifecycle.lifecycle_enabled || lifecycleBusy != null}
                      onClick={() => runLifecycle("start")}
                      title="docker run the MatrixLab runner container"
                    >
                      {lifecycleBusy === "start" ? "Starting…" : "Start"}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="chat-btn"
                      style={{ padding: "4px 10px", fontSize: 11, fontWeight: 600 }}
                      disabled={!lifecycle.lifecycle_enabled || !lifecycle.docker_available || lifecycleBusy != null}
                      onClick={() => runLifecycle("install")}
                      title="docker pull the MatrixLab runner + sandbox images"
                    >
                      {lifecycleBusy === "install" ? "Installing…" : "Install"}
                    </button>
                  )}
                  <button
                    type="button"
                    className="chat-btn secondary"
                    style={{ padding: "4px 10px", fontSize: 11 }}
                    disabled={lifecycleBusy != null}
                    onClick={loadLifecycle}
                  >
                    Refresh
                  </button>
                  {lifecycleLog.length > 0 && (
                    <button
                      type="button"
                      className="chat-btn secondary"
                      style={{ padding: "4px 10px", fontSize: 11 }}
                      onClick={() => setShowLifecycleLog((v) => !v)}
                    >
                      {showLifecycleLog ? "Hide log" : `Show log (${lifecycleLog.length})`}
                    </button>
                  )}
                </div>

                {lifecycle.instructions && (
                  <div style={{
                    fontSize: 11, lineHeight: 1.5, color: "#fde68a",
                    padding: "6px 8px", background: "#2a210d",
                    border: "1px solid #854d0e", borderRadius: 4,
                    marginBottom: 6,
                  }}>
                    {lifecycle.instructions}
                  </div>
                )}
                {lifecycle.error && (
                  <div style={{
                    fontSize: 11, color: "#fca5a5", fontFamily: "ui-monospace, monospace",
                    padding: "6px 8px", background: "#3d1111", border: "1px solid #7f1d1d",
                    borderRadius: 4, marginBottom: 6,
                  }}>
                    {lifecycle.error}
                  </div>
                )}

                {/* Per-step transcript — surfaced so failures are
                    debuggable from the UI without SSH'ing to the host. */}
                {showLifecycleLog && lifecycleLog.length > 0 && (
                  <div style={{
                    marginTop: 4, padding: 8, background: "#000",
                    borderRadius: 4, border: "1px solid #2c2d46",
                    fontFamily: "ui-monospace, monospace", fontSize: 11,
                    maxHeight: 240, overflow: "auto",
                  }}>
                    {lifecycleLog.map((step, i) => (
                      <div key={i} style={{ marginBottom: 8, paddingBottom: 6, borderBottom: i === lifecycleLog.length - 1 ? "0" : "1px dashed #2c2d46" }}>
                        <div style={{ color: "#a5b4fc" }}>$ {step.cmd}</div>
                        <div style={{
                          color: step.exit_code === 0 ? "#86efac" : "#fca5a5",
                          fontSize: 10, marginTop: 2,
                        }}>
                          exit {step.exit_code} · {step.duration_ms} ms
                        </div>
                        {step.stdout && <pre style={{ margin: "4px 0 0", color: "#D4D4D8", whiteSpace: "pre-wrap" }}>{step.stdout}</pre>}
                        {step.stderr && <pre style={{ margin: "4px 0 0", color: "#fca5a5", whiteSpace: "pre-wrap" }}>{step.stderr}</pre>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
              <button
                type="button"
                className="chat-btn secondary"
                style={{ padding: "4px 8px", fontSize: 11 }}
                onClick={loadSandboxStatus}
                disabled={sandboxBusy}
              >
                Test connection
              </button>
              <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12, color: "#c3c5dd" }}>
                <input
                  type="checkbox"
                  checked={!!sandbox.allow_network}
                  disabled={sandboxBusy}
                  onChange={(e) => updateSandbox({ allow_network: e.target.checked })}
                />
                Allow network egress
              </label>
              <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12, color: "#c3c5dd" }}>
                Timeout
                <input
                  type="number"
                  min={1}
                  max={600}
                  value={sandbox.timeout_sec || 120}
                  disabled={sandboxBusy}
                  onChange={(e) => setSandbox({ ...sandbox, timeout_sec: Number(e.target.value) || 120 })}
                  onBlur={() => updateSandbox({ timeout_sec: Number(sandbox.timeout_sec) || 120 })}
                  style={{
                    width: 64,
                    fontSize: 12, padding: "2px 6px",
                    background: "#14152a", color: "#e6e8ff",
                    border: "1px solid #2c2d46", borderRadius: 4,
                  }}
                />
                <span style={{ color: "#9092b5" }}>s</span>
              </label>
            </div>

            {sandboxStatus?.error && (
              <div style={{
                marginTop: 8,
                padding: "6px 10px",
                borderRadius: 6,
                background: "#3d1111",
                border: "1px solid #7f1d1d",
                color: "#fca5a5",
                fontSize: 11,
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
              }}>
                {sandboxStatus.error}
              </div>
            )}
            {sandbox.backend === "matrixlab" && sandboxStatus?.ok && sandboxStatus?.remote?.version && (
              <div style={{ marginTop: 6, fontSize: 11, color: "#9092b5" }}>
                MatrixLab Runner v{sandboxStatus.remote.version}
                {typeof sandboxStatus.remote.uptime_s === "number" &&
                  ` · up ${Math.round(sandboxStatus.remote.uptime_s / 60)} min`}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
