// frontend/components/AdminTabs/MatrixLabInstallModal.jsx
import React, { useCallback, useEffect, useState } from "react";
import { apiUrl } from "../../utils/api.js";

/**
 * MatrixLab install modal — the "addon store" experience.
 *
 * Drives a single state machine off /api/matrixlab/* so the user sees
 * one coherent state at a time (not "Unreachable" + "Running" + raw
 * stack-trace at once). Default view exposes a single primary button;
 * Runner URL / token / image / network / timeout live behind an
 * Advanced disclosure.
 *
 * Status names mirror the backend MatrixLabStatus enum:
 *   not_installed | installing | starting | stopping | checking |
 *   ready | needs_attention | failed
 */

const PROGRESS_STEPS = [
  { key: "system", label: "Checking system" },
  { key: "install", label: "Downloading MatrixLab" },
  { key: "start", label: "Starting runner" },
  { key: "test", label: "Testing connection" },
  { key: "activate", label: "Setting as active sandbox" },
];

function primaryAction(status) {
  return {
    not_installed: "Install and Start",
    installing: "Installing…",
    starting: "Starting…",
    stopping: "Stopping…",
    checking: "Checking…",
    needs_attention: "Retry connection",
    failed: "Retry installation",
    ready: "Done",
  }[status] || "Install and Start";
}

function statusPill(status) {
  const map = {
    not_installed: { label: "Not installed", bg: "#374151", fg: "#d1d5db" },
    installing:    { label: "Installing",    bg: "#0d3320", fg: "#86efac" },
    starting:      { label: "Starting",      bg: "#0d3320", fg: "#86efac" },
    stopping:      { label: "Stopping",      bg: "#3d2d11", fg: "#fde68a" },
    checking:      { label: "Checking",      bg: "#0d3320", fg: "#86efac" },
    ready:         { label: "Ready",         bg: "#0d3320", fg: "#86efac" },
    needs_attention: { label: "Needs attention", bg: "#3d2d11", fg: "#fde68a" },
    failed:        { label: "Failed",        bg: "#3d1111", fg: "#fca5a5" },
  };
  return map[status] || map.not_installed;
}

