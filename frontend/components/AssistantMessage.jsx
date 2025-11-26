import React from "react";
import PlanView from "./PlanView.jsx";

export default function AssistantMessage({ answer, plan, executionLog }) {
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

  return (
    <div className="chat-message-ai" style={styles.container}>
      {/* Answer section */}
      <section style={styles.section}>
        <header style={styles.header}>
          <h3 style={styles.title}>Answer</h3>
        </header>
        <div style={styles.content}>
          <p style={{ margin: 0 }}>{answer}</p>
        </div>
      </section>

      {/* Action Plan section */}
      {plan && (
        <section style={styles.section}>
          <header style={styles.header}>
            <h3 style={{ ...styles.title, color: "#D95C3D" }}>Action Plan</h3>
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
                </li>
              ))}
            </ul>
          </div>
        </section>
      )}
    </div>
  );
}