import React, { useEffect, useState } from "react";
import { authFetch } from "../utils/api.js";

export default function RepoSelector({ onSelect }) {
  const [query, setQuery] = useState("");
  const [repos, setRepos] = useState([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("");

  const search = async () => {
    setLoading(true);
    setStatus("");
    try {
      const url = "/api/repos" + (query ? `?query=${encodeURIComponent(query)}` : "");
      const res = await authFetch(url);
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || data.error || "Failed to load repos");
      }
      setRepos(data);
      if (!data.length) setStatus("No repositories found.");
    } catch (err) {
      console.error(err);
      setStatus(String(err.message || err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    search();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="repo-search-box">
      <div className="repo-search-row">
        <input
          className="repo-search-input"
          placeholder="Search repositories"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") search();
          }}
        />
        <button className="repo-search-btn" onClick={search} type="button">
          {loading ? "..." : "Search"}
        </button>
      </div>
      {status && <div className="repo-status">{status}</div>}
      <div className="repo-list">
        {repos.map((r) => (
          <button
            key={r.id}
            type="button"
            className="repo-item"
            onClick={() => onSelect(r)}
          >
            <span className="repo-name">{r.name}</span>
            <span className="repo-owner">{r.owner}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
