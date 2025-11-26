import React, { useState, useEffect } from "react";

export default function FileTree({ repo }) {
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!repo) return;

    setLoading(true);
    setError("");

    // Auth Header: Crucial for accessing private repos
    let headers = {};
    const token = localStorage.getItem("github_token");
    if (token) headers = { Authorization: `Bearer ${token}` };

    fetch(`/api/repos/${repo.owner}/${repo.name}/tree`, { headers })
      .then(async (res) => {
        if (!res.ok) {
           const data = await res.json().catch(() => ({}));
           // If 404/403, we rely on the parent component (ProjectContextPanel) to show the "Install App" card.
           // Here we just handle the error state gracefully without crashing.
           throw new Error(data.detail || "Could not fetch file tree");
        }
        return res.json();
      })
      .then((data) => {
        setFiles(data.files || []);
      })
      .catch((err) => {
        console.error("FileTree Error:", err);
        setError(err.message);
      })
      .finally(() => setLoading(false));
  }, [repo]);

  // --- Styles ---
  const styles = {
    container: {
      padding: "0 20px 20px 20px",
      fontSize: "13px",
      fontFamily: "monospace",
      color: "#A1A1AA",
    },
    item: {
      padding: "4px 0",
      display: "flex",
      alignItems: "center",
      gap: "8px",
      cursor: "default",
    },
    icon: {
      opacity: 0.6,
      width: "14px",
      textAlign: "center"
    },
    loading: {
      padding: "20px",
      textAlign: "center",
      color: "#52525B",
      fontSize: "12px",
    },
    error: {
        padding: "10px 20px",
        color: "#EF4444",
        fontSize: "12px"
    }
  };

  if (loading) {
    return <div style={styles.loading}>Loading file tree...</div>;
  }

  if (error) {
    // Return empty if error, as parent handles the visual alert
    return <div style={{...styles.loading, color: "#71717A"}}>Tree unavailable</div>;
  }

  if (files.length === 0) {
    return <div style={styles.loading}>No files found.</div>;
  }

  return (
    <div style={styles.container}>
      {files.map((file, idx) => (
        <div key={idx} style={styles.item}>
           <span style={styles.icon}>
             {file.type === "tree" || file.type === "dir" ? "📁" : "📄"}
           </span>
           <span>{file.path}</span>
        </div>
      ))}
    </div>
  );
}