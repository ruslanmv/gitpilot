import React, { useEffect, useState } from "react";

export default function SettingsModal({ onClose }) {
  const [settings, setSettings] = useState(null);

  const load = async () => {
    const res = await fetch("/api/settings");
    const data = await res.json();
    setSettings(data);
  };

  useEffect(() => {
    load();
  }, []);

  const changeProvider = async (provider) => {
    const res = await fetch("/api/settings/provider", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider }),
    });
    const data = await res.json();
    setSettings(data);
  };

  if (!settings) return null;

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
      </div>
    </div>
  );
}
