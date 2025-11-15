import React, { useState, useEffect } from "react";
import FileTree from "./FileTree.jsx";

export default function ProjectContextPanel({ repo }) {
  const [fileCount, setFileCount] = useState(0);
  const [branch, setBranch] = useState("main");
  const [analyzing, setAnalyzing] = useState(false);

  useEffect(() => {
    if (!repo) return;

    // Fetch file tree to count files
    setAnalyzing(true);
    fetch(`/api/repos/${repo.owner}/${repo.name}/tree`)
      .then((res) => res.json())
      .then((data) => {
        setFileCount(data.files?.length || 0);
      })
      .catch((err) => console.error("Failed to fetch file tree:", err))
      .finally(() => setAnalyzing(false));
  }, [repo]);

  if (!repo) {
    return (
      <section className="gp-context">
        <div className="gp-card">
          <div className="gp-context-empty">
            <p>Please select a repository to view project context.</p>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="gp-context">
      <div className="gp-card">
        <header className="gp-card-header">
          <h2>Project context</h2>
          <span className="gp-badge">
            {repo.owner}/{repo.name}
          </span>
        </header>

        <div className="gp-context-meta">
          <div className="gp-context-meta-item">
            <span className="gp-context-meta-label">Branch:</span>
            <strong>{branch}</strong>
          </div>
          <div className="gp-context-meta-item">
            <span className="gp-context-meta-label">Files:</span>
            <strong>{analyzing ? "..." : fileCount}</strong>
          </div>
          <div className="gp-context-meta-item">
            <span className="gp-context-meta-label">Analyzed:</span>
            <strong>just now</strong>
          </div>
        </div>

        <div className="gp-context-tree">
          <FileTree repo={repo} />
        </div>
      </div>
    </section>
  );
}
