import React, { useState } from "react";

export default function SetupWizard({ onComplete }) {
  const [step, setStep] = useState(1); // 1: Welcome, 2: GitHub, 3: LLM, 4: Complete
  const [githubConfig, setGithubConfig] = useState({
    authMode: "pat",
    personalToken: "",
    appSlug: "",
  });
  const [llmConfig, setLlmConfig] = useState({
    provider: "openai",
    openai: { api_key: "", model: "gpt-4o-mini" },
    claude: { api_key: "", model: "claude-3-5-sonnet-20241022" },
    watsonx: { api_key: "", project_id: "", model_id: "meta-llama/llama-3-1-70b-instruct" },
    ollama: { base_url: "http://localhost:11434", model: "llama3" },
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const totalSteps = 4;

  const handleNext = () => {
    if (step < totalSteps) {
      setStep(step + 1);
      setError(null);
    }
  };

  const handlePrevious = () => {
    if (step > 1) {
      setStep(step - 1);
      setError(null);
    }
  };

  const handleSkipSetup = async () => {
    try {
      setSaving(true);
      // Mark setup as completed but skip configuration
      await fetch("/api/settings/llm", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ setup_completed: true }),
      });
      onComplete();
    } catch (e) {
      console.error("Failed to skip setup:", e);
      setError("Failed to complete setup");
    } finally {
      setSaving(false);
    }
  };

  const handleSaveAndComplete = async () => {
    try {
      setSaving(true);
      setError(null);

      // Save GitHub configuration
      const github = {
        auth_mode: githubConfig.authMode,
        personal_token: githubConfig.authMode === "pat" ? githubConfig.personalToken : "",
        app_slug: githubConfig.authMode === "app" ? githubConfig.appSlug : "",
      };

      // Save LLM configuration
      const llmSettings = {
        provider: llmConfig.provider,
        [llmConfig.provider]: llmConfig[llmConfig.provider],
        github,
        setup_completed: true,
      };

      const res = await fetch("/api/settings/llm", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(llmSettings),
      });

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }

      onComplete();
    } catch (e) {
      console.error("Failed to save settings:", e);
      setError("Failed to save settings. Please check your configuration.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="setup-wizard-overlay">
      <div className="setup-wizard-container">
        {/* Progress bar */}
        <div className="setup-progress-bar">
          {[...Array(totalSteps)].map((_, i) => (
            <div
              key={i}
              className={`setup-progress-dot ${i + 1 <= step ? "active" : ""} ${
                i + 1 < step ? "completed" : ""
              }`}
            />
          ))}
        </div>

        {/* Step content */}
        <div className="setup-wizard-content">
          {step === 1 && (
            <WelcomeStep
              onNext={handleNext}
              onSkip={handleSkipSetup}
              saving={saving}
            />
          )}

          {step === 2 && (
            <GitHubStep
              config={githubConfig}
              onChange={setGithubConfig}
              onNext={handleNext}
              onPrevious={handlePrevious}
              error={error}
            />
          )}

          {step === 3 && (
            <LLMStep
              config={llmConfig}
              onChange={setLlmConfig}
              onNext={handleNext}
              onPrevious={handlePrevious}
              error={error}
            />
          )}

          {step === 4 && (
            <CompleteStep
              onComplete={handleSaveAndComplete}
              onPrevious={handlePrevious}
              saving={saving}
              error={error}
            />
          )}
        </div>
      </div>
    </div>
  );
}

// Welcome Step
function WelcomeStep({ onNext, onSkip, saving }) {
  return (
    <div className="setup-step">
      <div className="setup-step-icon">🚀</div>
      <h1 className="setup-step-title">Welcome to GitPilot</h1>
      <p className="setup-step-description">
        Let's get you set up in just a few steps. You'll need:
      </p>
      <ul className="setup-checklist">
        <li>
          <span className="setup-checklist-icon">✓</span>
          GitHub access (Personal Access Token or GitHub App)
        </li>
        <li>
          <span className="setup-checklist-icon">✓</span>
          LLM API key (OpenAI, Claude, Watsonx, or Ollama)
        </li>
      </ul>
      <p className="setup-step-note">
        Don't worry - you can change these settings later in the Admin panel.
      </p>
      <div className="setup-step-actions">
        <button
          type="button"
          className="setup-btn setup-btn-secondary"
          onClick={onSkip}
          disabled={saving}
        >
          Skip for Now
        </button>
        <button
          type="button"
          className="setup-btn setup-btn-primary"
          onClick={onNext}
        >
          Get Started
        </button>
      </div>
    </div>
  );
}

