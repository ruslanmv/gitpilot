import React, { useState } from "react";

/**
 * SessionItem — a single row in the sessions sidebar.
 *
 * Status mapping:
 *   running / executing  → orange animated pulse  (truly alive)
 *   selected-idle        → static muted orange    (current, not running)
 *   inactive / idle      → static dim gray        (old history)
 *   failed / error       → static red             (warning)
 *   completed / closed   → static gray            (archived)
 */
export default function SessionItem({ session, isActive, onSelect, onDelete }) {
  const [hovering, setHovering] = useState(false);

  const rawStatus = (session.status || "").toLowerCase();
  const isRunning = rawStatus === "running" || rawStatus === "executing";
  const isFailed = rawStatus === "failed" || rawStatus === "error";
  const isCompleted = rawStatus === "completed" || rawStatus === "closed";

  let dotClass;
  if (isRunning) {
    dotClass = "session-dot session-dot--active";
  } else if (isFailed) {
    dotClass = "session-dot session-dot--failed";
  } else if (isCompleted) {
    dotClass = "session-dot session-dot--completed";
  } else if (isActive) {
    dotClass = "session-dot session-dot--idle";
  } else {
    dotClass = "session-dot session-dot--inactive";
  }

  const timeAgo = formatTimeAgo(session.updated_at);
  const metaPrimary = isRunning ? "Active now" : timeAgo;

  const title =
    session.name ||
    (session.branch ? `${session.branch}` : `Session ${session.id?.slice(0, 8)}`);

  return (
    <div
      style={{
        ...styles.row,
        backgroundColor: isActive
          ? "rgba(255, 122, 60, 0.07)"
          : hovering
          ? "rgba(255,255,255,0.03)"
          : "transparent",
        borderLeft: isActive
          ? "2px solid rgba(255, 122, 60, 0.7)"
          : "2px solid transparent",
      }}
      onClick={onSelect}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
    >
      <span className={dotClass} aria-hidden="true" />

      {/* Content */}
      <div style={styles.content}>
        <div style={styles.title}>{title}</div>
        <div style={styles.meta}>
          <span
            style={isRunning ? styles.metaActive : undefined}
          >
            {metaPrimary}
          </span>
          {session.mode && (
            <span style={{
              ...styles.badge,
              background: session.mode === "github" ? "#1e3a5f" : "#2d2d1f",
              color: session.mode === "github" ? "#60a5fa" : "#d4d48a",
            }}>
              {session.mode === "github" ? "GH" : session.mode === "local-git" ? "Git" : "Dir"}
            </span>
          )}
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
    gap: 10,
    padding: "8px 10px",
    borderRadius: 6,
    cursor: "pointer",
    transition: "background-color 0.15s",
    position: "relative",
    marginBottom: 2,
    animation: "session-fade-in 0.25s ease-out",
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
  metaActive: {
    color: "#ffb089",
    fontWeight: 600,
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
