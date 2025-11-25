import React, { useState, useEffect } from "react";

export default function WelcomePage({ onLoginSuccess }) {
  const [loading, setLoading] = useState(false);

  // Check if user is already authenticated on mount
  useEffect(() => {
    checkAuthStatus();
  }, []);

  const checkAuthStatus = async () => {
    try {
      const res = await fetch("/api/auth/status");
      if (res.ok) {
        const data = await res.json();
        if (data.authenticated) {
          // User is already authenticated, proceed to app
          onLoginSuccess(data);
        }
      }
    } catch (e) {
      // Not authenticated, show welcome page
      console.log("Not authenticated");
    }
  };

  const handleConnect = async () => {
    try {
      setLoading(true);

      // Initiate OAuth flow
      const res = await fetch("/api/auth/login");
      if (!res.ok) {
        throw new Error("Failed to start authentication");
      }

      const data = await res.json();

      // Redirect to GitHub OAuth
      // This will prompt for app installation if needed
      window.location.href = data.url;

    } catch (e) {
      console.error("Authentication error:", e);
      setLoading(false);
      // Show user-friendly error
      alert("Unable to connect to GitHub. Please check your configuration and try again.");
    }
  };

  return (
    <div className="welcome-page">
      <div className="welcome-container">
        <div className="welcome-header">
          <div className="welcome-logo">
            <div className="logo-square large">GP</div>
          </div>
          <h1 className="welcome-title">Welcome to GitPilota</h1>
          <p className="welcome-subtitle">
            AI-powered assistant for your GitHub repositories
          </p>
        </div>

        <div className="welcome-features">
          <div className="feature-item">
            <div className="feature-icon">🤖</div>
            <h3>AI-Powered Analysis</h3>
            <p>Intelligent code understanding and automated suggestions</p>
          </div>
          <div className="feature-item">
            <div className="feature-icon">🔄</div>
            <h3>Multi-Agent Workflows</h3>
            <p>Advanced AI agents working together on complex tasks</p>
          </div>
          <div className="feature-item">
            <div className="feature-icon">🔒</div>
            <h3>Secure Integration</h3>
            <p>Enterprise-grade GitHub authentication</p>
          </div>
        </div>

        <div className="welcome-actions">
          <div className="connect-card-simple">
            <h3>Connect to GitHub</h3>
            <p>
              GitPilota uses GitHub OAuth to securely access your repositories.
              Click below to get started.
            </p>

            <button
              type="button"
              className="welcome-btn welcome-btn-primary welcome-btn-large"
              onClick={handleConnect}
              disabled={loading}
            >
              {loading ? (
                <>
                  <span className="spinner"></span>
                  Connecting to GitHub...
                </>
              ) : (
                <>
                  <svg className="btn-icon" viewBox="0 0 16 16" width="20" height="20" fill="currentColor">
                    <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"></path>
                  </svg>
                  Connect with GitHub
                </>
              )}
            </button>

            <div className="connect-info-simple">
              <p className="info-text">
                <strong>What happens next:</strong>
              </p>
              <ol className="info-list">
                <li>You'll be redirected to GitHub</li>
                <li>Install the GitPilota app (if you haven't already)</li>
                <li>Select which repositories to grant access to</li>
                <li>Authorize GitPilota to access your account</li>
                <li>Return here and start using GitPilota</li>
              </ol>
            </div>

            <div className="security-badge">
              <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor">
                <path fillRule="evenodd" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"></path>
              </svg>
              <span>Secured by GitHub OAuth 2.0</span>
            </div>
          </div>
        </div>

        <div className="welcome-footer">
          <p>
            Enterprise AI solution for repository management •{" "}
            <a
              href="https://github.com/ruslanmv/gitpilota"
              target="_blank"
              rel="noopener noreferrer"
            >
              Documentation
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}
