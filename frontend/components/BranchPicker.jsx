import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * BranchPicker — Claude-Code-on-Web parity branch selector.
 *
 * Fetches branches from the new /api/repos/{owner}/{repo}/branches endpoint.
 * Shows search, default branch badge, AI session branch highlighting.
 *
 * Fixes applied:
 * - Dropdown portaled to document.body (avoids overflow:hidden clipping)
 * - Branches cached per repo (no "No branches found" flash)
 * - Shows "Loading..." only on first fetch, keeps stale data otherwise
 */

// Simple per-repo branch cache so reopening the dropdown is instant
const branchCache = {};

export default function BranchPicker({
  repo,
  currentBranch,
  defaultBranch,
  sessionBranches = [],
  onBranchChange,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [branches, setBranches] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const triggerRef = useRef(null);
  const dropdownRef = useRef(null);
  const inputRef = useRef(null);

  const branch = currentBranch || defaultBranch || "main";
  const isAiSession = sessionBranches.includes(branch) && branch !== defaultBranch;

  const cacheKey = repo ? `${repo.owner}/${repo.name}` : null;

  // Seed from cache on mount / repo change
  useEffect(() => {
    if (cacheKey && branchCache[cacheKey]) {
      setBranches(branchCache[cacheKey]);
    }
  }, [cacheKey]);

  // Fetch branches
  const fetchBranches = useCallback(async (searchQuery) => {
    if (!repo) return;
    setLoading(true);
    setError(null);
    try {
      const token = localStorage.getItem("github_token");
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const params = new URLSearchParams({ per_page: "100" });
      if (searchQuery) params.set("query", searchQuery);

      const res = await fetch(
        `/api/repos/${repo.owner}/${repo.name}/branches?${params}`,
        { headers, cache: "no-cache" }
      );
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        const detail = errData.detail || `HTTP ${res.status}`;
        console.warn("BranchPicker: fetch failed:", detail);
        setError(detail);
        return;
      }
      const data = await res.json();
      const fetched = data.branches || [];
      setBranches(fetched);

      // Only cache the unfiltered result
      if (!searchQuery && cacheKey) {
        branchCache[cacheKey] = fetched;
      }
    } catch (err) {
      console.warn("Failed to fetch branches:", err);
    } finally {
      setLoading(false);
    }
  }, [repo, cacheKey]);

  useEffect(() => {
    if (open) {
      fetchBranches(query);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced search
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => fetchBranches(query), 300);
    return () => clearTimeout(t);
  }, [query, open, fetchBranches]);

  // Close on outside click (check both trigger and portal dropdown)
  useEffect(() => {
    if (!open) return;
    const handler = (e) => {
      const inTrigger = triggerRef.current && triggerRef.current.contains(e.target);
      const inDropdown = dropdownRef.current && dropdownRef.current.contains(e.target);
      if (!inTrigger && !inDropdown) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const handleSelect = (branchName) => {
    setOpen(false);
    setQuery("");
    if (branchName !== branch) {
      onBranchChange?.(branchName);
    }
  };

  // Merge API branches with session branches (AI branches might not show in GitHub API)
  const allBranches = [...branches];
  for (const sb of sessionBranches) {
    if (!allBranches.find((b) => b.name === sb)) {
      allBranches.push({ name: sb, is_default: false, protected: false });
    }
  }

  // Calculate portal position from trigger button
  const getDropdownPosition = () => {
    if (!triggerRef.current) return { top: 0, left: 0 };
    const rect = triggerRef.current.getBoundingClientRect();
    return {
      top: rect.bottom + 4,
      left: rect.left,
    };
  };

  const pos = open ? getDropdownPosition() : { top: 0, left: 0 };

  return (
    <div style={styles.container}>
      {/* Trigger button */}
      <button
        ref={triggerRef}
        type="button"
        style={{
          ...styles.trigger,
          borderColor: isAiSession ? "rgba(59, 130, 246, 0.3)" : "#3F3F46",
          color: isAiSession ? "#60a5fa" : "#E4E4E7",
          backgroundColor: isAiSession ? "rgba(59, 130, 246, 0.05)" : "transparent",
        }}
        onClick={() => setOpen((v) => !v)}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="6" y1="3" x2="6" y2="15" />
          <circle cx="18" cy="6" r="3" />
          <circle cx="6" cy="18" r="3" />
          <path d="M18 9a9 9 0 0 1-9 9" />
        </svg>
        <span style={styles.branchName}>{branch}</span>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {/* Dropdown — portaled to document.body to escape overflow:hidden */}
      {open && createPortal(
        <div
          ref={dropdownRef}
          style={{
            ...styles.dropdown,
            top: pos.top,
            left: pos.left,
          }}
        >
          {/* Search input */}
          <div style={styles.searchBox}>
            <input
              ref={inputRef}
              type="text"
              placeholder="Search branches..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              style={styles.searchInput}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  setOpen(false);
                  setQuery("");
                }
              }}
            />
          </div>

          {/* Branch list */}
          <div style={styles.branchList}>
            {loading && allBranches.length === 0 && (
              <div style={styles.loadingRow}>Loading...</div>
            )}

            {!loading && error && (
              <div style={styles.errorRow}>{error}</div>
            )}

            {!loading && !error && allBranches.length === 0 && (
              <div style={styles.loadingRow}>No branches found</div>
            )}

            {allBranches.map((b) => {
              const isDefault = b.is_default || b.name === defaultBranch;
              const isAi = sessionBranches.includes(b.name);
              const isCurrent = b.name === branch;

              return (
                <div
                  key={b.name}
                  style={{
                    ...styles.branchRow,
                    backgroundColor: isCurrent
                      ? isAi
                        ? "rgba(59, 130, 246, 0.10)"
                        : "#27272A"
                      : "transparent",
                  }}
                  onMouseDown={() => handleSelect(b.name)}
                >
                  <span style={{ opacity: isCurrent ? 1 : 0, width: 16, flexShrink: 0 }}>
                    &#10003;
                  </span>
                  <span
                    style={{
                      flex: 1,
                      fontFamily: "monospace",
                      fontSize: 12,
                      color: isAi ? "#60a5fa" : "#E4E4E7",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {b.name}
                  </span>
                  {isDefault && (
                    <span style={styles.defaultBadge}>default</span>
                  )}
                  {isAi && !isDefault && (
                    <span style={styles.aiBadge}>AI</span>
                  )}
                  {b.protected && (
                    <span style={styles.protectedBadge}>
                      <svg width="8" height="8" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z" />
                      </svg>
                    </span>
                  )}
                </div>
              );
            })}

            {/* Subtle loading indicator when refreshing with cached data visible */}
            {loading && allBranches.length > 0 && (
              <div style={styles.loadingRow}>Updating...</div>
            )}
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}

const styles = {
  container: {
    position: "relative",
  },
  trigger: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "4px 8px",
    borderRadius: 4,
    border: "1px solid #3F3F46",
    background: "transparent",
    fontSize: 13,
    cursor: "pointer",
    fontFamily: "monospace",
    maxWidth: 200,
  },
  branchName: {
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
    maxWidth: 140,
  },
  dropdown: {
    position: "fixed",
    width: 280,
    backgroundColor: "#1F1F23",
    border: "1px solid #27272A",
    borderRadius: 8,
    boxShadow: "0 8px 24px rgba(0,0,0,0.6)",
    zIndex: 9999,
    overflow: "hidden",
  },
  searchBox: {
    padding: "8px 10px",
    borderBottom: "1px solid #27272A",
  },
  searchInput: {
    width: "100%",
    padding: "6px 8px",
    borderRadius: 4,
    border: "1px solid #3F3F46",
    background: "#131316",
    color: "#E4E4E7",
    fontSize: 12,
    outline: "none",
    fontFamily: "monospace",
    boxSizing: "border-box",
  },
  branchList: {
    maxHeight: 260,
    overflowY: "auto",
  },
  branchRow: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "7px 10px",
    cursor: "pointer",
    transition: "background-color 0.1s",
    borderBottom: "1px solid rgba(39, 39, 42, 0.5)",
  },
  loadingRow: {
    padding: "12px 10px",
    textAlign: "center",
    fontSize: 12,
    color: "#71717A",
  },
  errorRow: {
    padding: "12px 10px",
    textAlign: "center",
    fontSize: 11,
    color: "#F59E0B",
  },
  defaultBadge: {
    fontSize: 9,
    padding: "1px 5px",
    borderRadius: 8,
    backgroundColor: "rgba(16, 185, 129, 0.15)",
    color: "#10B981",
    fontWeight: 600,
    textTransform: "uppercase",
    letterSpacing: "0.04em",
    flexShrink: 0,
  },
  aiBadge: {
    fontSize: 9,
    padding: "1px 5px",
    borderRadius: 8,
    backgroundColor: "rgba(59, 130, 246, 0.15)",
    color: "#60a5fa",
    fontWeight: 700,
    flexShrink: 0,
  },
  protectedBadge: {
    color: "#F59E0B",
    flexShrink: 0,
    display: "flex",
    alignItems: "center",
  },
};
