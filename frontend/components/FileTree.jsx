import React, { useEffect, useState } from "react";

export default function FileTree({ repo }) {
  const [files, setFiles] = useState([]);
  const [selected, setSelected] = useState(null);
  const [status, setStatus] = useState("");

  useEffect(() => {
    const load = async () => {
      setStatus("Loading files...");
      try {
        const res = await fetch(`/api/repos/${repo.owner}/${repo.name}/tree`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error || "Failed to load tree");
        setFiles(data.files || []);
        if (!(data.files || []).length) setStatus("No files found.");
        else setStatus("");
      } catch (err) {
        console.error(err);
        setStatus(String(err.message || err));
      }
    };
    load();
  }, [repo.owner, repo.name]);

  return (
    <>
      {status && <div className="repo-status">{status}</div>}
      <div className="files-list">
        {files.map((f) => (
          <button
            key={f.path}
            type="button"
            className={
              "files-item" + (selected === f.path ? " files-item-active" : "")
            }
            onClick={() => setSelected(f.path)}
          >
            <span className="file-icon">📄</span>
            <span className="file-path">{f.path}</span>
          </button>
        ))}
        {!files.length && !status && (
          <div className="files-empty">No files in this repository.</div>
        )}
      </div>
    </>
  );
}
