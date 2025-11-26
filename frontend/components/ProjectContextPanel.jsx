// frontend/components/ProjectContextPanel.jsx
import React, { useState, useEffect } from "react";
import FileTree from "./FileTree.jsx";

/**
 * GitPilot - Project Context Panel
 * Displays repository metadata and handles GitHub App Installation/Permissions
 */
export default function ProjectContextPanel({ repo }) {
  const [appUrl, setAppUrl] = useState("");
  const [fileCount, setFileCount] = useState(0);
  const [branch, setBranch] = useState("main");
  const [analyzing, setAnalyzing] = useState(false);
  const [authIssue, setAuthIssue] = useState(false);
  const [accessStatus, setAccessStatus] = useState({
    installed: false,
    can_write: false,
    auth_type: "none",
    app_installed: false,
  });
  const [refreshTrigger, setRefreshTrigger] = useState(0); // For manual retry

  // Fetch the GitHub App Installation URL on mount
  useEffect(() => {
    fetch("/api/auth/app-url")
      .then((res) => res.json())
      .then((data) => {
        if (data.app_url) setAppUrl(data.app_url);
      })
      .catch((err) => console.error("Failed to fetch App URL:", err));
  }, []);

  // Check per-repo access (user token vs GitHub App)
  useEffect(() => {
    if (!repo) return;

    let headers = {};
    try {
      const token = localStorage.getItem("github_token");
      if (token) {
        headers = { Authorization: `Bearer ${token}` };
      }
    } catch (e) {
      console.warn("Unable to read github_token from localStorage:", e);
    }

    const params = new URLSearchParams({
      owner: repo.owner,
      repo: repo.name,
    });

    fetch(`/api/auth/repo-access?${params.toString()}`, { headers })
      .then((res) => res.json())
      .then((data) => {
        setAccessStatus(data);
        // If we cannot write, show the "Enable Agent Access" card
        setAuthIssue(!data.can_write);
      })
      .catch((err) => {
        console.error("Failed to fetch repo access status:", err);
      });
  }, [repo?.owner, repo?.name, refreshTrigger]);

  // Fetch simple repo stats whenever repo changes or user retries
  useEffect(() => {
    if (!repo) return;

    setBranch(repo.default_branch || "main");
    setAnalyzing(true);

    let headers = {};
    try {
      const token = localStorage.getItem("github_token");
      if (token) {
        headers = { Authorization: `Bearer ${token}` };
      }
    } catch (e) {
      console.warn("Unable to read github_token from localStorage:", e);
    }

    fetch(`/api/repos/${repo.owner}/${repo.name}/tree`, { headers })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}));

        if (!res.ok) {
          const message =
            data.detail || data.error || "Failed to load repository tree.";
          throw new Error(message);
        }

        setFileCount(Array.isArray(data.files) ? data.files.length : 0);
      })
      .catch((err) => {
        console.error("Failed to fetch file tree:", err);
      })
      .finally(() => setAnalyzing(false));
  }, [repo?.owner, repo?.name, repo?.default_branch, refreshTrigger]);

  const handleInstallClick = () => {
    if (!appUrl) return;
    // Append '/installations/new' to force the Repo Selection UI
    const targetUrl = appUrl.endsWith("/")
      ? `${appUrl}installations/new`
      : `${appUrl}/installations/new`;

    window.open(targetUrl, "_blank", "noopener,noreferrer");
  };

  const handleRetry = () => {
    setAnalyzing(true);
    setRefreshTrigger((prev) => prev + 1);
  };

  // --- Styles (Anthropic/Claude Dark Theme) ---
  const theme = {
    bg: "#131316",
    border: "#27272A",
    textPrimary: "#EDEDED",
    textSecondary: "#A1A1AA",
    accent: "#D95C3D",
    accentHover: "#C44F32",
    warningBg: "rgba(245, 158, 11, 0.1)",
    warningBorder: "rgba(245, 158, 11, 0.2)",
    warningText: "#F59E0B",
    cardBg: "#18181B",
  };

  const styles = {
    container: {
      height: "100%",
      borderRight: `1px solid ${theme.border}`,
      backgroundColor: theme.bg,
      display: "flex",
      flexDirection: "column",
      fontFamily: '"Söhne", "Inter", -apple-system, system-ui, sans-serif',
    },
    header: {
      padding: "16px 20px",
      borderBottom: `1px solid ${theme.border}`,
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
    },
    title: {
      fontSize: "13px",
      fontWeight: "600",
      color: theme.textPrimary,
      letterSpacing: "-0.01em",
    },
    repoBadge: {
      backgroundColor: "#27272A",
      color: theme.textSecondary,
      fontSize: "11px",
      padding: "2px 8px",
      borderRadius: "12px",
      border: `1px solid ${theme.border}`,
      fontFamily: "monospace",
      maxWidth: "200px",
      overflow: "hidden",
      textOverflow: "ellipsis",
      whiteSpace: "nowrap",
    },
    content: {
      padding: "16px 20px 12px 20px",
      display: "flex",
      flexDirection: "column",
      gap: "16px",
      minHeight: 0,
    },
    statRow: {
      display: "flex",
      justifyContent: "space-between",
      fontSize: "13px",
      marginBottom: "4px",
    },
    label: { color: theme.textSecondary },
    value: { color: theme.textPrimary, fontWeight: "500" },

    installCard: {
      marginTop: "8px",
      padding: "16px",
      borderRadius: "8px",
      backgroundColor: theme.cardBg,
      border: `1px solid ${theme.border}`,
      display: "flex",
      flexDirection: "column",
      gap: "12px",
      boxShadow:
        "0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)",
    },
    installHeader: {
      display: "flex",
      alignItems: "center",
      gap: "10px",
      fontSize: "14px",
      fontWeight: "600",
      color: theme.textPrimary,
    },
    installText: {
      fontSize: "13px",
      color: theme.textSecondary,
      lineHeight: "1.5",
    },
    buttonRow: {
      display: "flex",
      gap: "10px",
      marginTop: "4px",
    },
    installButton: {
      flex: 1,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      gap: "8px",
      height: "36px",
      backgroundColor: theme.accent,
      color: "#FFFFFF",
      border: "none",
      borderRadius: "6px",
      fontSize: "13px",
      fontWeight: "500",
      cursor: appUrl ? "pointer" : "not-allowed",
      opacity: appUrl ? 1 : 0.6,
      transition: "background 0.2s ease",
    },
    retryButton: {
      height: "36px",
      padding: "0 12px",
      backgroundColor: "transparent",
      color: theme.textSecondary,
      border: `1px solid ${theme.border}`,
      borderRadius: "6px",
      fontSize: "13px",
      fontWeight: "500",
      cursor: "pointer",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
    },
    treeWrapper: {
      marginTop: "8px",
      paddingTop: "8px",
      borderTop: `1px solid ${theme.border}`,
      overflow: "auto",
      flex: 1,
    },
    emptyState: {
      padding: "20px",
      color: theme.textSecondary,
      fontSize: "13px",
    },
  };

  if (!repo) {
    return (
      <div style={styles.container}>
        <div style={styles.header}>
          <span style={styles.title}>Project context</span>
        </div>
        <div style={styles.emptyState}>Select a repository to view context.</div>
      </div>
    );
  }

  const shortName =
    repo.full_name && repo.full_name.length > 26
      ? repo.full_name.slice(0, 26) + "…"
      : repo.full_name || `${repo.owner}/${repo.name}`;

  return (
    <div style={styles.container}>
      {/* Header */}
      <div style={styles.header}>
        <span style={styles.title}>Project context</span>
        <span style={styles.repoBadge}>{shortName}</span>
      </div>

      {/* Stats & Actions */}
      <div style={styles.content}>
        <div>
          <div style={styles.statRow}>
            <span style={styles.label}>Branch:</span>
            <span style={styles.value}>{branch}</span>
          </div>
          <div style={styles.statRow}>
            <span style={styles.label}>Files:</span>
            <span style={styles.value}>{analyzing ? "…" : fileCount}</span>
          </div>
          <div style={styles.statRow}>
            <span style={styles.label}>Access via:</span>
            <span style={styles.value}>
              {accessStatus.auth_type === "user_token" &&
                "User token (OAuth/PAT)"}
              {accessStatus.auth_type === "github_app" &&
                (accessStatus.app_installed
                  ? "GitHub App (installed)"
                  : "GitHub App (not installed)")}
              {accessStatus.auth_type === "none" && "Not configured"}
            </span>
          </div>
          <div style={styles.statRow}>
            <span style={styles.label}>Status:</span>
            <span
              style={{
                ...styles.value,
                color: authIssue ? theme.warningText : "#10B981",
              }}
            >
              {authIssue ? "Access Needed" : "Connected"}
            </span>
          </div>
        </div>

        {/* INSTALL ACTION CARD: Only shows if there is an issue */}
        {authIssue && (
          <div style={styles.installCard}>
            <div style={styles.installHeader}>
              <span>⚡</span>
              <span>Enable Agent Access</span>
            </div>

            <p style={styles.installText}>
              GitPilot needs permission to access this repository. Install the
              GitHub App to enable agent capabilities without configuration
              files.
            </p>

            <div style={styles.buttonRow}>
              <button
                type="button"
                style={styles.installButton}
                disabled={!appUrl}
                onClick={handleInstallClick}
                onMouseOver={(e) => {
                  if (appUrl)
                    e.currentTarget.style.backgroundColor = theme.accentHover;
                }}
                onMouseOut={(e) => {
                  if (appUrl)
                    e.currentTarget.style.backgroundColor = theme.accent;
                }}
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="currentColor"
                >
                  <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405 1.02 0 2.04.135 3 .405 2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
                </svg>
                Install App
              </button>

              <button
                type="button"
                style={styles.retryButton}
                onClick={handleRetry}
                title="Click this after installing to reload"
              >
                Verify
              </button>
            </div>
          </div>
        )}
      </div>

      {!authIssue && (
        <div style={styles.treeWrapper}>
          <FileTree repo={repo} />
        </div>
      )}
    </div>
  );
}
