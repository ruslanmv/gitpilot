import React, { useEffect, useState, useCallback } from "react";
import { authFetch } from "../utils/api.js";

const GithubMark = () => (
  <svg height="22" width="22" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
    <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
  </svg>
);

export default function RepoSelector({ onSelect, workspace, onOpenLocal }) {
  // Runtime mental model (server-authoritative; defaults to local for dev):
  //   cloud → no user filesystem, GitHub is the way in (folder/Git hidden)
  //   local → folder / local Git are first-class, GitHub is optional
  const runtime = workspace?.runtime === "cloud" ? "cloud" : "local";
  const isCloud = runtime === "cloud";

  // Initial hint from localStorage to avoid a flash of the search box; the
  // server's github_connected flag (below) is the source of truth.
  const hasTokenHint =
    typeof window !== "undefined" && !!localStorage.getItem("github_token");
  const [needsGitHub, setNeedsGitHub] = useState(!hasTokenHint);

  // Local-project entry (local runtime only).
  const [localKind, setLocalKind] = useState("folder"); // "folder" | "local_git"
  const [localPath, setLocalPath] = useState("");
  const [opening, setOpening] = useState(false);
  const [localErr, setLocalErr] = useState("");

  const [query, setQuery] = useState("");
  const [repos, setRepos] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [totalCount, setTotalCount] = useState(null);

  /**
   * Fetch repositories with pagination and optional search
   * @param {number} pageNum - Page number to fetch
   * @param {boolean} append - Whether to append or replace results
   * @param {string} searchQuery - Search query (uses current query if not provided)
   */
  const fetchRepos = useCallback(async (pageNum = 1, append = false, searchQuery = query) => {
    // Set appropriate loading state
    if (pageNum === 1) {
      setLoading(true);
      setStatus("");
    } else {
      setLoadingMore(true);
    }

    try {
      // Build URL with query parameters
      const params = new URLSearchParams();
      params.append("page", pageNum);
      params.append("per_page", "100");
      if (searchQuery) {
        params.append("query", searchQuery);
      }

      const url = `/api/repos?${params.toString()}`;
      const res = await authFetch(url);
      const data = await res.json().catch(() => ({}));

      // GitHub not linked yet → a normal pre-link state, never an error. The
      // backend returns 200 + github_connected:false; a 401 means the same.
      if (res.status === 401 || data.github_connected === false) {
        setNeedsGitHub(true);
        return;
      }

      if (!res.ok) {
        throw new Error(data.detail || data.error || "Failed to load repositories");
      }

      setNeedsGitHub(false);

      // Update repositories - append or replace
      if (append) {
        setRepos((prev) => [...prev, ...(data.repositories || [])]);
      } else {
        setRepos(data.repositories || []);
      }

      // Update pagination state
      setPage(pageNum);
      setHasMore(data.has_more);
      setTotalCount(data.total_count);

      // Show status if no results
      if (!append && (data.repositories || []).length === 0) {
        if (searchQuery) {
          setStatus(`No repositories matching "${searchQuery}"`);
        } else {
          setStatus("No repositories found");
        }
      } else {
        setStatus("");
      }
    } catch (err) {
      console.error("Error fetching repositories:", err);
      // Keep it gentle — no raw 401/500 text in the UI.
      setStatus("Couldn't load repositories right now. Please try again.");
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [query]);

  /**
   * Load more repositories (next page)
   */
  const loadMore = () => {
    fetchRepos(page + 1, true);
  };

  /**
   * Handle search - resets to page 1
   */
  const handleSearch = () => {
    setPage(1);
    fetchRepos(1, false, query);
  };

  /**
   * Handle input change - trigger search on Enter key
   */
  const handleKeyDown = (e) => {
    if (e.key === "Enter") {
      handleSearch();
    }
  };

  /**
   * Clear search and show all repos
   */
  const clearSearch = () => {
    setQuery("");
    setPage(1);
    fetchRepos(1, false, "");
  };

  // Always probe on mount: the server is the source of truth for whether a
  // GitHub identity exists (a user token OR a server-side PAT). It returns a
  // clean github_connected flag, so this never surfaces an error.
  useEffect(() => {
    fetchRepos(1, false, "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * Format repository count for display
   */
  const getCountText = () => {
    if (totalCount !== null) {
      // Search mode - show filtered count
      return `${repos.length} of ${totalCount} repositories`;
    } else {
      // Pagination mode - show loaded count
      return `${repos.length} ${repos.length === 1 ? "repository" : "repositories"}${hasMore ? "+" : ""}`;
    }
  };

  const openLocal = async () => {
    const p = localPath.trim();
    if (!p) {
      setLocalErr("Enter a path to continue.");
      return;
    }
    setOpening(true);
    setLocalErr("");
    try {
      const payload =
        localKind === "folder"
          ? { mode: "folder", folder_path: p }
          : { mode: "local_git", repo_root: p };
      await onOpenLocal?.(payload);
    } catch (e) {
      setLocalErr(e?.message || "Couldn't open that path. Check it exists and try again.");
    } finally {
      setOpening(false);
    }
  };

  // ── Empty state, by runtime ────────────────────────────────────────────
  // CLOUD: there is no user filesystem, so "pick a folder" is meaningless.
  // Connecting GitHub is the way to bring in code → make that the one action.
  if (needsGitHub && isCloud) {
    return (
      <div className="repo-search-box">
        <div className="repo-connect-card">
          <div className="repo-connect-icon"><GithubMark /></div>
          <div className="repo-connect-title">Connect GitHub to start</div>
          <div className="repo-connect-text">
            GitPilot is running in a <strong>cloud workspace</strong>. Link your GitHub
            account so we can list your repositories and create a working copy to
            edit, review, and open pull requests.
          </div>
          <a className="repo-connect-btn" href="/auth?connect=github&view=github">
            <GithubMark /> Link GitHub account
          </a>
          <div className="repo-connect-hint">
            GitHub access is required in cloud mode. You'll authorize GitPilot with a
            short device code on github.com — no password shared.
          </div>
        </div>
      </div>
    );
  }

  // LOCAL: folder / local Git are first-class; GitHub is genuinely optional.
  if (needsGitHub) {
    const tabBtn = (id, label) => (
      <button
        type="button"
        onClick={() => { setLocalKind(id); setLocalErr(""); }}
        style={{
          flex: 1, padding: "6px 10px", fontSize: "12px", cursor: "pointer",
          borderRadius: "6px", border: "1px solid #2a2b36",
          background: localKind === id ? "#272a33" : "transparent",
          color: localKind === id ? "#e8e8e8" : "#a0a0b0",
          fontWeight: localKind === id ? 600 : 400,
        }}
      >
        {label}
      </button>
    );
    return (
      <div className="repo-search-box">
        <div className="repo-connect-card">
          <div className="repo-connect-title">Open a local project</div>
          <div className="repo-connect-text">
            Choose a <strong>folder</strong> or a <strong>local Git repository</strong>{" "}
            path where GitPilot should start working.
          </div>
          <div style={{ display: "flex", gap: "8px", width: "100%" }}>
            {tabBtn("folder", "Folder")}
            {tabBtn("local_git", "Local Git")}
          </div>
          <input
            className="repo-search-input"
            style={{ width: "100%", fontFamily: "monospace" }}
            placeholder={localKind === "folder" ? "/path/to/project" : "/path/to/repo"}
            value={localPath}
            onChange={(e) => setLocalPath(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") openLocal(); }}
            disabled={opening}
          />
          {localErr && <div className="repo-status">{localErr}</div>}
          <button
            type="button"
            className="repo-connect-btn"
            onClick={openLocal}
            disabled={opening || !localPath.trim()}
          >
            {opening ? "Opening…" : localKind === "folder" ? "Open folder" : "Open repository"}
          </button>
          <div className="repo-connect-hint">
            GitHub is optional in local mode.{" "}
            <a href="/auth?connect=github&view=github" style={{ color: "#8ab4f8" }}>
              Connect GitHub
            </a>{" "}
            later to access private repos or open pull requests.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="repo-search-box">
      <div style={{ fontSize: "11px", opacity: 0.6, padding: "4px 8px", marginBottom: "8px" }}>
        {isCloud
          ? "Choose a repository to create a working copy in this cloud workspace."
          : "GitHub is optional. You can also open a local folder or Git repo from Admin → Workspace Modes."}
      </div>
      {/* Search Header */}
      <div className="repo-search-header">
        <div className="repo-search-row">
          <input
            className="repo-search-input"
            placeholder="Search repositories..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading}
          />
          <button
            className="repo-search-btn"
            onClick={handleSearch}
            type="button"
            disabled={loading}
          >
            {loading ? "..." : "Search"}
          </button>
        </div>

        {/* Search Info Bar */}
        {(query || repos.length > 0) && (
          <div className="repo-info-bar">
            <span className="repo-count">{getCountText()}</span>
            {query && (
              <button
                className="repo-clear-btn"
                onClick={clearSearch}
                type="button"
                disabled={loading}
              >
                Clear search
              </button>
            )}
          </div>
        )}
      </div>

      {/* Status Message */}
      {status && !loading && (
        <div className="repo-status">
          {status}
        </div>
      )}

      {/* Repository List */}
      <div className="repo-list">
        {repos.map((r) => (
          <button
            key={r.id}
            type="button"
            className="repo-item"
            onClick={() => onSelect(r)}
          >
            <div className="repo-item-content">
              <span className="repo-name">{r.name}</span>
              <span className="repo-owner">{r.owner}</span>
            </div>
            {r.private && (
              <span className="repo-badge-private">Private</span>
            )}
          </button>
        ))}

        {/* Loading Indicator */}
        {loading && repos.length === 0 && (
          <div className="repo-loading">
            <div className="repo-loading-spinner"></div>
            <span>Loading repositories...</span>
          </div>
        )}

        {/* Load More Button */}
        {hasMore && !loading && repos.length > 0 && (
          <button
            type="button"
            className="repo-load-more"
            onClick={loadMore}
            disabled={loadingMore}
          >
            {loadingMore ? (
              <>
                <div className="repo-loading-spinner-small"></div>
                Loading more...
              </>
            ) : (
              <>
                Load more repositories
                <span className="repo-load-more-count">({repos.length} loaded)</span>
              </>
            )}
          </button>
        )}

        {/* All Loaded Message */}
        {!hasMore && !loading && repos.length > 0 && (
          <div className="repo-all-loaded">
            ✓ All repositories loaded ({repos.length} total)
          </div>
        )}
      </div>

      {/* GitHub App Installation Notice */}
      <div className="repo-github-notice">
        <svg
          className="repo-github-icon"
          height="20"
          width="20"
          viewBox="0 0 16 16"
          fill="currentColor"
        >
          <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
        </svg>

        <div className="repo-github-notice-content">
          <div className="repo-github-notice-title">
            Repository missing?
          </div>
          <div className="repo-github-notice-text">
            Install the{" "}
            <a
              href="https://github.com/apps/gitpilota"
              target="_blank"
              rel="noopener noreferrer"
              className="repo-github-link"
            >
              GitPilot GitHub App
            </a>{" "}
            to access private repositories.
          </div>
        </div>
      </div>
    </div>
  );
}