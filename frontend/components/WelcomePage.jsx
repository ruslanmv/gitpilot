import React, { useState, useEffect } from "react";

export default function WelcomePage({ onAuthComplete }) {
  const [authStatus, setAuthStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showInstructions, setShowInstructions] = useState(false);

  useEffect(() => {
    checkAuthStatus();

    // Poll for authentication status every 3 seconds (like Claude Code)
    // This automatically detects when user completes 'gitpilot login' in CLI
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

  const handleLogin = () => {
    setShowInstructions(true);
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
                <h2 className="section-title">Secure Authentication Required</h2>
                <p className="section-description">
                  GitPilot uses GitHub App authentication for secure, granular access to your repositories.
                </p>

                <div className="auth-methods">
                  <div className="auth-method primary">
                    <div className="method-icon">
                      <svg viewBox="0 0 16 16" fill="currentColor">
                        <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>
                      </svg>
                    </div>
                    <div className="method-content">
                      <h3>GitHub App (Recommended)</h3>
                      <p>Enterprise-grade authentication with granular repository permissions</p>
                      <button className="btn-primary" onClick={handleLogin}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                          <path d="M12 2a5 5 0 00-5 5v3H6a2 2 0 00-2 2v8a2 2 0 002 2h12a2 2 0 002-2v-8a2 2 0 00-2-2h-1V7a5 5 0 00-5-5z" />
                        </svg>
                        Setup GitHub App
                      </button>
                    </div>
                  </div>

                  <div className="divider">
                    <span>or</span>
                  </div>

                  <div className="auth-method secondary">
                    <div className="method-icon">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" />
                      </svg>
                    </div>
                    <div className="method-content">
                      <h3>Personal Access Token</h3>
                      <p>Quick setup for development and testing</p>
                      <details className="pat-details">
                        <summary>Setup instructions</summary>
                        <ol>
                          <li>Create a token at <a href="https://github.com/settings/tokens/new" target="_blank" rel="noopener noreferrer">github.com/settings/tokens</a></li>
                          <li>Add to <code>.env</code>: <code>GITPILOT_GITHUB_TOKEN=your_token</code></li>
                          <li>Set auth mode: <code>GITPILOT_GITHUB_AUTH_MODE=pat</code></li>
                          <li>Restart GitPilot server</li>
                        </ol>
                      </details>
                    </div>
                  </div>
                </div>

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
          ) : (
            <div className="instructions-panel">
              <button className="back-button" onClick={() => setShowInstructions(false)}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="20" height="20">
                  <path d="M19 12H5m7-7l-7 7 7 7"/>
                </svg>
                Back
              </button>

              <h2>GitHub App Setup</h2>
              <p className="instructions-description">Follow these steps to configure GitPilot with your GitHub App:</p>

              <div className="polling-status">
                <div className="polling-indicator"></div>
                <span>Waiting for authentication... (auto-refreshing)</span>
              </div>

              <div className="step-list">
                <div className="step">
                  <div className="step-number">1</div>
                  <div className="step-content">
                    <h3>Create and install GitHub App</h3>
                    <p>Follow the <a href="https://github.com/ruslanmv/gitpilot/blob/main/docs/GITHUB_APP_SETUP.md" target="_blank" rel="noopener noreferrer">setup guide</a> to create your GitHub App</p>
                    <button className="btn-secondary" onClick={handleSetupGitHubApp} style={{marginTop: '0.75rem'}}>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                        <path d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                      </svg>
                      Install GitHub App
                    </button>
                  </div>
                </div>

                <div className="step">
                  <div className="step-number">2</div>
                  <div className="step-content">
                    <h3>Run the login command</h3>
                    <p>The CLI will prompt you for your App ID, Installation ID, and private key</p>
                    <div className="code-block">
                      <code>gitpilot login</code>
                      <button className="copy-btn" onClick={() => navigator.clipboard.writeText('gitpilot login')}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="16" height="16">
                          <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                          <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/>
                        </svg>
                      </button>
                    </div>
                  </div>
                </div>

                <div className="step">
                  <div className="step-number">3</div>
                  <div className="step-content">
                    <h3>Refresh this page</h3>
                    <p>Once configured, refresh to access GitPilot</p>
                  </div>
                </div>
              </div>

              <button className="btn-refresh" onClick={() => window.location.reload()}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="20" height="20">
                  <path d="M1 4v6h6M23 20v-6h-6"/>
                  <path d="M20.49 9A9 9 0 005.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 013.51 15"/>
                </svg>
                Refresh Page
              </button>
            </div>
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
