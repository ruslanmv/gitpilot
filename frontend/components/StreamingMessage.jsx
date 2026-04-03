import React from "react";

/**
 * StreamingMessage — Claude-Code-on-Web parity streaming renderer.
 *
 * Renders agent messages incrementally as they arrive via WebSocket.
 * Shows tool use blocks (bash commands + output), explanatory text,
 * and status indicators.
 */
export default function StreamingMessage({ events }) {
  if (!events || events.length === 0) return null;

  return (
    <div style={styles.container}>
      {events.map((evt, idx) => (
        <StreamingEvent key={idx} event={evt} isLast={idx === events.length - 1} />
      ))}
    </div>
  );
}

function StreamingEvent({ event, isLast }) {
  const { type } = event;

  if (type === "agent_message") {
    return (
      <div style={styles.textBlock}>
        <span>{event.content}</span>
        {isLast && <span style={styles.cursor}>|</span>}
      </div>
    );
  }

  if (type === "tool_use") {
    return (
      <div style={styles.toolBlock}>
        <div style={styles.toolHeader}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <polyline points="4 17 10 11 4 5" />
            <line x1="12" y1="19" x2="20" y2="19" />
          </svg>
          <span style={styles.toolName}>{event.tool || "terminal"}</span>
        </div>
        <div style={styles.toolInput}>
          <code>$ {event.input}</code>
        </div>
      </div>
    );
  }

  if (type === "tool_result") {
    return (
      <div style={styles.toolBlock}>
        <div style={styles.toolOutput}>
          <pre style={styles.toolOutputPre}>{event.output}</pre>
        </div>
      </div>
    );
  }

  if (type === "status_change") {
    const statusLabels = {
      active: "Working...",
      waiting: "Waiting for input",
      completed: "Completed",
      failed: "Failed",
    };
    return (
      <div style={styles.statusLine}>
        <div style={{
          ...styles.statusDot,
          backgroundColor: {
            active: "#F59E0B",
            waiting: "#3B82F6",
            completed: "#10B981",
            failed: "#EF4444",
          }[event.status] || "#6B7280",
        }} />
        <span>{statusLabels[event.status] || event.status}</span>
      </div>
    );
  }

  if (type === "diff_update") {
    return null; // Handled by DiffStats in parent
  }

  if (type === "error") {
    return (
      <div style={styles.errorBlock}>
        {event.message}
      </div>
    );
  }

  return null;
}

const styles = {
  container: {
    display: "flex",
    flexDirection: "column",
    gap: 4,
  },
  textBlock: {
    fontSize: 14,
    lineHeight: 1.6,
    color: "#D4D4D8",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  },
  cursor: {
    display: "inline-block",
    animation: "blink 1s step-end infinite",
    color: "#3B82F6",
    fontWeight: 700,
  },
  toolBlock: {
    margin: "4px 0",
    borderRadius: 6,
    border: "1px solid #27272A",
    overflow: "hidden",
  },
  toolHeader: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "6px 10px",
    backgroundColor: "#18181B",
    fontSize: 11,
    color: "#71717A",
    fontFamily: "monospace",
  },
  toolName: {
    fontWeight: 600,
  },
  toolInput: {
    padding: "8px 10px",
    backgroundColor: "#0D0D0F",
    fontFamily: "monospace",
    fontSize: 12,
    color: "#10B981",
    whiteSpace: "pre-wrap",
    wordBreak: "break-all",
  },
  toolOutput: {
    padding: "8px 10px",
    backgroundColor: "#0D0D0F",
    maxHeight: 300,
    overflowY: "auto",
  },
  toolOutputPre: {
    margin: 0,
    fontFamily: "monospace",
    fontSize: 11,
    color: "#A1A1AA",
    whiteSpace: "pre-wrap",
    wordBreak: "break-all",
  },
  statusLine: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "4px 0",
    fontSize: 12,
    color: "#71717A",
    fontStyle: "italic",
  },
  statusDot: {
    width: 6,
    height: 6,
    borderRadius: "50%",
  },
  errorBlock: {
    padding: "8px 12px",
    borderRadius: 6,
    backgroundColor: "rgba(239, 68, 68, 0.08)",
    border: "1px solid rgba(239, 68, 68, 0.2)",
    color: "#FCA5A5",
    fontSize: 13,
  },
};
