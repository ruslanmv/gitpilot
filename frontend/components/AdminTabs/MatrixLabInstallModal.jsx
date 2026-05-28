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

// Progress checklists vary by which lifecycle the operator launched.
// The active "current step" key is set on the busy state so the
// checklist tracks the user's chosen journey, not a generic install.
const PROGRESS_STEPS = {
  install: [
    { key: "system",   label: "Checking system" },
    { key: "install",  label: "Downloading MatrixLab" },
    { key: "start",    label: "Starting runner" },
    { key: "test",     label: "Testing connection" },
    { key: "activate", label: "Activating MatrixLab" },
  ],
  repair: [
    { key: "system",   label: "Checking runner URL" },
    { key: "repair",   label: "Restarting runner" },
    { key: "test",     label: "Testing connection" },
    { key: "activate", label: "Activating MatrixLab" },
  ],
  reinstall: [
    { key: "system",   label: "Stopping current runner" },
    { key: "remove",   label: "Removing old addon" },
    { key: "install",  label: "Downloading fresh version" },
    { key: "start",    label: "Starting runner" },
    { key: "test",     label: "Testing connection" },
    { key: "activate", label: "Activating MatrixLab" },
  ],
};

// Primary action label and the journey it triggers, keyed by the
// addon's normalised status.  Mapping is exhaustive so the modal
// always has a clear "next thing to do" button.
const PRIMARY_BY_STATUS = {
  not_installed:   { label: "Install and Start",  action: "install"  },
  installing:      { label: "Installing…",        action: null       },
  starting:        { label: "Starting…",          action: null       },
  stopping:        { label: "Stopping…",          action: null       },
  checking:        { label: "Checking…",          action: null       },
  needs_attention: { label: "Repair connection",  action: "repair"   },
  failed:          { label: "Retry installation", action: "install"  },
  ready:           { label: "Done",               action: "done"     },
};

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
  // Which journey is currently in flight ("install" | "repair" |
  // "reinstall") — drives the progress checklist's stage list.
  const [journey, setJourney] = useState(null);
  const [progressStep, setProgressStep] = useState(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [logs, setLogs] = useState(null);
  const [showLogs, setShowLogs] = useState(false);
  // Reinstall confirmation prompt + the destructive "wipe images" opt-in.
  const [reinstallConfirm, setReinstallConfirm] = useState(false);
  const [reinstallWipe, setReinstallWipe] = useState(false);

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

  // Run a sequence of phased POSTs against /api/matrixlab/* and walk
  // the progress checklist as each one returns.  Used for install,
  // repair, and reinstall — they differ only in which endpoint kicks
  // off the journey.
  const runJourney = useCallback(async (journeyKey, kickoffPath, kickoffBody) => {
    setBusy(true);
    setJourney(journeyKey);
    setShowDetails(false);
    try {
      // Phase 1: kickoff (install / repair / reinstall).  Each backend
      // endpoint is itself idempotent and aggregates several docker
      // commands; the modal walks the checklist on the way.
      setProgressStep(journeyKey === "reinstall" ? "system" : journeyKey);
      let r = await fetch(apiUrl(kickoffPath), {
        method: "POST",
        headers: kickoffBody ? { "Content-Type": "application/json" } : undefined,
        body: kickoffBody ? JSON.stringify(kickoffBody) : undefined,
      });
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

      // Phase 3: activate (always safe to call when ready).
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
        message: `MatrixLab ${journeyKey} could not complete.`,
        errorCode: "NETWORK_ERROR",
        technicalDetails: { rawError: err?.message || String(err) },
      });
    } finally {
      setBusy(false);
      setProgressStep(null);
      setJourney(null);
      refreshAdvanced();
    }
  }, [onActivated, refreshAdvanced]);

  const runInstall   = useCallback(() => runJourney("install",   "/api/matrixlab/install"), [runJourney]);
  const runRepair    = useCallback(() => runJourney("repair",    "/api/matrixlab/repair"),  [runJourney]);
  const runReinstall = useCallback((removeData) => runJourney(
    "reinstall", "/api/matrixlab/reinstall", { remove_data: !!removeData },
  ), [runJourney]);
  // Cheap recovery: persist the discovered URL without restarting the
  // runner.  Most "needs_attention" cases are stale URLs from the
  // :8000 → :8765 port move, and this resolves them in <1s.
  const runUrlAutoDetect = useCallback(
    () => runJourney("repair", "/api/matrixlab/url/auto-detect"),
    [runJourney],
  );

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
    const next = (PRIMARY_BY_STATUS[status.status] || PRIMARY_BY_STATUS.not_installed).action;
    if (!next) return; // in-flight states have no primary action
    if (next === "done")    return onClose?.();
    if (next === "install") return runInstall();
    if (next === "repair")  return runRepair();
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
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, color: "#e6e8ff" }}>{status.message}</div>
            {status.runnerUrl && status.status !== "ready" && (
              <div style={{ fontSize: 11, color: "#9092b5", marginTop: 2 }}>
                Runner URL: <code style={{ color: "#c3c5dd" }}>{status.runnerUrl}</code>
              </div>
            )}
            {/* Stale-URL recovery: backend probes candidate ports
                when the configured URL is down. When a live one is
                found we offer a one-click switch — this fixes the
                very common "MatrixLab is installed but GitPilot
                cannot connect" after the :8000 → :8765 port move. */}
            {status.discoveredUrl && status.discoveredUrl !== status.runnerUrl && (
              <div style={{
                marginTop: 6, padding: "6px 10px", fontSize: 12,
                background: "#0d3320", color: "#86efac",
                border: "1px solid #166534", borderRadius: 4,
                display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
              }}>
                <span>
                  Found a live runner at{" "}
                  <code style={{ background: "#000", padding: "1px 4px", borderRadius: 3 }}>
                    {status.discoveredUrl}
                  </code>
                </span>
                <button
                  type="button"
                  onClick={runUrlAutoDetect}
                  disabled={busy}
                  style={{
                    padding: "3px 10px", fontSize: 11, fontWeight: 600,
                    background: "#10B981", color: "#052e1c",
                    border: "0", borderRadius: 4, cursor: "pointer",
                  }}
                >
                  Use this URL
                </button>
              </div>
            )}
          </div>
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
          <ProgressChecklist journey={journey} current={progressStep} />
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
            MatrixLab lifecycle automation is disabled. This GitPilot backend
            was started with{" "}
            <code style={{ background: "#1a1b26", padding: "1px 4px", borderRadius: 3 }}>
              GITPILOT_ENABLE_MATRIXLAB_LIFECYCLE=0
            </code>{" "}
            — restart it without that variable (the default is enabled), or
            use Manual setup under Advanced options.
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

        {/* Action buttons — state-aware. Reinstall is offered as a
            normal recovery action (not buried in Advanced) once the
            addon exists in some form; Open logs surfaces only when
            there's something to diagnose. */}
        {(() => {
          const primary = PRIMARY_BY_STATUS[status.status] || PRIMARY_BY_STATUS.not_installed;
          const canReinstall = status.installed === true ||
            ["needs_attention", "failed", "ready"].includes(status.status);
          const canSeeLogs = ["needs_attention", "failed"].includes(status.status);
          return (
            <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
              <button
                type="button"
                onClick={onPrimary}
                disabled={busy || primary.action === null}
                style={{
                  padding: "10px 20px", fontSize: 13, fontWeight: 600,
                  background: busy ? "#1e3a5f" : "#3B82F6",
                  color: "#fff", border: "none", borderRadius: 6,
                  cursor: busy ? "wait" : "pointer",
                  minWidth: 180,
                }}
              >
                {primary.label}
              </button>

              {canReinstall && (
                <button
                  type="button"
                  onClick={() => setReinstallConfirm(true)}
                  disabled={busy}
                  style={status.status === "ready" ? btnDanger : btnSecondary}
                  title="Stop, remove, and pull a fresh copy of MatrixLab"
                >
                  Reinstall addon
                </button>
              )}

              {canSeeLogs && (
                <button type="button" onClick={openLogs} disabled={busy} style={btnSecondary}>
                  Open logs
                </button>
              )}

              {status.status === "ready" && (
                <button type="button" onClick={runRepair} disabled={busy} style={btnSecondary}>
                  Run test snippet
                </button>
              )}
            </div>
          );
        })()}

        {/* Logs viewer */}
        {showLogs && (
          <div style={{ marginTop: 14 }}>
            <div style={{
              display: "flex", justifyContent: "space-between",
              alignItems: "baseline", marginBottom: 6,
            }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "#c3c5dd" }}>
                MatrixLab logs
                {logs?.container && (
                  <span style={{
                    marginLeft: 8, fontSize: 11, color: "#9092b5",
                    fontFamily: "ui-monospace, monospace", fontWeight: 400,
                  }}>
                    · {logs.container}
                  </span>
                )}
              </div>
              <button
                type="button"
                onClick={openLogs}
                disabled={busy}
                style={{
                  fontSize: 11, padding: "2px 8px",
                  background: "transparent", color: "#9092b5",
                  border: "1px solid #2c2d46", borderRadius: 4, cursor: "pointer",
                }}
              >
                ↻ Refresh
              </button>
            </div>

            {/* Friendly error + hint when the backend can't read logs.
                We show the hint as an actionable next step (run
                "make install-matrixlab") and list any matrixlab-
                shaped containers we found so the user can see whether
                the runner is up under a different name. */}
            {logs?.ok === false && (
              <div style={{
                marginBottom: 6, padding: 10,
                background: "#2a210d", border: "1px solid #854d0e",
                borderRadius: 4, fontSize: 12, color: "#fde68a",
              }}>
                <div style={{ fontWeight: 600 }}>{logs.error || "Could not read MatrixLab logs."}</div>
                {logs.hint && (
                  <div style={{ marginTop: 4, fontSize: 11, color: "#fcd34d" }}>
                    Next step:{" "}
                    <code style={{
                      background: "#000", padding: "1px 6px",
                      borderRadius: 3, color: "#86efac",
                    }}>
                      {logs.hint}
                    </code>
                  </div>
                )}
                {Array.isArray(logs.candidates) && logs.candidates.length > 0 && (
                  <div style={{ marginTop: 6, fontSize: 11 }}>
                    Found other matrixlab-shaped containers:
                    <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
                      {logs.candidates.map((c, i) => (
                        <li key={i}>
                          <code style={{ color: "#c3c5dd" }}>{c}</code>
                        </li>
                      ))}
                    </ul>
                    <div style={{ marginTop: 4, fontSize: 10, color: "#a1a1aa" }}>
                      Set <code>GITPILOT_MATRIXLAB_CONTAINER</code> to use one of these,
                      or run Reinstall to recreate the expected container.
                    </div>
                  </div>
                )}
                {logs.rawError && (
                  <details style={{ marginTop: 6 }}>
                    <summary style={{ cursor: "pointer", fontSize: 11, color: "#fbbf24" }}>
                      docker stderr
                    </summary>
                    <pre style={{
                      marginTop: 4, padding: 6, fontSize: 10,
                      background: "#000", color: "#fca5a5",
                      borderRadius: 4, whiteSpace: "pre-wrap",
                    }}>
                      {logs.rawError}
                    </pre>
                  </details>
                )}
              </div>
            )}

            <pre style={{
              margin: 0, padding: 10, background: "#000",
              border: "1px solid #2c2d46", borderRadius: 4,
              fontSize: 11, color: "#D4D4D8",
              fontFamily: "ui-monospace, monospace",
              maxHeight: 220, overflow: "auto", whiteSpace: "pre-wrap",
            }}>
              {logs == null
                ? "Loading logs…"
                : (logs.lines && logs.lines.length > 0)
                  ? logs.lines.join("\n")
                  : (logs.ok === false ? "(no log output captured)" : "Loading logs…")}
            </pre>
          </div>
        )}

        {/* Advanced options — collapsed by default so first-time
            users see the install/repair flow, not a wall of knobs.
            Operators who need runner URL / token / image / network /
            timeout / manual setup / local clone install / unsafe
            modes click the disclosure to expand. */}
        <details
          open={showAdvanced}
          onToggle={(e) => setShowAdvanced(e.target.open)}
          style={{ marginTop: 14 }}
        >
          <summary style={{
            cursor: "pointer", fontSize: 12, color: "#9092b5",
            padding: "6px 0", listStyle: "none",
          }}>
            Advanced options
          </summary>
          <AdvancedOptions
            advanced={advanced}
            tokenInput={tokenInput}
            setTokenInput={setTokenInput}
            onUpdate={updateAdvanced}
            disabled={busy}
            onLocalStatus={(data) => {
              setStatus(data);
              refreshAdvanced();
            }}
          />
        </details>

        {reinstallConfirm && (
          <ReinstallConfirm
            wipe={reinstallWipe}
            setWipe={setReinstallWipe}
            onCancel={() => {
              setReinstallConfirm(false);
              setReinstallWipe(false);
            }}
            onConfirm={() => {
              const wipe = reinstallWipe;
              setReinstallConfirm(false);
              setReinstallWipe(false);
              runReinstall(wipe);
            }}
          />
        )}
      </ModalShell>
    </Backdrop>
  );
}

