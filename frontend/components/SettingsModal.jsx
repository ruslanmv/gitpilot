import React, { useEffect, useState } from "react";

export default function SettingsModal({ onClose }) {
  const [settings, setSettings] = useState(null);
  const [models, setModels] = useState([]);
  const [modelsError, setModelsError] = useState(null);
  const [loadingModels, setLoadingModels] = useState(false);

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

          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
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
      </div>
    </div>
  );
}