// GitHub Step
function GitHubStep({ config, onChange, onNext, onPrevious, error }) {
  const [showToken, setShowToken] = useState(false);

  const canProceed =
    (config.authMode === "pat" && config.personalToken) ||
    (config.authMode === "app" && config.appSlug);

  return (
    <div className="setup-step">
      <div className="setup-step-icon">🔗</div>
      <h1 className="setup-step-title">Connect to GitHub</h1>
      <p className="setup-step-description">
        Choose how you want to authenticate with GitHub
      </p>

      <div className="setup-auth-modes">
        <button
          type="button"
          className={`setup-auth-mode ${
            config.authMode === "pat" ? "active" : ""
          }`}
          onClick={() => onChange({ ...config, authMode: "pat" })}
        >
          <div className="setup-auth-mode-icon">🔑</div>
          <div className="setup-auth-mode-label">Personal Access Token</div>
          <div className="setup-auth-mode-desc">Simple, quick setup</div>
        </button>
        <button
          type="button"
          className={`setup-auth-mode ${
            config.authMode === "app" ? "active" : ""
          }`}
          onClick={() => onChange({ ...config, authMode: "app" })}
        >
          <div className="setup-auth-mode-icon">🛡️</div>
          <div className="setup-auth-mode-label">GitHub App</div>
          <div className="setup-auth-mode-desc">Recommended for teams</div>
        </button>
      </div>

      {config.authMode === "pat" && (
        <div className="setup-form-group">
          <label className="setup-label">
            Personal Access Token
            <span className="setup-label-required">*</span>
          </label>
          <div className="setup-input-with-toggle">
            <input
              type={showToken ? "text" : "password"}
              className="setup-input"
              value={config.personalToken}
              onChange={(e) =>
                onChange({ ...config, personalToken: e.target.value })
              }
              placeholder="ghp_xxxxxxxxxxxx"
            />
            <button
              type="button"
              className="setup-input-toggle"
              onClick={() => setShowToken(!showToken)}
            >
              {showToken ? "👁️" : "👁️‍🗨️"}
            </button>
          </div>
          <p className="setup-hint">
            Get your token at{" "}
            <a
              href="https://github.com/settings/tokens"
              target="_blank"
              rel="noopener noreferrer"
            >
              github.com/settings/tokens
            </a>
            <br />
            Required scopes: <code>repo</code>
          </p>
        </div>
      )}

      {config.authMode === "app" && (
        <div className="setup-form-group">
          <label className="setup-label">
            GitHub App Slug
            <span className="setup-label-required">*</span>
          </label>
          <input
            type="text"
            className="setup-input"
            value={config.appSlug}
            onChange={(e) => onChange({ ...config, appSlug: e.target.value })}
            placeholder="your-app-name"
          />
          <p className="setup-hint">
            Create your GitHub App at{" "}
            <a
              href="https://github.com/settings/apps"
              target="_blank"
              rel="noopener noreferrer"
            >
              github.com/settings/apps
            </a>
            <br />
            The slug is the URL-friendly name of your app.
          </p>
        </div>
      )}

      {error && <div className="setup-error">{error}</div>}

      <div className="setup-step-actions">
        <button
          type="button"
          className="setup-btn setup-btn-secondary"
          onClick={onPrevious}
        >
          Back
        </button>
        <button
          type="button"
          className="setup-btn setup-btn-primary"
          onClick={onNext}
          disabled={!canProceed}
        >
          Continue
        </button>
      </div>
    </div>
  );
}

