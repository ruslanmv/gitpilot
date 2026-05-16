// frontend/components/AdminTabs/SandboxTab.jsx
import React, { useEffect, useState, useCallback } from "react";
import { apiUrl } from "../../utils/api.js";

/**
 * Sandbox tab — picks the runtime that executes code generated in chat
 * (the Run button on AssistantMessage code blocks, and the EXECUTE action
 * in the planner). Two production backends:
 *
 *   - subprocess (Local): host subprocess with workspace jail, no Docker
 *     needed, recommended for trying snippets quickly.
 *   - matrixlab (MatrixLab Runner): containerised per-language sandboxes
 *     served over HTTP, recommended for enterprise / untrusted code.
 *
 * "off" / pass-through is also exposed for local development.
 *
 * Mirrors the sandbox section that already lives in SettingsModal — kept
 * additive (the modal continues to work) so users can reach the same
 * controls from either Admin → Sandbox or the legacy settings cog.
 */

const SANDBOX_BACKENDS = [
  {
    id: "subprocess",
    label: "Local",
    sub: "Host subprocess with a workspace jail. Default — best for trying simple snippets.",
  },
  {
    id: "matrixlab",
    label: "MatrixLab",
    sub: "Containerised, ephemeral sandboxes from a MatrixLab Runner. Recommended for enterprise.",
  },
  {
    id: "off",
    label: "Pass-through",
    sub: "Run on the host with no jail. Local development only.",
  },
];

