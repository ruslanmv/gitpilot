import React, { useState, useEffect } from "react";
import { apiUrl } from "../utils/api.js";

/**
 * Simple recursive file tree viewer with refresh support
 * Fetches tree data directly using the API.
 */
export default function FileTree({ repo, refreshTrigger, branch }) {
  const [tree, setTree] = useState([]);
  const [loading, setLoading] = useState(false);
  const [isSwitchingBranch, setIsSwitchingBranch] = useState(false);
  const [error, setError] = useState(null);
  const [localRefresh, setLocalRefresh] = useState(0);
  // Search query for the in-sidebar filter.  Empty string == no filter.
  // Filter runs case-insensitively on path basenames *and* full paths
  // so users can pinpoint a file even in deeply nested folders.
  const [query, setQuery] = useState("");
  // Which file is currently focused in the preview panel — populated
  // by the ``gitpilot:file-opened`` event ChatPanel emits when the
  // preview finishes loading.  Drives the ◄ marker + active row tint.
  const [selectedPath, setSelectedPath] = useState(null);

  useEffect(() => {
    const onOpened = (e) => setSelectedPath(e?.detail?.path || null);
    const onClosed = () => setSelectedPath(null);
    const onRefresh = () => setLocalRefresh((n) => n + 1);
    window.addEventListener("gitpilot:file-opened", onOpened);
    window.addEventListener("gitpilot:file-closed", onClosed);
    window.addEventListener("gitpilot:refresh-tree", onRefresh);
    return () => {
      window.removeEventListener("gitpilot:file-opened", onOpened);
      window.removeEventListener("gitpilot:file-closed", onClosed);
      window.removeEventListener("gitpilot:refresh-tree", onRefresh);
    };
  }, []);

  useEffect(() => {
    if (!repo) return;

    // Determine if this is a branch switch (we already have data)
    const hasExistingData = tree.length > 0;
    if (hasExistingData) {
      setIsSwitchingBranch(true);
    } else {
      setLoading(true);
    }
    setError(null);

    // Construct headers manually
    let headers = {};
    try {
      const token = localStorage.getItem("github_token");
      if (token) {
        headers = { Authorization: `Bearer ${token}` };
      }
    } catch (e) {
      console.warn("Unable to read github_token", e);
    }

    // Add cache busting + selected branch ref
    const refParam = branch ? `&ref=${encodeURIComponent(branch)}` : "";
    const cacheBuster = `?_t=${Date.now()}${refParam}`;

    let cancelled = false;

    fetch(apiUrl(`/api/repos/${repo.owner}/${repo.name}/tree${cacheBuster}`), { headers })
      .then(async (res) => {
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || "Failed to load files");
        }
        return res.json();
      })
      .then((data) => {
        if (cancelled) return;
        if (data.files && Array.isArray(data.files)) {
          setTree(buildTree(data.files));
          setError(null);
        } else {
          setError("No files found in repository");
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err.message);
        console.error("FileTree error:", err);
      })
      .finally(() => {
        if (cancelled) return;
        setIsSwitchingBranch(false);
        setLoading(false);
      });

    return () => { cancelled = true; };
  }, [repo, branch, refreshTrigger, localRefresh]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleRefresh = () => {
    setLocalRefresh(prev => prev + 1);
  };

  // Theme matching parent component
  const theme = {
    border: "#27272A",
    textPrimary: "#EDEDED",
    textSecondary: "#A1A1AA",
    accent: "#D95C3D",
    warningText: "#F59E0B",
    warningBg: "rgba(245, 158, 11, 0.1)",
    warningBorder: "rgba(245, 158, 11, 0.2)",
  };

  const styles = {
    header: {
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      padding: "8px 20px 8px 10px",
      marginBottom: "8px",
      borderBottom: `1px solid ${theme.border}`,
    },
    headerTitle: {
      fontSize: "12px",
      fontWeight: "600",
      color: theme.textSecondary,
      textTransform: "uppercase",
      letterSpacing: "0.5px",
    },
    refreshButton: {
      backgroundColor: "transparent",
      border: `1px solid ${theme.border}`,
      color: theme.textSecondary,
      padding: "4px 8px",
      borderRadius: "4px",
      fontSize: "11px",
      cursor: loading ? "not-allowed" : "pointer",
      display: "flex",
      alignItems: "center",
      gap: "4px",
      transition: "all 0.2s",
      opacity: loading ? 0.5 : 1,
    },
    switchingBar: {
      padding: "6px 20px",
      fontSize: "11px",
      color: theme.textSecondary,
      backgroundColor: "rgba(59, 130, 246, 0.06)",
      borderBottom: `1px solid ${theme.border}`,
    },
    loadingText: {
      padding: "0 20px",
      color: theme.textSecondary,
      fontSize: "13px",
    },
    errorBox: {
      padding: "12px 20px",
      color: theme.warningText,
      fontSize: "12px",
      backgroundColor: theme.warningBg,
      border: `1px solid ${theme.warningBorder}`,
      borderRadius: "6px",
      margin: "0 10px",
    },
    emptyText: {
      padding: "0 20px",
      color: theme.textSecondary,
      fontSize: "13px",
    },
    treeContainer: {
      fontSize: "13px",
      color: theme.textSecondary,
      padding: "0 10px 20px 10px",
    },
  };

  return (
    <div>
      {/* Header with Refresh Button */}
      <div style={styles.header}>
        <span style={styles.headerTitle}>Files</span>
        <button
          type="button"
          style={styles.refreshButton}
          onClick={handleRefresh}
          disabled={loading}
          onMouseOver={(e) => {
            if (!loading) {
              e.currentTarget.style.backgroundColor = "rgba(255, 255, 255, 0.05)";
            }
          }}
          onMouseOut={(e) => {
            e.currentTarget.style.backgroundColor = "transparent";
          }}
        >
          <svg
            width="12"
            height="12"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            style={{
              transform: loading ? "rotate(360deg)" : "rotate(0deg)",
              transition: "transform 0.6s ease",
            }}
          >
            <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2" />
          </svg>
          {loading ? "..." : "Refresh"}
        </button>
      </div>

      {/* Branch switch indicator (shown above existing tree, doesn't clear it) */}
      {isSwitchingBranch && (
        <div style={styles.switchingBar}>Loading branch...</div>
      )}

      {/* Content */}
      {loading && tree.length === 0 && (
        <div style={styles.loadingText}>Loading files...</div>
      )}

      {!loading && !isSwitchingBranch && error && (
        <div style={styles.errorBox}>{error}</div>
      )}

      {!loading && !isSwitchingBranch && !error && tree.length === 0 && (
        <div style={styles.emptyText}>No files found</div>
      )}

      {tree.length > 0 && (
        <>
          {/* In-sidebar file search.  Enterprise repos run to hundreds
              of files; the explorer needs a quick narrowing affordance
              before any of the contextual menus matter. */}
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="🔍 Search files…"
            spellCheck={false}
            style={{
              width: "100%", boxSizing: "border-box",
              margin: "4px 0 8px",
              padding: "6px 10px", fontSize: 12,
              background: "#0d0e17", color: "#e4e4e7",
              border: "1px solid #27272A", borderRadius: 6,
              outline: "none",
              fontFamily: "system-ui, sans-serif",
            }}
          />

          <div style={{
            ...styles.treeContainer,
            opacity: isSwitchingBranch ? 0.5 : 1,
            transition: "opacity 0.15s ease",
          }}>
            {tree.map((node) => (
              <TreeNode
                key={node.path}
                node={node}
                level={0}
                filter={query.trim().toLowerCase()}
                selectedPath={selectedPath}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// Files with these extensions get a "Prepare Run" menu item.  Mirrors
// the backend's _RUNNABLE_EXTENSIONS so the menu only offers a run
// where the sandbox planner would actually accept the file.
const RUNNABLE_FILE_EXTS = new Set(["py", "js", "mjs", "cjs", "sh", "bash"]);

function isRunnableFile(name, type) {
  if (type === "tree") return false;
  if (!name || !name.includes(".")) return false;
  const ext = name.split(".").pop().toLowerCase();
  return RUNNABLE_FILE_EXTS.has(ext);
}

// Window-event dispatch helpers — keep FileTree decoupled from
// ChatPanel.  Same pattern as the existing approval-flow events.
function dispatchOpenFile(path) {
  try {
    window.dispatchEvent(new CustomEvent("gitpilot:open-file", { detail: { path } }));
  } catch (_e) { /* old browser */ }
}
function dispatchOpenInCanvas(path) {
  try {
    window.dispatchEvent(new CustomEvent("gitpilot:open-in-canvas", { detail: { path } }));
  } catch (_e) { /* old browser */ }
}
function dispatchRunFile(path) {
  try {
    window.dispatchEvent(new CustomEvent("gitpilot:run-file", { detail: { path } }));
  } catch (_e) { /* old browser */ }
}
function dispatchAskAboutFile(path) {
  try {
    window.dispatchEvent(new CustomEvent("gitpilot:ask-about-file", { detail: { path } }));
  } catch (_e) { /* old browser */ }
}
function copyToClipboard(text) {
  if (navigator?.clipboard) navigator.clipboard.writeText(text).catch(() => {});
}

// Tiny dropdown — positioned absolutely below the row's ⋯ trigger.
// Closes on outside click or Escape.  Built-in <select> would be
// faster to write but the visual contract for an enterprise file
// explorer demands a real menu (icons, separators, descriptions).
function FileActionsMenu({ path, runnable, onClose }) {
  React.useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    const onDown = () => onClose?.();
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [onClose]);

  // Menu vocabulary maps 1:1 to the design's recommended verbs.  "Open
  // Preview" is the calm default; "Open Workspace" widens the panel
  // for serious reading/editing; "Prepare Run…" surfaces the
  // ExecutionPlanCard.  Canvas (the runnable split editor) is moved
  // to the overflow because it's the heaviest mode and shouldn't
  // compete with Preview / Workspace for attention.
  const items = [
    { label: "Open Preview",   onClick: () => dispatchOpenFile(path) },
    {
      label: "Open Workspace",
      onClick: () => window.dispatchEvent(
        new CustomEvent("gitpilot:open-workspace", { detail: { path } })
      ),
    },
    {
      label: "Prepare Run…",
      onClick: () => dispatchRunFile(path),
      runnable: true,
      primary: true,
    },
    { divider: true },
    { label: "Ask GitPilot",   onClick: () => dispatchAskAboutFile(path) },
    { label: "Open in Canvas", onClick: () => dispatchOpenInCanvas(path), runnable: true },
    { label: "Copy path",      onClick: () => copyToClipboard(path) },
  ];

  return (
    <div
      role="menu"
      onMouseDown={(e) => e.stopPropagation()}
      style={{
        position: "absolute", right: 4, top: 22, zIndex: 50,
        minWidth: 180,
        background: "#18181B", border: "1px solid #3F3F46",
        borderRadius: 6, padding: "4px 0",
        boxShadow: "0 8px 20px rgba(0,0,0,0.45)",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      {items.map((it, i) => {
        if (it.divider) {
          return <div key={i} style={{ height: 1, background: "#27272A", margin: "4px 0" }} />;
        }
        if (it.runnable && !runnable) return null;
        return (
          <button
            key={i}
            role="menuitem"
            type="button"
            onClick={(e) => { e.stopPropagation(); it.onClick(); onClose?.(); }}
            style={{
              width: "100%", textAlign: "left",
              background: "transparent",
              color: it.primary ? "#86efac" : "#E4E4E7",
              border: "none",
              padding: "6px 12px", fontSize: 12, cursor: "pointer",
            }}
            onMouseEnter={(e) => e.currentTarget.style.background = "#27272A"}
            onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
          >
            {it.label}
          </button>
        );
      })}
    </div>
  );
}

// True when ``node`` or any descendant path/basename contains ``filter``.
// Folders are rendered if any descendant matches so the filter looks
// like a flat search even though the tree is recursive.
function _matchesFilter(node, filter) {
  if (!filter) return true;
  const inThis =
    (node.path && node.path.toLowerCase().includes(filter)) ||
    (node.name && node.name.toLowerCase().includes(filter));
  if (inThis) return true;
  if (!node.children) return false;
  return node.children.some((c) => _matchesFilter(c, filter));
}

// Recursive Node Component
function TreeNode({ node, level, filter = "", selectedPath = null }) {
  const [userExpanded, setUserExpanded] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const isFolder = node.children && node.children.length > 0;
  const runnable = !isFolder && isRunnableFile(node.name, node.type);
  const isSelected = !isFolder && selectedPath === node.path;

  // Honor the filter: drop subtrees that don't match anywhere.  When
  // the filter is active, folders auto-expand so users see the matches
  // without having to chase carets.
  if (filter && !_matchesFilter(node, filter)) return null;
  const expanded = userExpanded || Boolean(filter);

  const icon = isFolder ? (expanded ? "📂" : "📁") : "📄";

  const onRowClick = () => {
    if (isFolder) setUserExpanded(!userExpanded);
    else dispatchOpenFile(node.path);
  };

  return (
    <div style={{ position: "relative" }}>
      <div
        onClick={onRowClick}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => { setHovered(false); /* menu closes on outside-click */ }}
        style={{
          padding: "4px 0",
          paddingLeft: `${level * 12}px`,
          paddingRight: 6,
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: "6px",
          color: isFolder
            ? "#EDEDED"
            : (isSelected ? "#86efac" : "#D4D4D8"),
          whiteSpace: "nowrap",
          borderRadius: 4,
          background: isSelected
            ? "rgba(16,185,129,0.10)"
            : (hovered ? "#1f1f23" : "transparent"),
          borderLeft: isSelected ? "2px solid #10B981" : "2px solid transparent",
        }}
        title={isFolder ? "" : "Click to open · ⋯ for actions"}
      >
        <span style={{ fontSize: "14px", opacity: 0.7 }}>{icon}</span>
        <span style={{
          flex: 1, overflow: "hidden", textOverflow: "ellipsis",
          fontWeight: isSelected ? 600 : 400,
        }}>
          {node.name}
        </span>
        {/* Selected marker — orient users when scanning a long list. */}
        {isSelected && (
          <span style={{ color: "#10B981", fontSize: 11 }}>◄</span>
        )}

        {/* Per-row ⋯ menu trigger — visible on hover only so the
            list reads as a clean inventory at rest. */}
        {!isFolder && hovered && (
          <button
            type="button"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onMouseDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              setMenuOpen((v) => !v);
            }}
            title="File actions"
            style={{
              background: "transparent",
              border: "1px solid transparent",
              color: "#A1A1AA",
              cursor: "pointer",
              fontSize: 14,
              lineHeight: 1,
              padding: "0 6px",
              borderRadius: 4,
            }}
          >
            ⋯
          </button>
        )}
      </div>

      {menuOpen && (
        <FileActionsMenu
          path={node.path}
          runnable={runnable}
          onClose={() => setMenuOpen(false)}
        />
      )}

      {isFolder && expanded && (
        <div>
          {node.children.map(child => (
            <TreeNode
              key={child.path}
              node={child}
              level={level + 1}
              filter={filter}
              selectedPath={selectedPath}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// Helper to build tree structure from flat file list
function buildTree(files) {
  const root = [];
  
  files.forEach(file => {
    const parts = file.path.split('/');
    let currentLevel = root;
    let currentPath = "";

    parts.forEach((part, idx) => {
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      
      // Check if node exists at this level
      let existingNode = currentLevel.find(n => n.name === part);
      
      if (!existingNode) {
        const newNode = {
          name: part,
          path: currentPath,
          type: idx === parts.length - 1 ? file.type : 'tree',
          children: []
        };
        currentLevel.push(newNode);
        existingNode = newNode;
      }
      
      if (idx < parts.length - 1) {
        currentLevel = existingNode.children;
      }
    });
  });

  // Sort folders first, then files
  const sortNodes = (nodes) => {
    nodes.sort((a, b) => {
      const aIsFolder = a.children.length > 0;
      const bIsFolder = b.children.length > 0;
      if (aIsFolder && !bIsFolder) return -1;
      if (!aIsFolder && bIsFolder) return 1;
      return a.name.localeCompare(b.name);
    });
    nodes.forEach(n => {
      if (n.children.length > 0) sortNodes(n.children);
    });
  };

  sortNodes(root);
  return root;
}