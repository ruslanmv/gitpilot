// frontend/components/AdminTabs/mcp/ServerCard.jsx
// One installed MCP server. Collapsed shows summary + actions; expanded
// reveals the per-tool list with risk badges and individual toggles.

import React, { useState } from "react";
import ToolRow from "./ToolRow.jsx";

const RISK_PALETTE = {
  low: { bg: "#0f3a26", border: "#1f5a3e", text: "#86efac" },
  medium: { bg: "#3a2e0f", border: "#5a4a1f", text: "#fcd34d" },
  high: { bg: "#3a0f0f", border: "#5a1f1f", text: "#fca5a5" },
};

export default function ServerCard({
  server,
  onEnable,
  onDisable,
  onUninstall,
  onTest,
  onToggleTool,
  onForget,
}) {
  const [expanded, setExpanded] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  const handleTest = async () => {
    setTesting(true);
    try {
      const result = await onTest();
      setTestResult(result);
    } finally {
      setTesting(false);
    }
  };

  const statusDot = server.enabled ? "#10b981" : "#6b7280";
  const statusLabel = server.enabled ? "Enabled" : "Disabled";

  // Risk roll-up shown next to the tool count.
  const riskCounts = server.tools?.reduce(
    (acc, t) => ({ ...acc, [t.risk]: (acc[t.risk] || 0) + 1 }),
    {}
  ) || {};

  return (
    <div
      style={{
        background: "#1a1b26",
        border: "1px solid #2a2b36",
        borderRadius: "8px",
        overflow: "hidden",
      }}
    >
      <div style={{ padding: "16px 20px" }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            gap: "16px",
            flexWrap: "wrap",
          }}
        >
          <div style={{ flex: "1 1 320px", minWidth: 0 }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                marginBottom: "6px",
              }}
            >
              <span
                aria-hidden
                style={{
                  width: "8px",
                  height: "8px",
                  background: statusDot,
                  borderRadius: "50%",
                }}
              />
              <strong
                style={{
                  fontSize: "15px",
                  color: "#e0e0e7",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {server.id}
              </strong>
              <span
                style={{
                  padding: "2px 8px",
                  background: server.enabled ? "#0f3a26" : "#252634",
                  color: server.enabled ? "#86efac" : "#a0a0b0",
                  borderRadius: "4px",
                  fontSize: "11px",
                  fontWeight: 600,
                }}
              >
                {statusLabel}
              </span>
              {!server.is_known && (
                <span
                  title="Custom server (not part of the GitPilot catalog)"
                  style={{
                    padding: "2px 8px",
                    background: "#1e3a5f",
                    color: "#93c5fd",
                    borderRadius: "4px",
                    fontSize: "11px",
                  }}
                >
                  custom
                </span>
              )}
              {server.orphan && (
                <span
                  title="No longer registered in MCP Context Forge. Still works locally; click Forget to drop it, or re-attach it in Forge."
                  style={{
                    padding: "2px 8px",
                    background: "#3a2e0f",
                    color: "#fcd34d",
                    borderRadius: "4px",
                    fontSize: "11px",
                    fontWeight: 600,
                  }}
                >
                  orphan
                </span>
              )}
              {server.source === "forge-sync" && !server.orphan && (
                <span
                  title="Added by the most recent forge sync."
                  style={{
                    padding: "2px 8px",
                    background: "#1e3a5f",
                    color: "#93c5fd",
                    borderRadius: "4px",
                    fontSize: "11px",
                  }}
                >
                  via sync
                </span>
              )}
            </div>
            <div
              style={{
                fontSize: "12px",
                color: "#a0a0b0",
                marginBottom: "8px",
              }}
            >
              {server.description || "—"}
            </div>
            <div
              style={{
                fontSize: "11px",
                color: "#7a7d8a",
                marginBottom: "8px",
                wordBreak: "break-all",
              }}
            >
              <code>{server.endpoint || "—"}</code>
            </div>
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
              {(server.tags || []).map((t) => (
                <span
                  key={t}
                  style={{
                    padding: "2px 8px",
                    background: "#252634",
                    border: "1px solid #2a2b36",
                    borderRadius: "10px",
                    fontSize: "11px",
                    color: "#cdd0d8",
                  }}
                >
                  {t}
                </span>
              ))}
            </div>
          </div>

          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "8px",
              alignItems: "flex-end",
            }}
          >
            <div style={{ display: "flex", gap: "6px" }}>
              {server.enabled ? (
                <Btn variant="ghost" onClick={onDisable}>
                  Disable
                </Btn>
              ) : (
                <Btn variant="primary" onClick={onEnable}>
                  Enable
                </Btn>
              )}
              <Btn onClick={handleTest} disabled={testing}>
                {testing ? "Testing…" : "Test"}
              </Btn>
              {onForget ? (
                <Btn variant="danger" onClick={onForget}>
                  Forget
                </Btn>
              ) : (
                <Btn variant="danger" onClick={onUninstall}>
                  Uninstall
                </Btn>
              )}
            </div>
            <div style={{ fontSize: "11px", color: "#a0a0b0" }}>
              {server.tool_count} tool{server.tool_count === 1 ? "" : "s"}
              {Object.entries(riskCounts).map(([risk, count]) => (
                <span
                  key={risk}
                  title={`${count} ${risk}-risk tools`}
                  style={{
                    marginLeft: "6px",
                    padding: "1px 6px",
                    borderRadius: "10px",
                    background: RISK_PALETTE[risk]?.bg,
                    color: RISK_PALETTE[risk]?.text,
                    border: `1px solid ${RISK_PALETTE[risk]?.border}`,
                    fontSize: "10px",
                  }}
                >
                  {count} {risk}
                </span>
              ))}
            </div>
            <button
              onClick={() => setExpanded((v) => !v)}
              style={{
                padding: "4px 8px",
                background: "transparent",
                color: "#93c5fd",
                border: "none",
                cursor: "pointer",
                fontSize: "12px",
              }}
            >
              {expanded ? "Hide tools ▴" : "Show tools ▾"}
            </button>
          </div>
        </div>

        {testResult && (
          <div
            role="status"
            style={{
              marginTop: "12px",
              padding: "8px 12px",
              fontSize: "12px",
              borderRadius: "6px",
              background: testResult.ok ? "#0f3a26" : "#3a0f0f",
              border: `1px solid ${testResult.ok ? "#1f5a3e" : "#5a1f1f"}`,
              color: testResult.ok ? "#86efac" : "#fca5a5",
            }}
          >
            {testResult.ok
              ? "Healthy. Inspector confirmed the server is reachable and advertises its expected tools."
              : `Failed: ${testResult.reason || testResult.error || "unknown error"}`}
          </div>
        )}
      </div>

      {expanded && (
        <div
          style={{
            borderTop: "1px solid #2a2b36",
            background: "#16171f",
            padding: "8px 0",
          }}
        >
          {server.tools?.length ? (
            server.tools.map((t) => (
              <ToolRow
                key={t.name}
                tool={t}
                disabled={!server.enabled}
                onToggle={(enabled) => onToggleTool(t.name, enabled)}
              />
            ))
          ) : (
            <div
              style={{
                padding: "12px 20px",
                fontSize: "12px",
                color: "#a0a0b0",
              }}
            >
              No tools advertised by this server.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Btn({ children, variant = "default", ...props }) {
  const palettes = {
    default: { bg: "#252634", color: "#e0e0e7", border: "#3a3b4a" },
    primary: { bg: "#3B82F6", color: "#fff", border: "#3B82F6" },
    ghost: { bg: "transparent", color: "#cdd0d8", border: "#3a3b4a" },
    danger: { bg: "transparent", color: "#fca5a5", border: "#5a1f1f" },
  };
  const p = palettes[variant];
  return (
    <button
      {...props}
      style={{
        padding: "6px 12px",
        background: p.bg,
        color: p.color,
        border: `1px solid ${p.border}`,
        borderRadius: "4px",
        cursor: props.disabled ? "not-allowed" : "pointer",
        fontSize: "12px",
        opacity: props.disabled ? 0.6 : 1,
      }}
    >
      {children}
    </button>
  );
}
