import React, { useState, useEffect } from "react";

export default function WelcomePage({ onLoginSuccess }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [isOAuthConfigured, setIsOAuthConfigured] = useState(false);
  const [checkingConfig, setCheckingConfig] = useState(true);

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
    } finally {
      setCheckingConfig(false);
    }
  };

  const handleConnectGitHub = async () => {
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
      console.error("Login error:", e);
      setError(e.message || "Failed to connect to GitHub");
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
            AI-powered assistant for GitHub repositories
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
            <p>Enterprise-grade GitHub authentication and access control</p>
          </div>
        </div>

        {checkingConfig ? (
          <div className="welcome-actions">
            <div className="checking-status">
              <span className="spinner"></span>
              <p>Checking configuration...</p>
            </div>
          </div>
        ) : !isOAuthConfigured ? (
          <div className="welcome-actions">
            <div className="setup-required-card">
              <div className="setup-icon">⚙️</div>
              <h3>Setup Required</h3>
              <p>
                GitPilota requires a GitHub App to connect to your repositories.
                Please configure your GitHub App credentials to get started.
              </p>

              <div className="setup-steps">
                <div className="setup-step">
                  <div className="step-number">1</div>
                  <div className="step-content">
                    <h4>Create GitHub App</h4>
                    <p>Go to GitHub Settings → Developer settings → GitHub Apps</p>
                    <a
                      href="https://github.com/settings/apps/new"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="setup-link"
                    >
                      Create GitHub App →
                    </a>
                  </div>
                </div>

                <div className="setup-step">
                  <div className="step-number">2</div>
                  <div className="step-content">
                    <h4>Configure App Settings</h4>
                    <p>Set callback URL: <code>http://localhost:8000/auth/callback</code></p>
                    <p>Request permissions: <code>repo</code> (read & write)</p>
                  </div>
                </div>

                <div className="setup-step">
                  <div className="step-number">3</div>
                  <div className="step-content">
                    <h4>Set Environment Variables</h4>
                    <p>Add to your <code>.env</code> file:</p>
                    <pre className="env-example">
GITPILOTA_GH_APP_CLIENT_ID=your_client_id
GITPILOTA_GH_APP_CLIENT_SECRET=your_client_secret
                    </pre>
                  </div>
                </div>

                <div className="setup-step">
                  <div className="step-number">4</div>
                  <div className="step-content">
                    <h4>Restart GitPilota</h4>
                    <p>Restart the application to apply the changes</p>
                  </div>
                </div>
              </div>

              <div className="setup-help">
                <p>
                  <strong>Need help?</strong> Check our{" "}
                  <a
                    href="https://github.com/ruslanmv/gitpilota#setup"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    setup guide
                  </a>
                  {" "}for detailed instructions.
                </p>
              </div>
            </div>
          </div>
        ) : (
          <div className="welcome-actions">
            <div className="connect-card">
              <h3>Connect to GitHub</h3>
              <p>
                GitPilota will request access to your GitHub repositories.
                You'll be able to select which repositories to grant access to.
              </p>

              <button
                type="button"
                className="welcome-btn welcome-btn-primary"
                onClick={handleConnectGitHub}
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
                    Connect with GitHub
                  </>
                )}
              </button>

              {error && (
                <div className="welcome-error">
                  <span className="error-icon">❌</span>
                  {error}
                </div>
              )}

              <div className="connect-info">
                <div className="info-item">
                  <span className="info-icon">✓</span>
                  <span>Secure OAuth 2.0 authentication</span>
                </div>
                <div className="info-item">
                  <span className="info-icon">✓</span>
                  <span>Choose which repositories to access</span>
                </div>
                <div className="info-item">
                  <span className="info-icon">✓</span>
                  <span>Revoke access anytime from GitHub</span>
                </div>
              </div>
            </div>

            <p className="welcome-privacy">
              By connecting, you authorize GitPilota to access your selected repositories.
              We never store your GitHub password. Your credentials remain secure with GitHub.
            </p>
          </div>
        )}

        <div className="welcome-footer">
          <p>
            Enterprise solution for AI-powered repository management •{" "}
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
