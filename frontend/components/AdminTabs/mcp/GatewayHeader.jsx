// frontend/components/AdminTabs/mcp/GatewayHeader.jsx
// Top strip of the MCP Servers tab: gateway pill + roll-up counters.

import React from "react";

export default function GatewayHeader({
  status,
  installedCount,
  enabledCount,
  totalTools,
  onRefresh,
  onSync,
  syncing,
}) {
  const reachable = status?.gateway_reachable;
  const dotColor = reachable ? "#10b981" : "#ef4444";
  const dotLabel = reachable ? "Connected" : "Unreachable";
  const gatewayUrl = status?.gateway_url || "—";

  return (
    <div
      style={{
        background: "#1a1b26",
        border: "1px solid #2a2b36",
        borderRadius: "8px",
        padding: "16px 20px",
        marginBottom: "16px",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        flexWrap: "wrap",
        gap: "12px",
      }}
    >
      <div>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            marginBottom: "4px",
          }}
        >
          <span
            aria-hidden
            style={{
              width: "10px",
              height: "10px",
              background: dotColor,
              borderRadius: "50%",
              boxShadow: `0 0 8px ${dotColor}`,
            }}
          />
          <strong style={{ fontSize: "14px", color: "#e0e0e7" }}>
            MCP Context Forge — {dotLabel}
          </strong>
          {status?.plugin_enabled === false && (
            <span
              title="Set GITPILOT_MCP_ENABLED=true to let agents call MCP tools."
              style={{
                marginLeft: "8px",
                padding: "2px 8px",
                background: "#5c1a1a",
                color: "#fecaca",
                borderRadius: "4px",
                fontSize: "11px",
                fontWeight: 600,
              }}
            >
              plugin disabled
            </span>
          )}
        </div>
        <div style={{ fontSize: "12px", color: "#a0a0b0" }}>
          Gateway: <code>{gatewayUrl}</code>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          gap: "20px",
          alignItems: "center",
          fontSize: "12px",
          color: "#cdd0d8",
        }}
      >
        <Counter label="Installed" value={installedCount} />
        <Counter label="Enabled" value={enabledCount} />
        <Counter label="Tools" value={totalTools} />
        <button
          onClick={onRefresh}
          style={{
            padding: "6px 12px",
            background: "#252634",
            color: "#e0e0e7",
            border: "1px solid #3a3b4a",
            borderRadius: "4px",
            cursor: "pointer",
            fontSize: "12px",
          }}
        >
          Refresh
        </button>
        {onSync && (
          <button
            onClick={onSync}
            disabled={!reachable || syncing}
            title={
              reachable
                ? "Pull the server registry from MCP Context Forge"
                : "Gateway unreachable — start MCP Context Forge first (make run-mcp)"
            }
            style={{
              padding: "6px 12px",
              background: reachable ? "#1e3a5f" : "#252634",
              color: reachable ? "#93c5fd" : "#7a7d8a",
              border: `1px solid ${reachable ? "#3B82F6" : "#3a3b4a"}`,
              borderRadius: "4px",
              cursor: reachable && !syncing ? "pointer" : "not-allowed",
              fontSize: "12px",
              fontWeight: 600,
              opacity: !reachable || syncing ? 0.6 : 1,
            }}
          >
            {syncing ? "Syncing…" : "Sync"}
          </button>
        )}
      </div>
    </div>
  );
}

function Counter({ label, value }) {
  return (
    <div style={{ textAlign: "center" }}>
      <div style={{ fontSize: "18px", fontWeight: 700, color: "#e0e0e7" }}>
        {value ?? "—"}
      </div>
      <div style={{ fontSize: "11px", color: "#7a7d8a", textTransform: "uppercase" }}>
        {label}
      </div>
    </div>
  );
}
