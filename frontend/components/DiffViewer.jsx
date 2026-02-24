import React, { useState } from "react";

/**
 * DiffViewer — Claude-Code-on-Web parity diff overlay.
 *
 * Shows a file list on the left and unified diff on the right.
 * Green = additions, red = deletions. Additive component.
 */
export default function DiffViewer({ diff, onClose }) {
  const [selectedFile, setSelectedFile] = useState(0);

  if (!diff || !diff.files || diff.files.length === 0) {
    return (
      <div style={styles.overlay}>
        <div style={styles.panel}>
          <div style={styles.header}>
            <span style={styles.headerTitle}>Diff Viewer</span>
            <button type="button" style={styles.closeBtn} onClick={onClose}>
              &times;
            </button>
          </div>
          <div style={styles.emptyState}>No changes to display.</div>
        </div>
      </div>
    );
  }

  const files = diff.files || [];
  const currentFile = files[selectedFile] || files[0];

  return (
    <div style={styles.overlay}>
      <div style={styles.panel}>
        {/* Header */}
        <div style={styles.header}>
          <div style={styles.headerLeft}>
            <span style={styles.headerTitle}>Diff Viewer</span>
            <span style={styles.statBadge}>
              <span style={{ color: "#10B981" }}>+{diff.additions || 0}</span>
              {" "}
              <span style={{ color: "#EF4444" }}>-{diff.deletions || 0}</span>
              {" in "}
              {diff.files_changed || files.length} files
            </span>
          </div>
          <button type="button" style={styles.closeBtn} onClick={onClose}>
            &times;
          </button>
        </div>

        {/* Body */}
        <div style={styles.body}>
          {/* File list */}
          <div style={styles.fileList}>
            {files.map((f, idx) => (
              <div
                key={f.path}
                style={{
                  ...styles.fileItem,
                  backgroundColor:
                    idx === selectedFile ? "rgba(59, 130, 246, 0.10)" : "transparent",
                  borderLeft:
                    idx === selectedFile
                      ? "2px solid #3B82F6"
                      : "2px solid transparent",
                }}
                onClick={() => setSelectedFile(idx)}
              >
                <span style={styles.fileName}>{f.path}</span>
                <span style={styles.fileStats}>
                  <span style={{ color: "#10B981" }}>+{f.additions || 0}</span>
                  {" "}
                  <span style={{ color: "#EF4444" }}>-{f.deletions || 0}</span>
                </span>
              </div>
            ))}
          </div>

          {/* Diff content */}
          <div style={styles.diffContent}>
            <div style={styles.diffPath}>{currentFile.path}</div>
            <div style={styles.diffCode}>
              {(currentFile.hunks || []).map((hunk, hi) => (
                <div key={hi}>
                  <div style={styles.hunkHeader}>{hunk.header || `@@ hunk ${hi + 1} @@`}</div>
                  {(hunk.lines || []).map((line, li) => {
                    let bg = "transparent";
                    let color = "#D4D4D8";
                    if (line.startsWith("+")) {
                      bg = "rgba(16, 185, 129, 0.10)";
                      color = "#6EE7B7";
                    } else if (line.startsWith("-")) {
                      bg = "rgba(239, 68, 68, 0.10)";
                      color = "#FCA5A5";
                    }
                    return (
                      <div
                        key={li}
                        style={{
                          ...styles.diffLine,
                          backgroundColor: bg,
                          color,
                        }}
                      >
                        {line}
                      </div>
                    );
                  })}
                </div>
              ))}

              {(!currentFile.hunks || currentFile.hunks.length === 0) && (
                <div style={styles.diffPlaceholder}>
                  Diff content will appear here when the agent modifies files.
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const styles = {
  overlay: {
    position: "fixed",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "rgba(0, 0, 0, 0.7)",
    zIndex: 200,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  panel: {
    width: "90vw",
    maxWidth: 1100,
    height: "80vh",
    backgroundColor: "#131316",
    border: "1px solid #27272A",
    borderRadius: 12,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "12px 16px",
    borderBottom: "1px solid #27272A",
    backgroundColor: "#18181B",
  },
  headerLeft: {
    display: "flex",
    alignItems: "center",
    gap: 12,
  },
  headerTitle: {
    fontSize: 14,
    fontWeight: 600,
    color: "#E4E4E7",
  },
  statBadge: {
    fontSize: 12,
    color: "#A1A1AA",
  },
  closeBtn: {
    width: 28,
    height: 28,
    borderRadius: 6,
    border: "1px solid #3F3F46",
    background: "transparent",
    color: "#A1A1AA",
    fontSize: 18,
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  body: {
    flex: 1,
    display: "flex",
    overflow: "hidden",
  },
  fileList: {
    width: 240,
    borderRight: "1px solid #27272A",
    overflowY: "auto",
    flexShrink: 0,
  },
  fileItem: {
    padding: "8px 10px",
    cursor: "pointer",
    borderBottom: "1px solid rgba(39, 39, 42, 0.5)",
    transition: "background-color 0.1s",
  },
  fileName: {
    display: "block",
    fontSize: 12,
    fontFamily: "monospace",
    color: "#E4E4E7",
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
  },
  fileStats: {
    display: "block",
    fontSize: 10,
    marginTop: 2,
  },
  diffContent: {
    flex: 1,
    overflow: "auto",
    display: "flex",
    flexDirection: "column",
  },
  diffPath: {
    padding: "8px 12px",
    fontSize: 12,
    fontFamily: "monospace",
    color: "#A1A1AA",
    borderBottom: "1px solid #27272A",
    backgroundColor: "#18181B",
    position: "sticky",
    top: 0,
    zIndex: 1,
  },
  diffCode: {
    padding: "4px 0",
    fontFamily: "monospace",
    fontSize: 12,
    lineHeight: 1.6,
  },
  hunkHeader: {
    padding: "4px 12px",
    color: "#6B7280",
    backgroundColor: "rgba(59, 130, 246, 0.05)",
    fontSize: 11,
    fontStyle: "italic",
  },
  diffLine: {
    padding: "0 12px",
    whiteSpace: "pre",
  },
  diffPlaceholder: {
    padding: 20,
    textAlign: "center",
    color: "#52525B",
    fontSize: 13,
  },
  emptyState: {
    flex: 1,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "#52525B",
    fontSize: 14,
  },
};