export default function MatrixLabInstallModal({ onClose, onActivated }) {
  const [status, setStatus] = useState(null);   // MatrixLabStatus from backend
  const [busy, setBusy] = useState(false);
  const [progressStep, setProgressStep] = useState(null); // "install" | "start" | "test" | "activate"
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [logs, setLogs] = useState(null);       // { lines: [...], ok: bool }
  const [showLogs, setShowLogs] = useState(false);

  // Mirror of /api/sandbox/status for the Advanced panel.
  const [advanced, setAdvanced] = useState(null);
  const [tokenInput, setTokenInput] = useState("");

  const refresh = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/matrixlab/status"));
      const data = await r.json();
      setStatus(data);
    } catch (err) {
      setStatus({
        status: "failed",
        message: "GitPilot backend could not return a MatrixLab status.",
        errorCode: "BACKEND_UNREACHABLE",
        technicalDetails: { rawError: err?.message || String(err) },
      });
    }
  }, []);

  const refreshAdvanced = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/sandbox/status"));
      const data = await r.json();
      setAdvanced(data);
    } catch (err) {
      // Non-fatal — Advanced just stays empty.
    }
  }, []);

  useEffect(() => {
    refresh();
    refreshAdvanced();
  }, [refresh, refreshAdvanced]);

  const runInstall = useCallback(async () => {
    setBusy(true);
    setShowDetails(false);
    try {
      // Phase 1: install (idempotent — pulls images + starts runner)
      setProgressStep("install");
      let r = await fetch(apiUrl("/api/matrixlab/install"), { method: "POST" });
      let data = await r.json();
      setStatus(data);
      if (data.status === "failed") return;

      // Phase 2: explicit connection test so we never claim "ready"
      // without a green health probe.
      setProgressStep("test");
      r = await fetch(apiUrl("/api/matrixlab/test"), { method: "POST" });
      data = await r.json();
      setStatus(data);
      if (data.status !== "ready") return;

      // Phase 3: activate — flip the backend so the Run button uses it.
      setProgressStep("activate");
      r = await fetch(apiUrl("/api/matrixlab/activate"), { method: "POST" });
      data = await r.json();
      setStatus(data);
      if (data.status === "ready" && data.activeSandbox === "matrixlab") {
        onActivated?.(data);
      }
    } catch (err) {
      setStatus({
        status: "failed",
        message: "MatrixLab installation could not complete.",
        errorCode: "NETWORK_ERROR",
        technicalDetails: { rawError: err?.message || String(err) },
      });
    } finally {
      setBusy(false);
      setProgressStep(null);
      refreshAdvanced();
    }
  }, [onActivated, refreshAdvanced]);

  const retryConnection = useCallback(async () => {
    setBusy(true);
    setProgressStep("test");
    try {
      const r = await fetch(apiUrl("/api/matrixlab/test"), { method: "POST" });
      const data = await r.json();
      setStatus(data);
    } finally {
      setBusy(false);
      setProgressStep(null);
    }
  }, []);

  const openLogs = useCallback(async () => {
    setShowLogs(true);
    try {
      const r = await fetch(apiUrl("/api/matrixlab/logs?tail=200"));
      const data = await r.json();
      setLogs(data);
    } catch (err) {
      setLogs({ ok: false, error: err?.message || String(err), lines: [] });
    }
  }, []);

  const onPrimary = () => {
    if (!status) return;
    if (status.status === "ready") {
      onClose?.();
      return;
    }
    if (status.status === "needs_attention") {
      retryConnection();
      return;
    }
    runInstall();
  };

  const updateAdvanced = async (patch) => {
    try {
      const r = await fetch(apiUrl("/api/sandbox/config"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      const data = await r.json();
      if (r.ok) {
        setAdvanced((prev) => ({ ...(prev || {}), ...data }));
        if ("matrixlab_token" in patch) setTokenInput("");
        // Re-probe the addon status — URL / token changes may make us
        // reachable again.
        refresh();
      }
    } catch (err) {
      // surfaced through the next refresh
    }
  };

  if (!status) {
    return (
      <Backdrop onClose={onClose}>
        <ModalShell title="Install MatrixLab Addon" subtitle="Loading…" onClose={onClose}>
          <div style={{ padding: 40, textAlign: "center", opacity: 0.6 }}>
            Checking MatrixLab status…
          </div>
        </ModalShell>
      </Backdrop>
    );
  }

  const pill = statusPill(status.status);

  return (
    <Backdrop onClose={onClose}>
      <ModalShell
        title="Install MatrixLab Addon"
        subtitle="Run code safely in isolated, temporary containers."
        onClose={onClose}
      >
        {/* Status row */}
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "12px 14px", background: "#0e0f24",
          border: "1px solid #2c2d46", borderRadius: 6, marginBottom: 14,
        }}>
          <span style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            fontSize: 11, fontWeight: 600, padding: "2px 10px", borderRadius: 12,
            background: pill.bg, color: pill.fg,
          }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: pill.fg }} />
            {pill.label}
          </span>
          <div style={{ flex: 1, fontSize: 13, color: "#e6e8ff" }}>{status.message}</div>
        </div>

        {/* Inline body — copy depends on state */}
        {status.status === "not_installed" && (
          <p style={{ fontSize: 13, opacity: 0.8, lineHeight: 1.55, marginBottom: 16 }}>
            MatrixLab gives GitPilot an isolated sandbox for running code,
            testing snippets, and executing agent actions safely. It will be
            downloaded, started, and connected automatically.
          </p>
        )}

        {(busy || progressStep) && (
          <ProgressChecklist current={progressStep} />
        )}

        {status.status === "ready" && (
          <Checklist
            items={[
              ["Installed", true],
              ["Running", true],
              ["Connection verified", true],
              ["Set as active sandbox", status.activeSandbox === "matrixlab"],
            ]}
          />
        )}

        {/* Lifecycle disabled hint — friendly copy, admin detail under disclosure */}
        {status.errorCode === "LIFECYCLE_DISABLED" && (
          <div style={{
            background: "#2a210d", border: "1px solid #854d0e",
            borderRadius: 6, padding: 10, fontSize: 12,
            color: "#fde68a", marginBottom: 12,
          }}>
            Automatic installation is disabled on this GitPilot backend. Ask
            your administrator to enable MatrixLab lifecycle automation, or
            use manual setup under Advanced options.
          </div>
        )}

        {/* Technical details disclosure — only visible when there's an error */}
        {status.technicalDetails && (status.status === "needs_attention" || status.status === "failed") && (
          <details
            open={showDetails}
            onToggle={(e) => setShowDetails(e.target.open)}
            style={{ marginBottom: 12 }}
          >
            <summary style={{ cursor: "pointer", fontSize: 12, color: "#9092b5" }}>
              Technical details
            </summary>
            <pre style={{
              marginTop: 8, padding: 10, background: "#000",
              border: "1px solid #2c2d46", borderRadius: 4,
              fontSize: 11, color: "#fca5a5",
              fontFamily: "ui-monospace, monospace",
              whiteSpace: "pre-wrap", overflow: "auto", maxHeight: 200,
            }}>
              {status.technicalDetails.expected &&
                `Expected: ${status.technicalDetails.expected}\n`}
              {status.technicalDetails.actual &&
                `Actual:   ${status.technicalDetails.actual}\n`}
              {status.technicalDetails.rawError &&
                `\n${status.technicalDetails.rawError}`}
            </pre>
          </details>
        )}

        {/* Action buttons */}
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button
            type="button"
            onClick={onPrimary}
            disabled={busy}
            style={{
              padding: "10px 20px", fontSize: 13, fontWeight: 600,
              background: busy ? "#1e3a5f" : "#3B82F6",
              color: "#fff", border: "none", borderRadius: 6,
              cursor: busy ? "wait" : "pointer",
              minWidth: 180,
            }}
          >
            {primaryAction(status.status)}
          </button>

          {status.status === "ready" && (
            <button type="button" onClick={() => setShowAdvanced((v) => !v)} style={btnSecondary}>
              Run test snippet
            </button>
          )}

          {(status.status === "needs_attention" || status.status === "failed") && (
            <button type="button" onClick={openLogs} style={btnSecondary}>
              Open logs
            </button>
          )}

          <button type="button" onClick={onClose} style={btnGhost}>
            {status.status === "ready" ? "Close" : "Cancel"}
          </button>

          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            style={{
              marginLeft: "auto", padding: "6px 10px",
              background: "transparent", color: "#9092b5",
              border: "1px solid #2c2d46", borderRadius: 4,
              fontSize: 11, cursor: "pointer",
            }}
          >
            {showAdvanced ? "Hide advanced" : "Advanced options ▾"}
          </button>
        </div>

        {/* Logs viewer */}
        {showLogs && (
          <div style={{ marginTop: 14 }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: "#c3c5dd" }}>
              MatrixLab logs
            </div>
            <pre style={{
              margin: 0, padding: 10, background: "#000",
              border: "1px solid #2c2d46", borderRadius: 4,
              fontSize: 11, color: "#D4D4D8",
              fontFamily: "ui-monospace, monospace",
              maxHeight: 220, overflow: "auto", whiteSpace: "pre-wrap",
            }}>
              {logs?.ok === false ? (logs.error || "No logs available.") :
                logs?.lines?.join("\n") || "Loading logs…"}
            </pre>
          </div>
        )}

        {/* Advanced options — progressive disclosure */}
        {showAdvanced && (
          <AdvancedOptions
            advanced={advanced}
            tokenInput={tokenInput}
            setTokenInput={setTokenInput}
            onUpdate={updateAdvanced}
            disabled={busy}
          />
        )}
      </ModalShell>
    </Backdrop>
  );
}

