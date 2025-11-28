import React, { useEffect, useState, useCallback } from "react";
import { authFetch } from "../utils/api.js";

export default function RepoSelector({ onSelect }) {
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
      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || data.error || "Failed to load repositories");
      }

      // Update repositories - append or replace
      if (append) {
        setRepos((prev) => [...prev, ...data.repositories]);
      } else {
        setRepos(data.repositories);
      }

      // Update pagination state
      setPage(pageNum);
      setHasMore(data.has_more);
      setTotalCount(data.total_count);

      // Show status if no results
      if (!append && data.repositories.length === 0) {
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
      setStatus(err.message || "Failed to load repositories");
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

  // Initial load on mount
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

  return (
    <div className="repo-search-box">
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