// LLM Step
function LLMStep({ config, onChange, onNext, onPrevious, error }) {
  const [showApiKey, setShowApiKey] = useState(false);

  const currentProvider = config[config.provider];
  const canProceed =
    (config.provider === "openai" && currentProvider.api_key) ||
    (config.provider === "claude" && currentProvider.api_key) ||
    (config.provider === "watsonx" &&
      currentProvider.api_key &&
      currentProvider.project_id) ||
    config.provider === "ollama";

  return (
    <div className="setup-step">
      <div className="setup-step-icon">🤖</div>
      <h1 className="setup-step-title">Configure AI Provider</h1>
      <p className="setup-step-description">
        Select your preferred LLM provider
      </p>

      <div className="setup-provider-tabs">
        {["openai", "claude", "watsonx", "ollama"].map((provider) => (
          <button
            key={provider}
            type="button"
            className={`setup-provider-tab ${
              config.provider === provider ? "active" : ""
            }`}
            onClick={() => onChange({ ...config, provider })}
          >
            {provider.charAt(0).toUpperCase() + provider.slice(1)}
          </button>
        ))}
      </div>

      {(config.provider === "openai" ||
        config.provider === "claude" ||
        config.provider === "watsonx") && (
        <div className="setup-form-group">
          <label className="setup-label">
            API Key
            <span className="setup-label-required">*</span>
          </label>
          <div className="setup-input-with-toggle">
            <input
              type={showApiKey ? "text" : "password"}
              className="setup-input"
              value={currentProvider.api_key}
              onChange={(e) =>
                onChange({
                  ...config,
                  [config.provider]: {
                    ...currentProvider,
                    api_key: e.target.value,
                  },
                })
              }
              placeholder={
                config.provider === "openai"
                  ? "sk-xxxxxxxxxxxx"
                  : config.provider === "claude"
                  ? "sk-ant-xxxxxxxxxxxx"
                  : "api-key"
              }
            />
            <button
              type="button"
              className="setup-input-toggle"
              onClick={() => setShowApiKey(!showApiKey)}
            >
              {showApiKey ? "👁️" : "👁️‍🗨️"}
            </button>
          </div>
          {config.provider === "openai" && (
            <p className="setup-hint">
              Get your API key at{" "}
              <a
                href="https://platform.openai.com/api-keys"
                target="_blank"
                rel="noopener noreferrer"
              >
                platform.openai.com/api-keys
              </a>
            </p>
          )}
          {config.provider === "claude" && (
            <p className="setup-hint">
              Get your API key at{" "}
              <a
                href="https://console.anthropic.com/"
                target="_blank"
                rel="noopener noreferrer"
              >
                console.anthropic.com
              </a>
            </p>
          )}
        </div>
      )}

      {config.provider === "watsonx" && (
        <div className="setup-form-group">
          <label className="setup-label">
            Project ID
            <span className="setup-label-required">*</span>
          </label>
          <input
            type="text"
            className="setup-input"
            value={currentProvider.project_id}
            onChange={(e) =>
              onChange({
                ...config,
                watsonx: { ...currentProvider, project_id: e.target.value },
              })
            }
            placeholder="your-project-id"
          />
          <p className="setup-hint">
            Find your project ID at{" "}
            <a
              href="https://cloud.ibm.com/"
              target="_blank"
              rel="noopener noreferrer"
            >
              cloud.ibm.com
            </a>
          </p>
        </div>
      )}

      {config.provider === "ollama" && (
        <div className="setup-form-group">
          <label className="setup-label">Ollama Base URL</label>
          <input
            type="text"
            className="setup-input"
            value={currentProvider.base_url}
            onChange={(e) =>
              onChange({
                ...config,
                ollama: { ...currentProvider, base_url: e.target.value },
              })
            }
            placeholder="http://localhost:11434"
          />
          <p className="setup-hint">
            Make sure Ollama is running locally or provide a remote URL
          </p>
        </div>
      )}

      {error && <div className="setup-error">{error}</div>}

      <div className="setup-step-actions">
        <button
          type="button"
          className="setup-btn setup-btn-secondary"
          onClick={onPrevious}
        >
          Back
        </button>
        <button
          type="button"
          className="setup-btn setup-btn-primary"
          onClick={onNext}
          disabled={!canProceed}
        >
          Continue
        </button>
      </div>
    </div>
  );
}

// Complete Step
function CompleteStep({ onComplete, onPrevious, saving, error }) {
  return (
    <div className="setup-step">
      <div className="setup-step-icon">✨</div>
      <h1 className="setup-step-title">Ready to Go!</h1>
      <p className="setup-step-description">
        Your configuration is complete. Click "Finish Setup" to start using
        GitPilot.
      </p>

      <div className="setup-complete-features">
        <div className="setup-feature-card">
          <div className="setup-feature-icon">🔗</div>
          <div className="setup-feature-label">GitHub Connected</div>
        </div>
        <div className="setup-feature-card">
          <div className="setup-feature-icon">🤖</div>
          <div className="setup-feature-label">AI Provider Ready</div>
        </div>
        <div className="setup-feature-card">
          <div className="setup-feature-icon">💬</div>
          <div className="setup-feature-label">Chat Interface Active</div>
        </div>
      </div>

      <p className="setup-step-note">
        You can always update these settings in the Admin panel.
      </p>

      {error && <div className="setup-error">{error}</div>}

      <div className="setup-step-actions">
        <button
          type="button"
          className="setup-btn setup-btn-secondary"
          onClick={onPrevious}
          disabled={saving}
        >
          Back
        </button>
        <button
          type="button"
          className="setup-btn setup-btn-primary"
          onClick={onComplete}
          disabled={saving}
        >
          {saving ? "Saving..." : "Finish Setup"}
        </button>
      </div>
    </div>
  );
}
