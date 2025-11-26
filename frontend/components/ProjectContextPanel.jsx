import React, { useState, useEffect } from "react";
import FileTree from "./FileTree.jsx";

/**
 * GitPilot - Project Context Panel
 * WORKING VERSION: Shows correct status and includes refresh functionality
 */
export default function ProjectContextPanel({ repo }) {
  const [appUrl, setAppUrl] = useState("");
  const [fileCount, setFileCount] = useState(0);
  const [branch, setBranch] = useState("main");
  const [analyzing, setAnalyzing] = useState(false);
  const [accessInfo, setAccessInfo] = useState(null);
  const [treeError, setTreeError] = useState(null);
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  // Fetch the GitHub App Installation URL on mount
  useEffect(() => {
    fetch("/api/auth/app-url")
      .then((res) => res.json())
      .then((data) => {
        if (data.app_url) setAppUrl(data.app_url);
      })
      .catch((err) => console.error("Failed to fetch App URL:", err));
  }, []);

  // Fetch repo access info and stats whenever repo changes or user retries
  useEffect(() => {
    if (!repo) return;

    setBranch(repo.default_branch || "main");
    setAnalyzing(true);
    setAccessInfo(null);
    setTreeError(null);

    // Get GitHub token from localStorage
    let headers = {};
    try {
      const token = localStorage.getItem("github_token");
      if (token) {
        headers = { Authorization: `Bearer ${token}` };
      }
    } catch (e) {
      console.warn("Unable to read github_token from localStorage:", e);
    }

    // Add cache busting parameter when user clicks refresh
    const cacheBuster = refreshTrigger > 0 ? `&_t=${Date.now()}` : '';

    // Check repo access
    fetch(`/api/auth/repo-access?owner=${repo.owner}&repo=${repo.name}${cacheBuster}`, { headers })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}));
        
        if (!res.ok) {
          console.error("Failed to check repo access:", data);
          setAccessInfo({
            can_write: false,
            app_installed: false,
            auth_type: "none"
          });
        } else {
          setAccessInfo(data);
          console.log("Access info:", data);
        }
      })
      .catch((err) => {
        console.error("Failed to check repo access:", err);
        setAccessInfo({
          can_write: false,
          app_installed: false,
          auth_type: "none"
        });
      });

    // Fetch file tree for stats
    fetch(`/api/repos/${repo.owner}/${repo.name}/tree${cacheBuster ? '?' + cacheBuster.slice(1) : ''}`, { headers })
      .then(async (res) => {
        const data = await res.json().catch(() => ({}));

        if (!res.ok) {
          const message = data.detail || data.error || "Failed to load repository tree.";
          setTreeError(message);
        } else {
          setFileCount(Array.isArray(data.files) ? data.files.length : 0);
          setTreeError(null);
        }
      })
      .catch((err) => {
        console.error("Failed to fetch file tree:", err);
        setTreeError(err.message);
      })
      .finally(() => setAnalyzing(false));
  }, [repo?.owner, repo?.name, repo?.default_branch, refreshTrigger]);

  const handleInstallClick = () => {
    if (!appUrl) return;
    const targetUrl = appUrl.endsWith("/") 
        ? `${appUrl}installations/new` 
        : `${appUrl}/installations/new`;
    
    window.open(targetUrl, "_blank", "noopener,noreferrer");
  };

  const handleRefresh = () => {
    setAnalyzing(true);
    setRefreshTrigger(prev => prev + 1);
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
    successColor: "#10B981",
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
    
    // Refresh button style
    refreshButton: {
      marginTop: "8px",
      height: "32px",
      padding: "0 12px",
      backgroundColor: "transparent",
      color: theme.textSecondary,
      border: `1px solid ${theme.border}`,
      borderRadius: "6px",
      fontSize: "12px",
      fontWeight: "500",
      cursor: "pointer",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      gap: "6px",
      transition: "all 0.2s",
    },
    
    // Install Link (Subtle)
    installLink: {
      marginTop: "4px",
      fontSize: "12px",
      color: theme.accent,
      textDecoration: "none",
      cursor: "pointer",
      display: "flex",
      alignItems: "center",
      gap: "6px",
      transition: "opacity 0.2s",
    },
    
    // Enterprise Install Card
    installCard: {
      marginTop: "8px",
      padding: "16px",
      borderRadius: "8px",
      backgroundColor: theme.cardBg,
      border: `1px solid ${theme.warningBorder}`,
      display: "flex",
      flexDirection: "column",
      gap: "12px",
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
        display: 'flex',
        gap: '10px',
        marginTop: '4px'
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
    errorState: {
      padding: "20px",
      color: theme.warningText,
      fontSize: "12px",
      backgroundColor: theme.warningBg,
      border: `1px solid ${theme.warningBorder}`,
      borderRadius: "6px",
      margin: "10px 20px",
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

  // Determine status - SIMPLIFIED: Just check app_installed
  let statusText = "Checking...";
  let statusColor = theme.textSecondary;
  let showInstallCard = false;
  let showInstallLink = false;
  
  if (!analyzing && accessInfo) {
    if (accessInfo.app_installed) {
      // App installed (user has push access)
      statusText = "Write Access ✓";
      statusColor = theme.successColor;
      showInstallCard = false;
      showInstallLink = false;
    } else {
      // No push access - show install prompt
      statusText = "Read Only";
      statusColor = theme.warningText;
      showInstallCard = true;
      showInstallLink = true;
    }
  }

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
            <span style={styles.label}>Status:</span>
            <span style={{...styles.value, color: statusColor}}>
                 {statusText}
            </span>
          </div>
          
          {/* Refresh Button - Always visible */}
          <button
            type="button"
            style={styles.refreshButton}
            onClick={handleRefresh}
            disabled={analyzing}
            onMouseOver={(e) => {
              if (!analyzing) {
                e.currentTarget.style.backgroundColor = "rgba(255, 255, 255, 0.05)";
              }
            }}
            onMouseOut={(e) => {
              e.currentTarget.style.backgroundColor = "transparent";
            }}
          >
            <svg 
              width="14" 
              height="14" 
              viewBox="0 0 24 24" 
              fill="none" 
              stroke="currentColor" 
              strokeWidth="2"
              style={{
                transform: analyzing ? "rotate(360deg)" : "rotate(0deg)",
                transition: "transform 0.6s ease"
              }}
            >
              <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2" />
            </svg>
            {analyzing ? "Refreshing..." : "Refresh"}
          </button>
          
          {/* Install App link if needed */}
          {showInstallLink && appUrl && (
            <div style={{ marginTop: "8px" }}>
              <a
                href="#"
                style={styles.installLink}
                onClick={(e) => {
                  e.preventDefault();
                  handleInstallClick();
                }}
                onMouseOver={(e) => { e.currentTarget.style.opacity = "0.8"; }}
                onMouseOut={(e) => { e.currentTarget.style.opacity = "1"; }}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405 1.02 0 2.04.135 3 .405 2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
                </svg>
                Install GitPilot App to enable write access
              </a>
            </div>
          )}
        </div>

        {/* INSTALL ACTION CARD: Shows when app not installed */}
        {showInstallCard && (
          <div style={styles.installCard}>
            <div style={styles.installHeader}>
              <span>⚡</span>
              <span>Enable Agent Write Access</span>
            </div>

            <p style={styles.installText}>
              Install the GitPilot GitHub App to enable AI agent operations 
              (create, modify, delete files) on this repository.
            </p>

            <div style={styles.buttonRow}>
                <button
                type="button"
                style={styles.installButton}
                disabled={!appUrl}
                onClick={handleInstallClick}
                onMouseOver={(e) => {
                    if (appUrl) e.currentTarget.style.backgroundColor = theme.accentHover;
                }}
                onMouseOut={(e) => {
                    if (appUrl) e.currentTarget.style.backgroundColor = theme.accent;
                }}
                >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405 1.02 0 2.04.135 3 .405 2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
                </svg>
                Install App
                </button>
            </div>
          </div>
        )}
      </div>

      {/* File Tree - Always show */}
      <div style={styles.treeWrapper}>
        {treeError ? (
          <div style={styles.errorState}>
            ⚠️ Failed to load files: {treeError}
          </div>
        ) : (
          <FileTree repo={repo} refreshTrigger={refreshTrigger} />
        )}
      </div>
    </div>
  );
}