import React, { useEffect, useState } from "react";

function normalizeProvider(provider) {
  if (!provider) return "Checking...";
  if (typeof provider === "string") return provider.toUpperCase();
  if (typeof provider === "object") {
    return (
      provider.name ||
      provider.provider ||
      provider.type ||
      provider.label ||
      "Checking..."
    );
  }
  return "Checking...";
}

function normalizeVersion(version) {
  if (!version) return "Checking...";
  return String(version);
}

function phaseLabel(phase) {
  switch (phase) {
    case "booting":
      return "Starting services";
    case "checking-backend":
      return "Checking backend";
    case "validating-auth":
      return "Validating session";
    case "restoring-session":
      return "Restoring workspace";
    case "ready":
      return "Ready";
    case "fallback":
      return "Starting services";
    default:
      return "Checking backend";
  }
}

export default function StartupScreen({
  appName = "GitPilot",
  subtitle = "Enterprise Workspace Copilot",
  frontendVersion = "Checking...",
  backendVersion = "Checking...",
  provider = "Checking...",
  statusMessage = "Connecting to backend...",
  detailMessage = "Preparing your workspace. First launch may take a few seconds.",
  phase = "checking-backend",
  onRetry,
}) {
  const providerLabel = normalizeProvider(provider);
  const frontendLabel = normalizeVersion(frontendVersion);
  const backendLabel = normalizeVersion(backendVersion);

  const [elapsedSec, setElapsedSec] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const id = setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - start) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, []);

  const showSlowHint = elapsedSec >= 8 && elapsedSec < 20;
  const showRetry = elapsedSec >= 20;

  return (
    <div className="startup-screen" role="status" aria-live="polite">
      <div className="startup-card">
        <div className="startup-brand-row">
          <div className="startup-brand-mark startup-brand-mark--static" aria-hidden="true">
            <div className="startup-brand-core" />
          </div>

          <div className="startup-brand-copy">
            <div className="startup-title">{appName}</div>
            <div className="startup-subtitle">{subtitle}</div>
          </div>
        </div>

        <div className="startup-loader-wrap" aria-hidden="true">
          <div className="startup-orb-loader">
            <div className="startup-orb-ring-track" />
            <div className="startup-orb-ring-progress" />
            <div className="startup-orb-core" />
          </div>
        </div>

        <div className="startup-status-block">
          <div className="startup-status">{statusMessage}</div>
          <div className="startup-detail">{detailMessage}</div>
          {showSlowHint && (
            <div className="startup-detail startup-detail--hint">
              Still starting. This can take a little longer on first launch.
            </div>
          )}
        </div>

        <div className="startup-phase-row">
          <span className="startup-phase-badge">
            <span className="startup-phase-dot" />
            {phaseLabel(phase)}
          </span>
        </div>

        {showRetry && (
          <div className="startup-action-row">
            <div className="startup-action-text">
              Taking longer than expected.
            </div>
            <button
              type="button"
              className="startup-action-btn"
              onClick={() => (onRetry ? onRetry() : window.location.reload())}
            >
              Retry connection
            </button>
          </div>
        )}

        <div className="startup-meta-grid">
          <div className="startup-meta-item">
            <div className="startup-meta-label">Frontend</div>
            <div className="startup-meta-value">v{frontendLabel}</div>
          </div>

          <div className="startup-meta-item">
            <div className="startup-meta-label">Backend</div>
            <div className="startup-meta-value">v{backendLabel}</div>
          </div>

          <div className="startup-meta-item startup-meta-item-wide">
            <div className="startup-meta-label">Provider</div>
            <div className="startup-meta-value">{providerLabel}</div>
          </div>
        </div>

        <div className="startup-footer">
          Preparing workspace services, restoring session state, and checking
          platform readiness.
        </div>
      </div>
    </div>
  );
}
