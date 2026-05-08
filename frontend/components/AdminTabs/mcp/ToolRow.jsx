// frontend/components/AdminTabs/mcp/ToolRow.jsx
// One row inside an expanded ServerCard. Tool name, risk badge,
// "used by" agent chips, and a per-tool enable toggle.

import React from "react";

const RISK_PALETTE = {
  low: { bg: "#0f3a26", text: "#86efac", label: "low" },
  medium: { bg: "#3a2e0f", text: "#fcd34d", label: "med" },
  high: { bg: "#3a0f0f", text: "#fca5a5", label: "high" },
};

export default function ToolRow({ tool, disabled, onToggle }) {
  const risk = RISK_PALETTE[tool.risk] || RISK_PALETTE.low;

  // Destructive tools cannot be toggled on by the UI; the backend's
  // PolicyEngine will reject a toggle PUT anyway, but disabling the
  // control surfaces the constraint up front.
  const lockEnable = tool.destructive;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "12px",
        padding: "8px 20px",
        borderBottom: "1px solid #1a1b26",
      }}
    >
      <div style={{ flex: "1 1 auto", minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            marginBottom: "2px",
          }}
        >
          <code
            style={{
              fontSize: "12px",
              color: "#e0e0e7",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {tool.name}
          </code>
          <span
            title={`risk: ${tool.risk}`}
            style={{
              padding: "1px 6px",
              borderRadius: "10px",
              background: risk.bg,
              color: risk.text,
              fontSize: "10px",
              fontWeight: 600,
              textTransform: "uppercase",
            }}
          >
            {risk.label}
          </span>
        </div>
        <div
          style={{
            display: "flex",
            gap: "4px",
            flexWrap: "wrap",
            fontSize: "10px",
            color: "#7a7d8a",
          }}
        >
          {(tool.used_by || []).length > 0 && <span>used by</span>}
          {(tool.used_by || []).map((agent) => (
            <span
              key={agent}
              style={{
                padding: "1px 6px",
                borderRadius: "10px",
                background: "#1e3a5f",
                color: "#93c5fd",
              }}
            >
              {agent}
            </span>
          ))}
        </div>
      </div>

      <Toggle
        checked={tool.enabled}
        disabled={disabled || lockEnable}
        title={
          lockEnable
            ? "Destructive tool — blocked by policy"
            : disabled
            ? "Enable the server first"
            : tool.enabled
            ? "Disable this tool for GitPilot agents"
            : "Allow GitPilot agents to call this tool"
        }
        onChange={(checked) => onToggle(checked)}
      />
    </div>
  );
}

function Toggle({ checked, disabled, title, onChange }) {
  return (
    <label title={title} style={{ display: "inline-flex", alignItems: "center" }}>
      <input
        type="checkbox"
        checked={!!checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        style={{ position: "absolute", opacity: 0, pointerEvents: "none" }}
        aria-label={title}
      />
      <span
        style={{
          width: "32px",
          height: "18px",
          borderRadius: "9px",
          background: checked && !disabled ? "#3B82F6" : "#2a2b36",
          position: "relative",
          transition: "background 0.15s",
          opacity: disabled ? 0.5 : 1,
          cursor: disabled ? "not-allowed" : "pointer",
        }}
      >
        <span
          style={{
            width: "14px",
            height: "14px",
            borderRadius: "50%",
            background: "#fff",
            position: "absolute",
            top: "2px",
            left: checked ? "16px" : "2px",
            transition: "left 0.15s",
          }}
        />
      </span>
    </label>
  );
}
