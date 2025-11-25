import { useState, useEffect } from 'react';

/**
 * GitPilot GitHub App Installation
 *
 * Simplified login matching Claude Code's behavior:
 * 1. Install GitHub App
 * 2. Check installation status
 * 3. Automatically authenticate once installed
 */
export default function LoginPage({ onAuthenticated }) {
  const [installationStatus, setInstallationStatus] = useState('not_installed'); // 'not_installed', 'checking', 'installed'
  const [error, setError] = useState('');
  const [checking, setChecking] = useState(false);
  const [appUrl, setAppUrl] = useState('');

  useEffect(() => {
    checkInstallationStatus();
    loadAppUrl();
  }, []);

  // Load GitHub App URL from backend
  const loadAppUrl = async () => {
    try {
      const response = await fetch('/api/auth/app-url');
      const data = await response.json();
      setAppUrl(data.app_url || 'https://github.com/apps/gitpilot');
    } catch (err) {
      console.error('Failed to load app URL:', err);
      setAppUrl('https://github.com/apps/gitpilot');
    }
  };

  // Check if GitHub App is installed
  const checkInstallationStatus = async () => {
    setChecking(true);
    setError('');

    try {
      const response = await fetch('/api/auth/installation-status');
      const data = await response.json();

      if (data.installed) {
        // App is installed! Get user info and authenticate
        setInstallationStatus('installed');

        // Store token and user info
        if (data.access_token && data.user) {
          localStorage.setItem('github_token', data.access_token);
          localStorage.setItem('github_user', JSON.stringify(data.user));

          // Authenticate user
          onAuthenticated({
            access_token: data.access_token,
            user: data.user,
          });
        }
      } else {
        setInstallationStatus('not_installed');
        if (data.message) {
          setError(data.message);
        }
      }
    } catch (err) {
      console.error('Failed to check installation:', err);
      setError('Failed to check installation status. Please try again.');
      setInstallationStatus('not_installed');
    } finally {
      setChecking(false);
    }
  };

  // Open GitHub App installation page
  const handleInstallApp = () => {
    // Open GitHub App installation in new window
    window.open(appUrl, '_blank');

    // After opening, show that we're waiting for installation
    setInstallationStatus('checking');
  };

  return (
    <div className="install-modal-backdrop">
      <div className="install-modal">
        {/* Header */}
        <div className="install-modal-header">
          <div className="install-modal-logo">
            <div className="logo-icon-large">GP</div>
          </div>
          <h1 className="install-modal-title">Install GitPilot GitHub App</h1>
          <p className="install-modal-subtitle">
            {installationStatus === 'not_installed'
              ? 'The GitPilot GitHub app must be installed in your repositories to use GitPilot.'
              : 'Checking installation status...'}
          </p>
        </div>

        {/* Status indicator */}
        {installationStatus !== 'installed' && (
          <div className={`install-status ${error ? 'install-status-error' : 'install-status-pending'}`}>
            {error ? (
              <>
                <svg className="status-icon" width="20" height="20" viewBox="0 0 20 20" fill="none">
                  <circle cx="10" cy="10" r="9" stroke="currentColor" strokeWidth="2"/>
                  <path d="M13 7L7 13M7 7L13 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                </svg>
                <span>{error || 'GitHub app is not installed'}</span>
              </>
            ) : (
              <>
                {checking ? (
                  <>
                    <div className="status-spinner"></div>
                    <span>Checking installation status...</span>
                  </>
                ) : (
                  <>
                    <svg className="status-icon" width="20" height="20" viewBox="0 0 20 20" fill="none">
                      <circle cx="10" cy="10" r="9" stroke="currentColor" strokeWidth="2"/>
                      <path d="M13 7L7 13M7 7L13 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                    </svg>
                    <span>GitHub app is not installed</span>
                  </>
                )}
              </>
            )}
          </div>
        )}

        {/* Installation Steps */}
        {installationStatus === 'not_installed' && (
          <div className="install-steps">
            <div className="install-step">
              <div className="step-number">1</div>
              <div className="step-content">
                <h3>Install GitPilot App</h3>
                <p>Grant GitPilot access to your repositories</p>
              </div>
            </div>

            <div className="install-step">
              <div className="step-number">2</div>
              <div className="step-content">
                <h3>Authenticate Your Account</h3>
                <p>Sign in to start using GitPilot</p>
              </div>
            </div>
          </div>
        )}

        {/* Action Buttons */}
        <div className="install-modal-actions">
          {installationStatus === 'not_installed' ? (
            <>
              <button
                className="btn-install-primary"
                onClick={handleInstallApp}
                disabled={checking}
              >
                <svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M10 0C4.477 0 0 4.484 0 10.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0110 4.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.203 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.942.359.31.678.921.678 1.856 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0020 10.017C20 4.484 15.522 0 10 0z" clipRule="evenodd" />
                </svg>
                Install GitPilot →
              </button>
            </>
          ) : installationStatus === 'checking' ? (
            <>
              <button
                className="btn-check-status"
                onClick={checkInstallationStatus}
                disabled={checking}
              >
                {checking ? (
                  <>
                    <div className="btn-spinner"></div>
                    Checking...
                  </>
                ) : (
                  'Check status'
                )}
              </button>
              <button
                className="btn-install-secondary"
                onClick={handleInstallApp}
                disabled={checking}
              >
                <svg width="16" height="16" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M10 0C4.477 0 0 4.484 0 10.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0110 4.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.203 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.942.359.31.678.921.678 1.856 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0020 10.017C20 4.484 15.522 0 10 0z" clipRule="evenodd" />
                </svg>
                Install app →
              </button>
            </>
          ) : null}
        </div>

        {/* Help text */}
        <div className="install-modal-footer">
          <p>
            After installing the app on GitHub, return here and click <strong>"Check status"</strong> to continue.
          </p>
        </div>
      </div>
    </div>
  );
}
