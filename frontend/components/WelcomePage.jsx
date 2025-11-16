import React, { useState, useEffect } from "react";

export default function WelcomePage({ onLoginSuccess }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [isConfigured, setIsConfigured] = useState(false);

  useEffect(() => {
    checkOAuthConfig();
  }, []);

  const checkOAuthConfig = async () => {
    try {
      const res = await fetch("/api/settings");
      if (res.ok) {
        const data = await res.json();
        const hasOAuth = data.github?.app_client_id && data.github?.app_client_secret;
        setIsConfigured(hasOAuth);
      }
    } catch (e) {
      console.error("Failed to check OAuth config:", e);
    }
  };

  const handleLogin = async () => {
    try {
      setLoading(true);
      setError(null);

      const res = await fetch("/api/auth/login");
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || "Failed to initiate login");
      }

      const data = await res.json();

      // Open OAuth flow in a new window
      const width = 600;
      const height = 700;
      const left = window.screen.width / 2 - width / 2;
      const top = window.screen.height / 2 - height / 2;

      const authWindow = window.open(
        data.url,
        "GitPilot Login",
        `width=${width},height=${height},left=${left},top=${top}`
      );

      // Poll for auth completion
      const pollInterval = setInterval(() => {
        try {
          // Check if window is closed
          if (authWindow.closed) {
            clearInterval(pollInterval);
            setLoading(false);
            // Check auth status
            checkAuthStatus();
          }
        } catch (e) {
          // Ignore errors from cross-origin access
        }
      }, 500);

    } catch (e) {
      console.error("Login error:", e);
      setError(e.message || "Failed to login");
      setLoading(false);
    }
  };

  const checkAuthStatus = async () => {
    try {
      const res = await fetch("/api/auth/status");
      if (res.ok) {
        const data = await res.json();
        if (data.authenticated) {
          onLoginSuccess(data);
        }
      }
    } catch (e) {
      console.error("Failed to check auth status:", e);
    }
  };

  return (
    <div className="welcome-page">
      <div className="welcome-container">
        <div className="welcome-header">
          <div className="welcome-logo">
            <div className="logo-square large">GP</div>
          </div>
          <h1 className="welcome-title">Welcome to GitPilot</h1>
          <p className="welcome-subtitle">
            Your AI-powered assistant for GitHub repositories
          </p>
        </div>

        <div className="welcome-features">
          <div className="feature-item">
            <div className="feature-icon">🤖</div>
            <h3>AI-Powered Code Assistant</h3>
            <p>Get intelligent suggestions and automated code changes</p>
          </div>
          <div className="feature-item">
            <div className="feature-icon">🔄</div>
            <h3>Multi-Agent Workflows</h3>
            <p>Leverage advanced AI agents for complex tasks</p>
          </div>
          <div className="feature-item">
            <div className="feature-icon">🔒</div>
            <h3>Secure GitHub Integration</h3>
            <p>OAuth-based authentication with granular repository access</p>
          </div>
        </div>

        <div className="welcome-actions">
          {!isConfigured ? (
            <div className="welcome-setup-notice">
              <div className="notice-icon">⚠️</div>
              <p>
                GitHub OAuth is not configured. Please set up your GitHub App
                credentials in the environment or settings.
              </p>
              <p className="notice-hint">
                Set <code>GITPILOT_GH_APP_CLIENT_ID</code> and{" "}
                <code>GITPILOT_GH_APP_CLIENT_SECRET</code>
              </p>
            </div>
          ) : (
            <>
              <button
                type="button"
                className="welcome-btn welcome-btn-primary"
                onClick={handleLogin}
                disabled={loading}
              >
                {loading ? (
                  <>
                    <span className="spinner"></span>
                    Connecting...
                  </>
                ) : (
                  <>
                    <span className="btn-icon">🔗</span>
                    Sign in with GitHub
                  </>
                )}
              </button>

              {error && (
                <div className="welcome-error">
                  <span className="error-icon">❌</span>
                  {error}
                </div>
              )}

              <p className="welcome-privacy">
                By signing in, you authorize GitPilot to access your selected
                repositories. We never store your GitHub credentials.
              </p>
            </>
          )}
        </div>

        <div className="welcome-footer">
          <p>
            New to GitPilot?{" "}
            <a
              href="https://github.com/ruslanmv/gitpilot"
              target="_blank"
              rel="noopener noreferrer"
            >
              Read the documentation
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}
