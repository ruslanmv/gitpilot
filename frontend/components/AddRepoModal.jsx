import React, { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { authFetch } from "../utils/api.js";

/**
 * AddRepoModal — lightweight portal modal for adding repos to context.
 *
 * Embeds a minimal repo search/list (not the full RepoSelector) to keep
 * the modal focused. Filters out repos already in context.
 */
export default function AddRepoModal({ isOpen, onSelect, onClose, excludeKeys = [] }) {
  const [query, setQuery] = useState("");
  const [repos, setRepos] = useState([]);
  const [loading, setLoading] = useState(false);

  const fetchRepos = useCallback(
    async (searchQuery) => {
      setLoading(true);
      try {
        const params = new URLSearchParams({ per_page: "50" });
        if (searchQuery) params.set("query", searchQuery);
        const res = await authFetch(`/api/repos?${params}`);
        if (!res.ok) return;
        const data = await res.json();
        setRepos(data.repositories || []);
      } catch (err) {
        console.warn("AddRepoModal: fetch failed:", err);
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    if (isOpen) {
      setQuery("");
      fetchRepos("");
    }
  }, [isOpen, fetchRepos]);

  // Debounced search
  useEffect(() => {
    if (!isOpen) return;
    const t = setTimeout(() => fetchRepos(query), 300);
    return () => clearTimeout(t);
  }, [query, isOpen, fetchRepos]);

  const excludeSet = new Set(excludeKeys);
  const filtered = repos.filter((r) => {
    const key = r.full_name || `${r.owner}/${r.name}`;
    return !excludeSet.has(key);
  });

  if (!isOpen) return null;

  return createPortal(
    <div
      style={styles.overlay}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div style={styles.modal} onMouseDown={(e) => e.stopPropagation()}>
        <div style={styles.header}>
          <span style={styles.headerTitle}>Add Repository</span>
          <button type="button" style={styles.closeBtn} onClick={onClose}>
            &times;
          </button>
        </div>

        <div style={styles.searchBox}>
          <input
            type="text"
            placeholder="Search repositories..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={styles.searchInput}
            autoFocus
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
            }}
          />
        </div>

        <div style={styles.list}>
          {loading && filtered.length === 0 && (
            <div style={styles.statusRow}>Loading...</div>
          )}
          {!loading && filtered.length === 0 && (
            <div style={styles.statusRow}>
              {excludeKeys.length > 0 && repos.length > 0
                ? "All matching repos are already in context"
                : "No repositories found"}
            </div>
          )}
          {filtered.map((r) => {
            const key = r.full_name || `${r.owner}/${r.name}`;
            return (
              <button
                key={r.id || key}
                type="button"
                style={styles.repoRow}
                onClick={() => onSelect(r)}
              >
                <div style={styles.repoInfo}>
                  <span style={styles.repoName}>{r.name}</span>
                  <span style={styles.repoOwner}>{r.owner}</span>
                </div>
                <div style={styles.repoMeta}>
                  {r.private && <span style={styles.privateBadge}>Private</span>}
                  <span style={styles.branchHint}>{r.default_branch || "main"}</span>
                </div>
              </button>
            );
          })}
          {loading && filtered.length > 0 && (
            <div style={styles.statusRow}>Updating...</div>
          )}
        </div>
      </div>
    </div>,
    document.body
  );
}

const styles = {
  overlay: {
    position: "fixed",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "rgba(0, 0, 0, 0.6)",
    zIndex: 10000,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  modal: {
    width: 440,
    maxHeight: "70vh",
    backgroundColor: "#131316",
    border: "1px solid #27272A",
    borderRadius: 12,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
    boxShadow: "0 12px 40px rgba(0,0,0,0.5)",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "12px 14px",
    borderBottom: "1px solid #27272A",
    backgroundColor: "#18181B",
  },
  headerTitle: {
    fontSize: 14,
    fontWeight: 600,
    color: "#E4E4E7",
  },
  closeBtn: {
    width: 26,
    height: 26,
    borderRadius: 6,
    border: "1px solid #3F3F46",
    background: "transparent",
    color: "#A1A1AA",
    fontSize: 16,
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  searchBox: {
    padding: "10px 12px",
    borderBottom: "1px solid #27272A",
  },
  searchInput: {
    width: "100%",
    padding: "8px 10px",
    borderRadius: 6,
    border: "1px solid #3F3F46",
    background: "#18181B",
    color: "#E4E4E7",
    fontSize: 13,
    outline: "none",
    fontFamily: "monospace",
    boxSizing: "border-box",
  },
  list: {
    flex: 1,
    overflowY: "auto",
    maxHeight: 360,
  },
  statusRow: {
    padding: "16px 12px",
    textAlign: "center",
    fontSize: 12,
    color: "#71717A",
  },
  repoRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    width: "100%",
    padding: "10px 14px",
    border: "none",
    borderBottom: "1px solid rgba(39, 39, 42, 0.5)",
    background: "transparent",
    color: "#E4E4E7",
    cursor: "pointer",
    textAlign: "left",
    transition: "background-color 0.1s",
  },
  repoInfo: {
    display: "flex",
    flexDirection: "column",
    gap: 2,
    minWidth: 0,
  },
  repoName: {
    fontSize: 13,
    fontWeight: 600,
    fontFamily: "monospace",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  repoOwner: {
    fontSize: 11,
    color: "#71717A",
  },
  repoMeta: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    flexShrink: 0,
  },
  privateBadge: {
    fontSize: 9,
    padding: "1px 5px",
    borderRadius: 8,
    backgroundColor: "rgba(239, 68, 68, 0.12)",
    color: "#F87171",
    fontWeight: 600,
    textTransform: "uppercase",
  },
  branchHint: {
    fontSize: 10,
    color: "#52525B",
    fontFamily: "monospace",
  },
};