function ReinstallConfirm({ wipe, setWipe, onCancel, onConfirm }) {
  // Modal-in-modal stacked at zIndex 110 (Backdrop is 100).  Apes
  // GitHub's "Are you absolutely sure?" pattern: explicit destructive
  // checkbox stays off by default so a misclick can't wipe images.
  return (
    <div onClick={onCancel} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.65)",
      display: "flex", alignItems: "center", justifyContent: "center",
      zIndex: 110,
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        width: "min(440px, 92vw)", background: "#1a1b26",
        border: "1px solid #2a2b36", borderRadius: 10, padding: 20,
        color: "#e6e8ff",
      }}>
        <h4 style={{ margin: "0 0 6px", fontSize: 15 }}>Reinstall MatrixLab Addon?</h4>
        <p style={{ fontSize: 13, opacity: 0.8, lineHeight: 1.55, marginBottom: 14 }}>
          This will stop MatrixLab, remove the current addon container, download a
          fresh copy, start it again, and reconnect GitPilot. Your GitPilot
          settings will be kept.
        </p>
        <label style={{
          display: "flex", gap: 8, alignItems: "flex-start",
          padding: "8px 10px", borderRadius: 6, marginBottom: 14,
          border: "1px solid #2c2d46", background: "#0e0f24",
          fontSize: 12, cursor: "pointer",
        }}>
          <input
            type="checkbox"
            checked={wipe}
            onChange={(e) => setWipe(e.target.checked)}
            style={{ marginTop: 2 }}
          />
          <span>
            <strong style={{ color: "#fde68a" }}>Also remove local MatrixLab data</strong>
            <span style={{ display: "block", color: "#9092b5", marginTop: 2 }}>
              Deletes the cached MatrixLab runner image. Sandbox images and
              workspace data are preserved.
            </span>
          </span>
        </label>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button type="button" onClick={onCancel} style={btnSecondary}>Cancel</button>
          <button type="button" onClick={onConfirm} style={wipe ? btnDanger : btnPrimary}>
            {wipe ? "Reinstall and remove data" : "Reinstall MatrixLab"}
          </button>
        </div>
      </div>
    </div>
  );
}

