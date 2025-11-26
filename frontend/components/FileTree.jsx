import React, { useState, useEffect } from "react";

/**
 * Simple recursive file tree viewer
 * Fetches tree data directly using the API.
 */
export default function FileTree({ repo }) {
  const [tree, setTree] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!repo) return;
    setLoading(true);
    setError(null);
    
    // Construct headers manually to ensure no dependency on external utils for this component
    let headers = {};
    try {
      const token = localStorage.getItem("github_token");
      if (token) {
        headers = { Authorization: `Bearer ${token}` };
      }
    } catch (e) {
      console.warn("Unable to read github_token", e);
    }

    fetch(`/api/repos/${repo.owner}/${repo.name}/tree`, { headers })
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
  }, [repo]);

  if (loading) {
    return (
      <div style={{ padding: "0 20px", color: "#666", fontSize: "13px" }}>
        Loading files...
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ 
        padding: "12px 20px", 
        color: "#F59E0B", 
        fontSize: "12px",
        backgroundColor: "rgba(245, 158, 11, 0.1)",
        border: "1px solid rgba(245, 158, 11, 0.2)",
        borderRadius: "6px",
        margin: "0 10px"
      }}>
        ⚠️ {error}
      </div>
    );
  }

  if (tree.length === 0) {
    return (
      <div style={{ padding: "0 20px", color: "#666", fontSize: "13px" }}>
        No files found
      </div>
    );
  }

  return (
    <div style={{ fontSize: "13px", color: "#A1A1AA", padding: "0 10px 20px 10px" }}>
      {tree.map((node) => (
        <TreeNode key={node.path} node={node} level={0} />
      ))}
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