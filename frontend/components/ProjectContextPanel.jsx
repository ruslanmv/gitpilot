import React, { useEffect, useMemo, useRef, useState } from "react";
import { apiUrl } from "../utils/api.js";
import FileTree from "./FileTree.jsx";
import BranchPicker from "./BranchPicker.jsx";
import SandboxStatusWidget from "./SandboxStatusWidget.jsx";

// --- INJECTED STYLES FOR ANIMATIONS ---
const animationStyles = `
  @keyframes highlight-pulse {
    0% { background-color: rgba(59, 130, 246, 0.10); }
    50% { background-color: rgba(59, 130, 246, 0.22); }
    100% { background-color: transparent; }
  }
  .pulse-context {
    animation: highlight-pulse 1.1s ease-out;
  }
`;

/**
 * ProjectContextPanel (Production-ready)
 *
 * Controlled component:
 * - Branch source of truth is App.jsx:
 * - defaultBranch (prod)
 * - currentBranch (what user sees)
 * - sessionBranches (list of all active AI session branches)
 *
 * Responsibilities:
 * - Show project context + branch dropdown + AI badge/banner
 * - Fetch access status + file count for the currentBranch
 * - Trigger visual pulse on pulseNonce (Hard Switch)
 */
export default function ProjectContextPanel({
  repo,
  defaultBranch,
  currentBranch,
  sessionBranch, // Active session branch (optional, for specific highlighting)
  sessionBranches = [], // List of all AI branches
  onBranchChange,
  pulseNonce,
  onSettingsClick,
  runtime,
}) {
  const [appUrl, setAppUrl] = useState("");
  const [fileCount, setFileCount] = useState(0);

  const [isDropdownOpen, setIsDropdownOpen] = useState(false);

  // Data Loading State
  const [analyzing, setAnalyzing] = useState(false);
  const [accessInfo, setAccessInfo] = useState(null);
  const [treeError, setTreeError] = useState(null);

  // Retry / Refresh Logic
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [retryCount, setRetryCount] = useState(0);
  const retryTimeoutRef = useRef(null);

  // UX State
  const [animateHeader, setAnimateHeader] = useState(false);
  const [toast, setToast] = useState({ visible: false, title: "", msg: "" });

  // Calculate effective default to prevent 'main' fallback errors
  const effectiveDefaultBranch = defaultBranch || repo?.default_branch || "main";
  const branch = currentBranch || effectiveDefaultBranch;

  // Determine if we are currently viewing an AI Session branch
  const isAiSession = (sessionBranches.includes(branch)) || (sessionBranch === branch && branch !== effectiveDefaultBranch);

  // Fetch App URL on mount
  useEffect(() => {
    fetch(apiUrl("/api/auth/app-url"))
      .then((res) => res.json())
      .then((data) => {
        if (data.app_url) setAppUrl(data.app_url);
      })
      .catch((err) => console.error("Failed to fetch App URL:", err));
  }, []);

  // Hard Switch pulse: whenever App increments pulseNonce
  useEffect(() => {
    if (!pulseNonce) return;
    setAnimateHeader(true);
    const t = window.setTimeout(() => setAnimateHeader(false), 1100);
    return () => window.clearTimeout(t);
  }, [pulseNonce]);

  // Main data fetcher (Access + Tree stats) for currentBranch
  // Stale-while-revalidate: keep previous data visible during fetch
  useEffect(() => {
    if (!repo) return;

    // Only show full "analyzing" spinner if we have no data yet
    if (!accessInfo) setAnalyzing(true);
    setTreeError(null);

    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current);
      retryTimeoutRef.current = null;
    }

    let headers = {};
    try {
      const token = localStorage.getItem("github_token");
      if (token) headers = { Authorization: `Bearer ${token}` };
    } catch (e) {
      console.warn("Unable to read github_token:", e);
    }

    let cancelled = false;
    const cacheBuster = `&_t=${Date.now()}&retry=${retryCount}`;

    // A) Access Check (with Stale Cache Fix)
    fetch(apiUrl(`/api/auth/repo-access?owner=${repo.owner}&repo=${repo.name}${cacheBuster}`), {
      headers,
      cache: "no-cache",
    })
      .then(async (res) => {
        if (cancelled) return;
        const data = await res.json().catch(() => ({}));

        if (!res.ok) {
          setAccessInfo({ can_write: false, app_installed: false, auth_type: "none" });
          return;
        }

        setAccessInfo(data);

        // Auto-retry if user has push access but App is not detected yet (Stale Cache)
        if (data.can_write && !data.app_installed && retryCount === 0) {
          retryTimeoutRef.current = setTimeout(() => {
            setRetryCount(1);
          }, 1000);
        }
      })
      .catch(() => {
        if (!cancelled) setAccessInfo({ can_write: false, app_installed: false, auth_type: "none" });
      });

    // B) Tree count for the selected branch
    // Don't clear fileCount — keep stale value visible until new one arrives
    const hadFileCount = fileCount > 0;
    if (!hadFileCount) setAnalyzing(true);

    fetch(apiUrl(`/api/repos/${repo.owner}/${repo.name}/tree?ref=${encodeURIComponent(branch)}&_t=${Date.now()}`), {
      headers,
      cache: "no-cache",
    })
      .then(async (res) => {
        if (cancelled) return;
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          setTreeError(data.detail || "Failed to load tree");
          setFileCount(0);
          return;
        }
        setFileCount(Array.isArray(data.files) ? data.files.length : 0);
      })
      .catch((err) => {
        if (cancelled) return;
        setTreeError(err.message);
        setFileCount(0);
      })
      .finally(() => { if (!cancelled) setAnalyzing(false); });

    return () => {
      cancelled = true;
      if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repo?.owner, repo?.name, branch, refreshTrigger, retryCount]);

  const showToast = (title, msg) => {
    setToast({ visible: true, title, msg });
    setTimeout(() => setToast((prev) => ({ ...prev, visible: false })), 3000);
  };

  const handleManualSwitch = (targetBranch) => {
    if (!targetBranch || targetBranch === branch) {
      setIsDropdownOpen(false);
      return;
    }

    // Local UI feedback (App.jsx will handle the actual state change)
    const goingAi = sessionBranches.includes(targetBranch);
    showToast(
      goingAi ? "Context Switched" : "Switched to Production",
      goingAi ? `Viewing AI Session: ${targetBranch}` : `Viewing ${targetBranch}.`
    );

    setIsDropdownOpen(false);
    if (onBranchChange) onBranchChange(targetBranch);
  };

  const handleRefresh = () => {
    setAnalyzing(true);
    setRetryCount(0);
    setRefreshTrigger((prev) => prev + 1);
  };

  const handleInstallClick = () => {
    if (!appUrl) return;
    const targetUrl = appUrl.endsWith("/") ? `${appUrl}installations/new` : `${appUrl}/installations/new`;
    window.open(targetUrl, "_blank", "noopener,noreferrer");
  };

  // --- STYLES ---
  const theme = useMemo(
    () => ({
      bg: "#131316",
      border: "#27272A",
      textPrimary: "#EDEDED",
      textSecondary: "#A1A1AA",
      accent: "#3b82f6",
      warningBorder: "rgba(245, 158, 11, 0.2)",
      warningText: "#F59E0B",
      successColor: "#10B981",
      cardBg: "#18181B",
      aiBg: "rgba(59, 130, 246, 0.10)",
      aiBorder: "rgba(59, 130, 246, 0.30)",
      aiText: "#60a5fa",
    }),
    []
  );

  const styles = useMemo(
    () => ({
      container: {
        height: "100%",
        borderRight: `1px solid ${theme.border}`,
        backgroundColor: theme.bg,
        display: "flex",
        flexDirection: "column",
        fontFamily: '"Söhne", "Inter", sans-serif',
        position: "relative",
        overflow: "hidden",
      },
      header: {
        padding: "16px 20px",
        borderBottom: `1px solid ${theme.border}`,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        transition: "background-color 0.3s ease",
      },
      titleGroup: { display: "flex", alignItems: "center", gap: "8px" },
      title: { fontSize: "13px", fontWeight: "600", color: theme.textPrimary },
      repoBadge: {
        backgroundColor: "#27272A",
        color: theme.textSecondary,
        fontSize: "11px",
        padding: "2px 8px",
        borderRadius: "12px",
        border: `1px solid ${theme.border}`,
        fontFamily: "monospace",
      },
      aiBadge: {
        display: "flex",
        alignItems: "center",
        gap: "6px",
        backgroundColor: theme.aiBg,
        color: theme.aiText,
        fontSize: "10px",
        fontWeight: "bold",
        padding: "2px 8px",
        borderRadius: "12px",
        border: `1px solid ${theme.aiBorder}`,
        textTransform: "uppercase",
        letterSpacing: "0.5px",
      },
      content: {
        padding: "16px 20px 12px 20px",
        display: "flex",
        flexDirection: "column",
        gap: "12px",
      },
      statRow: { display: "flex", justifyContent: "space-between", fontSize: "13px", marginBottom: "4px" },
      label: { color: theme.textSecondary },
      value: { color: theme.textPrimary, fontWeight: "500" },
      dropdownContainer: { position: "relative" },
      branchButton: {
        display: "flex",
        alignItems: "center",
        gap: "6px",
        padding: "4px 8px",
        borderRadius: "4px",
        border: `1px solid ${isAiSession ? theme.aiBorder : theme.border}`,
        backgroundColor: isAiSession ? "rgba(59, 130, 246, 0.05)" : "transparent",
        color: isAiSession ? theme.aiText : theme.textPrimary,
        fontSize: "13px",
        cursor: "pointer",
        fontFamily: "monospace",
      },
      dropdownMenu: {
        position: "absolute",
        top: "100%",
        left: 0,
        marginTop: "4px",
        width: "240px",
        backgroundColor: "#1F1F23",
        border: `1px solid ${theme.border}`,
        borderRadius: "6px",
        boxShadow: "0 4px 12px rgba(0,0,0,0.5)",
        zIndex: 50,
        display: isDropdownOpen ? "block" : "none",
        overflow: "hidden",
      },
      dropdownItem: {
        padding: "8px 12px",
        fontSize: "13px",
        color: theme.textSecondary,
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        gap: "8px",
        borderBottom: `1px solid ${theme.border}`,
      },
      contextBanner: {
        backgroundColor: theme.aiBg,
        borderTop: `1px solid ${theme.aiBorder}`,
        padding: "8px 20px",
        fontSize: "11px",
        color: theme.aiText,
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
      },
      toast: {
        position: "absolute",
        top: "16px",
        right: "16px",
        backgroundColor: "#18181B",
        border: `1px solid ${theme.border}`,
        borderLeft: `3px solid ${theme.accent}`,
        borderRadius: "6px",
        padding: "12px",
        boxShadow: "0 4px 12px rgba(0,0,0,0.5)",
        zIndex: 100,
        minWidth: "240px",
        transition: "all 0.3s cubic-bezier(0.16, 1, 0.3, 1)",
        transform: toast.visible ? "translateX(0)" : "translateX(120%)",
        opacity: toast.visible ? 1 : 0,
      },
      toastTitle: { fontSize: "13px", fontWeight: "bold", color: theme.textPrimary, marginBottom: "2px" },
      toastMsg: { fontSize: "11px", color: theme.textSecondary },
      refreshButton: {
        marginTop: "8px",
        height: "32px",
        padding: "0 12px",
        backgroundColor: "transparent",
        color: theme.textSecondary,
        border: `1px solid ${theme.border}`,
        borderRadius: "6px",
        fontSize: "12px",
        cursor: analyzing ? "not-allowed" : "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: "6px",
      },
      settingsBtn: {
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: "28px",
        height: "28px",
        borderRadius: "6px",
        border: `1px solid ${theme.border}`,
        backgroundColor: "transparent",
        color: theme.textSecondary,
        cursor: "pointer",
        padding: 0,
        transition: "color 0.15s, border-color 0.15s",
      },
      treeWrapper: { flex: 1, overflow: "auto", borderTop: `1px solid ${theme.border}` },
      installCard: {
        marginTop: "8px",
        padding: "12px",
        borderRadius: "8px",
        backgroundColor: theme.cardBg,
        border: `1px solid ${theme.warningBorder}`,
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
    }),
    [analyzing, isAiSession, isDropdownOpen, theme, toast.visible]
  );

  // Determine status text
  let statusText = "Checking...";
  let statusColor = theme.textSecondary;
  let showInstallCard = false;

  if (!analyzing && accessInfo) {
    if (accessInfo.app_installed) {
      statusText = "Write Access ✓";
      statusColor = theme.successColor;
    } else if (accessInfo.can_write && retryCount === 0) {
      statusText = "Verifying...";
    } else if (accessInfo.can_write) {
      statusText = "Push Access (No App)";
      statusColor = theme.warningText;
      showInstallCard = true;
    } else {
      statusText = "Read Only";
      statusColor = theme.warningText;
      showInstallCard = true;
    }
  }

  if (!repo) {
    return (
      <div style={styles.container}>
        <div style={styles.content}>Select a Repo</div>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      <style>{animationStyles}</style>

      {/* TOAST */}
      <div style={styles.toast}>
        <div style={styles.toastTitle}>{toast.title}</div>
        <div style={styles.toastMsg}>{toast.msg}</div>
      </div>

      {/* HEADER */}
      <div style={styles.header} className={animateHeader ? "pulse-context" : ""}>
        <div style={styles.titleGroup}>
          <span style={styles.title}>Project context</span>
          {isAiSession && (
            <span style={styles.aiBadge}>
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
              </svg>
              AI Session
            </span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
          {!isAiSession && <span style={styles.repoBadge}>{repo.name}</span>}
          {onSettingsClick && (
            <button
              type="button"
              onClick={onSettingsClick}
              title="Project settings"
              style={styles.settingsBtn}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* CONTENT */}
      <div style={styles.content}>
        {/* Branch selector (Claude-Code-on-Web parity — uses BranchPicker with search) */}
        <div style={styles.statRow}>
          <span style={styles.label}>Branch:</span>
          <BranchPicker
            repo={repo}
            currentBranch={branch}
            defaultBranch={effectiveDefaultBranch}
            sessionBranches={sessionBranches}
            onBranchChange={handleManualSwitch}
          />
        </div>

        {/* Stats */}
        <div style={styles.statRow}>
          <span style={styles.label}>Files:</span>
          <span style={styles.value}>{analyzing ? "…" : fileCount}</span>
        </div>

        <div style={styles.statRow}>
          <span style={styles.label}>Status:</span>
          <span style={{ ...styles.value, color: statusColor }}>{statusText}</span>
        </div>

        {/* Tree error (optional display) */}
        {treeError && (
          <div style={{ fontSize: 11, color: theme.warningText }}>
            {treeError}
          </div>
        )}

        {/* Refresh */}
        <button type="button" style={styles.refreshButton} onClick={handleRefresh} disabled={analyzing}>
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            style={{
              transform: analyzing ? "rotate(360deg)" : "rotate(0deg)",
              transition: "transform 0.6s ease",
            }}
          >
            <path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2" />
          </svg>
          {analyzing ? "Refreshing..." : "Refresh"}
        </button>

        {/* Install card */}
        {showInstallCard && (
          <div style={styles.installCard}>
            <div style={styles.installHeader}>
              <span>⚡</span>
              <span>Enable Write Access</span>
            </div>
            <p style={{ ...styles.installText, margin: "8px 0" }}>
              Install the GitPilot App to enable AI agent operations.
            </p>
            {runtime !== "cloud" && (
              <p style={{ ...styles.installText, margin: "0 0 8px 0", fontSize: "11px", opacity: 0.7 }}>
                Alternatively, use Folder or Local Git mode for local-first workflows without GitHub.
              </p>
            )}
            <button
              type="button"
              style={{
                ...styles.refreshButton,
                width: "100%",
                backgroundColor: theme.accent,
                color: "#fff",
                border: "none",
              }}
              onClick={handleInstallClick}
            >
              Install App
            </button>
          </div>
        )}
      </div>

      {/* Context banner */}
      {isAiSession && (
        <div style={styles.contextBanner}>
          <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="16" x2="12" y2="12"></line>
              <line x1="12" y1="8" x2="12.01" y2="8"></line>
            </svg>
            You are viewing an AI Session branch.
          </span>
          <span style={{ textDecoration: "underline", cursor: "pointer" }} onClick={() => handleManualSwitch(effectiveDefaultBranch)}>
            Return to {effectiveDefaultBranch}
          </span>
        </div>
      )}

      {/* File tree (branch-aware) */}
      <div style={styles.treeWrapper}>
        <FileTree repo={repo} refreshTrigger={refreshTrigger} branch={branch} />
      </div>

      {/* Sandbox status — always visible.  Click "Change" / "Repair"
          to open Settings (matches the existing settings affordance
          at the top), or "Use Local" to one-click flip the backend
          when MatrixLab is unreachable. */}
      <div style={{ marginTop: 12 }}>
        <SandboxStatusWidget onOpenSettings={onSettingsClick} />
      </div>
    </div>
  );
}