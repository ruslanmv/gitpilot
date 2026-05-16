import React from "react";
import PlanView from "./PlanView.jsx";
import RunnableCodeBlock, { splitFences } from "./RunnableCodeBlock.jsx";

export default function AssistantMessage({ answer, plan, executionLog, planStatus }) {
  // ``planStatus`` is optional metadata about the lifecycle of the plan
  // attached to this message: "executed" | "rejected" | null.  It drives
  // the badge next to the Action Plan header so the user can tell at a
  // glance, in chat history, whether a previous plan was approved or
  // dismissed.  Defaults to null (no badge) to keep the legacy render
  // path untouched.
  const styles = {
    container: {
      marginBottom: "20px",
      padding: "20px",
      backgroundColor: "#18181B", // Zinc-900
      borderRadius: "12px",
      border: "1px solid #27272A", // Zinc-800
      color: "#F4F4F5", // Zinc-100
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
      boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)",
    },
    section: {
      marginBottom: "20px",
    },
    lastSection: {
      marginBottom: "0",
    },
    header: {
      display: "flex",
      alignItems: "center",
      marginBottom: "12px",
      paddingBottom: "8px",
      borderBottom: "1px solid #3F3F46", // Zinc-700
    },
    title: {
      fontSize: "12px",
      fontWeight: "600",
      textTransform: "uppercase",
      letterSpacing: "0.05em",
      color: "#A1A1AA", // Zinc-400
      margin: 0,
    },
    content: {
      fontSize: "14px",
      lineHeight: "1.6",
      whiteSpace: "pre-wrap",
    },
    executionList: {
      listStyle: "none",
      padding: 0,
      margin: 0,
      display: "flex",
      flexDirection: "column",
      gap: "8px",
    },
    executionStep: {
      display: "flex",
      flexDirection: "column",
      gap: "4px",
      padding: "10px",
      backgroundColor: "#09090B", // Zinc-950
      borderRadius: "6px",
      border: "1px solid #27272A",
      fontSize: "13px",
    },
    stepNumber: {
      fontSize: "11px",
      fontWeight: "600",
      color: "#10B981", // Emerald-500
      textTransform: "uppercase",
    },
    stepSummary: {
      color: "#D4D4D8", // Zinc-300
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    },
  };

  // Only show Action Plan section when there are actual file actions.
  // For Lite Mode Q&A responses (all steps have 0 files), the plan
  // just duplicates the answer — hiding it avoids showing the same text 3x.
  const hasFileActions = plan?.steps?.some(s => s.files?.length > 0);

  return (
    <div className="chat-message-ai" style={styles.container}>
      {/* Answer section.  ``splitFences`` cuts the answer at fenced code
          blocks so each runnable snippet gets its own RunnableCodeBlock
          (with a per-block Run button); the surrounding prose still
          renders as the existing pre-wrapped paragraph. */}
      <section style={styles.section}>
        <header style={styles.header}>
          <h3 style={styles.title}>Answer</h3>
        </header>
        <div style={styles.content}>
          {splitFences(answer).map((seg, i) =>
            seg.type === "code" ? (
              <RunnableCodeBlock key={i} language={seg.language} code={seg.code} />
            ) : (
              <p key={i} style={{ margin: "0 0 8px" }}>{seg.value}</p>
            )
          )}
        </div>
      </section>

      {/* Action Plan section — only when there are file changes */}
      {plan && hasFileActions && (
        <section style={styles.section}>
          <header style={{ ...styles.header, display: "flex", alignItems: "center", gap: "10px" }}>
            <h3 style={{ ...styles.title, color: "#D95C3D", margin: 0 }}>Action Plan</h3>
            {planStatus === "executed" && (
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "4px",
                  fontSize: "11px",
                  fontWeight: 600,
                  color: "#10B981",
                  border: "1px solid rgba(16, 185, 129, 0.35)",
                  background: "rgba(16, 185, 129, 0.08)",
                  borderRadius: "6px",
                  padding: "2px 6px",
                  letterSpacing: "0.02em",
                }}
                title="This plan was approved and executed."
              >
                ✓ Executed
              </span>
            )}
            {planStatus === "rejected" && (
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "4px",
                  fontSize: "11px",
                  fontWeight: 600,
                  color: "#9CA3AF",
                  border: "1px solid rgba(156, 163, 175, 0.35)",
                  background: "rgba(156, 163, 175, 0.08)",
                  borderRadius: "6px",
                  padding: "2px 6px",
                  letterSpacing: "0.02em",
                }}
                title="This plan was rejected. No files were changed."
              >
                ✕ Rejected
              </span>
            )}
          </header>
          <div>
            <PlanView plan={plan} />
          </div>
        </section>
      )}

      {/* Execution Log section (shown after execution) */}
      {executionLog && (
        <section style={styles.lastSection}>
          <header style={styles.header}>
            <h3 style={{ ...styles.title, color: "#10B981" }}>Execution Log</h3>
          </header>
          <div>
            <ul style={styles.executionList}>
              {executionLog.steps.map((s) => (
                <li key={s.step_number} style={styles.executionStep}>
                  <span style={styles.stepNumber}>Step {s.step_number}</span>
                  <span style={styles.stepSummary}>{s.summary}</span>
                  {Array.isArray(s.executions) && s.executions.map((ex, i) => (
                    <ExecutionCard key={i} ex={ex} />
                  ))}
                </li>
              ))}
            </ul>
          </div>
        </section>
      )}
    </div>
  );
}

