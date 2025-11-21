import { useState, useEffect } from 'react';

/**
 * Enterprise GitHub Login Page
 *
 * Professional authentication interface with Claude Code branding.
 * Supports both OAuth and Personal Access Token authentication.
 */
export default function LoginPage({ onAuthenticated }) {
  const [authMethod, setAuthMethod] = useState('loading'); // 'oauth', 'pat', 'none', 'loading'
  const [showPATInput, setShowPATInput] = useState(false);
  const [patToken, setPATToken] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    checkAuthStatus();
  }, []);

  // Check which authentication method is configured
  const checkAuthStatus = async () => {
    try {
      const response = await fetch('/api/auth/status');
      const data = await response.json();
      setAuthMethod(data.auth_method);
    } catch (err) {
      console.error('Failed to check auth status:', err);
      setAuthMethod('none');
    }
  };

  // Initiate GitHub OAuth flow
  const handleOAuthLogin = async () => {
    setLoading(true);
    setError('');

    try {
      const response = await fetch('/api/auth/url');
      const data = await response.json();

      // Store state for callback verification
      sessionStorage.setItem('oauth_state', data.state);

      // Redirect to GitHub
      window.location.href = data.authorization_url;
    } catch (err) {
      setError('Failed to initiate GitHub login. Please try again.');
      setLoading(false);
    }
  };

  // Handle OAuth callback
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const state = params.get('state');

    if (code && state) {
      handleOAuthCallback(code, state);
    }
  }, []);

  const handleOAuthCallback = async (code, state) => {
    setLoading(true);
    const savedState = sessionStorage.getItem('oauth_state');

    if (state !== savedState) {
      setError('Invalid authentication state. Please try again.');
      setLoading(false);
      return;
    }

    try {
      const response = await fetch('/api/auth/callback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, state }),
      });

      if (!response.ok) {
        throw new Error('Authentication failed');
      }

      const session = await response.json();

      // Store access token
      localStorage.setItem('github_token', session.access_token);
      localStorage.setItem('github_user', JSON.stringify(session.user));

      // Clear OAuth state
      sessionStorage.removeItem('oauth_state');

      // Clean URL
      window.history.replaceState({}, document.title, '/');

      // Notify parent
      onAuthenticated(session);
    } catch (err) {
      setError('Authentication failed. Please try again.');
      setLoading(false);
    }
  };

  // Handle Personal Access Token login
  const handlePATLogin = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    if (!patToken.trim()) {
      setError('Please enter a valid token');
      setLoading(false);
      return;
    }

    try {
      const response = await fetch('/api/auth/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ access_token: patToken }),
      });

      const data = await response.json();

      if (!data.authenticated) {
        setError('Invalid token. Please check your Personal Access Token.');
        setLoading(false);
        return;
      }

      // Store token and user info
      localStorage.setItem('github_token', patToken);
      localStorage.setItem('github_user', JSON.stringify(data.user));

      // Notify parent
      onAuthenticated({ access_token: patToken, user: data.user });
    } catch (err) {
      setError('Failed to validate token. Please try again.');
      setLoading(false);
    }
  };

  if (authMethod === 'loading') {
    return (
      <div className="login-page">
        <div className="login-container">
          <div className="loading-spinner"></div>
        </div>
      </div>
    );
  }

  return (
    <div className="login-page">
      <div className="login-container">
        {/* Header */}
        <div className="login-header">
          <div className="login-logo">
            <div className="logo-icon">GP</div>
          </div>
          <h1 className="login-title">GitPilot Enterprise</h1>
          <p className="login-subtitle">
            Powered by Claude Code - Your AI Coding Companion
          </p>
        </div>

        {/* Welcome Message */}
        <div className="login-welcome">
          <h2>Welcome</h2>
          <p>
            Connect your GitHub account to start building with AI-powered
            development tools. GitPilot helps you understand codebases, plan
            features, and execute changes with confidence.
          </p>
        </div>

        {/* Error Message */}
        {error && (
          <div className="login-error">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="2"/>
              <path d="M8 4V9" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              <circle cx="8" cy="11.5" r="0.5" fill="currentColor"/>
            </svg>
            <span>{error}</span>
          </div>
        )}

        {/* OAuth Login Button */}
        {authMethod === 'oauth' && !showPATInput && (
          <div className="login-actions">
            <button
              className="btn-primary btn-large"
              onClick={handleOAuthLogin}
              disabled={loading}
            >
              {loading ? (
                <>
                  <span className="btn-spinner"></span>
                  Connecting...
                </>
              ) : (
                <>
                  <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
                    <path fillRule="evenodd" d="M10 0C4.477 0 0 4.484 0 10.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0110 4.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.203 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.942.359.31.678.921.678 1.856 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0020 10.017C20 4.484 15.522 0 10 0z" clipRule="evenodd" />
                  </svg>
                  Continue with GitHub
                </>
              )}
            </button>

            <div className="login-divider">
              <span>or</span>
            </div>

            <button
              className="btn-secondary btn-large"
              onClick={() => setShowPATInput(true)}
            >
              Use Personal Access Token
            </button>
          </div>
        )}

        {/* PAT Login Form */}
        {(authMethod === 'pat' || showPATInput) && (
          <form className="login-form" onSubmit={handlePATLogin}>
            <div className="form-group">
              <label htmlFor="pat-token">GitHub Personal Access Token</label>
              <input
                id="pat-token"
                type="password"
                className="form-input"
                placeholder="ghp_xxxxxxxxxxxxxxxxxxxx"
                value={patToken}
                onChange={(e) => setPATToken(e.target.value)}
                disabled={loading}
              />
              <p className="form-hint">
                Need a token?{' '}
                <a
                  href="https://github.com/settings/tokens/new?scopes=repo,user:email"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="form-link"
                >
                  Create one here
                </a>
                {' '}with <code>repo</code> and <code>user:email</code> scopes.
              </p>
            </div>

            <button
              type="submit"
              className="btn-primary btn-large"
              disabled={loading || !patToken.trim()}
            >
              {loading ? (
                <>
                  <span className="btn-spinner"></span>
                  Authenticating...
                </>
              ) : (
                'Continue'
              )}
            </button>

            {authMethod === 'oauth' && (
              <button
                type="button"
                className="btn-text"
                onClick={() => setShowPATInput(false)}
              >
                ← Back to OAuth
              </button>
            )}
          </form>
        )}

        {/* No Authentication Configured */}
        {authMethod === 'none' && (
          <div className="login-notice">
            <h3>Authentication Not Configured</h3>
            <p>
              Please configure GitHub authentication by setting up one of the following:
            </p>
            <ul>
              <li>
                <strong>OAuth App:</strong> Set <code>GITHUB_CLIENT_ID</code> and{' '}
                <code>GITHUB_CLIENT_SECRET</code> environment variables
              </li>
              <li>
                <strong>Personal Access Token:</strong> Set <code>GITPILOT_GITHUB_TOKEN</code>{' '}
                or <code>GITHUB_TOKEN</code> environment variable
              </li>
            </ul>
          </div>
        )}

        {/* Features */}
        <div className="login-features">
          <div className="feature-item">
            <div className="feature-icon">
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                <path d="M3 10L7 14L17 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
            <div className="feature-text">
              <strong>Repository Access</strong>
              <span>Browse and select your GitHub repositories</span>
            </div>
          </div>

          <div className="feature-item">
            <div className="feature-icon">
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                <path d="M3 10L7 14L17 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
            <div className="feature-text">
              <strong>AI-Powered Planning</strong>
              <span>Generate intelligent development plans</span>
            </div>
          </div>

          <div className="feature-item">
            <div className="feature-icon">
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
                <path d="M3 10L7 14L17 4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
            <div className="feature-text">
              <strong>Secure & Private</strong>
              <span>Your code stays in your repositories</span>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="login-footer">
          <p>
            By continuing, you agree to allow GitPilot to access your GitHub
            repositories. You can revoke access at any time from your GitHub settings.
          </p>
        </div>
      </div>
    </div>
  );
}