function ProgressChecklist({ current }) {
  // Map the modal's progress phase onto a row in PROGRESS_STEPS.
  const phaseIndex = current
    ? PROGRESS_STEPS.findIndex((s) => s.key === current)
    : 0;
  return (
    <div style={{ marginBottom: 14 }}>
      {PROGRESS_STEPS.map((s, i) => {
        const done = i < phaseIndex;
        const active = i === phaseIndex;
        return (
          <div key={s.key} style={{
            display: "flex", alignItems: "center", gap: 8,
            fontSize: 12, padding: "3px 0",
            color: done ? "#86efac" : active ? "#e6e8ff" : "#9092b5",
          }}>
            <span style={{ width: 16, textAlign: "center" }}>
              {done ? "✓" : active ? "⏳" : "○"}
            </span>
            {s.label}
          </div>
        );
      })}
    </div>
  );
}

function Checklist({ items }) {
  return (
    <div style={{ marginBottom: 14 }}>
      {items.map(([label, ok]) => (
        <div key={label} style={{
          display: "flex", alignItems: "center", gap: 8,
          fontSize: 12, padding: "3px 0",
          color: ok ? "#86efac" : "#9092b5",
        }}>
          <span style={{ width: 16, textAlign: "center" }}>{ok ? "✓" : "○"}</span>
          {label}
        </div>
      ))}
    </div>
  );
}

