import React, { useState, useEffect } from "react";

export default function WelcomePage({ onLoginSuccess }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [isOAuthConfigured, setIsOAuthConfigured] = useState(false);
  const [patToken, setPatToken] = useState("");
  const [showToken, setShowToken] = useState(false);

  useEffect(() => {
    checkOAuthConfig();
  }, []);

  const checkOAuthConfig = async () => {
    try {
      const res = await fetch("/api/settings");
      if (res.ok) {
        const data = await res.json();
        const hasOAuth = data.github?.app_client_id && data.github?.app_client_secret;
        setIsOAuthConfigured(hasOAuth);
      }
    } catch (e) {
      console.error("Failed to check OAuth config:", e);
    }
  };

  const handlePATLogin = async (e) => {
    e.preventDefault();

    if (!patToken.trim()) {
      setError("Please enter your GitHub Personal Access Token");
      return;
    }

    try {
      setLoading(true);
      setError(null);

      const res = await fetch("/api/auth/login-pat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ token: patToken }),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Invalid token");
      }

      const data = await res.json();

      // Check auth status to trigger app reload
      const authRes = await fetch("/api/auth/status");
      if (authRes.ok) {
        const authData = await authRes.json();
        if (authData.authenticated) {
          onLoginSuccess(authData);
        }
      }
    } catch (e) {
      console.error("Login error:", e);
      setError(e.message || "Failed to login. Please check your token.");
      setLoading(false);
    }
  };

  const handleOAuthLogin = async () => {
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
        "GitPilota Login",
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
      console.error("OAuth login error:", e);
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
          <h1 className="welcome-title">Welcome to GitPilota</h1>
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
            <p>Simple and secure authentication</p>
          </div>
        </div>

        <div className="welcome-actions">
          <form onSubmit={handlePATLogin} className="pat-login-form">
            <div className="form-group">
              <label htmlFor="pat-token" className="form-label">
                GitHub Personal Access Token
              </label>
              <div className="input-with-toggle">
                <input
                  id="pat-token"
                  type={showToken ? "text" : "password"}
                  className="form-input"
                  placeholder="ghp_xxxxxxxxxxxxxxxxxxxx"
                  value={patToken}
                  onChange={(e) => setPatToken(e.target.value)}
                  disabled={loading}
                  autoComplete="off"
                />
                <button
                  type="button"
                  className="input-toggle-btn"
                  onClick={() => setShowToken(!showToken)}
                  disabled={loading}
                  title={showToken ? "Hide token" : "Show token"}
                >
                  {showToken ? "👁️" : "👁️‍🗨️"}
                </button>
              </div>
              <p className="form-hint">
                Get your token at{" "}
                <a
                  href="https://github.com/settings/tokens/new?description=GitPilota&scopes=repo"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  github.com/settings/tokens
                </a>
                <br />
                Required scope: <code>repo</code>
              </p>
            </div>

            <button
              type="submit"
              className="welcome-btn welcome-btn-primary"
              disabled={loading || !patToken.trim()}
            >
              {loading ? (
                <>
                  <span className="spinner"></span>
                  Signing in...
                </>
              ) : (
                <>
                  <span className="btn-icon">🔑</span>
                  Sign in with Token
                </>
              )}
            </button>
          </form>

          {isOAuthConfigured && (
            <div className="login-divider">
              <span>or</span>
            </div>
          )}

          {isOAuthConfigured && (
            <button
              type="button"
              className="welcome-btn welcome-btn-secondary"
              onClick={handleOAuthLogin}
              disabled={loading}
            >
              <span className="btn-icon">🔗</span>
              Sign in with GitHub OAuth
            </button>
          )}

          {error && (
            <div className="welcome-error">
              <span className="error-icon">❌</span>
              {error}
            </div>
          )}

          <p className="welcome-privacy">
            By signing in, you authorize GitPilota to access your repositories.
            We never store your credentials.
          </p>
        </div>

        <div className="welcome-footer">
          <p>
            New to GitPilota?{" "}
            <a
              href="https://github.com/ruslanmv/gitpilota"
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
