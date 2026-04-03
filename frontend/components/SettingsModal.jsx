import React, { useEffect, useState } from "react";

export default function SettingsModal({ onClose }) {
  const [settings, setSettings] = useState(null);
  const [models, setModels] = useState([]);
  const [modelsError, setModelsError] = useState(null);
  const [loadingModels, setLoadingModels] = useState(false);
  const [testResult, setTestResult] = useState(null); // { ok: bool, message: string }
  const [testing, setTesting] = useState(false);

  const loadSettings = async () => {
    const res = await fetch("/api/settings");
    const data = await res.json();
    setSettings(data);
  };

  useEffect(() => {
    loadSettings();
  }, []);

  const changeProvider = async (provider) => {
    const res = await fetch("/api/settings/provider", {
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

    const res = await fetch("/api/settings/llm", {
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
      const res = await fetch(`/api/settings/test?provider=${settings.provider}`);
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
      const res = await fetch("/api/settings/lite-mode", {
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
      </div>
    </div>
  );
}