export default function SandboxTab({ showToast }) {
  const [sandbox, setSandbox] = useState(null);
  const [sandboxStatus, setSandboxStatus] = useState(null);
  const [sandboxTokenInput, setSandboxTokenInput] = useState("");
  const [sandboxBusy, setSandboxBusy] = useState(false);
  const [lifecycle, setLifecycle] = useState(null);
  const [lifecycleBusy, setLifecycleBusy] = useState(null);
  const [lifecycleLog, setLifecycleLog] = useState([]);
  const [showLifecycleLog, setShowLifecycleLog] = useState(false);
  const [loading, setLoading] = useState(true);

  const loadSandboxStatus = useCallback(async () => {
    try {
      const res = await fetch(apiUrl("/api/sandbox/status"));
      const data = await res.json();
      setSandboxStatus({ ok: data.ok, error: data.error, remote: data.remote });
      setSandbox((prev) => ({
        ...(prev || {}),
        backend: data.backend,
        matrixlab_url: data.matrixlab_url,
        matrixlab_image: data.matrixlab_image,
        allow_network: data.allow_network,
        timeout_sec: data.timeout_sec,
        has_token: data.has_token,
        env_override: data.env_override,
      }));
    } catch (err) {
      setSandboxStatus({ ok: false, error: err.message || "status probe failed" });
    }
  }, []);

  const updateSandbox = useCallback(async (patch) => {
    setSandboxBusy(true);
    try {
      const res = await fetch(apiUrl("/api/sandbox/config"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      const data = await res.json();
      if (!res.ok) {
        setSandboxStatus({ ok: false, error: data.detail || "update failed" });
        return;
      }
      setSandbox((prev) => ({
        ...(prev || {}),
        backend: data.backend,
        matrixlab_url: data.matrixlab_url,
        matrixlab_image: data.matrixlab_image,
        allow_network: data.allow_network,
        timeout_sec: data.timeout_sec,
        has_token: data.has_token,
        env_override: data.env_override,
      }));
      setSandboxStatus({ ok: data.ok, error: data.error, remote: data.remote });
      if ("matrixlab_token" in patch) setSandboxTokenInput("");
      if ("backend" in patch && showToast) {
        showToast?.("Sandbox backend updated", `Now using ${data.backend}.`);
      }
    } finally {
      setSandboxBusy(false);
    }
  }, [showToast]);

  const loadLifecycle = useCallback(async () => {
    try {
      const res = await fetch(apiUrl("/api/sandbox/matrixlab/lifecycle"));
      const data = await res.json();
      setLifecycle(data);
      if (Array.isArray(data.steps) && data.steps.length) {
        setLifecycleLog(data.steps);
      }
    } catch (err) {
      setLifecycle({
        docker_available: false,
        installed: false,
        running: false,
        lifecycle_enabled: false,
        error: err.message || "lifecycle probe failed",
      });
    }
  }, []);

  const runLifecycle = useCallback(async (action) => {
    if (!["install", "start", "stop"].includes(action)) return;
    setLifecycleBusy(action);
    setShowLifecycleLog(true);
    try {
      const res = await fetch(apiUrl(`/api/sandbox/matrixlab/${action}`), { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setLifecycle((prev) => ({ ...(prev || {}), error: data.detail || `HTTP ${res.status}` }));
        return;
      }
      setLifecycle(data);
      setLifecycleLog(data.steps || []);
      loadSandboxStatus();
    } finally {
      setLifecycleBusy(null);
    }
  }, [loadSandboxStatus]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await Promise.all([loadSandboxStatus(), loadLifecycle()]);
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [loadSandboxStatus, loadLifecycle]);

  if (loading || !sandbox) {
    return (
      <div>
        <h3 style={{ marginBottom: "16px" }}>Sandbox</h3>
        <div style={{
          background: "#1a1b26", borderRadius: "8px",
          padding: "40px 20px", textAlign: "center",
          border: "1px solid #2a2b36", fontSize: "12px", opacity: 0.6,
        }}>
          Loading sandbox status…
        </div>
      </div>
    );
  }

  return (
    <div>
      <h3 style={{ marginBottom: "8px" }}>Sandbox</h3>
      <p style={{ fontSize: "12px", opacity: 0.7, marginBottom: "16px" }}>
        Pick where GitPilot executes code: the local subprocess sandbox or
        the MatrixLab Runner. The choice applies to the Run button on chat
        code blocks and to the agent's EXECUTE action.
      </p>

      {/* Header card with reachability pill */}
      <div style={{
        background: "#1a1b26", borderRadius: "8px",
        padding: "16px", border: "1px solid #2a2b36",
        marginBottom: "12px",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
          <div style={{ color: "#c3c5dd", fontWeight: 600, fontSize: 14 }}>Sandbox runtime</div>
          {sandboxStatus && (
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 6,
              fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 10,
              background: sandboxStatus.ok ? "#0d3320" : "#3d1111",
              border: `1px solid ${sandboxStatus.ok ? "#166534" : "#7f1d1d"}`,
              color: sandboxStatus.ok ? "#86efac" : "#fca5a5",
            }}>
              <span style={{
                width: 6, height: 6, borderRadius: "50%",
                background: sandboxStatus.ok ? "#10B981" : "#ef4444",
              }} />
              {sandboxStatus.ok ? "Reachable" : "Unreachable"}
            </span>
          )}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {SANDBOX_BACKENDS.map((b) => (
            <label
              key={b.id}
              style={{
                display: "flex", gap: 10, padding: "8px 10px", borderRadius: 6,
                border: `1px solid ${sandbox.backend === b.id ? "#4f46e5" : "#2c2d46"}`,
                background: sandbox.backend === b.id ? "#1a1a3a" : "transparent",
                cursor: sandboxBusy ? "not-allowed" : "pointer",
                opacity: sandboxBusy ? 0.6 : 1,
              }}
            >
              <input
                type="radio"
                name="sandbox-backend"
                value={b.id}
                checked={sandbox.backend === b.id}
                disabled={sandboxBusy}
                onChange={() => updateSandbox({ backend: b.id })}
                style={{ marginTop: 2 }}
              />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 500, color: "#e6e8ff" }}>{b.label}</div>
                <div style={{ fontSize: 11, color: "#9092b5", marginTop: 2 }}>{b.sub}</div>
              </div>
            </label>
          ))}
        </div>

        {sandbox.backend === "matrixlab" && (
          <div style={{ marginTop: 10, padding: 10, background: "#0e0f24", borderRadius: 6, border: "1px solid #2c2d46" }}>
            <div style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: 8, alignItems: "center" }}>
              <label style={{ fontSize: 12, color: "#c3c5dd" }}>Runner URL</label>
              <input
                type="text"
                value={sandbox.matrixlab_url || ""}
                onChange={(e) => setSandbox({ ...sandbox, matrixlab_url: e.target.value })}
                onBlur={() => updateSandbox({ matrixlab_url: sandbox.matrixlab_url })}
                placeholder="http://localhost:8000"
                style={{
                  fontSize: 12, padding: "4px 6px",
                  background: "#14152a", color: "#e6e8ff",
                  border: "1px solid #2c2d46", borderRadius: 4,
                }}
              />
              <label style={{ fontSize: 12, color: "#c3c5dd" }}>Bearer token</label>
              <div style={{ display: "flex", gap: 6 }}>
                <input
                  type="password"
                  value={sandboxTokenInput}
                  onChange={(e) => setSandboxTokenInput(e.target.value)}
                  placeholder={sandbox.has_token ? "•••••••• (saved)" : "Optional"}
                  style={{
                    flex: 1,
                    fontSize: 12, padding: "4px 6px",
                    background: "#14152a", color: "#e6e8ff",
                    border: "1px solid #2c2d46", borderRadius: 4,
                  }}
                />
                <button
                  type="button"
                  style={btnSecondary}
                  disabled={sandboxBusy}
                  onClick={() => updateSandbox({ matrixlab_token: sandboxTokenInput })}
                >
                  Save token
                </button>
                {sandbox.has_token && (
                  <button
                    type="button"
                    style={btnSecondary}
                    disabled={sandboxBusy}
                    onClick={() => updateSandbox({ matrixlab_token: "" })}
                    title="Clear the saved token"
                  >
                    Clear
                  </button>
                )}
              </div>
              <label style={{ fontSize: 12, color: "#c3c5dd" }}>Default image</label>
              <input
                type="text"
                value={sandbox.matrixlab_image || ""}
                onChange={(e) => setSandbox({ ...sandbox, matrixlab_image: e.target.value })}
                onBlur={() => updateSandbox({ matrixlab_image: sandbox.matrixlab_image })}
                placeholder="matrixlab-python (let runner pick)"
                style={{
                  fontSize: 12, padding: "4px 6px",
                  background: "#14152a", color: "#e6e8ff",
                  border: "1px solid #2c2d46", borderRadius: 4,
                }}
              />
            </div>
          </div>
        )}

        {sandbox.backend === "matrixlab" && lifecycle && (
          <div style={{
            marginTop: 10, padding: 10,
            background: "#0e0f24", borderRadius: 6,
            border: "1px solid #2c2d46",
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "#c3c5dd" }}>
                MatrixLab lifecycle
              </div>
              <span style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 10,
                background: lifecycle.running ? "#0d3320"
                  : lifecycle.installed ? "#3d2d11"
                  : "#3d1111",
                border: `1px solid ${lifecycle.running ? "#166534"
                  : lifecycle.installed ? "#854d0e"
                  : "#7f1d1d"}`,
                color: lifecycle.running ? "#86efac"
                  : lifecycle.installed ? "#fde68a"
                  : "#fca5a5",
              }}>
                <span style={{
                  width: 6, height: 6, borderRadius: "50%",
                  background: lifecycle.running ? "#10B981"
                    : lifecycle.installed ? "#f59e0b"
                    : "#ef4444",
                }} />
                {lifecycle.running ? "Running"
                  : lifecycle.installed ? "Installed · stopped"
                  : "Not installed"}
              </span>
              {sandbox.env_override && (
                <span style={{
                  marginLeft: 8, fontSize: 10, fontWeight: 600,
                  padding: "1px 6px", borderRadius: 9,
                  background: "rgba(217, 119, 6, 0.12)",
                  color: "#f59e0b",
                  border: "1px solid rgba(217, 119, 6, 0.35)",
                }}
                title={`Env var ${sandbox.env_override} is overriding the persisted setting`}>
                  env override
                </span>
              )}
            </div>

            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 6 }}>
              {lifecycle.running ? (
                <button
                  type="button"
                  style={btnSecondary}
                  disabled={!lifecycle.lifecycle_enabled || lifecycleBusy != null}
                  onClick={() => runLifecycle("stop")}
                  title="docker stop the GitPilot-managed runner container"
                >
                  {lifecycleBusy === "stop" ? "Stopping…" : "Stop"}
                </button>
              ) : lifecycle.installed ? (
                <button
                  type="button"
                  style={btnPrimary}
                  disabled={!lifecycle.lifecycle_enabled || lifecycleBusy != null}
                  onClick={() => runLifecycle("start")}
                  title="docker run the MatrixLab runner container"
                >
                  {lifecycleBusy === "start" ? "Starting…" : "Start"}
                </button>
              ) : (
                <button
                  type="button"
                  style={btnPrimary}
                  disabled={!lifecycle.lifecycle_enabled || !lifecycle.docker_available || lifecycleBusy != null}
                  onClick={() => runLifecycle("install")}
                  title="docker pull the MatrixLab runner + sandbox images"
                >
                  {lifecycleBusy === "install" ? "Installing…" : "Install"}
                </button>
              )}
              <button
                type="button"
                style={btnSecondary}
                disabled={lifecycleBusy != null}
                onClick={loadLifecycle}
              >
                Refresh
              </button>
              {lifecycleLog.length > 0 && (
                <button
                  type="button"
                  style={btnSecondary}
                  onClick={() => setShowLifecycleLog((v) => !v)}
                >
                  {showLifecycleLog ? "Hide log" : `Show log (${lifecycleLog.length})`}
                </button>
              )}
            </div>

            {lifecycle.instructions && (
              <div style={{
                fontSize: 11, lineHeight: 1.5, color: "#fde68a",
                padding: "6px 8px", background: "#2a210d",
                border: "1px solid #854d0e", borderRadius: 4,
                marginBottom: 6,
              }}>
                {lifecycle.instructions}
              </div>
            )}
            {lifecycle.error && (
              <div style={{
                fontSize: 11, color: "#fca5a5", fontFamily: "ui-monospace, monospace",
                padding: "6px 8px", background: "#3d1111", border: "1px solid #7f1d1d",
                borderRadius: 4, marginBottom: 6,
              }}>
                {lifecycle.error}
              </div>
            )}

            {showLifecycleLog && lifecycleLog.length > 0 && (
              <div style={{
                marginTop: 4, padding: 8, background: "#000",
                borderRadius: 4, border: "1px solid #2c2d46",
                fontFamily: "ui-monospace, monospace", fontSize: 11,
                maxHeight: 240, overflow: "auto",
              }}>
                {lifecycleLog.map((step, i) => (
                  <div key={i} style={{ marginBottom: 8, paddingBottom: 6, borderBottom: i === lifecycleLog.length - 1 ? "0" : "1px dashed #2c2d46" }}>
                    <div style={{ color: "#a5b4fc" }}>$ {step.cmd}</div>
                    <div style={{
                      color: step.exit_code === 0 ? "#86efac" : "#fca5a5",
                      fontSize: 10, marginTop: 2,
                    }}>
                      exit {step.exit_code} · {step.duration_ms} ms
                    </div>
                    {step.stdout && <pre style={{ margin: "4px 0 0", color: "#D4D4D8", whiteSpace: "pre-wrap" }}>{step.stdout}</pre>}
                    {step.stderr && <pre style={{ margin: "4px 0 0", color: "#fca5a5", whiteSpace: "pre-wrap" }}>{step.stderr}</pre>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 10, flexWrap: "wrap" }}>
          <button
            type="button"
            style={btnSecondary}
            onClick={loadSandboxStatus}
            disabled={sandboxBusy}
          >
            Test connection
          </button>
          <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12, color: "#c3c5dd" }}>
            <input
              type="checkbox"
              checked={!!sandbox.allow_network}
              disabled={sandboxBusy}
              onChange={(e) => updateSandbox({ allow_network: e.target.checked })}
            />
            Allow network egress
          </label>
          <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 12, color: "#c3c5dd" }}>
            Timeout
            <input
              type="number"
              min={1}
              max={600}
              value={sandbox.timeout_sec || 120}
              disabled={sandboxBusy}
              onChange={(e) => setSandbox({ ...sandbox, timeout_sec: Number(e.target.value) || 120 })}
              onBlur={() => updateSandbox({ timeout_sec: Number(sandbox.timeout_sec) || 120 })}
              style={{
                width: 64,
                fontSize: 12, padding: "2px 6px",
                background: "#14152a", color: "#e6e8ff",
                border: "1px solid #2c2d46", borderRadius: 4,
              }}
            />
            <span style={{ color: "#9092b5" }}>s</span>
          </label>
        </div>

        {sandboxStatus?.error && (
          <div style={{
            marginTop: 8, padding: "6px 10px", borderRadius: 6,
            background: "#3d1111", border: "1px solid #7f1d1d",
            color: "#fca5a5", fontSize: 11,
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          }}>
            {sandboxStatus.error}
          </div>
        )}
        {sandbox.backend === "matrixlab" && sandboxStatus?.ok && sandboxStatus?.remote?.version && (
          <div style={{ marginTop: 6, fontSize: 11, color: "#9092b5" }}>
            MatrixLab Runner v{sandboxStatus.remote.version}
            {typeof sandboxStatus.remote.uptime_s === "number" &&
              ` · up ${Math.round(sandboxStatus.remote.uptime_s / 60)} min`}
          </div>
        )}
      </div>
    </div>
  );
}

const btnPrimary = {
  padding: "4px 10px",
  fontSize: 11,
  fontWeight: 600,
  background: "#3B82F6",
  color: "#fff",
  border: "none",
  borderRadius: 4,
  cursor: "pointer",
};

const btnSecondary = {
  padding: "4px 10px",
  fontSize: 11,
  background: "transparent",
  color: "#c3c5dd",
  border: "1px solid #2c2d46",
  borderRadius: 4,
  cursor: "pointer",
};
