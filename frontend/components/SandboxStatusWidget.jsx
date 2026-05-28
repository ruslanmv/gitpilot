// frontend/components/SandboxStatusWidget.jsx
//
// Always-visible sandbox health pill for the sidebar.  Polls
// /api/sandbox/status every 30s and surfaces:
//
//   ● MatrixLab Ready          (backend up, /health green)
//   ⚠ MatrixLab unavailable     (network/timeout on /health)
//   ● Local active              (using subprocess by choice)
//
// When degraded, offers two one-click recoveries:
//   * Repair  — opens Settings → Sandbox to run the install/repair flow
//   * Use Local — flips backend to subprocess via PUT /api/sandbox/config
//
// Purely informational: failures here never block the chat / planner.

import React, { useCallback, useEffect, useRef, useState } from "react";

const POLL_MS = 30_000;

const BACKEND_LABEL = {
  subprocess: "Local",
  matrixlab: "MatrixLab",
  off: "Pass-through",
};

export default function SandboxStatusWidget({ onOpenSettings }) {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [switching, setSwitching] = useState(false);
  const timerRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/sandbox/status");
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || `HTTP ${res.status}`);
        return;
      }
      setStatus(data);
      setError(null);
    } catch (err) {
      setError(err.message || "Unable to reach sandbox status");
    }
  }, []);

  useEffect(() => {
    refresh();
    timerRef.current = setInterval(refresh, POLL_MS);
    return () => clearInterval(timerRef.current);
  }, [refresh]);

  const switchToLocal = async () => {
    setSwitching(true);
    try {
      await fetch("/api/sandbox/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ backend: "subprocess" }),
      });
      await refresh();
    } finally {
      setSwitching(false);
    }
  };

  if (!status && !error) {
    return (
      <div style={s.shell}>
        <div style={s.title}>Sandbox</div>
        <div style={s.dim}>Checking…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div style={s.shell}>
        <div style={s.title}>Sandbox</div>
        <div style={{ ...s.statusRow, color: "#fca5a5" }}>
          <span>⚠</span> Status check failed
        </div>
        <div style={s.dim}>{error}</div>
      </div>
    );
  }

  const backend = status.backend || "subprocess";
  const ok = !!status.ok;
  const label = BACKEND_LABEL[backend] || backend;
  const dot = ok ? "●" : "⚠";
  const dotColor = ok ? "#10B981" : "#f59e0b";
  const stateText = ok ? "Ready" : "Unavailable";

  return (
    <div style={s.shell}>
      <div style={s.titleRow}>
        <span style={s.title}>Sandbox</span>
        <button type="button" onClick={refresh} style={s.refresh}
                title="Refresh sandbox status">↻</button>
      </div>
      <div style={s.statusRow}>
        <span style={{ color: dotColor, marginRight: 6 }}>{dot}</span>
        <strong>{label}</strong>
        <span style={s.dim}> · {stateText}</span>
      </div>
      {status.matrixlab_url && backend === "matrixlab" && (
        <div style={s.metaRow}>
          <span style={s.metaKey}>URL</span>
          <span style={s.metaVal}>{status.matrixlab_url}</span>
        </div>
      )}
      <div style={s.metaRow}>
        <span style={s.metaKey}>Timeout</span>
        <span style={s.metaVal}>{status.timeout_sec}s</span>
      </div>
      <div style={s.metaRow}>
        <span style={s.metaKey}>Network</span>
        <span style={s.metaVal}>
          {status.allow_network ? "Enabled" : "Disabled"}
        </span>
      </div>

      <div style={s.actions}>
        {onOpenSettings && (
          <button type="button" onClick={onOpenSettings} style={s.action}>
            {ok ? "Change" : "Repair"}
          </button>
        )}
        {!ok && backend === "matrixlab" && (
          <button
            type="button"
            onClick={switchToLocal}
            disabled={switching}
            style={{ ...s.action, opacity: switching ? 0.6 : 1 }}
            title="Switch the sandbox backend to Local subprocess"
          >
            {switching ? "Switching…" : "Use Local"}
          </button>
        )}
      </div>
    </div>
  );
}

const s = {
  shell: {
    padding: "10px 12px",
    background: "#0d0e17",
    border: "1px solid #1f2937",
    borderRadius: 8,
    fontFamily: "system-ui, sans-serif",
    fontSize: 12,
    color: "#e4e4e7",
  },
  titleRow: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  title: {
    fontSize: 10, fontWeight: 700, letterSpacing: "0.06em",
    color: "#9092b5", textTransform: "uppercase",
  },
  refresh: {
    background: "transparent", color: "#71717a", border: "none",
    cursor: "pointer", fontSize: 14, padding: 0,
  },
  statusRow: { marginTop: 6, display: "flex", alignItems: "center" },
  metaRow: { marginTop: 4, display: "flex", justifyContent: "space-between" },
  metaKey: { color: "#9092b5", fontSize: 11 },
  metaVal: { color: "#d4d4d8", fontSize: 11, fontFamily: "ui-monospace, monospace" },
  actions: { display: "flex", gap: 6, marginTop: 8 },
  action: {
    flex: 1, padding: "4px 8px", fontSize: 11,
    background: "transparent", color: "#a1a1aa",
    border: "1px solid #3F3F46", borderRadius: 4, cursor: "pointer",
  },
  dim: { color: "#71717a", fontSize: 11 },
};
