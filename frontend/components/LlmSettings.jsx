import React, { useEffect, useMemo, useState } from "react";
import { testProvider, apiUrl } from "../utils/api";

const PROVIDERS = ["ollabridge", "openai", "claude", "watsonx", "ollama"];

const PROVIDER_LABELS = {
  ollabridge: "OllaBridge Cloud",
  openai: "OpenAI",
  claude: "Claude",
  watsonx: "Watsonx",
  ollama: "Ollama",
};

const AUTH_MODES = [
  { id: "device", label: "Device Pairing", icon: "📱" },
  { id: "apikey", label: "API Key", icon: "🔑" },
  { id: "local", label: "Local Trust", icon: "🏠" },
];

function LoadingState({ loadingMessage, loadingSlow, onRetry }) {
  return (
    <div className="settings-loading-shell">
      <div className="settings-loading-card">
        <div className="settings-loading-spinner" aria-hidden="true" />
        <h1>AI Providers</h1>
        <div className="settings-loading-subtitle">Admin / LLM Settings</div>
        <p className="settings-loading-text">{loadingMessage}</p>

        {loadingSlow && (
          <div className="settings-loading-slow">
            <p>
              This is taking longer than expected. The backend may still be
              starting or the settings endpoint may be slow.
            </p>
            <button
              type="button"
              className="settings-secondary-btn"
              onClick={onRetry}
            >
              Retry
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default function LlmSettings() {
  const [settings, setSettings] = useState(null);
  const [initialLoading, setInitialLoading] = useState(true);
  const [loadingSlow, setLoadingSlow] = useState(false);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [savedMsg, setSavedMsg] = useState("");

  const [modelsByProvider, setModelsByProvider] = useState({});
  const [modelsError, setModelsError] = useState("");
  const [loadingModelsFor, setLoadingModelsFor] = useState("");

  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  const [authMode, setAuthMode] = useState("local");
  const [pairCode, setPairCode] = useState("");
  const [pairing, setPairing] = useState(false);
  const [pairResult, setPairResult] = useState(null);

  const loadingMessage = useMemo(() => {
    if (loadingSlow) {
      return "Still loading provider configuration…";
    }
    return "Loading current configuration…";
  }, [loadingSlow]);

  const loadSettings = async () => {
    setInitialLoading(true);
    setError("");
    setLoadingSlow(false);

    let slowTimer;
    try {
      slowTimer = window.setTimeout(() => {
        setLoadingSlow(true);
      }, 1500);

      const res = await fetch(apiUrl("/api/settings"));
      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.error || "Failed to load settings");
      }

      setSettings(data);
    } catch (e) {
      console.error(e);
      setError(e.message || "Failed to load settings");
    } finally {
      window.clearTimeout(slowTimer);
      setInitialLoading(false);
    }
  };

  useEffect(() => {
    loadSettings();
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
      const res = await fetch(apiUrl("/api/settings/llm"), {
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
      setError(e.message || "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const loadModelsForProvider = async (provider) => {
    setModelsError("");
    setLoadingModelsFor(provider);

    try {
      const res = await fetch(apiUrl(`/api/settings/models?provider=${provider}`));
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
      setModelsError(e.message || "Failed to load models");
    } finally {
      setLoadingModelsFor("");
    }
  };

  const handlePair = async () => {
    if (!pairCode.trim()) return;

    setPairing(true);
    setPairResult(null);

    try {
      const baseUrl =
        settings?.ollabridge?.base_url || "https://ruslanmv-ollabridge.hf.space";

      const res = await fetch(apiUrl("/api/ollabridge/pair"), {
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
        setPairResult({
          ok: false,
          message: data.error || "Pairing failed",
        });
      }
    } catch (e) {
      setPairResult({ ok: false, message: e.message || "Pairing failed" });
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
      setTestResult({
        health: "error",
        warning: err.message || "Test failed",
      });
    } finally {
      setTesting(false);
    }
  };

  if (initialLoading) {
    return (
      <LoadingState
        loadingMessage={loadingMessage}
        loadingSlow={loadingSlow}
        onRetry={loadSettings}
      />
    );
  }

  if (!settings) {
    return (
      <div className="settings-root">
        <div className="settings-inline-error-card">
          <h1>AI Providers</h1>
          <div className="settings-loading-subtitle">Admin / LLM Settings</div>
          <p className="settings-error-text">
            {error || "Unable to load current configuration."}
          </p>
          <button
            type="button"
            className="settings-secondary-btn"
            onClick={loadSettings}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const { provider } = settings;
  const availableModels = modelsByProvider[provider] || [];

  return (
    <div className="settings-root">
      <h1>AI Providers</h1>
      <p className="settings-muted">
        Choose which LLM provider GitPilot should use for planning and agent
        workflows. Provider settings are stored on the server.
      </p>

      <div className="settings-upsell">
        <div className="settings-upsell-icon" aria-hidden="true">✨</div>
        <div className="settings-upsell-body">
          <div className="settings-upsell-title">
            {provider === "ollabridge"
              ? "You're on OllaBridge Cloud — free, no setup required."
              : "Tip: OllaBridge Cloud is free and needs no setup."}
          </div>
          <div className="settings-upsell-text">
            Perfect for getting started. Need sharper results? Switch to a premium
            provider (OpenAI, Claude, Watsonx) below using your own API key — or run
            your own models on your machine and connect them with{" "}
            <a
              href="https://huggingface.co/spaces/ruslanmv/ollabridge"
              target="_blank"
              rel="noopener noreferrer"
            >
              OllaBridge ↗
            </a>
            .
          </div>
        </div>
      </div>

      {error && <div className="settings-error-banner">{error}</div>}
      {savedMsg && <div className="settings-success-banner">{savedMsg}</div>}

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
              onClick={() => setSettings((prev) => ({ ...prev, provider: p }))}
            >
              {PROVIDER_LABELS[p] || p}
            </button>
          ))}
        </div>
      </div>

      {provider === "ollabridge" && (
        <div className="settings-card">
          <div className="settings-title">OllaBridge Cloud Configuration</div>
          <div className="settings-hint" style={{ marginBottom: 12 }}>
            Connect to OllaBridge Cloud or any OllaBridge instance for LLM
            inference. No API key required for public endpoints.
          </div>

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

          {authMode === "device" && (
            <div className="ob-auth-panel">
              <div className="ob-auth-desc">
                Enter the pairing code from your OllaBridge console and click
                Pair.
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
                  {pairing ? "Pairing…" : "Pair"}
                </button>
              </div>
              {pairResult && (
                <div
                  className={
                    pairResult.ok ? "settings-success-banner" : "settings-error-banner"
                  }
                >
                  {pairResult.message}
                </div>
              )}
            </div>
          )}

          <label className="settings-label">Base URL</label>
          <input
            className="settings-input"
            value={settings.ollabridge?.base_url || ""}
            onChange={(e) =>
              updateField("ollabridge", "base_url", e.target.value)
            }
            placeholder="https://your-ollabridge-endpoint"
          />

          {(authMode === "apikey" || authMode === "local") && (
            <>
              <label className="settings-label">API Key</label>
              <input
                className="settings-input"
                type="password"
                value={settings.ollabridge?.api_key || ""}
                onChange={(e) =>
                  updateField("ollabridge", "api_key", e.target.value)
                }
                placeholder="Optional API key"
              />
            </>
          )}

          <label className="settings-label">Model</label>
          <div className="settings-inline-row">
            <input
              className="settings-input"
              value={settings.ollabridge?.model || ""}
              onChange={(e) =>
                updateField("ollabridge", "model", e.target.value)
              }
              placeholder="qwen2.5:1.5b"
            />
            <button
              type="button"
              className="settings-secondary-btn"
              onClick={() => loadModelsForProvider("ollabridge")}
              disabled={loadingModelsFor === "ollabridge"}
            >
              {loadingModelsFor === "ollabridge" ? "Loading…" : "Load Models"}
            </button>
          </div>
        </div>
      )}

      {provider === "openai" && (
        <div className="settings-card">
          <div className="settings-title">OpenAI Configuration</div>

          <label className="settings-label">API Key</label>
          <input
            className="settings-input"
            type="password"
            value={settings.openai?.api_key || ""}
            onChange={(e) => updateField("openai", "api_key", e.target.value)}
            placeholder="sk-..."
          />

          <label className="settings-label">Base URL</label>
          <input
            className="settings-input"
            value={settings.openai?.base_url || ""}
            onChange={(e) => updateField("openai", "base_url", e.target.value)}
            placeholder="Optional custom base URL"
          />

          <label className="settings-label">Model</label>
          <input
            className="settings-input"
            value={settings.openai?.model || ""}
            onChange={(e) => updateField("openai", "model", e.target.value)}
            placeholder="gpt-4o-mini"
          />
        </div>
      )}

      {provider === "claude" && (
        <div className="settings-card">
          <div className="settings-title">Claude Configuration</div>

          <label className="settings-label">API Key</label>
          <input
            className="settings-input"
            type="password"
            value={settings.claude?.api_key || ""}
            onChange={(e) => updateField("claude", "api_key", e.target.value)}
            placeholder="Anthropic API key"
          />

          <label className="settings-label">Base URL</label>
          <input
            className="settings-input"
            value={settings.claude?.base_url || ""}
            onChange={(e) => updateField("claude", "base_url", e.target.value)}
            placeholder="Optional custom base URL"
          />

          <label className="settings-label">Model</label>
          <input
            className="settings-input"
            value={settings.claude?.model || ""}
            onChange={(e) => updateField("claude", "model", e.target.value)}
            placeholder="claude-sonnet-4-5"
          />
        </div>
      )}

      {provider === "watsonx" && (
        <div className="settings-card">
          <div className="settings-title">Watsonx Configuration</div>

          <label className="settings-label">API Key</label>
          <input
            className="settings-input"
            type="password"
            value={settings.watsonx?.api_key || ""}
            onChange={(e) => updateField("watsonx", "api_key", e.target.value)}
            placeholder="Watsonx API key"
          />

          <label className="settings-label">Project ID</label>
          <input
            className="settings-input"
            value={settings.watsonx?.project_id || ""}
            onChange={(e) =>
              updateField("watsonx", "project_id", e.target.value)
            }
            placeholder="Watsonx project ID"
          />

          <label className="settings-label">Base URL</label>
          <input
            className="settings-input"
            value={settings.watsonx?.base_url || ""}
            onChange={(e) => updateField("watsonx", "base_url", e.target.value)}
            placeholder="https://api.watsonx.ai/v1"
          />

          <label className="settings-label">Model</label>
          <input
            className="settings-input"
            value={settings.watsonx?.model_id || ""}
            onChange={(e) =>
              updateField("watsonx", "model_id", e.target.value)
            }
            placeholder="meta-llama/llama-3-3-70b-instruct"
          />
        </div>
      )}

      {provider === "ollama" && (
        <div className="settings-card">
          <div className="settings-title">Ollama Configuration</div>

          <label className="settings-label">Base URL</label>
          <input
            className="settings-input"
            value={settings.ollama?.base_url || ""}
            onChange={(e) => updateField("ollama", "base_url", e.target.value)}
            placeholder="http://localhost:11434"
          />

          <label className="settings-label">Model</label>
          <div className="settings-inline-row">
            <input
              className="settings-input"
              value={settings.ollama?.model || ""}
              onChange={(e) => updateField("ollama", "model", e.target.value)}
              placeholder="llama3"
            />
            <button
              type="button"
              className="settings-secondary-btn"
              onClick={() => loadModelsForProvider("ollama")}
              disabled={loadingModelsFor === "ollama"}
            >
              {loadingModelsFor === "ollama" ? "Loading…" : "Load Models"}
            </button>
          </div>
        </div>
      )}

      {availableModels.length > 0 && (
        <div className="settings-card">
          <div className="settings-title">Available Models</div>
          <div className="settings-model-list">
            {availableModels.map((model) => (
              <button
                key={model}
                type="button"
                className="settings-model-chip"
                onClick={() => updateField(provider, "model", model)}
              >
                {model}
              </button>
            ))}
          </div>
        </div>
      )}

      {modelsError && <div className="settings-error-banner">{modelsError}</div>}

      {testResult && (
        <div
          className={
            testResult.health === "ok"
              ? "settings-success-banner"
              : "settings-error-banner"
          }
        >
          {testResult.health === "ok"
            ? testResult.details || "Provider connection successful."
            : testResult.warning || "Provider connection failed."}
        </div>
      )}

      <div className="settings-actions">
        <button
          type="button"
          className="settings-save-btn"
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? "Saving…" : "Save Settings"}
        </button>

        <button
          type="button"
          className="settings-secondary-btn"
          onClick={handleTestConnection}
          disabled={testing}
        >
          {testing ? "Testing…" : "Test Connection"}
        </button>
      </div>
    </div>
  );
}