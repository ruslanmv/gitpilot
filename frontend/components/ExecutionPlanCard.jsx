// frontend/components/ExecutionPlanCard.jsx
//
// The approval-first surface for sandbox runs.  Renders a
// deterministic ExecutionPlan returned by POST /api/sandbox/plan
// and gates the actual run on an explicit user click.
//
// Two visual variants, same component:
//
//   variant = "full"    — used in chat for file-run and chat-command
//                         plans.  Big card, every safety check / warning
//                         visible, primary CTA "Run in Sandbox".
//   variant = "compact" — used as a popover above a code-block ▶ click.
//                         Same info, less chrome.
//
// The component is stateless about the run itself: it produces an
// approval event (onApprove with the plan object) and lets the
// parent decide how to execute.  This keeps the streaming/state
// machine work for Batch 3 — here we only render and consent.

import React from "react";
import { apiUrl } from "../utils/api.js";

const SEVERITY_STYLE = {
  high:   { bg: "#3d1111", fg: "#fca5a5", border: "#7f1d1d", icon: "⛔" },
  medium: { bg: "#3d2d11", fg: "#fde68a", border: "#854d0e", icon: "⚠" },
  low:    { bg: "#1f2937", fg: "#a5b4fc", border: "#3730a3", icon: "ⓘ" },
};

const BACKEND_LABELS = {
  subprocess: "Local",
  matrixlab: "MatrixLab",
  off: "Pass-through",
};

export default function ExecutionPlanCard({
  plan,
  variant = "full",
  busy = false,
  onApprove,
  onCancel,
  onOpenFile,
}) {
  if (!plan) return null;
  const isCompact = variant === "compact";
  const styles = isCompact ? compactStyles : fullStyles;

  const commandStr = Array.isArray(plan.command)
    ? plan.command.join(" ")
    : String(plan.command || "");

  return (
    <div style={styles.card} role="region" aria-label="Execution plan">
      <header style={styles.header}>
        <span style={styles.badge}>EXECUTION PLAN</span>
        <h3 style={styles.title}>{plan.goal || "Run in sandbox"}</h3>
      </header>

      <dl style={styles.fields}>
        {plan.file && (
          <Field label="File" value={<code>{plan.file}</code>} />
        )}
        <Field label="Command" value={<code>{commandStr}</code>} />
        <Field
          label="Sandbox"
          value={BACKEND_LABELS[plan.sandbox] || plan.sandbox}
        />
        <Field label="Timeout" value={`${plan.timeout_sec}s`} />
        <Field
          label="Network"
          value={plan.network ? "Enabled" : "Disabled"}
        />
        {plan.workdir && plan.workdir !== "." && (
          <Field label="Working dir" value={<code>{plan.workdir}</code>} />
        )}
      </dl>

      {/* Safety checks — always green, no opinion */}
      {Array.isArray(plan.safety?.checks) && plan.safety.checks.length > 0 && (
        <ul style={styles.checks}>
          {plan.safety.checks.map((c, i) => (
            <li key={i} style={styles.check}>
              <span style={styles.checkIcon}>✓</span>
              {c.label}
            </li>
          ))}
        </ul>
      )}

      {/* Warnings — non-blocking, sorted high → low by the backend */}
      {Array.isArray(plan.safety?.warnings) && plan.safety.warnings.length > 0 && (
        <ul style={styles.warnings}>
          {plan.safety.warnings.map((w, i) => {
            const st = SEVERITY_STYLE[w.severity] || SEVERITY_STYLE.low;
            return (
              <li key={i} style={{
                ...styles.warning,
                background: st.bg, color: st.fg, borderColor: st.border,
              }}>
                <span style={styles.warnIcon}>{st.icon}</span>
                <span>
                  <strong>{w.label}</strong>
                  {w.detail && <span style={styles.warnDetail}> — {w.detail}</span>}
                </span>
              </li>
            );
          })}
        </ul>
      )}

      {plan.inline_code && (
        <details style={styles.snippetWrap}>
          <summary style={styles.snippetLabel}>
            Snippet to run ({plan.inline_code.length} chars)
          </summary>
          <pre style={styles.snippet}>{plan.inline_code}</pre>
        </details>
      )}

      <footer style={styles.footer}>
        <button
          type="button"
          style={{ ...styles.primary, opacity: busy ? 0.6 : 1 }}
          onClick={() => onApprove?.(plan)}
          disabled={busy}
          autoFocus
        >
          {busy ? "Starting…" : "▶ Run in Sandbox"}
        </button>
        {plan.file && onOpenFile && (
          <button type="button" style={styles.secondary}
                  onClick={() => onOpenFile(plan.file)}>
            Open {plan.file.split("/").pop()}
          </button>
        )}
        <button
          type="button"
          style={styles.tertiary}
          onClick={() => onCancel?.(plan)}
          disabled={busy}
        >
          Cancel
        </button>
      </footer>
    </div>
  );
}

