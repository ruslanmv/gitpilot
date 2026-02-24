import React, { useState } from "react";

/**
 * SessionItem — a single row in the sessions sidebar.
 *
 * Shows status dot (pulsing/static), title, timestamp, message count.
 * Claude-Code-on-Web parity: active=amber pulse, completed=green,
 * failed=red, waiting=blue.
 */
export default function SessionItem({ session, isActive, onSelect, onDelete }) {
  const [hovering, setHovering] = useState(false);

  const status = session.status || "active";

  const dotColor = {
    active: "#F59E0B",
    completed: "#10B981",
    failed: "#EF4444",
    waiting: "#3B82F6",
    paused: "#6B7280",
  }[status] || "#6B7280";

  const isPulsing = status === "active";

  const timeAgo = formatTimeAgo(session.updated_at);

  const title =
    session.name ||
    (session.branch ? `Session on ${session.branch}` : `Session ${session.id?.slice(0, 8)}`);

  return (
    <div
      style={{
        ...styles.row,
        backgroundColor: isActive
          ? "rgba(59, 130, 246, 0.08)"
          : hovering
          ? "rgba(255,255,255,0.03)"
          : "transparent",
        borderLeft: isActive ? "2px solid #3B82F6" : "2px solid transparent",
      }}
      onClick={onSelect}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
    >
      <style>{`
        @keyframes session-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>

      {/* Status dot */}
      <div
        style={{
          ...styles.dot,
          backgroundColor: dotColor,
          animation: isPulsing ? "session-pulse 1.5s ease-in-out infinite" : "none",
        }}
      />

      {/* Content */}
      <div style={styles.content}>
        <div style={styles.title}>{title}</div>
        <div style={styles.meta}>
          {timeAgo}
          {session.message_count > 0 && (
            <span style={styles.badge}>{session.message_count} msgs</span>
          )}
        </div>
      </div>

      {/* Delete button (on hover) */}
      {hovering && (
        <button
          type="button"
          style={styles.deleteBtn}
          onClick={(e) => {
            e.stopPropagation();
            onDelete?.();
          }}
          title="Delete session"
        >
          &times;
        </button>
      )}
    </div>
  );
}

function formatTimeAgo(isoStr) {
  if (!isoStr) return "";
  try {
    const date = new Date(isoStr);
    const now = new Date();
    const diffMs = now - date;
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffHr = Math.floor(diffMin / 60);
    if (diffHr < 24) return `${diffHr}h ago`;
    const diffDay = Math.floor(diffHr / 24);
    return `${diffDay}d ago`;
  } catch {
    return "";
  }
}

const styles = {
  row: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "8px 10px",
    borderRadius: 6,
    cursor: "pointer",
    transition: "background-color 0.15s",
    position: "relative",
    marginBottom: 2,
    animation: "session-fade-in 0.25s ease-out",
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: "50%",
    flexShrink: 0,
  },
  content: {
    flex: 1,
    minWidth: 0,
    overflow: "hidden",
  },
  title: {
    fontSize: 12,
    fontWeight: 500,
    color: "#E4E4E7",
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
  },
  meta: {
    fontSize: 10,
    color: "#71717A",
    marginTop: 2,
    display: "flex",
    alignItems: "center",
    gap: 6,
  },
  badge: {
    fontSize: 9,
    background: "#27272A",
    padding: "1px 5px",
    borderRadius: 8,
    color: "#A1A1AA",
  },
  deleteBtn: {
    position: "absolute",
    right: 6,
    top: 6,
    width: 18,
    height: 18,
    borderRadius: 3,
    border: "none",
    background: "rgba(239, 68, 68, 0.15)",
    color: "#EF4444",
    fontSize: 14,
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    lineHeight: 1,
  },
};
