import React, { useState, useEffect } from "react";

/**
 * Simple recursive file tree viewer with refresh support
 * Fetches tree data directly using the API.
 */
export default function FileTree({ repo, refreshTrigger }) {
  const [tree, setTree] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [localRefresh, setLocalRefresh] = useState(0);

  useEffect(() => {
    if (!repo) return;
    setLoading(true);
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

    // Add cache busting
    const cacheBuster = `?_t=${Date.now()}`;

    fetch(`/api/repos/${repo.owner}/${repo.name}/tree${cacheBuster}`, { headers })
      .then(async (res) => {
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || "Failed to load files");
        }
        return res.json();
      })
      .then((data) => {
        if (data.files && Array.isArray(data.files)) {
          setTree(buildTree(data.files));
          setError(null);
        } else {
          setError("No files found in repository");
        }
      })
      .catch((err) => {
        setError(err.message);
        console.error("FileTree error:", err);
      })
      .finally(() => setLoading(false));
  }, [repo, refreshTrigger, localRefresh]);

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

      {/* Content */}
      {loading && (
        <div style={styles.loadingText}>Loading files...</div>
      )}

      {!loading && error && (
        <div style={styles.errorBox}>⚠️ {error}</div>
      )}

      {!loading && !error && tree.length === 0 && (
        <div style={styles.emptyText}>No files found</div>
      )}

      {!loading && !error && tree.length > 0 && (
        <div style={styles.treeContainer}>
          {tree.map((node) => (
            <TreeNode key={node.path} node={node} level={0} />
          ))}
        </div>
      )}
    </div>
  );
}

// Recursive Node Component
function TreeNode({ node, level }) {
  const [expanded, setExpanded] = useState(false);
  const isFolder = node.children && node.children.length > 0;
  
  const icon = isFolder ? (expanded ? "📂" : "📁") : "📄";

  return (
    <div>
      <div 
        onClick={() => isFolder && setExpanded(!expanded)}
        style={{ 
          padding: "4px 0", 
          paddingLeft: `${level * 12}px`,
          cursor: isFolder ? "pointer" : "default",
          display: "flex",
          alignItems: "center",
          gap: "6px",
          color: isFolder ? "#EDEDED" : "#A1A1AA",
          whiteSpace: "nowrap"
        }}
      >
        <span style={{ fontSize: "14px", opacity: 0.7 }}>{icon}</span>
        <span>{node.name}</span>
      </div>
      
      {isFolder && expanded && (
        <div>
          {node.children.map(child => (
            <TreeNode key={child.path} node={child} level={level + 1} />
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