function Field({ label, value }) {
  return (
    <>
      <dt style={dtStyle}>{label}</dt>
      <dd style={ddStyle}>{value}</dd>
    </>
  );
}

// ---------------------------------------------------------------------------
// Approve helper — single source of truth so chat, codeblock, canvas
// all build the plan the same way.  Returns the plan object or throws.
// ---------------------------------------------------------------------------

export async function fetchExecutionPlan(payload) {
  const res = await fetch(apiUrl("/api/sandbox/plan"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || `Plan failed (HTTP ${res.status})`);
  }
  return data.plan;
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const dtStyle = {
  fontSize: 11, color: "#9092b5", textTransform: "uppercase",
  letterSpacing: "0.05em", margin: 0,
};
const ddStyle = { margin: "0 0 6px", fontSize: 13, color: "#e4e4e7" };

const fullStyles = {
  card: {
    margin: "8px 0",
    background: "#0d1117",
    border: "1px solid #1f2937",
    borderLeft: "3px solid #10B981",
    borderRadius: 10,
    padding: 16,
    color: "#e4e4e7",
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  },
  header: { display: "flex", alignItems: "center", gap: 10, marginBottom: 10 },
  badge: {
    fontSize: 10, fontWeight: 700, letterSpacing: "0.06em",
    padding: "2px 8px", borderRadius: 4,
    background: "#0d3320", color: "#86efac", textTransform: "uppercase",
  },
  title: { margin: 0, fontSize: 15, fontWeight: 600 },
  fields: {
    display: "grid", gridTemplateColumns: "120px 1fr",
    rowGap: 4, columnGap: 12, margin: "10px 0",
  },
  checks: {
    listStyle: "none", padding: 0, margin: "8px 0",
    display: "flex", flexWrap: "wrap", gap: 6,
  },
  check: {
    fontSize: 11, color: "#86efac",
    background: "rgba(16,185,129,0.08)",
    border: "1px solid rgba(16,185,129,0.25)",
    borderRadius: 4, padding: "2px 8px",
  },
  checkIcon: { marginRight: 4 },
  warnings: { listStyle: "none", padding: 0, margin: "10px 0 4px" },
  warning: {
    fontSize: 12,
    padding: "6px 10px", borderRadius: 4, border: "1px solid",
    marginBottom: 4, display: "flex", gap: 8, alignItems: "flex-start",
  },
  warnIcon: { fontSize: 14, lineHeight: 1 },
  warnDetail: { opacity: 0.8 },
  snippetWrap: {
    margin: "8px 0",
    border: "1px solid #1f2937", borderRadius: 6, padding: "6px 10px",
  },
  snippetLabel: { fontSize: 11, color: "#9092b5", cursor: "pointer" },
  snippet: {
    margin: "6px 0 0", padding: 8, fontSize: 12,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    background: "#000", color: "#d4d4d8", borderRadius: 4,
    maxHeight: 240, overflow: "auto", whiteSpace: "pre-wrap",
  },
  footer: { display: "flex", gap: 8, marginTop: 12 },
  primary: {
    background: "#10B981", color: "#052e1c", border: "0",
    borderRadius: 6, padding: "8px 16px", fontSize: 13, fontWeight: 600,
    cursor: "pointer",
  },
  secondary: {
    background: "transparent", color: "#a1a1aa",
    border: "1px solid #3F3F46", borderRadius: 6,
    padding: "7px 14px", fontSize: 13, cursor: "pointer",
  },
  tertiary: {
    background: "transparent", color: "#71717a",
    border: "1px solid #27272a", borderRadius: 6,
    padding: "7px 14px", fontSize: 13, cursor: "pointer",
    marginLeft: "auto",
  },
};

// Compact variant — same structure, denser padding, no inline snippet
// expander.  Used for code-block Run confirmation popovers.
const compactStyles = {
  ...fullStyles,
  card: {
    ...fullStyles.card,
    padding: 10,
    margin: "6px 0",
    borderRadius: 8,
  },
  title: { margin: 0, fontSize: 13, fontWeight: 600 },
  fields: {
    ...fullStyles.fields,
    gridTemplateColumns: "90px 1fr",
    rowGap: 2,
    margin: "6px 0",
  },
  primary: { ...fullStyles.primary, padding: "6px 12px", fontSize: 12 },
  secondary: { ...fullStyles.secondary, padding: "5px 10px", fontSize: 12 },
  tertiary: { ...fullStyles.tertiary, padding: "5px 10px", fontSize: 12 },
};