function ProgressChecklist({ journey, current }) {
  // Pick the right stage list for the journey in flight; default to
  // the install list (the most informative one) when no journey is
  // active yet.  Once ``current`` advances past the kickoff key, the
  // visited rows render with a ✓.
  const steps = PROGRESS_STEPS[journey] || PROGRESS_STEPS.install;
  const phaseIndex = current ? Math.max(0, steps.findIndex(s => s.key === current)) : 0;
  return (
    <div style={{ marginBottom: 14 }}>
      {steps.map((s, i) => {
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

function LocalCloneSection({ disabled, onStatus }) {
  const [native, setNative] = useState(null);
  const [busy, setBusy] = useState(null); // "install" | "start" | "stop"

  const refresh = useCallback(async () => {
    try {
      const r = await fetch(apiUrl("/api/matrixlab/native/status"));
      setNative(await r.json());
    } catch (err) {
      // non-fatal — leave section empty
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const runAction = useCallback(async (action) => {
    setBusy(action);
    try {
      const path = {
        install: "/api/matrixlab/install_local",
        start: "/api/matrixlab/start_local",
        stop: "/api/matrixlab/stop_local",
      }[action];
      const r = await fetch(apiUrl(path), { method: "POST" });
      const data = await r.json();
      onStatus?.(data);
    } finally {
      setBusy(null);
      refresh();
    }
  }, [onStatus, refresh]);

  if (!native) return null;

  return (
    <details style={{ marginTop: 12 }}>
      <summary style={{ cursor: "pointer", fontSize: 11, color: "#9092b5" }}>
        Local clone install (no Docker for the runner itself)
      </summary>
      <div style={{ marginTop: 8, fontSize: 11, color: "#9092b5", lineHeight: 1.6 }}>
        Clones MatrixLab into <code style={{ color: "#c3c5dd" }}>{native.local_dir}</code>,
        creates a dedicated Python virtualenv, and runs <code style={{ color: "#c3c5dd" }}>uvicorn app.main:app</code>
        from the runner directory. The Runner still spawns per-language sandboxes
        via Docker, so the host needs <code style={{ color: "#c3c5dd" }}>docker</code>{" "}
        on PATH for code execution.
      </div>
      <div style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 8 }}>
        <span style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          fontSize: 11, padding: "2px 10px", borderRadius: 10,
          background: native.running ? "#0d3320" : native.installed ? "#3d2d11" : "#374151",
          color:      native.running ? "#86efac" : native.installed ? "#fde68a" : "#d1d5db",
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: "50%",
            background: native.running ? "#10B981" : native.installed ? "#f59e0b" : "#9ca3af",
          }} />
          {native.running ? "Running (PID " + native.pid + ")"
            : native.installed ? "Installed · stopped"
            : "Not installed"}
        </span>
        {!native.installed && (
          <button
            type="button"
            disabled={disabled || busy != null || !native.lifecycleEnabled}
            onClick={() => runAction("install")}
            style={btnPrimarySmall}
          >
            {busy === "install" ? "Cloning + installing…" : "Clone and install"}
          </button>
        )}
        {native.installed && !native.running && (
          <button
            type="button"
            disabled={disabled || busy != null || !native.lifecycleEnabled}
            onClick={() => runAction("start")}
            style={btnPrimarySmall}
          >
            {busy === "start" ? "Starting…" : "Start runner"}
          </button>
        )}
        {native.running && (
          <button
            type="button"
            disabled={disabled || busy != null || !native.lifecycleEnabled}
            onClick={() => runAction("stop")}
            style={btnSecondarySmall}
          >
            {busy === "stop" ? "Stopping…" : "Stop runner"}
          </button>
        )}
        <button
          type="button"
          disabled={busy != null}
          onClick={refresh}
          style={btnSecondarySmall}
        >
          Refresh
        </button>
      </div>
      {!native.lifecycleEnabled && (
        <div style={{
          marginTop: 8, padding: "6px 8px",
          background: "#2a210d", border: "1px solid #854d0e",
          borderRadius: 4, fontSize: 11, color: "#fde68a",
        }}>
          Local clone install needs lifecycle automation enabled on the GitPilot
          backend.
        </div>
      )}
    </details>
  );
}

function AdvancedOptions({ advanced, tokenInput, setTokenInput, onUpdate, disabled, onLocalStatus }) {
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
          placeholder="http://localhost:8765"
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

      <LocalCloneSection disabled={disabled} onStatus={onLocalStatus} />

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

const btnPrimary = {
  padding: "8px 14px",
  fontSize: 12,
  fontWeight: 600,
  background: "#3B82F6",
  color: "#fff",
  border: "none",
  borderRadius: 6,
  cursor: "pointer",
};

// "Reinstall and remove data" is destructive — surface it with a
// danger tone so the operator pauses before clicking.
const btnDanger = {
  padding: "8px 14px",
  fontSize: 12,
  fontWeight: 600,
  background: "#7f1d1d",
  color: "#fecaca",
  border: "1px solid #991b1b",
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

const btnPrimarySmall = {
  padding: "4px 10px", fontSize: 11, fontWeight: 600,
  background: "#3B82F6", color: "#fff",
  border: "none", borderRadius: 4, cursor: "pointer",
};
const btnSecondarySmall = {
  padding: "4px 10px", fontSize: 11,
  background: "transparent", color: "#c3c5dd",
  border: "1px solid #2c2d46", borderRadius: 4, cursor: "pointer",
};