function AdvancedOptions({ advanced, tokenInput, setTokenInput, onUpdate, disabled }) {
  if (!advanced) return null;
  return (
    <div style={{
      marginTop: 14, padding: 12,
      background: "#0e0f24", border: "1px solid #2c2d46",
      borderRadius: 6,
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 10, color: "#c3c5dd" }}>
        Advanced options
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: 8, alignItems: "center" }}>
        <label style={fieldLabel}>Runner URL</label>
        <input
          type="text"
          defaultValue={advanced.matrixlab_url || ""}
          onBlur={(e) => onUpdate({ matrixlab_url: e.target.value })}
          placeholder="http://localhost:8000"
          disabled={disabled}
          style={fieldInput}
        />

        <label style={fieldLabel}>Bearer token</label>
        <div style={{ display: "flex", gap: 6 }}>
          <input
            type="password"
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder={advanced.has_token ? "•••••••• (saved)" : "Optional"}
            disabled={disabled}
            style={{ ...fieldInput, flex: 1 }}
          />
          <button
            type="button"
            onClick={() => onUpdate({ matrixlab_token: tokenInput })}
            disabled={disabled}
            style={btnSecondary}
          >
            Save token
          </button>
        </div>

        <label style={fieldLabel}>Default image</label>
        <input
          type="text"
          defaultValue={advanced.matrixlab_image || ""}
          onBlur={(e) => onUpdate({ matrixlab_image: e.target.value })}
          placeholder="matrixlab-python"
          disabled={disabled}
          style={fieldInput}
        />

        <label style={fieldLabel}>Network access</label>
        <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12, color: "#c3c5dd" }}>
          <input
            type="checkbox"
            checked={!!advanced.allow_network}
            disabled={disabled}
            onChange={(e) => onUpdate({ allow_network: e.target.checked })}
          />
          Allow network egress
        </label>

        <label style={fieldLabel}>Timeout</label>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            type="number"
            min={1}
            max={600}
            defaultValue={advanced.timeout_sec || 120}
            onBlur={(e) => onUpdate({ timeout_sec: Number(e.target.value) || 120 })}
            disabled={disabled}
            style={{ ...fieldInput, width: 80 }}
          />
          <span style={{ fontSize: 11, color: "#9092b5" }}>seconds</span>
        </div>
      </div>

      <details style={{ marginTop: 12 }}>
        <summary style={{ cursor: "pointer", fontSize: 11, color: "#9092b5" }}>
          Manual setup
        </summary>
        <pre style={{
          margin: "8px 0 0", padding: 10, background: "#000",
          border: "1px solid #2c2d46", borderRadius: 4,
          fontSize: 11, color: "#D4D4D8",
          fontFamily: "ui-monospace, monospace",
        }}>{`# In a MatrixLab checkout:
docker compose up -d

# Or directly:
docker run -d --name gitpilot-matrixlab \\
  -p 8000:8000 \\
  -v /var/run/docker.sock:/var/run/docker.sock \\
  ruslanmv/matrixlab-runner:latest`}</pre>
      </details>

      <details style={{ marginTop: 12 }}>
        <summary style={{ cursor: "pointer", fontSize: 11, color: "#9092b5" }}>
          Developer options · Unsafe modes
        </summary>
        <div style={{ marginTop: 8, fontSize: 11, color: "#fca5a5" }}>
          Pass-through runs code directly on the host without isolation. Use
          only for local development.
        </div>
      </details>
    </div>
  );
}

function Backdrop({ children, onClose }) {
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 100,
      }}
    >
      <div onClick={(e) => e.stopPropagation()}>{children}</div>
    </div>
  );
}

function ModalShell({ title, subtitle, onClose, children }) {
  return (
    <div style={{
      width: "min(640px, 92vw)",
      maxHeight: "90vh", overflow: "auto",
      background: "#1a1b26",
      border: "1px solid #2a2b36",
      borderRadius: 10,
      padding: 20,
      color: "#e6e8ff",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 4 }}>
        <h3 style={{ margin: 0, fontSize: 18 }}>{title}</h3>
        <button
          type="button"
          onClick={onClose}
          style={{
            background: "transparent", border: "none",
            color: "#9092b5", cursor: "pointer", fontSize: 18,
          }}
          aria-label="Close"
        >
          ✕
        </button>
      </div>
      <div style={{ fontSize: 12, color: "#9092b5", marginBottom: 16 }}>{subtitle}</div>
      {children}
    </div>
  );
}

const btnSecondary = {
  padding: "8px 14px",
  fontSize: 12,
  background: "transparent",
  color: "#c3c5dd",
  border: "1px solid #2c2d46",
  borderRadius: 6,
  cursor: "pointer",
};

const btnGhost = {
  padding: "8px 14px",
  fontSize: 12,
  background: "transparent",
  color: "#9092b5",
  border: "none",
  cursor: "pointer",
};

const fieldLabel = { fontSize: 12, color: "#c3c5dd" };
const fieldInput = {
  fontSize: 12, padding: "4px 6px",
  background: "#14152a", color: "#e6e8ff",
  border: "1px solid #2c2d46", borderRadius: 4,
};
