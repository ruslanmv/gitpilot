// frontend/App.jsx
import React, { useEffect, useMemo, useState } from "react";
import LoginPage from "./components/LoginPage.jsx";
import RepoSelector from "./components/RepoSelector.jsx";
import ProjectContextPanel from "./components/ProjectContextPanel.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import LlmSettings from "./components/LlmSettings.jsx";
import FlowViewer from "./components/FlowViewer.jsx";
import Footer from "./components/Footer.jsx";

function makeRepoKey(repo) {
  if (!repo) return null;
  return repo.full_name || `${repo.owner}/${repo.name}`;
}

function uniq(arr) {
  return Array.from(new Set((arr || []).filter(Boolean)));
}

export default function App() {
  const [repo, setRepo] = useState(null);
  const [activePage, setActivePage] = useState("workspace"); // "workspace" | "admin" | "flow"
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [userInfo, setUserInfo] = useState(null);

  // ---------------------------------------------------------------------------
  // Repo + Session State Machine (per repo)
  // ---------------------------------------------------------------------------
  // We store state PER REPO so switching repos doesn't blow away sessions.
  //
  // shape:
  // repoStateByKey[repoKey] = {
  //   defaultBranch: "main" | "master" | ...
  //   currentBranch: string
  //   sessionBranches: string[]  // all AI branches created in this repo during this app lifetime
  //   lastExecution: { mode, branch, ts } | null
  //   pulseNonce: number
  //   chatByBranch: { [branchName]: { messages: [], plan: any } }
  // }
  const [repoStateByKey, setRepoStateByKey] = useState({});
  const [toast, setToast] = useState(null); // { title, message }

  const repoKey = useMemo(() => makeRepoKey(repo), [repo]);

  // Convenient selectors for current repo state
  const currentRepoState = repoKey ? repoStateByKey[repoKey] : null;

  const defaultBranch =
    currentRepoState?.defaultBranch || repo?.default_branch || "main";
  const currentBranch = currentRepoState?.currentBranch || defaultBranch;
  const sessionBranches = currentRepoState?.sessionBranches || [];
  const lastExecution = currentRepoState?.lastExecution || null;
  const pulseNonce = currentRepoState?.pulseNonce || 0;
  const chatByBranch = currentRepoState?.chatByBranch || {};

  // Init / reconcile repo state when repo changes / first selected
  useEffect(() => {
    if (!repoKey || !repo) return;

    setRepoStateByKey((prev) => {
      const existing = prev[repoKey];
      const d = repo.default_branch || "main";

      // If repo has never been seen in this app lifetime, initialize it
      if (!existing) {
        return {
          ...prev,
          [repoKey]: {
            defaultBranch: d,
            currentBranch: d,
            sessionBranches: [],
            lastExecution: null,
            pulseNonce: 0,
            chatByBranch: {
              // Persist production chat bucket too (no surprise deletes)
              [d]: { messages: [], plan: null },
            },
          },
        };
      }

      // Reconcile defaultBranch if it changed
      const next = { ...existing };
      next.defaultBranch = d;

      // Ensure default branch chat bucket exists
      if (!next.chatByBranch?.[d]) {
        next.chatByBranch = {
          ...(next.chatByBranch || {}),
          [d]: { messages: [], plan: null },
        };
      }

      // Ensure currentBranch is set (fallback)
      if (!next.currentBranch) next.currentBranch = d;

      return { ...prev, [repoKey]: next };
    });
  }, [repoKey, repo?.id, repo?.default_branch]);

  const showToast = (title, message) => {
    setToast({ title, message });
    window.setTimeout(() => setToast(null), 5000);
  };

  // ---------------------------------------------------------------------------
  // Chat persistence helpers (per repo + per branch)
  // ---------------------------------------------------------------------------
  const updateChatForCurrentBranch = (patch) => {
    if (!repoKey) return;

    setRepoStateByKey((prev) => {
      const cur = prev[repoKey];
      if (!cur) return prev;

      const branchKey =
        cur.currentBranch || cur.defaultBranch || defaultBranch;
      const existing = cur.chatByBranch?.[branchKey] || {
        messages: [],
        plan: null,
      };

      return {
        ...prev,
        [repoKey]: {
          ...cur,
          chatByBranch: {
            ...(cur.chatByBranch || {}),
            [branchKey]: { ...existing, ...patch },
          },
        },
      };
    });
  };

  const currentChatState = useMemo(() => {
    const b = currentBranch || defaultBranch;
    return chatByBranch[b] || { messages: [], plan: null };
  }, [chatByBranch, currentBranch, defaultBranch]);

  // ---------------------------------------------------------------------------
  // Branch change (manual)
  // ---------------------------------------------------------------------------
  const handleBranchChange = (nextBranch) => {
    if (!repoKey) return;
    if (!nextBranch || nextBranch === currentBranch) return;

    setRepoStateByKey((prev) => {
      const cur = prev[repoKey];
      if (!cur) return prev;

      const nextState = { ...cur, currentBranch: nextBranch };

      // LOGIC FIX: If switching BACK to Main/Default -> CLEAR CHAT (Start fresh)
      if (nextBranch === cur.defaultBranch) {
        nextState.chatByBranch = {
          ...nextState.chatByBranch,
          [nextBranch]: { messages: [], plan: null } // Wipe history for main
        };
      }
      // LOGIC: If switching to AI branch -> History is preserved in 'chatByBranch' automatically

      return { ...prev, [repoKey]: nextState };
    });

    // UX nudge only (no destructive behavior)
    if (nextBranch === defaultBranch) {
      showToast(
        "New Session",
        `Switched to ${defaultBranch}. Chat cleared for new task.`
      );
    } else {
      showToast("Context Switched", `Now viewing ${nextBranch}.`);
    }
  };

  // ---------------------------------------------------------------------------
  // Execution complete (from ChatPanel)
  // Implements:
  // - Hard Switch: from default branch -> AI branch created, auto-switch UI to it
  // - Sticky Context: already on AI branch -> commit to same branch, stay there
  //
  // CRITICAL FIX:
  // ✅ DO NOT reset chat after hard-switch.
  // ✅ Keep prior conversation and restore per branch.
  // ✅ Inject System Success Message with Link
  // ---------------------------------------------------------------------------
  const handleExecutionComplete = ({ branch, mode, commit_url, message }) => {
    if (!repoKey || !branch) return;

    setRepoStateByKey((prev) => {
      const cur =
        prev[repoKey] || {
          defaultBranch,
          currentBranch: defaultBranch,
          sessionBranches: [],
          lastExecution: null,
          pulseNonce: 0,
          chatByBranch: { [defaultBranch]: { messages: [], plan: null } },
        };

      const next = { ...cur };
      next.lastExecution = { mode, branch, ts: Date.now() };

      if (mode === "hard-switch") {
        // Track all AI session branches for this repo
        next.sessionBranches = uniq([...(next.sessionBranches || []), branch]);

        // Auto-follow AI execution context (visual context = execution context)
        next.currentBranch = branch;

        // Pulse header
        next.pulseNonce = (next.pulseNonce || 0) + 1;

        // Ensure chat bucket exists for new branch.
        // Preserve chat continuity by seeding new branch with the previous branch chat
        const prevBranchKey =
          cur.currentBranch || cur.defaultBranch || defaultBranch;
        const prevChat =
          (cur.chatByBranch && cur.chatByBranch[prevBranchKey]) || {
            messages: [],
            plan: null,
          };

        if (!next.chatByBranch) next.chatByBranch = {};
        
        // Seed new branch with history + System Success Message
        next.chatByBranch[branch] = { 
            messages: [
                ...prevChat.messages, 
                { 
                    role: "system", 
                    content: `🌱 **Session Started:** Created branch \`${branch}\`.`,
                    isSuccess: true,
                    link: commit_url
                }
            ], 
            plan: null // Clear plan UI after execution
        };

        // Ensure production bucket exists too
        if (!next.chatByBranch[next.defaultBranch]) {
          next.chatByBranch[next.defaultBranch] = { messages: [], plan: null };
        }
      } else if (mode === "sticky") {
        // Stay on current branch; backend may also echo branch
        next.currentBranch = cur.currentBranch || branch;
        
        // Append Success Message to current history
        const existingChat = (next.chatByBranch && next.chatByBranch[branch]) || { messages: [] };
        
        // Ensure chatByBranch exists
        if (!next.chatByBranch) next.chatByBranch = {};
        
        next.chatByBranch[branch] = {
            messages: [
                ...existingChat.messages,
                {
                    role: "system",
                    content: `✅ **Update Published:** Commits pushed to \`${branch}\`.`,
                    isSuccess: true,
                    link: commit_url
                }
            ],
            plan: null // Clear plan UI
        };
      }

      return { ...prev, [repoKey]: next };
    });

    if (mode === "hard-switch") {
      showToast(
        "Context Switched",
        `Changes pushed to new branch ${branch}. Switched current session to this branch.`
      );
    } else {
      showToast("Changes Committed", `Updated ${branch} with new commits.`);
    }
  };

  // ---------------------------------------------------------------------------
  // Authentication
  // ---------------------------------------------------------------------------
  useEffect(() => {
    checkAuthentication();
  }, []);

  const checkAuthentication = async () => {
    const token = localStorage.getItem("github_token");
    const user = localStorage.getItem("github_user");

    if (token && user) {
      try {
        const response = await fetch("/api/auth/validate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
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
        console.error("Token validation failed:", err);
      }

      localStorage.removeItem("github_token");
      localStorage.removeItem("github_user");
    }

    setIsAuthenticated(false);
    setIsLoading(false);
  };

  const handleAuthenticated = (session) => {
    setIsAuthenticated(true);
    setUserInfo(session.user);
  };

  const handleLogout = () => {
    localStorage.removeItem("github_token");
    localStorage.removeItem("github_user");
    setIsAuthenticated(false);
    setUserInfo(null);
    setRepo(null);
  };

  // Loading state
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

  // Not authenticated
  if (!isAuthenticated) {
    return <LoginPage onAuthenticated={handleAuthenticated} />;
  }

  // Main app
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

          {userInfo && (
            <div className="user-profile">
              <div className="user-profile-header">
                <img
                  src={userInfo.avatar_url}
                  alt={userInfo.login}
                  className="user-avatar"
                />
                <div className="user-info">
                  <div className="user-name">
                    {userInfo.name || userInfo.login}
                  </div>
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
                    // IMPORTANT: show ALL session branches, not just the last one
                    sessionBranches={sessionBranches}
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