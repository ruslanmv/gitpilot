import React, { useEffect, useState } from "react";
import { testProvider } from "../utils/api";

const PROVIDERS = ["ollabridge", "openai", "claude", "watsonx", "ollama"];

const PROVIDER_LABELS = {
  ollabridge: "OllaBridge Cloud",
  openai: "OpenAI",
  claude: "Claude",
  watsonx: "Watsonx",
  ollama: "Ollama",
};

const AUTH_MODES = [
  { id: "device", label: "Device Pairing", icon: "\uD83D\uDCF1" },
  { id: "apikey", label: "API Key", icon: "\uD83D\uDD11" },
  { id: "local", label: "Local Trust", icon: "\uD83C\uDFE0" },
];

export default function LlmSettings() {
  const [settings, setSettings] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [savedMsg, setSavedMsg] = useState("");

  const [modelsByProvider, setModelsByProvider] = useState({});
  const [modelsError, setModelsError] = useState("");
  const [loadingModelsFor, setLoadingModelsFor] = useState("");

  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  // OllaBridge pairing state
  const [authMode, setAuthMode] = useState("local");
  const [pairCode, setPairCode] = useState("");
  const [pairing, setPairing] = useState(false);
  const [pairResult, setPairResult] = useState(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch("/api/settings");
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed to load settings");
        setSettings(data);
      } catch (e) {
        console.error(e);
        setError(e.message);
      }
    };
    load();
  }, []);

  const updateField = (section, field, value) => {
    setSettings((prev) => ({
      ...prev,
      [section]: {
        ...prev[section],
        [field]: value,
      },
    }));
  };

  const handleSave = async () => {
    setSaving(true);
    setError("");
    setSavedMsg("");
    try {
      const res = await fetch("/api/settings/llm", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to save settings");
      setSettings(data);
      setSavedMsg("Settings saved successfully!");
      setTimeout(() => setSavedMsg(""), 3000);
    } catch (e) {
      console.error(e);
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const loadModelsForProvider = async (provider) => {
    setModelsError("");
    setLoadingModelsFor(provider);
    try {
      const res = await fetch(`/api/settings/models?provider=${provider}`);
      const data = await res.json();
      if (!res.ok || data.error) {
        throw new Error(data.error || "Failed to load models");
      }
      setModelsByProvider((prev) => ({
        ...prev,
        [provider]: data.models || [],
      }));
    } catch (e) {
      console.error(e);
      setModelsError(e.message);
    } finally {
      setLoadingModelsFor("");
    }
  };

  // OllaBridge device pairing
  const handlePair = async () => {
    if (!pairCode.trim()) return;
    setPairing(true);
    setPairResult(null);
    try {
      const baseUrl = settings?.ollabridge?.base_url || "https://ruslanmv-ollabridge.hf.space";
      const res = await fetch("/api/ollabridge/pair", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_url: baseUrl, code: pairCode.trim() }),
      });
      const data = await res.json();
      if (data.success) {
        setPairResult({ ok: true, message: "Paired successfully!" });
        if (data.token) {
          updateField("ollabridge", "api_key", data.token);
        }
      } else {
        setPairResult({ ok: false, message: data.error || "Pairing failed" });
      }
    } catch (e) {
      setPairResult({ ok: false, message: e.message });
    } finally {
      setPairing(false);
    }
  };

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const activeProvider = settings?.provider || "ollama";
      const config = { provider: activeProvider };

      // Add provider-specific config
      if (activeProvider === "openai" && settings?.openai) {
        config.openai = {
          api_key: settings.openai.api_key,
          base_url: settings.openai.base_url,
          model: settings.openai.model,
        };
      } else if (activeProvider === "claude" && settings?.claude) {
        config.claude = {
          api_key: settings.claude.api_key,
          base_url: settings.claude.base_url,
          model: settings.claude.model,
        };
      } else if (activeProvider === "watsonx" && settings?.watsonx) {
        config.watsonx = {
          api_key: settings.watsonx.api_key,
          project_id: settings.watsonx.project_id,
          base_url: settings.watsonx.base_url,
          model_id: settings.watsonx.model_id,
        };
      } else if (activeProvider === "ollama" && settings?.ollama) {
        config.ollama = {
          base_url: settings.ollama.base_url,
          model: settings.ollama.model,
        };
      } else if (activeProvider === "ollabridge" && settings?.ollabridge) {
        config.ollabridge = {
          base_url: settings.ollabridge.base_url,
          model: settings.ollabridge.model,
          api_key: settings.ollabridge.api_key,
        };
      }

      const result = await testProvider(config);
      setTestResult(result);
    } catch (err) {
      setTestResult({ health: "error", warning: err.message || "Test failed" });
    } finally {
      setTesting(false);
    }
  };

  if (!settings) {
    return (
      <div className="settings-root">
        <h1>Admin / LLM Settings</h1>
        <p className="settings-muted">Loading current configuration\u2026</p>
      </div>
    );
  }

  const { provider } = settings;
  const availableModels = modelsByProvider[provider] || [];

  return (
    <div className="settings-root">
      <h1>Admin / LLM Settings</h1>
      <p className="settings-muted">
        Choose which LLM provider GitPilot should use for planning and agent
        workflows. Provider settings are stored on the server.
      </p>

      {/* ACTIVE PROVIDER */}
      <div className="settings-card">
        <label className="settings-label">Active provider</label>
        <div className="settings-provider-tabs">
          {PROVIDERS.map((p) => (
            <button
              key={p}
              type="button"
              className={
                "settings-provider-tab" +
                (provider === p ? " settings-provider-tab-active" : "")
              }
              onClick={() =>
                setSettings((prev) => ({ ...prev, provider: p }))
              }
            >
              {PROVIDER_LABELS[p] || p}
            </button>
          ))}
        </div>
      </div>

      {/* ============================================================ */}
      {/* OLLABRIDGE CLOUD                                              */}
      {/* ============================================================ */}
      {provider === "ollabridge" && (
        <div className="settings-card">
          <div className="settings-title">OllaBridge Cloud Configuration</div>
          <div className="settings-hint" style={{ marginBottom: 12 }}>
            Connect to OllaBridge Cloud or any OllaBridge instance for LLM
            inference. No API key required for public endpoints.
          </div>

          {/* AUTH MODE TABS */}
          <label className="settings-label">Authentication Mode</label>
          <div className="ob-auth-tabs">
            {AUTH_MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                className={
                  "ob-auth-tab" +
                  (authMode === m.id ? " ob-auth-tab-active" : "")
                }
                onClick={() => setAuthMode(m.id)}
              >
                <span className="ob-auth-tab-icon">{m.icon}</span>
                <span>{m.label}</span>
              </button>
            ))}
          </div>

          {/* DEVICE PAIRING MODE */}
          {authMode === "device" && (
            <div className="ob-auth-panel">
              <div className="ob-auth-desc">
                Enter the pairing code from your OllaBridge console and
                click Pair.
              </div>
              <div className="ob-pair-row">
                <input
                  className="settings-input ob-pair-input"
                  type="text"
                  maxLength={9}
                  placeholder="ABCD-1234"
                  value={pairCode}
                  onChange={(e) => setPairCode(e.target.value.toUpperCase())}
                  onKeyDown={(e) => e.key === "Enter" && handlePair()}
                />
                <button
                  type="button"
                  className="ob-pair-btn"
                  onClick={handlePair}
                  disabled={pairing || !pairCode.trim()}
                >
                  {pairing ? (
                    <span className="ob-pair-spinner" />
                  ) : (
                    "\uD83D\uDD17"
                  )}{" "}
                  Pair
                </button>
              </div>
              {pairResult && (
                <div
                  className={
                    "ob-pair-result " +
                    (pairResult.ok
                      ? "ob-pair-result-ok"
                      : "ob-pair-result-err")
                  }
                >
                  {pairResult.message}
                </div>
              )}
            </div>
          )}

          {/* API KEY MODE */}
          {authMode === "apikey" && (
            <div className="ob-auth-panel">
              <div className="ob-auth-desc">
                Enter your OllaBridge API key or device token for authenticated
                access.
              </div>
              <label className="settings-label">API Key / Device Token</label>
              <input
                className="settings-input"
                type="password"
                placeholder="Enter API key or device token"
                value={settings.ollabridge?.api_key || ""}
                onChange={(e) =>
                  updateField("ollabridge", "api_key", e.target.value)
                }
              />
            </div>
          )}

          {/* LOCAL TRUST MODE */}
          {authMode === "local" && (
            <div className="ob-auth-panel">
              <div className="ob-auth-desc">
                Connect to a local or trusted OllaBridge instance without
                authentication. Ideal for local development or pre-configured
                cloud endpoints.
              </div>
            </div>
          )}

          {/* BASE URL */}
          <label className="settings-label" style={{ marginTop: 12 }}>
            Base URL
          </label>
          <input
            className="settings-input"
            type="text"
            placeholder="https://ruslanmv-ollabridge.hf.space"
            value={settings.ollabridge?.base_url || ""}
            onChange={(e) =>
              updateField("ollabridge", "base_url", e.target.value)
            }
          />
          <div className="settings-hint">
            Default: https://ruslanmv-ollabridge.hf.space (free, no key
            needed)
          </div>

          {/* MODEL */}
          <label className="settings-label" style={{ marginTop: 12 }}>
            Model
          </label>
          <div className="ob-model-row">
            <input
              className="settings-input"
              type="text"
              placeholder="qwen2.5:1.5b"
              value={settings.ollabridge?.model || ""}
              onChange={(e) =>
                updateField("ollabridge", "model", e.target.value)
              }
              style={{ flex: 1 }}
            />
            <button
              type="button"
              className="ob-fetch-btn"
              onClick={() => loadModelsForProvider("ollabridge")}
              disabled={loadingModelsFor === "ollabridge"}
            >
              {loadingModelsFor === "ollabridge" ? (
                <span className="ob-pair-spinner" />
              ) : (
                "\uD83D\uDD04"
              )}{" "}
              Fetch Models
            </button>
          </div>

          {availableModels.length > 0 && (
            <>
              <label className="settings-label" style={{ marginTop: 8 }}>
                Available models
              </label>
              <select
                className="settings-select"
                value={settings.ollabridge?.model || ""}
                onChange={(e) =>
                  updateField("ollabridge", "model", e.target.value)
                }
              >
                <option value="">-- select a model --</option>
                {availableModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </>
          )}

          <div className="settings-hint" style={{ marginTop: 4 }}>
            Examples: qwen2.5:1.5b, llama3, mistral, codellama,
            deepseek-coder
          </div>
        </div>
      )}

      {/* OPENAI */}
      {provider === "openai" && (
        <div className="settings-card">
          <div className="settings-title">OpenAI Configuration</div>

          <label className="settings-label">API Key</label>
          <input
            className="settings-input"
            type="password"
            placeholder="sk-..."
            value={settings.openai?.api_key || ""}
            onChange={(e) => updateField("openai", "api_key", e.target.value)}
          />

          <label className="settings-label" style={{ marginTop: 12 }}>
            Model
          </label>
          <input
            className="settings-input"
            type="text"
            placeholder="gpt-4o-mini"
            value={settings.openai?.model || ""}
            onChange={(e) => updateField("openai", "model", e.target.value)}
          />

          <button
            type="button"
            className="settings-load-btn"
            onClick={() => loadModelsForProvider("openai")}
            disabled={loadingModelsFor === "openai"}
          >
            {loadingModelsFor === "openai"
              ? "Loading models\u2026"
              : "Load available models"}
          </button>

          {availableModels.length > 0 && (
            <>
              <label className="settings-label" style={{ marginTop: 12 }}>
                Choose from discovered models
              </label>
              <select
                className="settings-select"
                value={settings.openai?.model || ""}
                onChange={(e) =>
                  updateField("openai", "model", e.target.value)
                }
              >
                <option value="">-- select a model --</option>
                {availableModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </>
          )}

          <label className="settings-label" style={{ marginTop: 12 }}>
            Base URL (optional)
          </label>
          <input
            className="settings-input"
            type="text"
            placeholder="Leave empty for default, or use Azure OpenAI endpoint"
            value={settings.openai?.base_url || ""}
            onChange={(e) => updateField("openai", "base_url", e.target.value)}
          />
          <div className="settings-hint">
            Examples: gpt-4o, gpt-4o-mini, gpt-4.1, gpt-4.1-mini
          </div>
        </div>
      )}

      {/* CLAUDE */}
      {provider === "claude" && (
        <div className="settings-card">
          <div className="settings-title">Claude Configuration</div>

          <label className="settings-label">API Key</label>
          <input
            className="settings-input"
            type="password"
            placeholder="sk-ant-..."
            value={settings.claude?.api_key || ""}
            onChange={(e) => updateField("claude", "api_key", e.target.value)}
          />

          <label className="settings-label" style={{ marginTop: 12 }}>
            Model
          </label>
          <input
            className="settings-input"
            type="text"
            placeholder="claude-sonnet-4-5"
            value={settings.claude?.model || ""}
            onChange={(e) => updateField("claude", "model", e.target.value)}
          />

          <button
            type="button"
            className="settings-load-btn"
            onClick={() => loadModelsForProvider("claude")}
            disabled={loadingModelsFor === "claude"}
          >
            {loadingModelsFor === "claude"
              ? "Loading models\u2026"
              : "Load available models"}
          </button>

          {availableModels.length > 0 && (
            <>
              <label className="settings-label" style={{ marginTop: 12 }}>
                Choose from discovered models
              </label>
              <select
                className="settings-select"
                value={settings.claude?.model || ""}
                onChange={(e) =>
                  updateField("claude", "model", e.target.value)
                }
              >
                <option value="">-- select a model --</option>
                {availableModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </>
          )}

          <label className="settings-label" style={{ marginTop: 12 }}>
            Base URL (optional)
          </label>
          <input
            className="settings-input"
            type="text"
            placeholder="Leave empty for default Anthropic endpoint"
            value={settings.claude?.base_url || ""}
            onChange={(e) => updateField("claude", "base_url", e.target.value)}
          />
          <div className="settings-hint">
            Examples: claude-sonnet-4-5, claude-3.7-sonnet, claude-3-opus-20240229
          </div>
        </div>
      )}

      {/* WATSONX */}
      {provider === "watsonx" && (
        <div className="settings-card">
          <div className="settings-title">IBM watsonx.ai Configuration</div>

          <label className="settings-label">API Key</label>
          <input
            className="settings-input"
            type="password"
            placeholder="Your watsonx API key"
            value={settings.watsonx?.api_key || ""}
            onChange={(e) => updateField("watsonx", "api_key", e.target.value)}
          />

          <label className="settings-label" style={{ marginTop: 12 }}>
            Project ID
          </label>
          <input
            className="settings-input"
            type="text"
            placeholder="Your watsonx project ID"
            value={settings.watsonx?.project_id || ""}
            onChange={(e) =>
              updateField("watsonx", "project_id", e.target.value)
            }
          />

          <label className="settings-label" style={{ marginTop: 12 }}>
            Model ID
          </label>
          <input
            className="settings-input"
            type="text"
            placeholder="meta-llama/llama-3-3-70b-instruct"
            value={settings.watsonx?.model_id || ""}
            onChange={(e) =>
              updateField("watsonx", "model_id", e.target.value)
            }
          />

          <button
            type="button"
            className="settings-load-btn"
            onClick={() => loadModelsForProvider("watsonx")}
            disabled={loadingModelsFor === "watsonx"}
          >
            {loadingModelsFor === "watsonx"
              ? "Loading models\u2026"
              : "Load available models"}
          </button>

          {availableModels.length > 0 && (
            <>
              <label className="settings-label" style={{ marginTop: 12 }}>
                Choose from discovered models
              </label>
              <select
                className="settings-select"
                value={settings.watsonx?.model_id || ""}
                onChange={(e) =>
                  updateField("watsonx", "model_id", e.target.value)
                }
              >
                <option value="">-- select a model --</option>
                {availableModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </>
          )}

          <label className="settings-label" style={{ marginTop: 12 }}>
            Base URL
          </label>
          <input
            className="settings-input"
            type="text"
            placeholder="https://api.watsonx.ai/v1"
            value={settings.watsonx?.base_url || ""}
            onChange={(e) =>
              updateField("watsonx", "base_url", e.target.value)
            }
          />
          <div className="settings-hint">
            Examples: meta-llama/llama-3-3-70b-instruct, ibm/granite-13b-chat-v2
          </div>
        </div>
      )}

      {/* OLLAMA */}
      {provider === "ollama" && (
        <div className="settings-card">
          <div className="settings-title">Ollama Configuration</div>

          <label className="settings-label">Base URL</label>
          <input
            className="settings-input"
            type="text"
            placeholder="http://localhost:11434"
            value={settings.ollama?.base_url || ""}
            onChange={(e) => updateField("ollama", "base_url", e.target.value)}
          />

          <label className="settings-label" style={{ marginTop: 12 }}>
            Model
          </label>
          <input
            className="settings-input"
            type="text"
            placeholder="llama3"
            value={settings.ollama?.model || ""}
            onChange={(e) => updateField("ollama", "model", e.target.value)}
          />

          <button
            type="button"
            className="settings-load-btn"
            onClick={() => loadModelsForProvider("ollama")}
            disabled={loadingModelsFor === "ollama"}
          >
            {loadingModelsFor === "ollama"
              ? "Loading models\u2026"
              : "Load available models"}
          </button>

          {availableModels.length > 0 && (
            <>
              <label className="settings-label" style={{ marginTop: 12 }}>
                Choose from discovered models
              </label>
              <select
                className="settings-select"
                value={settings.ollama?.model || ""}
                onChange={(e) =>
                  updateField("ollama", "model", e.target.value)
                }
              >
                <option value="">-- select a model --</option>
                {availableModels.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </>
          )}

          <div className="settings-hint">
            Examples: llama3, mistral, codellama, phi3
          </div>
        </div>
      )}

      {modelsError && (
        <div className="settings-error" style={{ marginTop: 8 }}>
          {modelsError}
        </div>
      )}

      <div className="settings-actions">
        <button
          onClick={handleTestConnection}
          disabled={testing}
          style={{
            padding: "8px 16px",
            background: "#2563EB",
            color: "#fff",
            border: "none",
            borderRadius: "6px",
            cursor: testing ? "not-allowed" : "pointer",
            opacity: testing ? 0.6 : 1,
            marginRight: "8px",
          }}
        >
          {testing ? "Testing..." : "Test Connection"}
        </button>
        <button
          className="settings-save-btn"
          type="button"
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? "Saving\u2026" : "Save settings"}
        </button>
        {savedMsg && <span className="settings-success">{savedMsg}</span>}
        {error && <span className="settings-error">{error}</span>}
        {testResult && (
          <div style={{
            marginTop: "12px",
            padding: "10px 14px",
            borderRadius: "6px",
            background: testResult.health === "ok" ? "#0d3320" : "#3d1111",
            border: `1px solid ${testResult.health === "ok" ? "#166534" : "#7f1d1d"}`,
            fontSize: "13px",
            width: "100%",
          }}>
            <span style={{ fontWeight: 600 }}>
              {testResult.health === "ok" ? "\u2713 Connection successful" : "\u2717 Connection failed"}
            </span>
            {testResult.warning && (
              <div style={{ marginTop: "4px", opacity: 0.8, fontSize: "12px" }}>
                {testResult.warning}
              </div>
            )}
            {testResult.model && (
              <div style={{ marginTop: "4px", opacity: 0.7, fontSize: "12px" }}>
                Model: {testResult.model}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
