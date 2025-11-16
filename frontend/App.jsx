import React, { useState, useEffect } from "react";
import GithubConnectPanel from "./components/GithubConnectPanel.jsx";
import RepoSelector from "./components/RepoSelector.jsx";
import ProjectContextPanel from "./components/ProjectContextPanel.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import LlmSettings from "./components/LlmSettings.jsx";
import FlowViewer from "./components/FlowViewer.jsx";
import Footer from "./components/Footer.jsx";
import SetupWizard from "./components/SetupWizard.jsx";

export default function App() {
  const [repo, setRepo] = useState(null);
  const [activePage, setActivePage] = useState("workspace"); // "workspace" | "admin" | "flow"
  const [showSetupWizard, setShowSetupWizard] = useState(false);
  const [settingsLoading, setSettingsLoading] = useState(true);

  // Check if setup is needed on mount
  useEffect(() => {
    checkSetupStatus();
  }, []);

  const checkSetupStatus = async () => {
    try {
      setSettingsLoading(true);
      const res = await fetch("/api/settings");
      if (res.ok) {
        const data = await res.json();
        // Show wizard if setup not completed
        setShowSetupWizard(!data.setup_completed);
      }
    } catch (e) {
      console.error("Failed to load settings:", e);
      // On error, show wizard to allow setup
      setShowSetupWizard(true);
    } finally {
      setSettingsLoading(false);
    }
  };

  const handleSetupComplete = () => {
    setShowSetupWizard(false);
    // Optionally reload the page to refresh all components with new settings
    window.location.reload();
  };

  // Show loading state while checking setup status
  if (settingsLoading) {
    return (
      <div className="app-root">
        <div className="loading-screen">
          <div className="loading-spinner" />
          <p>Loading GitPilot...</p>
        </div>
      </div>
    );
  }

  // Show setup wizard if needed
  if (showSetupWizard) {
    return <SetupWizard onComplete={handleSetupComplete} />;
  }

  return (
    <div className="app-root">
      <div className="main-wrapper">
        <aside className="sidebar">
          <div className="logo-row">
            <div className="logo-square">GP</div>
            <div>
              <div className="logo-title">GitPilot</div>
              <div className="logo-subtitle">Agentic GitHub copilot</div>
            </div>
          </div>

          <div className="main-nav">
            <button
              type="button"
              className={
                "nav-btn" + (activePage === "workspace" ? " nav-btn-active" : "")
              }
              onClick={() => setActivePage("workspace")}
            >
              📁 Workspace
            </button>
            <button
              type="button"
              className={
                "nav-btn" + (activePage === "flow" ? " nav-btn-active" : "")
              }
              onClick={() => setActivePage("flow")}
            >
              🔄 Agent Flow
            </button>
            <button
              type="button"
              className={
                "nav-btn" + (activePage === "admin" ? " nav-btn-active" : "")
              }
              onClick={() => setActivePage("admin")}
            >
              ⚙️ Admin / Settings
            </button>
          </div>

          {activePage === "workspace" && (
            <>
              <GithubConnectPanel />

              <div className="sidebar-section">
                <div className="sidebar-section-header">
                  <span className="sidebar-section-title">Repository</span>
                </div>
                <RepoSelector onSelect={setRepo} />
              </div>

              {repo && (
                <div className="sidebar-repo-info">
                  <div className="sidebar-repo-name">{repo.full_name}</div>
                  <div className="sidebar-repo-meta">
                    {repo.private ? "Private" : "Public"} repository
                  </div>
                </div>
              )}
            </>
          )}
        </aside>

        <main className="workspace">
          {activePage === "admin" && <LlmSettings />}

          {activePage === "flow" && <FlowViewer />}

          {activePage === "workspace" &&
            (repo ? (
              <div className="workspace-grid">
                <aside className="gp-context-column">
                  <ProjectContextPanel repo={repo} />
                </aside>
                <main className="gp-chat-column">
                  <div className="panel-header">
                    <span>GitPilot chat</span>
                  </div>
                  <ChatPanel repo={repo} />
                </main>
              </div>
            ) : (
              <div className="empty-state">
                <div className="empty-bot">🤖</div>
                <h1>Select a repository</h1>
                <p>
                  Use the selector on the left to pick a GitHub repo. Then chat
                  with GitPilot: it will propose a plan before applying any code
                  changes.
                </p>
              </div>
            ))}
        </main>
      </div>
      <Footer />
    </div>
  );
}