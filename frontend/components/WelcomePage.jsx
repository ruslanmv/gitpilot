import React, { useState } from "react";

export default function WelcomePage({ onLoginSuccess }) {
  const [loading, setLoading] = useState(false);
  const [checkingStatus, setCheckingStatus] = useState(false);
  const [installStatus, setInstallStatus] = useState(null); // null, 'checking', 'installed', 'not_installed'

  const handleInstallApp = () => {
    // Redirect to GitHub App installation page
    const appSlug = "gitpilota"; // Update this to match your actual GitHub App slug
    const installUrl = `https://github.com/apps/${appSlug}/installations/new`;
    window.location.href = installUrl;
  };

  const handleCheckStatus = async () => {
    try {
      setCheckingStatus(true);
      setInstallStatus('checking');

      // Check if user is authenticated first
      const authRes = await fetch("/api/auth/status");
      const authData = await authRes.json();

      if (!authData.authenticated) {
        // User needs to authenticate first
        setInstallStatus('not_authenticated');
        setCheckingStatus(false);
        // Auto-redirect to OAuth after a moment
        setTimeout(() => {
          handleConnectGitHub();
        }, 1000);
        return;
      }

      // User is authenticated, they can proceed
      // Trigger app reload with authentication
      onLoginSuccess(authData);

    } catch (e) {
      console.error("Status check error:", e);
      setInstallStatus('error');
      setCheckingStatus(false);
    }
  };

  const handleConnectGitHub = async () => {
    try {
      setLoading(true);

      // Initiate OAuth flow
      const res = await fetch("/api/auth/login");
      if (!res.ok) {
        // If OAuth not configured, silently redirect to installation page
        console.log("OAuth not configured, redirecting to app installation");
        handleInstallApp();
        return;
      }

      const data = await res.json();

      // Redirect to GitHub OAuth
      window.location.href = data.url;

    } catch (e) {
      console.error("Login error:", e);
      // Fallback to installation page
      handleInstallApp();
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
          <div className="connect-card">
            <h3>Get Started with GitPilota</h3>
            <p>
              Install the GitPilota GitHub App and authenticate your account
            </p>

            {installStatus === 'checking' && (
              <div className="status-message checking">
                <span className="spinner"></span>
                <span>Checking installation status...</span>
              </div>
            )}

            {installStatus === 'not_authenticated' && (
              <div className="status-message info">
                <span className="info-icon">ℹ️</span>
                <span>Redirecting to authentication...</span>
              </div>
            )}

            {installStatus === 'error' && (
              <div className="status-message error">
                <span className="error-icon">❌</span>
                <span>Could not verify installation. Please try again.</span>
              </div>
            )}

            <div className="setup-steps-simple">
              <div className="step-simple">
                <div className="step-simple-number">1</div>
                <div className="step-simple-content">
                  <h4>Install GitPilota App</h4>
                  <p>Click below to install GitPilota on your GitHub account and select repositories</p>
                  <button
                    type="button"
                    className="welcome-btn welcome-btn-secondary"
                    onClick={handleInstallApp}
                    disabled={loading || checkingStatus}
                  >
                    <svg className="btn-icon" viewBox="0 0 16 16" width="18" height="18" fill="currentColor">
                      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"></path>
                    </svg>
                    Install GitPilota
                  </button>
                </div>
              </div>

              <div className="step-divider">↓</div>

              <div className="step-simple">
                <div className="step-simple-number">2</div>
                <div className="step-simple-content">
                  <h4>Complete Authentication</h4>
                  <p>After installing the app, check status to authenticate</p>
                  <div className="button-group">
                    <button
                      type="button"
                      className="welcome-btn welcome-btn-outlined"
                      onClick={handleCheckStatus}
                      disabled={loading || checkingStatus}
                    >
                      {checkingStatus ? (
                        <>
                          <span className="spinner"></span>
                          Checking...
                        </>
                      ) : (
                        <>
                          <svg className="btn-icon" viewBox="0 0 16 16" width="18" height="18" fill="currentColor">
                            <path fillRule="evenodd" d="M1.5 8a6.5 6.5 0 1113 0 6.5 6.5 0 01-13 0zM8 0a8 8 0 100 16A8 8 0 008 0zm.75 4.75a.75.75 0 00-1.5 0v2.5h-2.5a.75.75 0 000 1.5h2.5v2.5a.75.75 0 001.5 0v-2.5h2.5a.75.75 0 000-1.5h-2.5v-2.5z"></path>
                          </svg>
                          Check Status
                        </>
                      )}
                    </button>
                    <button
                      type="button"
                      className="welcome-btn welcome-btn-primary"
                      onClick={handleConnectGitHub}
                      disabled={loading || checkingStatus}
                    >
                      {loading ? (
                        <>
                          <span className="spinner"></span>
                          Connecting...
                        </>
                      ) : (
                        <>
                          <svg className="btn-icon" viewBox="0 0 16 16" width="18" height="18" fill="currentColor">
                            <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"></path>
                          </svg>
                          Authenticate
                        </>
                      )}
                    </button>
                  </div>
                </div>
              </div>
            </div>

            <div className="connect-info">
              <div className="info-item">
                <span className="info-icon">✓</span>
                <span>Secure OAuth 2.0 authentication</span>
              </div>
              <div className="info-item">
                <span className="info-icon">✓</span>
                <span>Select specific repositories during installation</span>
              </div>
              <div className="info-item">
                <span className="info-icon">✓</span>
                <span>Revoke access anytime from GitHub settings</span>
              </div>
            </div>

            <p className="connect-note">
              <strong>How it works:</strong> First, install the GitPilota app and select your repositories.
              Then click "Check Status" to verify installation and complete authentication automatically.
            </p>
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
