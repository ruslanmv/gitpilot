import React, { useState, useEffect } from "react";

export default function WelcomePage({ onAuthComplete }) {
  const [authStatus, setAuthStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showInstructions, setShowInstructions] = useState(false);
  const [checkingStatus, setCheckingStatus] = useState(false);

  useEffect(() => {
    checkAuthStatus();

    // Poll for authentication status every 3 seconds (like Claude Code)
    // This automatically detects when user completes authentication
    const pollInterval = setInterval(() => {
      checkAuthStatus();
    }, 3000);

    return () => clearInterval(pollInterval);
  }, []);

  const checkAuthStatus = async () => {
    try {
      const response = await fetch("/api/auth/status");
      const data = await response.json();
      setAuthStatus(data);
      setLoading(false);

      // If authenticated, notify parent to proceed to main app
      if (data.authenticated) {
        onAuthComplete?.();
      }
    } catch (err) {
      setError("Failed to check authentication status");
      setLoading(false);
    }
  };

  const handleCheckStatus = async () => {
    setCheckingStatus(true);
    setError(null);
    try {
      await checkAuthStatus();
      if (!authStatus?.authenticated) {
        setError("GitPilota is not installed yet. Please complete the installation in GitHub.");
      }
    } catch (err) {
      setError("Failed to check status. Please try again.");
    } finally {
      setCheckingStatus(false);
    }
  };

  const handleLogin = () => {
    setShowInstructions(true);
  };

  const handleUseEnvCredentials = async () => {
    try {
      await fetch("/api/auth/use-env", { method: "POST" });
      // Refresh auth status to proceed to app
      checkAuthStatus();
    } catch (err) {
      setError("Failed to use .env credentials");
    }
  };

  const handleUseCustomCredentials = async () => {
    try {
      await fetch("/api/auth/use-custom", { method: "POST" });
      // Show login instructions
      setShowInstructions(true);
    } catch (err) {
      setError("Failed to switch to custom credentials");
    }
  };

  const handleSetupGitHubApp = async () => {
    try {
      const response = await fetch("/api/auth/github-app-install-url");
      const data = await response.json();
      window.open(data.install_url, "_blank");
    } catch (err) {
      setError("Failed to get GitHub App installation URL");
    }
  };

  if (loading) {
    return (
      <div className="welcome-loading">
        <div className="spinner"></div>
        <p>Loading...</p>
        <style jsx>{`
          .welcome-loading {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            background: #0d1117;
            color: #c9d1d9;
          }
          .spinner {
            width: 40px;
            height: 40px;
            border: 3px solid rgba(136, 147, 255, 0.2);
            border-top-color: #8893ff;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
          }
          @keyframes spin {
            to { transform: rotate(360deg); }
          }
          p {
            margin-top: 1rem;
            font-size: 14px;
            color: #8b949e;
          }
        `}</style>
      </div>
    );
  }

  if (authStatus?.authenticated) {
    return null; // Don't show welcome page if authenticated
  }

  return (
    <div className="welcome-page">
      {/* Background gradient */}
      <div className="welcome-bg"></div>

      {/* Main content */}
      <div className="welcome-container">
        <div className="welcome-card">
          {/* Header */}
          <div className="welcome-header">
            <div className="logo-container">
              <svg className="logo-icon" viewBox="0 0 24 24" fill="none">
                <path d="M12 2L2 7L12 12L22 7L12 2Z" fill="url(#gradient1)" />
                <path d="M2 17L12 22L22 17V12L12 17L2 12V17Z" fill="url(#gradient2)" />
                <defs>
                  <linearGradient id="gradient1" x1="2" y1="2" x2="22" y2="12">
                    <stop offset="0%" stopColor="#8893ff" />
                    <stop offset="100%" stopColor="#6366f1" />
                  </linearGradient>
                  <linearGradient id="gradient2" x1="2" y1="12" x2="22" y2="22">
                    <stop offset="0%" stopColor="#6366f1" />
                    <stop offset="100%" stopColor="#4f46e5" />
                  </linearGradient>
                </defs>
              </svg>
              <div className="logo-text">
                <h1>GitPilot</h1>
                <p>Enterprise AI Development Platform</p>
              </div>
            </div>
          </div>

          {/* Content */}
          {!showInstructions ? (
            <>
              <div className="welcome-content">
                {authStatus?.has_env_credentials ? (
                  <>
                    <h2 className="section-title">Welcome Back!</h2>
                    <p className="section-description">
                      Credentials detected in .env file. Choose how you'd like to proceed:
                    </p>

                    <div className="auth-methods">
                      <div className="auth-method primary">
                        <div className="method-icon">
                          <svg viewBox="0 0 24 24" fill="currentColor">
                            <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/>
                          </svg>
                        </div>
                        <div className="method-content">
                          <h3>Continue with .env credentials</h3>
                          {authStatus?.env_username && (
                            <p>Logged in as: <strong>@{authStatus.env_username}</strong></p>
                          )}
                          {!authStatus?.env_username && (
                            <p>Use the credentials configured in your .env file</p>
                          )}
                          <button className="btn-primary" onClick={handleUseEnvCredentials}>
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                              <path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                            Continue
                          </button>
                        </div>
                      </div>

                      <div className="divider">
                        <span>or</span>
                      </div>

                      <div className="auth-method secondary">
                        <div className="method-icon">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                          </svg>
                        </div>
                        <div className="method-content">
                          <h3>Login with different account</h3>
                          <p>Configure custom GitHub App credentials for a different user</p>
                          <button className="btn-secondary-action" onClick={handleUseCustomCredentials}>
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                              <path d="M18 9v3m0 0v3m0-3h3m-3 0h-3m-2-5a4 4 0 11-8 0 4 4 0 018 0zM3 20a6 6 0 0112 0v1H3v-1z" />
                            </svg>
                            Login as Different User
                          </button>
                        </div>
                      </div>
                    </div>
                  </>
                ) : (
                  <>
                    <h2 className="section-title">Get Started with GitPilota</h2>
                    <p className="section-description">
                      Connect GitPilota to your GitHub account in two simple steps
                    </p>

                    <div className="step-list" style={{marginBottom: '2rem'}}>
                      <div className="step">
                        <div className="step-number">1</div>
                        <div className="step-content">
                          <h3>Install GitPilota App</h3>
                          <p>Grant GitPilota access to your repositories</p>
                          <button className="btn-primary" onClick={handleSetupGitHubApp} style={{marginTop: '0.75rem'}}>
                            <svg viewBox="0 0 16 16" fill="currentColor" width="16" height="16">
                              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>
                            </svg>
                            Install GitPilota ↗
                          </button>
                        </div>
                      </div>

                      <div className="step">
                        <div className="step-number">2</div>
                        <div className="step-content">
                          <h3>Authenticate Your Account</h3>
                          <p>After installation, verify to start using GitPilota</p>
                          <button
                            className="btn-secondary-check"
                            onClick={handleCheckStatus}
                            disabled={checkingStatus}
                            style={{marginTop: '0.75rem'}}
                          >
                            {checkingStatus ? (
                              <>
                                <div className="spinner-small"></div>
                                Checking...
                              </>
                            ) : (
                              <>
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                                  <path d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                                Check Status
                              </>
                            )}
                          </button>
                        </div>
                      </div>
                    </div>

                    <div className="help-text">
                      <p><strong>What happens:</strong></p>
                      <ol>
                        <li>Click "Install GitPilota" - opens GitHub in new window</li>
                        <li>Select which repositories to grant access</li>
                        <li>Click "Install & Authorize" on GitHub</li>
                        <li>Return here and click "Check Status"</li>
                      </ol>
                    </div>
                  </>
                )}

                {error && (
                  <div className="error-banner">
                    <svg viewBox="0 0 24 24" fill="currentColor" width="20" height="20">
                      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/>
                    </svg>
                    <span>{error}</span>
                  </div>
                )}
              </div>

              <div className="welcome-footer">
                <div className="footer-links">
                  <a href="https://github.com/ruslanmv/gitpilot#readme" target="_blank" rel="noopener noreferrer">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                      <path d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/>
                    </svg>
                    Documentation
                  </a>
                  <a href="https://github.com/ruslanmv/gitpilot/issues" target="_blank" rel="noopener noreferrer">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                      <circle cx="12" cy="12" r="10"/>
                      <path d="M12 16v-4m0-4h.01"/>
                    </svg>
                    Support
                  </a>
                </div>
                <p className="footer-text">Enterprise-grade security with GitHub App authentication</p>
              </div>
            </>
          )}
        </div>
      </div>

      <style jsx>{`
        .welcome-page {
          position: relative;
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          background: #0d1117;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif;
          color: #c9d1d9;
          padding: 2rem;
          overflow: hidden;
        }

        .welcome-bg {
          position: absolute;
          top: 0;
          left: 0;
          right: 0;
          bottom: 0;
          background:
            radial-gradient(circle at 20% 50%, rgba(136, 147, 255, 0.1) 0%, transparent 50%),
            radial-gradient(circle at 80% 80%, rgba(99, 102, 241, 0.08) 0%, transparent 50%);
          pointer-events: none;
        }

        .welcome-container {
          position: relative;
          z-index: 1;
          width: 100%;
          max-width: 520px;
        }

        .welcome-card {
          background: #161b22;
          border: 1px solid #30363d;
          border-radius: 12px;
          box-shadow: 0 16px 32px rgba(1, 4, 9, 0.85);
          overflow: hidden;
        }

        .welcome-header {
          padding: 2.5rem 2rem 2rem;
          border-bottom: 1px solid #21262d;
        }

        .logo-container {
          display: flex;
          align-items: center;
          gap: 1rem;
        }

        .logo-icon {
          width: 48px;
          height: 48px;
          flex-shrink: 0;
        }

        .logo-text h1 {
          margin: 0;
          font-size: 24px;
          font-weight: 600;
          color: #f0f6fc;
          letter-spacing: -0.02em;
        }

        .logo-text p {
          margin: 0.25rem 0 0;
          font-size: 13px;
          color: #8b949e;
          font-weight: 400;
        }

        .welcome-content {
          padding: 2rem;
        }

        .section-title {
          margin: 0 0 0.5rem;
          font-size: 18px;
          font-weight: 600;
          color: #f0f6fc;
          letter-spacing: -0.01em;
        }

        .section-description {
          margin: 0 0 2rem;
          font-size: 14px;
          color: #8b949e;
          line-height: 1.5;
        }

        .auth-methods {
          display: flex;
          flex-direction: column;
          gap: 1rem;
        }

        .auth-method {
          display: flex;
          gap: 1rem;
          padding: 1.5rem;
          background: #0d1117;
          border: 1px solid #30363d;
          border-radius: 8px;
          transition: all 0.2s;
        }

        .auth-method:hover {
          border-color: #8893ff;
          background: #161b22;
        }

        .method-icon {
          width: 40px;
          height: 40px;
          flex-shrink: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          background: linear-gradient(135deg, #8893ff 0%, #6366f1 100%);
          border-radius: 8px;
          color: white;
        }

        .auth-method.secondary .method-icon {
          background: #21262d;
          color: #8b949e;
        }

        .method-content {
          flex: 1;
          min-width: 0;
        }

        .method-content h3 {
          margin: 0 0 0.25rem;
          font-size: 15px;
          font-weight: 600;
          color: #f0f6fc;
        }

        .method-content > p {
          margin: 0 0 1rem;
          font-size: 13px;
          color: #8b949e;
          line-height: 1.4;
        }

        .btn-primary {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 0.5rem;
          width: 100%;
          padding: 0.75rem 1rem;
          background: linear-gradient(180deg, #8893ff 0%, #7681f0 100%);
          color: white;
          border: 0;
          border-radius: 6px;
          font-size: 14px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.2s;
          font-family: inherit;
        }

        .btn-primary:hover {
          background: linear-gradient(180deg, #9ba4ff 0%, #8893ff 100%);
          transform: translateY(-1px);
          box-shadow: 0 4px 12px rgba(136, 147, 255, 0.3);
        }

        .btn-primary:active {
          transform: translateY(0);
        }

        .divider {
          display: flex;
          align-items: center;
          justify-content: center;
          margin: 0.5rem 0;
          color: #484f58;
          font-size: 12px;
          font-weight: 500;
        }

        .divider span {
          padding: 0 0.75rem;
        }

        .pat-details {
          margin-top: 0.75rem;
        }

        .pat-details summary {
          font-size: 13px;
          color: #8893ff;
          cursor: pointer;
          user-select: none;
          list-style: none;
        }

        .pat-details summary::-webkit-details-marker {
          display: none;
        }

        .pat-details[open] summary {
          margin-bottom: 0.75rem;
        }

        .pat-details ol {
          margin: 0;
          padding-left: 1.5rem;
          font-size: 13px;
          color: #8b949e;
          line-height: 1.6;
        }

        .pat-details li {
          margin-bottom: 0.5rem;
        }

        .pat-details code {
          padding: 0.2rem 0.4rem;
          background: #0d1117;
          border: 1px solid #30363d;
          border-radius: 3px;
          font-size: 12px;
          font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, monospace;
        }

        .pat-details a {
          color: #8893ff;
          text-decoration: none;
        }

        .pat-details a:hover {
          text-decoration: underline;
        }

        .error-banner {
          display: flex;
          align-items: center;
          gap: 0.75rem;
          margin-top: 1.5rem;
          padding: 1rem;
          background: rgba(248, 81, 73, 0.1);
          border: 1px solid rgba(248, 81, 73, 0.3);
          border-radius: 6px;
          color: #f85149;
          font-size: 13px;
        }

        .error-banner svg {
          flex-shrink: 0;
        }

        .welcome-footer {
          padding: 1.5rem 2rem;
          background: #0d1117;
          border-top: 1px solid #21262d;
        }

        .footer-links {
          display: flex;
          gap: 1.5rem;
          margin-bottom: 0.75rem;
        }

        .footer-links a {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          font-size: 13px;
          color: #8b949e;
          text-decoration: none;
          transition: color 0.2s;
        }

        .footer-links a:hover {
          color: #8893ff;
        }

        .footer-text {
          margin: 0;
          font-size: 12px;
          color: #6e7681;
        }

        /* Instructions Panel */
        .instructions-panel {
          padding: 2rem;
        }

        .back-button {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          padding: 0.5rem 1rem;
          background: transparent;
          border: 1px solid #30363d;
          border-radius: 6px;
          color: #8b949e;
          font-size: 13px;
          cursor: pointer;
          transition: all 0.2s;
          margin-bottom: 1.5rem;
          font-family: inherit;
        }

        .back-button:hover {
          background: #21262d;
          border-color: #8893ff;
          color: #c9d1d9;
        }

        .instructions-panel h2 {
          margin: 0 0 0.5rem;
          font-size: 18px;
          font-weight: 600;
          color: #f0f6fc;
        }

        .instructions-description {
          margin: 0 0 2rem;
          font-size: 14px;
          color: #8b949e;
        }

        .step-list {
          display: flex;
          flex-direction: column;
          gap: 1.5rem;
          margin-bottom: 2rem;
        }

        .step {
          display: flex;
          gap: 1rem;
        }

        .step-number {
          width: 32px;
          height: 32px;
          flex-shrink: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          background: linear-gradient(135deg, #8893ff 0%, #6366f1 100%);
          border-radius: 50%;
          color: white;
          font-size: 14px;
          font-weight: 600;
        }

        .step-content {
          flex: 1;
        }

        .step-content h3 {
          margin: 0 0 0.25rem;
          font-size: 15px;
          font-weight: 600;
          color: #f0f6fc;
        }

        .step-content p {
          margin: 0;
          font-size: 13px;
          color: #8b949e;
        }

        .code-block {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-top: 0.75rem;
          padding: 0.75rem 1rem;
          background: #0d1117;
          border: 1px solid #30363d;
          border-radius: 6px;
        }

        .code-block code {
          font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, monospace;
          font-size: 13px;
          color: #8893ff;
        }

        .copy-btn {
          padding: 0.25rem 0.5rem;
          background: transparent;
          border: 1px solid #30363d;
          border-radius: 4px;
          color: #8b949e;
          cursor: pointer;
          transition: all 0.2s;
        }

        .copy-btn:hover {
          background: #21262d;
          border-color: #8893ff;
          color: #c9d1d9;
        }

        .btn-refresh {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 0.5rem;
          width: 100%;
          padding: 0.75rem 1rem;
          background: linear-gradient(180deg, #238636 0%, #2ea043 100%);
          color: white;
          border: 0;
          border-radius: 6px;
          font-size: 14px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.2s;
          font-family: inherit;
        }

        .btn-refresh:hover {
          background: linear-gradient(180deg, #2ea043 0%, #238636 100%);
          transform: translateY(-1px);
          box-shadow: 0 4px 12px rgba(46, 160, 67, 0.3);
        }

        .btn-secondary {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 0.5rem;
          width: 100%;
          padding: 0.625rem 1rem;
          background: transparent;
          color: #8893ff;
          border: 1px solid #30363d;
          border-radius: 6px;
          font-size: 13px;
          font-weight: 500;
          cursor: pointer;
          transition: all 0.2s;
          font-family: inherit;
        }

        .btn-secondary:hover {
          background: #21262d;
          border-color: #8893ff;
          transform: translateY(-1px);
        }

        .btn-secondary-action {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 0.5rem;
          width: 100%;
          padding: 0.75rem 1rem;
          background: transparent;
          color: #8b949e;
          border: 1px solid #30363d;
          border-radius: 6px;
          font-size: 14px;
          font-weight: 500;
          cursor: pointer;
          transition: all 0.2s;
          font-family: inherit;
        }

        .btn-secondary-action:hover {
          background: #161b22;
          border-color: #8893ff;
          color: #c9d1d9;
          transform: translateY(-1px);
        }

        .btn-secondary-check {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 0.5rem;
          width: 100%;
          padding: 0.75rem 1rem;
          background: transparent;
          color: #c9d1d9;
          border: 1px solid #30363d;
          border-radius: 6px;
          font-size: 14px;
          font-weight: 500;
          cursor: pointer;
          transition: all 0.2s;
          font-family: inherit;
        }

        .btn-secondary-check:hover:not(:disabled) {
          background: #161b22;
          border-color: #8893ff;
          transform: translateY(-1px);
        }

        .btn-secondary-check:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .spinner-small {
          width: 16px;
          height: 16px;
          border: 2px solid rgba(201, 209, 217, 0.3);
          border-top-color: #c9d1d9;
          border-radius: 50%;
          animation: spin 0.8s linear infinite;
        }

        @keyframes spin {
          to { transform: rotate(360deg); }
        }

        .help-text {
          padding: 1.25rem 1.5rem;
          background: rgba(136, 147, 255, 0.05);
          border: 1px solid rgba(136, 147, 255, 0.15);
          border-radius: 8px;
          font-size: 13px;
          color: #c9d1d9;
        }

        .help-text p {
          margin: 0 0 0.75rem;
          font-weight: 600;
          color: #f0f6fc;
        }

        .help-text ol {
          margin: 0;
          padding-left: 1.5rem;
          line-height: 1.8;
        }

        .help-text li {
          margin-bottom: 0.5rem;
        }

        .info-panel {
          padding: 1.5rem;
          margin: 1.5rem 0;
          background: rgba(136, 147, 255, 0.05);
          border: 1px solid rgba(136, 147, 255, 0.15);
          border-radius: 8px;
        }

        .info-panel h3 {
          margin: 0 0 0.75rem;
          font-size: 16px;
          font-weight: 600;
          color: #f0f6fc;
        }

        .info-panel p {
          margin: 0 0 0.75rem;
          font-size: 14px;
          color: #c9d1d9;
          line-height: 1.6;
        }

        .info-note {
          margin-top: 1rem;
          padding: 0.75rem 1rem;
          background: rgba(136, 147, 255, 0.08);
          border-left: 3px solid #8893ff;
          border-radius: 4px;
          font-size: 13px;
          color: #c9d1d9;
        }

        .permission-list {
          margin: 0.75rem 0;
          padding-left: 1.5rem;
          font-size: 13px;
          color: #c9d1d9;
          line-height: 1.8;
        }

        .permission-list li {
          margin-bottom: 0.5rem;
        }

        .permission-list strong {
          color: #8893ff;
        }

        .polling-status {
          display: flex;
          align-items: center;
          gap: 0.75rem;
          padding: 0.875rem 1rem;
          margin-bottom: 1.5rem;
          background: rgba(136, 147, 255, 0.08);
          border: 1px solid rgba(136, 147, 255, 0.2);
          border-radius: 6px;
          font-size: 13px;
          color: #8893ff;
        }

        .polling-indicator {
          width: 8px;
          height: 8px;
          background: #8893ff;
          border-radius: 50%;
          animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite;
        }

        @keyframes pulse {
          0%, 100% {
            opacity: 1;
          }
          50% {
            opacity: 0.3;
          }
        }

        @media (max-width: 640px) {
          .welcome-page {
            padding: 1rem;
          }

          .welcome-header {
            padding: 2rem 1.5rem 1.5rem;
          }

          .welcome-content,
          .instructions-panel {
            padding: 1.5rem;
          }

          .welcome-footer {
            padding: 1rem 1.5rem;
          }

          .auth-method {
            flex-direction: column;
          }

          .footer-links {
            flex-direction: column;
            gap: 0.75rem;
          }
        }
      `}</style>
    </div>
  );
}
