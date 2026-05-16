// frontend/components/AdminTabs/SandboxTab.jsx
import React, { useCallback, useEffect, useState } from "react";
import { apiUrl } from "../../utils/api.js";
import MatrixLabInstallModal from "./MatrixLabInstallModal.jsx";

/**
 * Sandbox tab — enterprise default view.
 *
 * Surfaces a single MatrixLab addon card plus a clean Local-sandbox
 * card. Implementation knobs (Runner URL, token, image, network,
 * timeout, pass-through, lifecycle env flag) live behind the install
 * modal's Advanced disclosure — the primary view never shows them.
 *
 * Status pill is driven off /api/matrixlab/status so we never end up
 * with the "Unreachable + Running" contradiction the legacy panel
 * produced.
 */

function StatusPill({ status }) {
  const map = {
    not_installed:    { label: "Not installed",    bg: "#374151", fg: "#d1d5db" },
    installing:       { label: "Installing",       bg: "#0d3320", fg: "#86efac" },
    starting:         { label: "Starting",         bg: "#0d3320", fg: "#86efac" },
    stopping:         { label: "Stopping",         bg: "#3d2d11", fg: "#fde68a" },
    checking:         { label: "Checking",         bg: "#0d3320", fg: "#86efac" },
    ready:            { label: "Ready",            bg: "#0d3320", fg: "#86efac" },
    needs_attention:  { label: "Needs attention",  bg: "#3d2d11", fg: "#fde68a" },
    failed:           { label: "Failed",           bg: "#3d1111", fg: "#fca5a5" },
  };
  const pill = map[status] || map.not_installed;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 6,
      fontSize: 11, fontWeight: 600, padding: "2px 10px", borderRadius: 12,
      background: pill.bg, color: pill.fg,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: pill.fg }} />
      {pill.label}
    </span>
  );
}

export default function SandboxTab({ showToast }) {
  const [matrixlab, setMatrixlab] = useState(null);  // /api/matrixlab/status payload
  const [sandbox, setSandbox] = useState(null);      // /api/sandbox/status payload
  const [loading, setLoading] = useState(true);
  const [modalOpen, setModalOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      const [ml, sb] = await Promise.all([
        fetch(apiUrl("/api/matrixlab/status")).then((r) => r.json()).catch(() => null),
        fetch(apiUrl("/api/sandbox/status")).then((r) => r.json()).catch(() => null),
      ]);
      setMatrixlab(ml);
      setSandbox(sb);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const useLocal = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/sandbox/config"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ backend: "subprocess" }),
      });
      const data = await r.json();
      if (r.ok) {
        setSandbox(data);
        showToast?.("Switched to Local sandbox", "Code runs in a host subprocess with a workspace jail.");
      }
    } catch (err) {
      // surfaced through the next load()
    }
  }, [showToast]);

  if (loading) {
    return (
      <div>
        <h3 style={{ marginBottom: 16 }}>Sandbox</h3>
        <div style={{
          background: "#1a1b26", borderRadius: 8,
          padding: "40px 20px", textAlign: "center",
          border: "1px solid #2a2b36", fontSize: 12, opacity: 0.6,
        }}>
          Loading sandbox status…
        </div>
      </div>
    );
  }

  const activeBackend = (sandbox?.backend || "subprocess").toLowerCase();
  const mlStatus = matrixlab?.status || "not_installed";

  return (
    <div>
      <h3 style={{ marginBottom: 8 }}>Sandbox</h3>
      <p style={{ fontSize: 12, opacity: 0.7, marginBottom: 20 }}>
        Pick where GitPilot executes code: the local subprocess sandbox or
        the MatrixLab Runner. The choice applies to the Run button on chat
        code blocks and the agent's EXECUTE action.
      </p>

      {/* MatrixLab addon card */}
      <div style={cardStyle}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 14 }}>
          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
              <h4 style={{ margin: 0, fontSize: 15 }}>MatrixLab Addon</h4>
              <StatusPill status={mlStatus} />
              {activeBackend === "matrixlab" && (
                <span style={{
                  fontSize: 10, fontWeight: 600,
                  padding: "1px 6px", borderRadius: 8,
                  background: "#1e3a5f", color: "#93c5fd",
                  border: "1px solid #3B82F6",
                }}>ACTIVE</span>
              )}
            </div>
            <p style={{ fontSize: 12, opacity: 0.75, margin: 0, lineHeight: 1.55 }}>
              Run code safely in isolated, temporary containers. Recommended
              for enterprise deployments and untrusted code.
            </p>
            {mlStatus !== "not_installed" && matrixlab?.message && (
              <p style={{ fontSize: 11, opacity: 0.6, marginTop: 6, marginBottom: 0 }}>
                {matrixlab.message}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={() => setModalOpen(true)}
            style={{
              padding: "8px 16px", fontSize: 12, fontWeight: 600,
              background: mlStatus === "ready" ? "transparent" : "#3B82F6",
              color: mlStatus === "ready" ? "#93c5fd" : "#fff",
              border: mlStatus === "ready" ? "1px solid #3B82F6" : "none",
              borderRadius: 6, cursor: "pointer",
              whiteSpace: "nowrap",
            }}
          >
            {mlStatus === "ready" ? "Manage" :
             mlStatus === "needs_attention" ? "Fix connection" :
             mlStatus === "failed" ? "Retry install" :
             "Install and Start"}
          </button>
        </div>
      </div>

      {/* Local sandbox card */}
      <div style={cardStyle}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 14 }}>
          <div style={{ flex: 1 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
              <h4 style={{ margin: 0, fontSize: 15 }}>Local sandbox</h4>
              {activeBackend === "subprocess" && (
                <span style={{
                  fontSize: 10, fontWeight: 600,
                  padding: "1px 6px", borderRadius: 8,
                  background: "#1e3a5f", color: "#93c5fd",
                  border: "1px solid #3B82F6",
                }}>ACTIVE</span>
              )}
            </div>
            <p style={{ fontSize: 12, opacity: 0.75, margin: 0, lineHeight: 1.55 }}>
              Host subprocess with a workspace jail. No Docker required —
              best for trying simple snippets quickly.
            </p>
          </div>
          <button
            type="button"
            onClick={useLocal}
            disabled={activeBackend === "subprocess"}
            style={{
              padding: "8px 16px", fontSize: 12, fontWeight: 600,
              background: activeBackend === "subprocess" ? "transparent" : "#374151",
              color: activeBackend === "subprocess" ? "#9092b5" : "#fff",
              border: activeBackend === "subprocess" ? "1px solid #2c2d46" : "none",
              borderRadius: 6,
              cursor: activeBackend === "subprocess" ? "default" : "pointer",
              whiteSpace: "nowrap",
            }}
          >
            {activeBackend === "subprocess" ? "Active" : "Use Local"}
          </button>
        </div>
      </div>

      {modalOpen && (
        <MatrixLabInstallModal
          onClose={() => {
            setModalOpen(false);
            load();
          }}
          onActivated={(data) => {
            showToast?.("MatrixLab ready", "Code now runs in MatrixLab sandboxes.");
            // Refresh both panels so the ACTIVE badge moves to the addon card.
            load();
          }}
        />
      )}
    </div>
  );
}

const cardStyle = {
  background: "#1a1b26",
  borderRadius: 8,
  padding: 16,
  border: "1px solid #2a2b36",
  marginBottom: 12,
};
