import React from "react";

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

export default function StartupScreen({
  appName = "GitPilot",
  subtitle = "Enterprise Workspace Copilot",
  frontendVersion = "Checking...",
  backendVersion = "Checking...",
  provider = "Checking...",
  statusMessage = "Starting application...",
  detailMessage = "Initializing authentication, provider, and workspace context.",
  phase = "booting",
}) {
  const providerLabel = normalizeProvider(provider);
  const frontendLabel = normalizeVersion(frontendVersion);
  const backendLabel = normalizeVersion(backendVersion);

  return (
    <div className="startup-screen" role="status" aria-live="polite">
      <div className="startup-card">
        <div className="startup-brand-row">
          <div className="startup-brand-mark" aria-hidden="true">
            <div className="startup-brand-ring" />
            <div className="startup-brand-core" />
          </div>

          <div className="startup-brand-copy">
            <div className="startup-title">{appName}</div>
            <div className="startup-subtitle">{subtitle}</div>
          </div>
        </div>

        <div className="startup-loader-wrap" aria-hidden="true">
          <div className="startup-loader">
            <div className="startup-loader-ring startup-loader-ring-outer" />
            <div className="startup-loader-ring startup-loader-ring-inner" />
          </div>
        </div>

        <div className="startup-status-block">
          <div className="startup-status">{statusMessage}</div>
          <div className="startup-detail">{detailMessage}</div>
        </div>

        <div className="startup-phase-row">
          <span className="startup-phase-badge">{phase}</span>
        </div>

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