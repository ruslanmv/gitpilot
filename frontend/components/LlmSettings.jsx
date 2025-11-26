import React, { useEffect, useState } from "react";

const PROVIDERS = ["openai", "claude", "watsonx", "ollama"];

export default function LlmSettings() {
  const [settings, setSettings] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [savedMsg, setSavedMsg] = useState("");

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

  if (!settings) {
    return (
      <div className="settings-root">
        <h1>Admin / LLM Settings</h1>
        <p className="settings-muted">Loading current configuration…</p>
      </div>
    );
  }

  const { provider } = settings;

  return (
    <div className="settings-root">
      <h1>Admin / LLM Settings</h1>
      <p className="settings-muted">
        Choose which LLM provider GitPilot should use for planning and agent
        workflows. Provider settings are stored on the server.
      </p>

      <div className="settings-card">
        <label className="settings-label">Active provider</label>
        <select
          className="settings-select"
          value={provider}
          onChange={(e) =>
            setSettings((prev) => ({ ...prev, provider: e.target.value }))
          }
        >
          {PROVIDERS.map((p) => (
            <option key={p} value={p}>
              {p.charAt(0).toUpperCase() + p.slice(1)}
            </option>
          ))}
        </select>
      </div>

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
          <label className="settings-label">Model</label>
          <input
            className="settings-input"
            type="text"
            placeholder="gpt-4o-mini"
            value={settings.openai?.model || ""}
            onChange={(e) => updateField("openai", "model", e.target.value)}
          />
          <label className="settings-label">Base URL (optional)</label>
          <input
            className="settings-input"
            type="text"
            placeholder="Leave empty for default, or use Azure OpenAI endpoint"
            value={settings.openai?.base_url || ""}
            onChange={(e) => updateField("openai", "base_url", e.target.value)}
          />
          <div className="settings-hint">
            Examples: gpt-4o, gpt-4o-mini, gpt-4-turbo
          </div>
        </div>
      )}

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
          <label className="settings-label">Model</label>
          <input
            className="settings-input"
            type="text"
            placeholder="claude-sonnet-4-5"
            value={settings.claude?.model || ""}
            onChange={(e) => updateField("claude", "model", e.target.value)}
          />
          <label className="settings-label">Base URL (optional)</label>
          <input
            className="settings-input"
            type="text"
            placeholder="Leave empty for default Anthropic endpoint"
            value={settings.claude?.base_url || ""}
            onChange={(e) => updateField("claude", "base_url", e.target.value)}
          />
          <div className="settings-hint">
            Examples: claude-sonnet-4-5, claude-3-opus-20240229
          </div>
        </div>
      )}

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
          <label className="settings-label">Project ID</label>
          <input
            className="settings-input"
            type="text"
            placeholder="Your watsonx project ID"
            value={settings.watsonx?.project_id || ""}
            onChange={(e) =>
              updateField("watsonx", "project_id", e.target.value)
            }
          />
          <label className="settings-label">Model ID</label>
          <input
            className="settings-input"
            type="text"
            placeholder="meta-llama/llama-3-3-70b-instruct"
            value={settings.watsonx?.model_id || ""}
            onChange={(e) =>
              updateField("watsonx", "model_id", e.target.value)
            }
          />
          <label className="settings-label">Base URL</label>
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
          <label className="settings-label">Model</label>
          <input
            className="settings-input"
            type="text"
            placeholder="llama3"
            value={settings.ollama?.model || ""}
            onChange={(e) => updateField("ollama", "model", e.target.value)}
          />
          <div className="settings-hint">
            Examples: llama3, mistral, codellama, phi3
          </div>
        </div>
      )}

      <div className="settings-actions">
        <button
          className="settings-save-btn"
          type="button"
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? "Saving…" : "Save settings"}
        </button>
        {savedMsg && <span className="settings-success">{savedMsg}</span>}
        {error && <span className="settings-error">{error}</span>}
      </div>
    </div>
  );
}
