import React from "react";

/**
 * DiffStats — Claude-Code-on-Web parity inline diff indicator.
 *
 * Clickable "+N -N in M files" badge that appears in agent messages.
 * Clicking opens the DiffViewer overlay.
 */
export default function DiffStats({ diff, onClick }) {
  if (!diff || (!diff.additions && !diff.deletions && !diff.files_changed)) {
    return null;
  }

  return (
    <button type="button" style={styles.container} onClick={onClick}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 3v18M3 12h18" opacity="0.3" />
        <rect x="3" y="3" width="18" height="18" rx="2" />
      </svg>
      <span style={styles.additions}>+{diff.additions || 0}</span>
      <span style={styles.deletions}>-{diff.deletions || 0}</span>
      <span style={styles.files}>
        in {diff.files_changed || (diff.files || []).length} file{(diff.files_changed || (diff.files || []).length) !== 1 ? "s" : ""}
      </span>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ opacity: 0.5 }}>
        <polyline points="9 18 15 12 9 6" />
      </svg>
    </button>
  );
}

const styles = {
  container: {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: "5px 10px",
    borderRadius: 6,
    border: "1px solid #27272A",
    backgroundColor: "rgba(24, 24, 27, 0.8)",
    cursor: "pointer",
    fontSize: 12,
    fontFamily: "monospace",
    color: "#A1A1AA",
    transition: "border-color 0.15s, background-color 0.15s",
    marginTop: 8,
  },
  additions: {
    color: "#10B981",
    fontWeight: 600,
  },
  deletions: {
    color: "#EF4444",
    fontWeight: 600,
  },
  files: {
    color: "#71717A",
  },
};
