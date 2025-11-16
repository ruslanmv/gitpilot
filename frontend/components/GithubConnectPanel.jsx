import React, { useEffect, useState } from "react";

export default function GithubConnectPanel() {
  const [status, setStatus] = useState({ connected: false, mode: "none" });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    loadStatus();
  }, []);

  const loadStatus = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await fetch("/api/github/status");
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const data = await res.json();
      setStatus(data);
    } catch (e) {
      console.error("Failed to load GitHub status:", e);
      setError("Failed to load connection status");
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = async () => {
    try {
      setLoading(true);
      const res = await fetch("/api/github/logout", { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      // Reload status
      await loadStatus();
      setError("Logged out successfully. You can reconnect anytime.");
    } catch (e) {
      console.error("Failed to logout:", e);
      setError("Failed to logout");
    } finally {
      setLoading(false);
    }
  };

  const handleConnectClick = async () => {
    try {
      const res = await fetch("/api/github/app-install-url");
      if (!res.ok) {
        // If app slug not configured, suggest going to admin panel
        setError(
          "GitHub App not configured. Please configure GitHub credentials in the Admin panel."
        );
        return;
      }
      const data = await res.json();
      // Open GitHub App installation in new window
      window.open(data.url, "_blank", "noopener,noreferrer");

      // Show a helper message
      setError(
        "After installing the app on GitHub, refresh this page to see the connection status."
      );
    } catch (e) {
      console.error("Failed to get install URL:", e);
      setError("Please configure GitHub credentials in the Admin panel.");
    }
  };

  const getStatusText = () => {
    if (status.mode === "pat") {
      return "Connected via Personal Access Token";
    } else if (status.mode === "app") {
      return "Connected via GitHub App";
    }
    return "Not connected";
  };

  return (
    <div className="github-connect-card">
      <div className="github-connect-header">
        <span className="github-connect-title">GitHub Connection</span>
        {loading ? (
          <span className="github-connect-status">Checking…</span>
        ) : (
          <span
            className={
              "github-connect-badge " +
              (status.connected ? "connected" : "disconnected")
            }
          >
            {status.connected ? "Connected" : "Not Connected"}
          </span>
        )}
      </div>

      <p className="github-connect-text">
        {status.connected
          ? getStatusText()
          : "Authorize GitPilot to access selected repositories through a GitHub App."}
      </p>

      {!status.connected && !loading && (
        <button
          type="button"
          className="primary-button full-width"
          onClick={handleConnectClick}
        >
          Connect with GitHub
        </button>
      )}

      {status.connected && (
        <div className="github-connect-info">
          <div className="github-connect-subtext">{getStatusText()}</div>
          {status.mode === "app" && status.app_installation_id && (
            <div className="github-connect-subtext">
              Installation ID: {status.app_installation_id}
            </div>
          )}
          <button
            type="button"
            className="github-logout-btn"
            onClick={handleLogout}
            disabled={loading}
          >
            Logout
          </button>
        </div>
      )}

      {error && (
        <div className="github-connect-message">
          {error}
        </div>
      )}
    </div>
  );
}
