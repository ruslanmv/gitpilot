// frontend/components/AdminTabs/IntegrationsTab.jsx
import React, { useEffect, useState } from "react";
import { apiUrl, safeFetchJSON } from "../../utils/api.js";

/**
 * Integrations tab — shows connection status for GitHub (and future
 * third-party integrations) with Connect/Disconnect actions.
 *
 * Best practices applied:
 * - Fetch current status on mount via /api/auth/status
 * - Show connected user info if already authenticated
 * - "Connect GitHub" button opens /api/auth/url in the same window
 *   (OAuth flow will redirect back with ?code=...)
 * - Disconnect clears localStorage token and re-fetches status
 * - Handles both Web OAuth and Device Flow modes
 */

export default function IntegrationsTab({ userInfo, onDisconnect, showToast }) {
  const [authStatus, setAuthStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await safeFetchJSON(apiUrl("/api/auth/status"), { timeout: 5000 });
        if (!cancelled) setAuthStatus(data);
      } catch (err) {
        if (!cancelled) setError(err?.message || "Failed to check auth status");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleConnect = async () => {
    setConnecting(true);
    setError(null);
    try {
      if (authStatus?.mode === "web") {
        // Web OAuth flow — redirect to GitHub authorization URL
        const { authorization_url, state } = await safeFetchJSON(
          apiUrl("/api/auth/url"),
          { timeout: 5000 }
        );
        if (state) {
          sessionStorage.setItem("gitpilot_oauth_state", state);
        }
        // Full page redirect (OAuth providers don't support iframes)
        window.location.href = authorization_url;
      } else {
        // Device flow — the LoginPage already handles this.
        showToast?.(
          "Device flow",
          "GitHub device flow is configured. Sign out and sign in again to reconnect."
        );
      }
    } catch (err) {
      setError(err?.message || "Failed to start OAuth flow");
      setConnecting(false);
    }
  };

  const handleDisconnect = () => {
    if (!window.confirm("Disconnect GitHub? You will be signed out.")) return;
    localStorage.removeItem("github_token");
    localStorage.removeItem("github_user");
    onDisconnect?.();
    showToast?.("Disconnected", "GitHub token removed.");
  };

  const isConnected = !!(userInfo && userInfo.login);

  return (
    <div>
      <h3 style={{ marginBottom: "16px" }}>Integrations</h3>
      <p style={{ fontSize: "12px", opacity: 0.7, marginBottom: "16px" }}>
        Connect third-party services to unlock additional GitPilot features.
      </p>

      {/* GitHub integration card */}
      <div
        style={{
          background: "#1a1b26",
          borderRadius: "8px",
          padding: "20px",
          border: "1px solid #2a2b36",
          marginBottom: "16px",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            marginBottom: "12px",
          }}
        >
          <div>
            <h4 style={{ marginBottom: "4px" }}>GitHub</h4>
            <p style={{ fontSize: "12px", opacity: 0.7 }}>
              Pull requests, issues, and remote repository workflows.
            </p>
          </div>
          <span
            style={{
              padding: "2px 10px",
              borderRadius: "10px",
              fontSize: "11px",
              fontWeight: 600,
              background: isConnected ? "#064e3b" : "#374151",
              color: isConnected ? "#a7f3d0" : "#9ca3af",
              border: `1px solid ${isConnected ? "#065f46" : "#4b5563"}`,
            }}
          >
            {loading ? "CHECKING..." : isConnected ? "CONNECTED" : "NOT CONNECTED"}
          </span>
        </div>

        {isConnected && userInfo && (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "12px",
              padding: "12px",
              background: "#0d0e15",
              borderRadius: "6px",
              marginBottom: "12px",
            }}
          >
            {userInfo.avatar_url && (
              <img
                src={userInfo.avatar_url}
                alt={userInfo.login}
                style={{ width: "36px", height: "36px", borderRadius: "50%" }}
              />
            )}
            <div>
              <div style={{ fontSize: "13px", fontWeight: 600 }}>
                {userInfo.name || userInfo.login}
              </div>
              <div style={{ fontSize: "11px", opacity: 0.6 }}>@{userInfo.login}</div>
            </div>
          </div>
        )}

        {error && (
          <div
            role="alert"
            style={{
              padding: "8px 12px",
              background: "#7f1d1d",
              color: "#fecaca",
              border: "1px solid #991b1b",
              borderRadius: "4px",
              fontSize: "11px",
              marginBottom: "12px",
            }}
          >
            {error}
          </div>
        )}

        <div style={{ display: "flex", gap: "8px" }}>
          {isConnected ? (
            <button
              type="button"
              onClick={handleDisconnect}
              style={{
                padding: "8px 16px",
                background: "transparent",
                color: "#f87171",
                border: "1px solid #991b1b",
                borderRadius: "4px",
                cursor: "pointer",
                fontSize: "12px",
                fontWeight: 600,
              }}
            >
              Disconnect
            </button>
          ) : (
            <button
              type="button"
              onClick={handleConnect}
              disabled={connecting || loading}
              style={{
                padding: "8px 16px",
                background: connecting ? "#555" : "#3B82F6",
                color: "#fff",
                border: "none",
                borderRadius: "4px",
                cursor: connecting || loading ? "not-allowed" : "pointer",
                fontSize: "12px",
                fontWeight: 600,
                display: "inline-flex",
                alignItems: "center",
                gap: "6px",
              }}
            >
              {connecting ? "Connecting..." : "Connect GitHub"}
            </button>
          )}
        </div>

        {authStatus && !isConnected && (
          <div style={{ fontSize: "10px", opacity: 0.5, marginTop: "10px" }}>
            Auth mode: {authStatus.mode || "unknown"}
            {authStatus.oauth_configured && " (Web OAuth)"}
            {authStatus.pat_configured && " (Personal Access Token)"}
          </div>
        )}
      </div>

      {/* Placeholder for future integrations */}
      <div
        style={{
          background: "#1a1b26",
          borderRadius: "8px",
          padding: "20px",
          border: "1px dashed #2a2b36",
          opacity: 0.5,
          textAlign: "center",
        }}
      >
        <p style={{ fontSize: "12px", margin: 0 }}>
          More integrations coming soon (GitLab, Bitbucket, Jira, Slack)
        </p>
      </div>
    </div>
  );
}
