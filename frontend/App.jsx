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
  const [activePage, setActivePage] = useState("workspace");
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [userInfo, setUserInfo] = useState(null);

  // Repo + Session State Machine
  const [repoStateByKey, setRepoStateByKey] = useState({});
  const [toast, setToast] = useState(null);

  const repoKey = useMemo(() => makeRepoKey(repo), [repo]);

  // Convenient selectors
  const currentRepoState = repoKey ? repoStateByKey[repoKey] : null;

  const defaultBranch = currentRepoState?.defaultBranch || repo?.default_branch || "main";
  const currentBranch = currentRepoState?.currentBranch || defaultBranch;
  const sessionBranches = currentRepoState?.sessionBranches || [];
  const lastExecution = currentRepoState?.lastExecution || null;
  const pulseNonce = currentRepoState?.pulseNonce || 0;
  const chatByBranch = currentRepoState?.chatByBranch || {};

  // Init / reconcile repo state
  useEffect(() => {
    if (!repoKey || !repo) return;

    setRepoStateByKey((prev) => {
      const existing = prev[repoKey];
      const d = repo.default_branch || "main";

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
              [d]: { messages: [], plan: null },
            },
          },
        };
      }

      const next = { ...existing };
      next.defaultBranch = d;

      if (!next.chatByBranch?.[d]) {
        next.chatByBranch = {
          ...(next.chatByBranch || {}),
          [d]: { messages: [], plan: null },
        };
      }

      if (!next.currentBranch) next.currentBranch = d;

      return { ...prev, [repoKey]: next };
    });
  }, [repoKey, repo?.id, repo?.default_branch]);

  const showToast = (title, message) => {
    setToast({ title, message });
    window.setTimeout(() => setToast(null), 5000);
  };

  // ---------------------------------------------------------------------------
  // Chat persistence helpers
  // ---------------------------------------------------------------------------
  const updateChatForCurrentBranch = (patch) => {
    if (!repoKey) return;

    setRepoStateByKey((prev) => {
      const cur = prev[repoKey];
      if (!cur) return prev;

      const branchKey = cur.currentBranch || cur.defaultBranch || defaultBranch;

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

      // If switching BACK to main/default -> clear main chat (new task start)
      if (nextBranch === cur.defaultBranch) {
        nextState.chatByBranch = {
          ...nextState.chatByBranch,
          [nextBranch]: { messages: [], plan: null },
        };
      }

      return { ...prev, [repoKey]: nextState };
    });

    if (nextBranch === defaultBranch) {
      showToast("New Session", `Switched to ${defaultBranch}. Chat cleared.`);
    } else {
      showToast("Context Switched", `Now viewing ${nextBranch}.`);
    }
  };

  // ---------------------------------------------------------------------------
  // Execution complete
  // ---------------------------------------------------------------------------
  // ✅ FIX: accept completionMsg and seed branch with it
  const handleExecutionComplete = ({
    branch,
    mode,
    commit_url,
    message,
    completionMsg,
    sourceBranch,
  }) => {
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

      if (!next.chatByBranch) next.chatByBranch = {};

      // Determine the branch we executed from (best-effort, but stable)
      const prevBranchKey =
        sourceBranch || cur.currentBranch || cur.defaultBranch || defaultBranch;

      const successSystemMsg = {
        role: "system",
        isSuccess: true,
        link: commit_url,
        content:
          mode === "hard-switch"
            ? `🌱 **Session Started:** Created branch \`${branch}\`.`
            : `✅ **Update Published:** Commits pushed to \`${branch}\`.`,
      };

      // Make sure completionMsg is valid and normalized
      const normalizedCompletion =
        completionMsg && (completionMsg.answer || completionMsg.content || completionMsg.executionLog)
          ? {
              from: completionMsg.from || "ai",
              role: completionMsg.role || "assistant",
              answer: completionMsg.answer,
              content: completionMsg.content,
              executionLog: completionMsg.executionLog,
            }
          : null;

      if (mode === "hard-switch") {
        // 1) Register branch
        next.sessionBranches = uniq([...(next.sessionBranches || []), branch]);

        // 2) Switch UI context
        next.currentBranch = branch;
        next.pulseNonce = (next.pulseNonce || 0) + 1;

        // 3) Handle chat history seeding/appending (✅ FIX)
        const existingTargetChat = next.chatByBranch[branch];
        const isExistingSession =
          existingTargetChat && (existingTargetChat.messages || []).length > 0;

        if (isExistingSession) {
          // Existing branch: append completion + success message
          const appended = [
            ...(existingTargetChat.messages || []),
            ...(normalizedCompletion ? [normalizedCompletion] : []),
            successSystemMsg,
          ];

          next.chatByBranch[branch] = {
            ...existingTargetChat,
            messages: appended,
            plan: null,
          };
        } else {
          // New branch: seed from previous branch history + completion + success
          const prevChat =
            (cur.chatByBranch && cur.chatByBranch[prevBranchKey]) || { messages: [], plan: null };

          next.chatByBranch[branch] = {
            messages: [
              ...(prevChat.messages || []),
              ...(normalizedCompletion ? [normalizedCompletion] : []),
              successSystemMsg,
            ],
            plan: null,
          };
        }

        // Ensure default branch bucket exists
        if (!next.chatByBranch[next.defaultBranch]) {
          next.chatByBranch[next.defaultBranch] = { messages: [], plan: null };
        }
      } else if (mode === "sticky") {
        // Sticky mode: stay on current branch, update that branch history
        next.currentBranch = cur.currentBranch || branch;

        const targetChat = next.chatByBranch[branch] || { messages: [], plan: null };

        next.chatByBranch[branch] = {
          messages: [
            ...(targetChat.messages || []),
            ...(normalizedCompletion ? [normalizedCompletion] : []),
            successSystemMsg,
          ],
          plan: null,
        };
      }

      return { ...prev, [repoKey]: next };
    });

    if (mode === "hard-switch") {
      showToast("Context Switched", `Active on ${branch}.`);
    } else {
      showToast("Changes Committed", `Updated ${branch}.`);
    }
  };

  // ---------------------------------------------------------------------------
  // Auth & Render
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
        console.error(err);
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

  if (isLoading)
    return (
      <div className="app-root">
        <div className="loading-spinner"></div>
      </div>
    );

  if (!isAuthenticated) return <LoginPage onAuthenticated={handleAuthenticated} />;

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
              className={"nav-btn" + (activePage === "workspace" ? " nav-btn-active" : "")}
              onClick={() => setActivePage("workspace")}
            >
              📁 Workspace
            </button>
            <button
              className={"nav-btn" + (activePage === "flow" ? " nav-btn-active" : "")}
              onClick={() => setActivePage("flow")}
            >
              🔄 Agent Flow
            </button>
            <button
              className={"nav-btn" + (activePage === "admin" ? " nav-btn-active" : "")}
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
                <img src={userInfo.avatar_url} alt={userInfo.login} className="user-avatar" />
                <div className="user-info">
                  <div className="user-name">{userInfo.name || userInfo.login}</div>
                  <div className="user-login">@{userInfo.login}</div>
                </div>
              </div>
              <button className="btn-logout" onClick={handleLogout}>
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
                <p>Select a repo to begin agentic workflow.</p>
              </div>
            ))}
        </main>
      </div>

      <Footer />

      {toast && (
        <div className="toast-notification">
          <div style={{ fontSize: 12, fontWeight: 700 }}>{toast.title}</div>
          <div style={{ fontSize: 12, opacity: 0.82 }}>{toast.message}</div>
        </div>
      )}

      <style>{`
        .toast-notification {
          position: fixed;
          top: 72px;
          right: 18px;
          z-index: 9999;
          background: #0b0b0d;
          color: #EDEDED;
          border: 1px solid rgba(255,255,255,0.12);
          border-left: 3px solid #3B82F6;
          border-radius: 10px;
          padding: 12px 14px;
          min-width: 320px;
          box-shadow: 0 10px 30px rgba(0,0,0,0.4);
        }
      `}</style>
    </div>
  );
}
