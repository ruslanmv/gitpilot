import React, { useState, useEffect } from "react";
import RepoSelector from "./components/RepoSelector.jsx";
import ProjectContextPanel from "./components/ProjectContextPanel.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import LlmSettings from "./components/LlmSettings.jsx";
import FlowViewer from "./components/FlowViewer.jsx";
import Footer from "./components/Footer.jsx";
import WelcomePage from "./components/WelcomePage.jsx";
import SetupWizard from "./components/SetupWizard.jsx";
import UserMenu from "./components/UserMenu.jsx";

export default function App() {
  const [repo, setRepo] = useState(null);
  const [activePage, setActivePage] = useState("workspace"); // "workspace" | "admin" | "flow"
  const [authState, setAuthState] = useState({
    loading: true,
    authenticated: false,
    user: null,
    setupCompleted: false,
  });

  useEffect(() => {
    checkAuthStatus();
  }, []);

  const checkAuthStatus = async () => {
    try {
      const res = await fetch("/api/auth/status");
      if (res.ok) {
        const data = await res.json();
        setAuthState({
          loading: false,
          authenticated: data.authenticated,
          user: data.user,
          setupCompleted: data.setup_completed,
        });
      } else {
        setAuthState({
          loading: false,
          authenticated: false,
          user: null,
          setupCompleted: false,
        });
      }
    } catch (e) {
      console.error("Failed to check auth status:", e);
      setAuthState({
        loading: false,
        authenticated: false,
        user: null,
        setupCompleted: false,
      });
    }
  };

  const handleLoginSuccess = (data) => {
    setAuthState({
      loading: false,
      authenticated: true,
      user: data.user,
      setupCompleted: data.setup_completed,
    });
  };

  const handleLogout = () => {
    setAuthState({
      loading: false,
      authenticated: false,
      user: null,
      setupCompleted: false,
    });
  };

  const handleSetupComplete = () => {
    setAuthState({
      ...authState,
      setupCompleted: true,
    });
  };

  // Show loading state
  if (authState.loading) {
    return (
      <div className="app-loading">
        <div className="loading-spinner"></div>
        <p>Loading GitPilot...</p>
      </div>
    );
  }

  // Show welcome/login page if not authenticated
  if (!authState.authenticated) {
    return <WelcomePage onLoginSuccess={handleLoginSuccess} />;
  }

  // Show setup wizard if not completed
  if (!authState.setupCompleted) {
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

          {authState.user && (
            <div className="sidebar-user-section">
              <UserMenu user={authState.user} onLogout={handleLogout} />
            </div>
          )}

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
              <RepoSelector onSelect={setRepo} />

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