import React, { useState } from "react";

export default function SetupWizard({ onComplete }) {
  const [step, setStep] = useState(1); // 1: Welcome, 2: GitHub, 3: LLM, 4: Complete
  const [githubMode, setGithubMode] = useState("oauth"); // oauth, pat
  const [githubToken, setGithubToken] = useState("");
  const [llmConfig, setLlmConfig] = useState({
    provider: "openai",
    openai: { api_key: "", model: "gpt-4o-mini" },
    claude: { api_key: "", model: "claude-3-5-sonnet-20241022" },
    watsonx: {
      api_key: "",
      project_id: "",
      model_id: "meta-llama/llama-3-1-70b-instruct",
      base_url: "https://us-south.ml.cloud.ibm.com",
    },
    ollama: { base_url: "http://localhost:11434", model: "llama3" },
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  const handleGitHubOAuthLogin = async () => {
    try {
      const res = await fetch("/api/github/oauth/url");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      window.location.href = data.url; // Redirect to GitHub
    } catch (e) {
      console.error("Failed to initiate GitHub login:", e);
      setError("Failed to connect to GitHub. Please try again.");
    }
  };

  const handleSaveAndComplete = async () => {
    try {
      setSaving(true);
      setError(null);

      // Save GitHub configuration (if using PAT)
      const github = githubMode === "pat" && githubToken
        ? { auth_mode: "pat", personal_token: githubToken }
        : {};

      // Save LLM configuration
      const llmSettings = {
        provider: llmConfig.provider,
        [llmConfig.provider]: llmConfig[llmConfig.provider],
        ...(Object.keys(github).length > 0 && { github }),
        setup_completed: true,
      };

      const res = await fetch("/api/settings/llm", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(llmSettings),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      onComplete();
    } catch (e) {
      console.error("Failed to save settings:", e);
      setError("Failed to save settings. Please check your configuration.");
    } finally {
      setSaving(false);
    }
  };

  const handleSkipSetup = async () => {
    try {
      setSaving(true);
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

  const totalSteps = 3;
  const currentProvider = llmConfig[llmConfig.provider];
  const canProceedLLM =
    (llmConfig.provider === "openai" && currentProvider.api_key) ||
    (llmConfig.provider === "claude" && currentProvider.api_key) ||
    (llmConfig.provider === "watsonx" && currentProvider.api_key && currentProvider.project_id) ||
    llmConfig.provider === "ollama";

  return (
    <div className="setup-wizard-overlay">
      <div className="setup-wizard-container">
        {/* Progress indicator */}
        <div className="setup-progress">
          <div className="setup-progress-bar">
            <div
              className="setup-progress-fill"
              style={{ width: `${(step / totalSteps) * 100}%` }}
            />
          </div>
          <div className="setup-progress-text">
            Step {step} of {totalSteps}
          </div>
        </div>

        {/* Step content */}
        {step === 1 && (
          <div className="setup-step">
            <div className="setup-icon">🚀</div>
            <h1 className="setup-title">Welcome to GitPilot</h1>
            <p className="setup-description">
              Let's get you set up. This will only take a minute.
            </p>
            <p className="setup-description" style={{ fontSize: "14px", color: "#9a9ca8", marginTop: "8px" }}>
              You'll need to connect your GitHub account to use GitPilot.
            </p>
            <div className="setup-actions">
              <button className="setup-btn-primary" onClick={() => setStep(2)}>
                Get Started
              </button>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="setup-step">
            <div className="setup-icon">🔗</div>
            <h1 className="setup-title">Connect GitHub</h1>
            <p className="setup-description">
              Choose how you'd like to authenticate
            </p>

            <div className="setup-options">
              <button
                className={`setup-option-card ${githubMode === "oauth" ? "active" : ""}`}
                onClick={() => setGithubMode("oauth")}
              >
                <div className="setup-option-icon">🔐</div>
                <div className="setup-option-label">Sign in with GitHub</div>
                <div className="setup-option-desc">Recommended - Quick & secure</div>
              </button>

              <button
                className={`setup-option-card ${githubMode === "pat" ? "active" : ""}`}
                onClick={() => setGithubMode("pat")}
              >
                <div className="setup-option-icon">🔑</div>
                <div className="setup-option-label">Personal Access Token</div>
                <div className="setup-option-desc">For advanced users</div>
              </button>
            </div>

            {githubMode === "oauth" && (
              <div className="setup-oauth-info">
                <p>Click Continue to sign in with GitHub. You'll be able to select which repositories GitPilot can access.</p>
              </div>
            )}

            {githubMode === "pat" && (
              <div className="setup-form-group">
                <label className="setup-label">Personal Access Token</label>
                <input
                  type="password"
                  className="setup-input"
                  value={githubToken}
                  onChange={(e) => setGithubToken(e.target.value)}
                  placeholder="ghp_xxxxxxxxxxxx"
                />
                <p className="setup-hint">
                  Create a token at{" "}
                  <a href="https://github.com/settings/tokens/new?scopes=repo&description=GitPilot" target="_blank" rel="noopener noreferrer">
                    github.com/settings/tokens
                  </a>{" "}
                  with <code>repo</code> scope
                </p>
              </div>
            )}

            <div className="setup-actions">
              <button className="setup-btn-secondary" onClick={() => setStep(1)}>
                Back
              </button>
              <button
                className="setup-btn-primary"
                onClick={() => {
                  if (githubMode === "oauth") {
                    handleGitHubOAuthLogin();
                  } else {
                    setStep(3);
                  }
                }}
                disabled={githubMode === "pat" && !githubToken}
              >
                Continue
              </button>
            </div>
          </div>
        )}

        {step === 3 && (
          <div className="setup-step">
            <div className="setup-icon">🤖</div>
            <h1 className="setup-title">Configure AI</h1>
            <p className="setup-description">Select your preferred AI provider</p>

            <div className="setup-provider-grid">
              {["openai", "claude", "watsonx", "ollama"].map((provider) => (
                <button
                  key={provider}
                  className={`setup-provider-btn ${llmConfig.provider === provider ? "active" : ""}`}
                  onClick={() => setLlmConfig({ ...llmConfig, provider })}
                >
                  {provider === "openai" && "OpenAI"}
                  {provider === "claude" && "Claude"}
                  {provider === "watsonx" && "Watsonx"}
                  {provider === "ollama" && "Ollama"}
                </button>
              ))}
            </div>

            <div className="setup-provider-config">
              {llmConfig.provider === "openai" && (
                <>
                  <div className="setup-form-group">
                    <label className="setup-label">API Key</label>
                    <input
                      type="password"
                      className="setup-input"
                      value={currentProvider.api_key}
                      onChange={(e) =>
                        setLlmConfig({
                          ...llmConfig,
                          openai: { ...currentProvider, api_key: e.target.value },
                        })
                      }
                      placeholder="sk-xxxxxxxxxxxx"
                    />
                    <p className="setup-hint">
                      Get your API key from{" "}
                      <a href="https://platform.openai.com/api-keys" target="_blank" rel="noopener noreferrer">
                        platform.openai.com
                      </a>
                    </p>
                  </div>
                </>
              )}

              {llmConfig.provider === "claude" && (
                <>
                  <div className="setup-form-group">
                    <label className="setup-label">API Key</label>
                    <input
                      type="password"
                      className="setup-input"
                      value={currentProvider.api_key}
                      onChange={(e) =>
                        setLlmConfig({
                          ...llmConfig,
                          claude: { ...currentProvider, api_key: e.target.value },
                        })
                      }
                      placeholder="sk-ant-xxxxxxxxxxxx"
                    />
                    <p className="setup-hint">
                      Get your API key from{" "}
                      <a href="https://console.anthropic.com/" target="_blank" rel="noopener noreferrer">
                        console.anthropic.com
                      </a>
                    </p>
                  </div>
                </>
              )}

              {llmConfig.provider === "watsonx" && (
                <>
                  <div className="setup-form-group">
                    <label className="setup-label">API Key</label>
                    <input
                      type="password"
                      className="setup-input"
                      value={currentProvider.api_key}
                      onChange={(e) =>
                        setLlmConfig({
                          ...llmConfig,
                          watsonx: { ...currentProvider, api_key: e.target.value },
                        })
                      }
                      placeholder="Your Watsonx API key"
                    />
                  </div>
                  <div className="setup-form-group">
                    <label className="setup-label">Project ID</label>
                    <input
                      type="text"
                      className="setup-input"
                      value={currentProvider.project_id}
                      onChange={(e) =>
                        setLlmConfig({
                          ...llmConfig,
                          watsonx: { ...currentProvider, project_id: e.target.value },
                        })
                      }
                      placeholder="your-project-id"
                    />
                  </div>
                  <div className="setup-form-group">
                    <label className="setup-label">Watsonx URL</label>
                    <input
                      type="text"
                      className="setup-input"
                      value={currentProvider.base_url}
                      onChange={(e) =>
                        setLlmConfig({
                          ...llmConfig,
                          watsonx: { ...currentProvider, base_url: e.target.value },
                        })
                      }
                      placeholder="https://us-south.ml.cloud.ibm.com"
                    />
                    <p className="setup-hint">Default: https://us-south.ml.cloud.ibm.com</p>
                  </div>
                </>
              )}

              {llmConfig.provider === "ollama" && (
                <div className="setup-ollama-info">
                  <p>✅ Ollama uses local models - no API key needed</p>
                  <p className="setup-hint">Make sure Ollama is running at {currentProvider.base_url}</p>
                </div>
              )}
            </div>

            {error && <div className="setup-error">{error}</div>}

            <div className="setup-actions">
              <button className="setup-btn-secondary" onClick={() => setStep(2)}>
                Back
              </button>
              <button className="setup-btn-link" onClick={handleSkipSetup} disabled={saving}>
                Skip for now
              </button>
              <button
                className="setup-btn-primary"
                onClick={handleSaveAndComplete}
                disabled={!canProceedLLM || saving}
              >
                {saving ? "Saving..." : "Finish Setup"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
