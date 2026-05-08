// frontend/components/AdminTabs/mcp/CatalogList.jsx
// Browse curated MCP servers shipped with GitPilot. Each item has a
// one-click "Install" that lands the server in the Installed tab,
// disabled by default.

import React from "react";

export default function CatalogList({ items, onInstall }) {
  if (!items?.length) {
    return (
      <p style={{ color: "#a0a0b0", fontSize: "13px" }}>
        No catalog entries shipped with this build.
      </p>
    );
  }

  return (
    <div style={{ display: "grid", gap: "12px", gridTemplateColumns: "1fr" }}>
      {items.map((item) => (
        <div
          key={item.id}
          style={{
            background: "#1a1b26",
            border: "1px solid #2a2b36",
            borderRadius: "8px",
            padding: "16px 20px",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "16px",
          }}
        >
          <div style={{ flex: "1 1 auto", minWidth: 0 }}>
            <div
              style={{
                display: "flex",
                gap: "8px",
                alignItems: "center",
                marginBottom: "4px",
              }}
            >
              <strong style={{ fontSize: "14px", color: "#e0e0e7" }}>
                {item.id}
              </strong>
              {item.installed && (
                <span
                  style={{
                    padding: "2px 8px",
                    background: "#0f3a26",
                    color: "#86efac",
                    borderRadius: "4px",
                    fontSize: "10px",
                    fontWeight: 600,
                  }}
                >
                  installed
                </span>
              )}
            </div>
            <div style={{ fontSize: "12px", color: "#a0a0b0", marginBottom: "6px" }}>
              {item.description}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
              {(item.tags || []).map((t) => (
                <span
                  key={t}
                  style={{
                    padding: "1px 6px",
                    background: "#252634",
                    border: "1px solid #2a2b36",
                    borderRadius: "10px",
                    fontSize: "10px",
                    color: "#cdd0d8",
                  }}
                >
                  {t}
                </span>
              ))}
            </div>
          </div>
          <button
            onClick={() => onInstall(item.id)}
            disabled={item.installed}
            style={{
              padding: "6px 14px",
              background: item.installed ? "#252634" : "#3B82F6",
              color: item.installed ? "#7a7d8a" : "#fff",
              border: "none",
              borderRadius: "4px",
              cursor: item.installed ? "not-allowed" : "pointer",
              fontSize: "12px",
              fontWeight: 600,
            }}
          >
            {item.installed ? "Installed" : "Install"}
          </button>
        </div>
      ))}
    </div>
  );
}
