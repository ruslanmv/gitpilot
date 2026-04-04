// frontend/components/AdminTabs/AdvancedTab.jsx
import React, { useEffect, useState, useCallback } from "react";
import { apiUrl, safeFetchJSON } from "../../utils/api.js";

/**
 * Advanced tab — inline toggles for:
 *   - Lite Mode (via /api/settings/topology — sets topology to "lite_mode")
 *   - Permission Mode (normal | auto | plan via /api/permissions/mode)
 *   - Link to full Settings modal for power users
 *
 * Best practices applied:
 * - Optimistic UI with rollback on error
 * - Each setting has its own loading indicator (no global lock)
 * - Descriptions explain what each mode does
 * - ARIA-labeled toggle switches for accessibility
 */

const PERMISSION_MODES = [
  {
    value: "normal",
    label: "Normal",
    description:
      "Ask before writing files or running commands (recommended).",
  },
  {
    value: "auto",
    label: "Auto",
    description:
      "Approve all tool calls automatically. Use only when you trust the agent.",
  },
  {
    value: "plan",
    label: "Plan Only",
    description:
      "Read-only mode. Agent cannot write files or run commands.",
  },
];

function ToggleSwitch({ checked, onChange, disabled, ariaLabel }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      onClick={() => !disabled && onChange(!checked)}
      disabled={disabled}
      style={{
        position: "relative",
        width: "44px",
        height: "24px",
        borderRadius: "12px",
        background: checked ? "#3B82F6" : "#374151",
        border: "none",
        cursor: disabled ? "not-allowed" : "pointer",
        transition: "background 150ms ease",
        padding: 0,
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <div
        style={{
          position: "absolute",
          top: "2px",
          left: checked ? "22px" : "2px",
          width: "20px",
          height: "20px",
          borderRadius: "50%",
          background: "#fff",
          transition: "left 150ms ease",
          boxShadow: "0 1px 3px rgba(0,0,0,0.3)",
        }}
      />
    </button>
  );
}

export default function AdvancedTab({ showToast, onOpenFullSettings }) {
  const [liteMode, setLiteMode] = useState(false);
  const [permissionMode, setPermissionMode] = useState("normal");
  const [loading, setLoading] = useState(true);
  const [updatingLite, setUpdatingLite] = useState(false);
  const [updatingPerm, setUpdatingPerm] = useState(false);
  const [error, setError] = useState(null);

  // Initial fetch: topology preference + permission mode
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [topo, perms] = await Promise.all([
          safeFetchJSON(apiUrl("/api/settings/topology"), { timeout: 5000 })
            .catch(() => ({ topology: null })),
          safeFetchJSON(apiUrl("/api/permissions"), { timeout: 5000 })
            .catch(() => ({ mode: "normal" })),
        ]);
        if (cancelled) return;
        setLiteMode(topo?.topology === "lite_mode");
        setPermissionMode(perms?.mode || perms?.policy?.mode || "normal");
      } catch (err) {
        if (!cancelled) setError(err?.message || "Failed to load settings");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleLiteToggle = useCallback(async (next) => {
    setUpdatingLite(true);
    setError(null);
    const previous = liteMode;
    setLiteMode(next); // optimistic
    try {
      await safeFetchJSON(apiUrl("/api/settings/topology"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ topology: next ? "lite_mode" : null }),
        timeout: 5000,
      });
      showToast?.(
        "Lite Mode " + (next ? "enabled" : "disabled"),
        next
          ? "Single-agent path — better for small local models."
          : "Multi-agent path — uses full CrewAI orchestration."
      );
    } catch (err) {
      setLiteMode(previous); // rollback
      setError(err?.message || "Failed to update lite mode");
    } finally {
      setUpdatingLite(false);
    }
  }, [liteMode, showToast]);

  const handlePermissionChange = useCallback(async (next) => {
    setUpdatingPerm(true);
    setError(null);
    const previous = permissionMode;
    setPermissionMode(next); // optimistic
    try {
      const res = await fetch(apiUrl("/api/permissions/mode"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: next }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      showToast?.(
        "Permission mode updated",
        `Set to ${next}.`
      );
    } catch (err) {
      setPermissionMode(previous); // rollback
      setError(err?.message || "Failed to update permission mode");
    } finally {
      setUpdatingPerm(false);
    }
  }, [permissionMode, showToast]);

  if (loading) {
    return (
      <div>
        <h3 style={{ marginBottom: "16px" }}>Advanced</h3>
        <div
          style={{
            background: "#1a1b26",
            borderRadius: "8px",
            padding: "40px 20px",
            textAlign: "center",
            border: "1px solid #2a2b36",
            fontSize: "12px",
            opacity: 0.6,
          }}
        >
          Loading advanced settings...
        </div>
      </div>
    );
  }

  return (
    <div>
      <h3 style={{ marginBottom: "16px" }}>Advanced</h3>
      <p style={{ fontSize: "12px", opacity: 0.7, marginBottom: "16px" }}>
        Fine-tune GitPilot's agent behavior and safety settings.
      </p>

      {error && (
        <div
          role="alert"
          style={{
            background: "#7f1d1d",
            color: "#fecaca",
            border: "1px solid #991b1b",
            borderRadius: "8px",
            padding: "12px",
            fontSize: "12px",
            marginBottom: "16px",
          }}
        >
          {error}
        </div>
      )}

      {/* Lite Mode toggle */}
      <div
        style={{
          background: "#1a1b26",
          borderRadius: "8px",
          padding: "16px",
          border: "1px solid #2a2b36",
          marginBottom: "12px",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            gap: "16px",
          }}
        >
          <div style={{ flex: 1 }}>
            <h4 style={{ marginBottom: "4px", fontSize: "14px" }}>Lite Mode</h4>
            <p style={{ fontSize: "12px", opacity: 0.7, lineHeight: 1.5 }}>
              Use a simplified single-agent prompt instead of the multi-agent
              CrewAI pipeline. Recommended for small local models
              (qwen2.5:1.5b, deepseek-r1, phi3:mini) that struggle with the
              ReAct format.
            </p>
          </div>
          <ToggleSwitch
            checked={liteMode}
            onChange={handleLiteToggle}
            disabled={updatingLite}
            ariaLabel="Toggle Lite Mode"
          />
        </div>
      </div>

      {/* Permission Mode selector */}
      <div
        style={{
          background: "#1a1b26",
          borderRadius: "8px",
          padding: "16px",
          border: "1px solid #2a2b36",
          marginBottom: "12px",
        }}
      >
        <h4 style={{ marginBottom: "4px", fontSize: "14px" }}>Permission Mode</h4>
        <p style={{ fontSize: "12px", opacity: 0.7, marginBottom: "12px" }}>
          Controls when the agent needs your approval before writing files or
          running commands.
        </p>

        <div style={{ display: "grid", gap: "8px" }}>
          {PERMISSION_MODES.map((mode) => {
            const selected = permissionMode === mode.value;
            return (
              <label
                key={mode.value}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: "10px",
                  padding: "10px 12px",
                  background: selected ? "#1e3a5f" : "#0d0e15",
                  border: selected ? "1px solid #3B82F6" : "1px solid #2a2b36",
                  borderRadius: "6px",
                  cursor: updatingPerm ? "not-allowed" : "pointer",
                  opacity: updatingPerm && !selected ? 0.5 : 1,
                }}
              >
                <input
                  type="radio"
                  name="permission-mode"
                  value={mode.value}
                  checked={selected}
                  onChange={() => handlePermissionChange(mode.value)}
                  disabled={updatingPerm}
                  style={{ marginTop: "2px", cursor: "inherit" }}
                />
                <div>
                  <div
                    style={{
                      fontSize: "13px",
                      fontWeight: 600,
                      color: selected ? "#93c5fd" : "#fff",
                    }}
                  >
                    {mode.label}
                  </div>
                  <div
                    style={{
                      fontSize: "11px",
                      opacity: 0.7,
                      marginTop: "2px",
                    }}
                  >
                    {mode.description}
                  </div>
                </div>
              </label>
            );
          })}
        </div>
      </div>

      {/* Link to full settings modal */}
      <div
        style={{
          background: "#1a1b26",
          borderRadius: "8px",
          padding: "16px",
          border: "1px solid #2a2b36",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "16px",
          }}
        >
          <div>
            <h4 style={{ marginBottom: "4px", fontSize: "14px" }}>
              Full Settings
            </h4>
            <p style={{ fontSize: "12px", opacity: 0.7 }}>
              Server URL, telemetry, debug logs, environment variables, and more.
            </p>
          </div>
          <button
            type="button"
            onClick={onOpenFullSettings}
            style={{
              padding: "8px 16px",
              background: "transparent",
              color: "#93c5fd",
              border: "1px solid #3B82F6",
              borderRadius: "4px",
              cursor: "pointer",
              fontSize: "12px",
              fontWeight: 600,
              whiteSpace: "nowrap",
            }}
          >
            Open Settings Modal
          </button>
        </div>
      </div>
    </div>
  );
}
