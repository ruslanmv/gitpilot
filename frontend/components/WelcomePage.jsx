import React, { useState, useEffect } from "react";

export default function WelcomePage({ onAuthComplete }) {
  const [authStatus, setAuthStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    checkAuthStatus();
  }, []);

  const checkAuthStatus = async () => {
    try {
      const response = await fetch("/api/auth/status");
      const data = await response.json();
      setAuthStatus(data);
      setLoading(false);

      // If authenticated, notify parent
      if (data.authenticated) {
        onAuthComplete?.();
      }
    } catch (err) {
      setError("Failed to check authentication status");
      setLoading(false);
    }
  };

  const handleLogin = () => {
    setError(null);
    // Show instructions for CLI login
    const message = `
To log in to GitPilot, please use the command line:

1. Open your terminal
2. Run: gitpilot login
3. Follow the instructions to authenticate with GitHub
4. Once complete, refresh this page

Alternatively, you can set up a Personal Access Token in your .env file:
GITPILOT_GITHUB_TOKEN=your_token_here
    `.trim();

    alert(message);
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
      <div className="welcome-page">
        <div className="welcome-container">
          <div className="welcome-loader">Loading...</div>
        </div>
      </div>
    );
  }

  if (authStatus?.authenticated) {
    return null; // Don't show welcome page if authenticated
  }

  return (
    <div className="welcome-page">
      <div className="welcome-container">
        <div className="welcome-header">
          <div className="welcome-logo">
            <div className="logo-square-large">GP</div>
          </div>
          <h1 className="welcome-title">Welcome to GitPilot</h1>
          <p className="welcome-subtitle">
            Agentic AI Assistant for GitHub Repositories
          </p>
        </div>

        <div className="welcome-content">
          <div className="welcome-section">
            <h2>🔐 Authentication Required</h2>
            <p>
              GitPilot uses a two-layer enterprise authentication system to
              securely access your GitHub repositories:
            </p>

            <div className="auth-layers">
              <div className="auth-layer">
                <div className="auth-layer-number">1</div>
                <div className="auth-layer-content">
                  <h3>User Authentication (GitHub OAuth)</h3>
                  <p>
                    Authenticate your personal GitHub account using OAuth. This
                    establishes your identity and allows GitPilot to access
                    repositories you have permission to view.
                  </p>
                  <button
                    className="btn-primary"
                    onClick={handleLogin}
                  >
                    🔑 Login with GitHub
                  </button>
                </div>
              </div>

              <div className="auth-layer">
                <div className="auth-layer-number">2</div>
                <div className="auth-layer-content">
                  <h3>Repository Access (GitHub App)</h3>
                  <p>
                    Install the GitPilot GitHub App to grant repository access.
                    You can choose to install it on all repositories or select
                    specific ones.
                  </p>
                  <button
                    className="btn-secondary"
                    onClick={handleSetupGitHubApp}
                  >
                    📦 Install GitHub App
                  </button>
                </div>
              </div>
            </div>
          </div>

          {error && (
            <div className="welcome-error">
              <strong>Error:</strong> {error}
            </div>
          )}

          <div className="welcome-section">
            <h3>Alternative: Personal Access Token</h3>
            <p className="text-muted">
              For quick setup or development purposes, you can also use a
              Personal Access Token (PAT) instead of OAuth:
            </p>
            <ol className="setup-steps">
              <li>
                Create a token at{" "}
                <a
                  href="https://github.com/settings/tokens/new"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  github.com/settings/tokens
                </a>
              </li>
              <li>Add it to your <code>.env</code> file as <code>GITPILOT_GITHUB_TOKEN</code></li>
              <li>Restart the GitPilot server</li>
            </ol>
          </div>
        </div>

        <div className="welcome-footer">
          <p className="text-muted">
            <strong>New to GitPilot?</strong> Check out the{" "}
            <a
              href="https://github.com/ruslanmv/gitpilot#readme"
              target="_blank"
              rel="noopener noreferrer"
            >
              documentation
            </a>{" "}
            to learn more.
          </p>
        </div>
      </div>

      <style jsx>{`
        .welcome-page {
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          padding: 2rem;
        }

        .welcome-container {
          max-width: 800px;
          background: white;
          border-radius: 12px;
          box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
          padding: 3rem;
        }

        .welcome-header {
          text-align: center;
          margin-bottom: 2rem;
        }

        .welcome-logo {
          display: flex;
          justify-content: center;
          margin-bottom: 1rem;
        }

        .logo-square-large {
          width: 80px;
          height: 80px;
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          color: white;
          display: flex;
          align-items: center;
          justify-content: center;
          border-radius: 12px;
          font-size: 32px;
          font-weight: bold;
        }

        .welcome-title {
          font-size: 2.5rem;
          margin: 0.5rem 0;
          color: #1a202c;
        }

        .welcome-subtitle {
          font-size: 1.1rem;
          color: #718096;
          margin: 0;
        }

        .welcome-content {
          margin-top: 2rem;
        }

        .welcome-section {
          margin-bottom: 2rem;
        }

        .welcome-section h2 {
          font-size: 1.5rem;
          margin-bottom: 1rem;
          color: #2d3748;
        }

        .welcome-section h3 {
          font-size: 1.2rem;
          margin-bottom: 0.5rem;
          color: #2d3748;
        }

        .welcome-section p {
          color: #4a5568;
          line-height: 1.6;
          margin-bottom: 1rem;
        }

        .auth-layers {
          display: flex;
          flex-direction: column;
          gap: 1.5rem;
          margin-top: 1.5rem;
        }

        .auth-layer {
          display: flex;
          gap: 1rem;
          padding: 1.5rem;
          background: #f7fafc;
          border-radius: 8px;
          border: 2px solid #e2e8f0;
        }

        .auth-layer-number {
          width: 40px;
          height: 40px;
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          color: white;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          font-weight: bold;
          font-size: 1.2rem;
          flex-shrink: 0;
        }

        .auth-layer-content {
          flex: 1;
        }

        .auth-layer-content h3 {
          margin-top: 0;
          margin-bottom: 0.5rem;
        }

        .auth-layer-content p {
          margin-bottom: 1rem;
          font-size: 0.95rem;
        }

        .btn-primary,
        .btn-secondary {
          padding: 0.75rem 1.5rem;
          border: none;
          border-radius: 6px;
          font-size: 1rem;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.2s;
        }

        .btn-primary {
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          color: white;
        }

        .btn-primary:hover {
          transform: translateY(-2px);
          box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
        }

        .btn-secondary {
          background: #48bb78;
          color: white;
        }

        .btn-secondary:hover {
          background: #38a169;
          transform: translateY(-2px);
          box-shadow: 0 4px 12px rgba(72, 187, 120, 0.4);
        }

        .welcome-error {
          padding: 1rem;
          background: #fed7d7;
          border: 1px solid #fc8181;
          border-radius: 6px;
          color: #742a2a;
          margin-bottom: 1rem;
        }

        .text-muted {
          color: #718096;
          font-size: 0.9rem;
        }

        .setup-steps {
          margin-left: 1.5rem;
          color: #4a5568;
          line-height: 1.8;
        }

        .setup-steps code {
          background: #edf2f7;
          padding: 0.2rem 0.4rem;
          border-radius: 3px;
          font-family: 'Monaco', 'Courier New', monospace;
          font-size: 0.9em;
        }

        .setup-steps a {
          color: #667eea;
          text-decoration: none;
        }

        .setup-steps a:hover {
          text-decoration: underline;
        }

        .welcome-footer {
          margin-top: 2rem;
          padding-top: 1.5rem;
          border-top: 1px solid #e2e8f0;
          text-align: center;
        }

        .welcome-footer a {
          color: #667eea;
          text-decoration: none;
          font-weight: 600;
        }

        .welcome-footer a:hover {
          text-decoration: underline;
        }

        .welcome-loader {
          text-align: center;
          padding: 3rem;
          font-size: 1.2rem;
          color: #4a5568;
        }

        @media (max-width: 768px) {
          .welcome-container {
            padding: 2rem 1.5rem;
          }

          .welcome-title {
            font-size: 2rem;
          }

          .auth-layers {
            gap: 1rem;
          }

          .auth-layer {
            flex-direction: column;
            padding: 1rem;
          }
        }
      `}</style>
    </div>
  );
}