function ExecutionCard({ ex }) {
  // Renders a structured execution result emitted by the EXECUTE
  // branch of agentic.execute_plan.  Distinguishes pending / completed /
  // failed / skipped at a glance, exposes the command + sandbox metadata
  // operators care about, and folds long stdout/stderr into <details>
  // disclosures so a single Print(...) line doesn't push the whole
  // conversation off-screen.
  const status = ex.status || "pending";
  const tone =
    status === "completed" ? { color: "#86efac", bg: "#0d3320", border: "#166534" } :
    status === "failed"    ? { color: "#fca5a5", bg: "#3d1111", border: "#7f1d1d" } :
    status === "skipped"   ? { color: "#fde68a", bg: "#3d2d11", border: "#854d0e" } :
                             { color: "#93c5fd", bg: "#1e3a5f", border: "#3B82F6" };
  return (
    <div style={{
      marginTop: 10, padding: 12, borderRadius: 8,
      background: "#0d0e17", border: `1px solid ${tone.border}`,
      fontFamily: "system-ui, sans-serif", fontSize: 13,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 }}>
        <div>
          <strong style={{ fontFamily: "ui-monospace, monospace" }}>{ex.path}</strong>
          {ex.sandbox && (
            <span style={{ marginLeft: 8, fontSize: 11, color: "#9092b5" }}>
              · {ex.sandbox}
            </span>
          )}
        </div>
        <span style={{
          padding: "2px 8px", borderRadius: 10,
          fontSize: 11, fontWeight: 600,
          background: tone.bg, color: tone.color,
          border: `1px solid ${tone.border}`,
        }}>
          {status === "completed" && `Exit ${ex.exit_code} · ${ex.duration_ms} ms`}
          {status === "failed" && (typeof ex.exit_code === "number"
            ? `Failed · exit ${ex.exit_code}` : "Failed")}
          {status === "skipped" && "Skipped"}
          {status === "pending" && "Running…"}
        </span>
      </div>
      {ex.command && (
        <div style={{
          fontSize: 12, padding: "6px 10px", borderRadius: 4,
          background: "#000", color: "#93c5fd",
          fontFamily: "ui-monospace, SFMono-Regular, monospace",
          marginBottom: 8,
        }}>
          $ {ex.command}
        </div>
      )}
      {ex.matplotlib_shim && (
        <div style={{ fontSize: 11, color: "#9092b5", marginBottom: 6 }}>
          ⓘ Matplotlib detected — Agg backend forced so the headless sandbox doesn't hang on <code>plt.show()</code>.
        </div>
      )}
      {ex.timed_out && (
        <div style={{ fontSize: 11, color: "#fca5a5", marginBottom: 6 }}>
          ⏱ Timed out before completion.
        </div>
      )}
      {ex.stdout && (
        <details open style={{ marginTop: 4 }}>
          <summary style={{ cursor: "pointer", fontSize: 11, color: "#86efac" }}>stdout</summary>
          <pre style={{
            margin: "6px 0 0", padding: "8px 10px", borderRadius: 4,
            background: "#000", color: "#d4d4d8", fontSize: 12,
            fontFamily: "ui-monospace, monospace",
            maxHeight: 240, overflow: "auto", whiteSpace: "pre-wrap",
          }}>{ex.stdout}</pre>
        </details>
      )}
      {ex.stderr && (
        <details open={status === "failed"} style={{ marginTop: 4 }}>
          <summary style={{ cursor: "pointer", fontSize: 11, color: "#fca5a5" }}>stderr</summary>
          <pre style={{
            margin: "6px 0 0", padding: "8px 10px", borderRadius: 4,
            background: "#000", color: "#fca5a5", fontSize: 12,
            fontFamily: "ui-monospace, monospace",
            maxHeight: 240, overflow: "auto", whiteSpace: "pre-wrap",
          }}>{ex.stderr}</pre>
        </details>
      )}
      {ex.error && !ex.stderr && (
        <div style={{ fontSize: 12, color: "#fca5a5", marginTop: 4 }}>
          {ex.error}
        </div>
      )}
      {ex.reason && (
        <div style={{ fontSize: 12, color: "#9092b5", marginTop: 4 }}>
          {ex.reason}
        </div>
      )}
    </div>
  );
}