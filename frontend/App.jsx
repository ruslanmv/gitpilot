import React, { useState, useEffect } from "react";
import LoginPage from "./components/LoginPage.jsx";
import RepoSelector from "./components/RepoSelector.jsx";
import ProjectContextPanel from "./components/ProjectContextPanel.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import LlmSettings from "./components/LlmSettings.jsx";
import FlowViewer from "./components/FlowViewer.jsx";
import Footer from "./components/Footer.jsx";

export default function App() {
  const [repo, setRepo] = useState(null);
  const [activePage, setActivePage] = useState("workspace"); // "workspace" | "admin" | "flow"
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [userInfo, setUserInfo] = useState(null);

  // ---------------------------------------------------------------------------
  // Branch/session state
  // ---------------------------------------------------------------------------
  // Visual Context (what the user is browsing) must match Execution Context (where AI writes).
  // - defaultBranch: production branch (main/master/etc)
  // - currentBranch: the branch currently displayed in the UI (file tree + context)
  // - sessionBranch: the AI-created branch for the current workflow session
  const [defaultBranch, setDefaultBranch] = useState("main");
  const [currentBranch, setCurrentBranch] = useState("main");
  const [sessionBranch, setSessionBranch] = useState(null); // e.g. gitpilot-...-417032
  const [toast, setToast] = useState(null); // { title, message }

  // Per-branch chat persistence.
  // - For main/defaultBranch: chat resets (fresh start).
  // - For AI branches: chat is saved/restored when user toggles branches.
  const [chatByBranch, setChatByBranch] = useState({});

  // Visual feedback triggers
  const [pulseNonce, setPulseNonce] = useState(0);
  const [lastExecution, setLastExecution] = useState(null); // { mode, branch, ts }

  // When repo changes, reset session state to the repo's default branch.
  useEffect(() => {
    if (!repo) return;
    const d = repo.default_branch || "main";
    setDefaultBranch(d);
    setCurrentBranch(d);
    setSessionBranch(null);

    // Reset chat state across repos.
    setChatByBranch({});
    setLastExecution(null);
    setPulseNonce(0);
  }, [repo?.full_name, repo?.default_branch]);

  const showToast = (title, message) => {
    setToast({ title, message });
    // auto-hide after 5s
    window.setTimeout(() => setToast(null), 5000);
  };

  // Called by ChatPanel after an execution finishes.
  // Implements:
  // - Rule 1 (Hard Switch) when starting from defaultBranch
  // - Rule 2 (Sticky Context) when already in a session branch
  const handleExecutionComplete = ({ branch, mode }) => {
    if (!branch) return;

    setLastExecution({ mode, branch, ts: Date.now() });

    if (mode === "hard-switch") {
      setSessionBranch(branch);
      setCurrentBranch(branch);
      setPulseNonce((n) => n + 1);
      showToast(
        "Context Switched",
        `Changes pushed to new branch ${branch}. Switched current session to this branch.`
      );
    } else if (mode === "sticky") {
      // No branch switch; keep currentBranch
      showToast("Updated", `New commits added to ${branch}.`);
    }
  };

  const handleBranchChange = (nextBranch) => {
    if (!nextBranch || nextBranch === currentBranch) return;
    setCurrentBranch(nextBranch);

    // Chat persistence rules:
    // - Switching to production (defaultBranch) resets chat (fresh start).
    // - Switching to an AI branch restores its chat.
    if (nextBranch === defaultBranch) {
      setChatByBranch((prev) => {
        const copy = { ...prev };
        delete copy[defaultBranch];
        return copy;
      });
    }

    // Optional nudge when user leaves the AI session branch.
    if (sessionBranch && nextBranch === defaultBranch) {
      showToast(
        "Viewing production branch",
        `You are viewing ${defaultBranch}. AI changes are on ${sessionBranch}. New prompts will start a fresh session from ${defaultBranch}.`
      );
    }
  };

  const updateChatForCurrentBranch = (patch) => {
    setChatByBranch((prev) => {
      const key = currentBranch || defaultBranch;
      const existing = prev[key] || { messages: [], plan: null };
      return { ...prev, [key]: { ...existing, ...patch } };
    });
  };

  const currentChatState =
    chatByBranch[currentBranch || defaultBranch] ||
    (currentBranch === defaultBranch
      ? { messages: [], plan: null, reset: true }
      : { messages: [], plan: null });

  // Check for existing authentication on mount
  useEffect(() => {
    checkAuthentication();
  }, []);

  const checkAuthentication = async () => {
    const token = localStorage.getItem('github_token');
    const user = localStorage.getItem('github_user');

    if (token && user) {
      try {
        // Validate the token is still valid
        const response = await fetch('/api/auth/validate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ access_token: token }),
        });

        const data = await response.json();

        if (data.authenticated) {
          setIsAuthenticated(true);
          setUserInfo(JSON.parse(user));
          setIsLoading(false);
          return;
        }
      } catch (err) {
        console.error('Token validation failed:', err);
      }

      // Token invalid, clear storage
      localStorage.removeItem('github_token');
      localStorage.removeItem('github_user');
    }

    setIsAuthenticated(false);
    setIsLoading(false);
  };

  const handleAuthenticated = (session) => {
    setIsAuthenticated(true);
    setUserInfo(session.user);
  };

  const handleLogout = () => {
    localStorage.removeItem('github_token');
    localStorage.removeItem('github_user');
    setIsAuthenticated(false);
    setUserInfo(null);
    setRepo(null);
  };

  // Show loading state while checking authentication
  if (isLoading) {
    return (
      <div className="app-root">
        <div className="login-page">
          <div className="login-container">
            <div className="loading-spinner"></div>
          </div>
        </div>
      </div>
    );
  }

  // Show login page if not authenticated
  if (!isAuthenticated) {
    return <LoginPage onAuthenticated={handleAuthenticated} />;
  }

  // Main application (authenticated)
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

          {/* User Profile Section */}
          {userInfo && (
            <div className="user-profile">
              <div className="user-profile-header">
                <img
                  src={userInfo.avatar_url}
                  alt={userInfo.login}
                  className="user-avatar"
                />
                <div className="user-info">
                  <div className="user-name">{userInfo.name || userInfo.login}</div>
                  <div className="user-login">@{userInfo.login}</div>
                </div>
              </div>
              <button
                type="button"
                className="btn-logout"
                onClick={handleLogout}
              >
                Logout
              </button>
            </div>
          )}
        </aside>

        <main className="workspace">
          {activePage === "admin" && <LlmSettings />}

          {activePage === "flow" && <FlowViewer />}

          {activePage === "workspace" &&
            (repo ? (
              <div className="workspace-grid">
                <aside className="gp-context-column">
                  <ProjectContextPanel
                    repo={repo}
                    defaultBranch={defaultBranch}
                    currentBranch={currentBranch}
                    sessionBranch={sessionBranch}
                    onBranchChange={handleBranchChange}
                    pulseNonce={pulseNonce}
                    lastExecution={lastExecution}
                  />
                </aside>
                <main className="gp-chat-column">
                  <div className="panel-header">
                    <span>GitPilot chat</span>
                  </div>
                  <ChatPanel
                    repo={repo}
                    defaultBranch={defaultBranch}
                    currentBranch={currentBranch}
                    onExecutionComplete={handleExecutionComplete}
                    sessionChatState={currentChatState}
                    onSessionChatStateChange={updateChatForCurrentBranch}
                  />
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

      {/* Toast */}
      {toast && (
        <div
          style={{
            position: "fixed",
            top: 72,
            right: 18,
            zIndex: 9999,
            background: "#0b0b0d",
            color: "#EDEDED",
            border: "1px solid rgba(255,255,255,0.12)",
            borderLeft: "3px solid #3B82F6",
            borderRadius: 10,
            padding: "12px 14px",
            minWidth: 320,
            boxShadow: "0 10px 30px rgba(0,0,0,0.4)",
          }}
          role="status"
          aria-live="polite"
        >
          <div style={{ fontSize: 12, fontWeight: 700 }}>{toast.title}</div>
          <div style={{ fontSize: 12, opacity: 0.82, marginTop: 2 }}>
            {toast.message}
          </div>
        </div>
      )}
    </div>
  );
}