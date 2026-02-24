// frontend/App.jsx
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import LoginPage from "./components/LoginPage.jsx";
import RepoSelector from "./components/RepoSelector.jsx";
import ProjectContextPanel from "./components/ProjectContextPanel.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import LlmSettings from "./components/LlmSettings.jsx";
import FlowViewer from "./components/FlowViewer.jsx";
import Footer from "./components/Footer.jsx";
import ProjectSettingsModal from "./components/ProjectSettingsModal.jsx";
import SessionSidebar from "./components/SessionSidebar.jsx";
import ContextBar from "./components/ContextBar.jsx";
import AddRepoModal from "./components/AddRepoModal.jsx";
import { apiUrl, safeFetchJSON } from "./utils/api.js";

function makeRepoKey(repo) {
  if (!repo) return null;
  return repo.full_name || `${repo.owner}/${repo.name}`;
}

function uniq(arr) {
  return Array.from(new Set((arr || []).filter(Boolean)));
}

export default function App() {
  // ---- Multi-repo context state ----
  const [contextRepos, setContextRepos] = useState([]);
  // Each entry: { repoKey: "owner/repo", repo: {...}, branch: "main" }
  const [activeRepoKey, setActiveRepoKey] = useState(null);
  const [addRepoOpen, setAddRepoOpen] = useState(false);

  const [activePage, setActivePage] = useState("workspace");
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [userInfo, setUserInfo] = useState(null);

  // Repo + Session State Machine
  const [repoStateByKey, setRepoStateByKey] = useState({});
  const [toast, setToast] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  // Claude-Code-on-Web: Session sidebar + Environment state
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [activeEnvId, setActiveEnvId] = useState("default");
  const [sessionRefreshNonce, setSessionRefreshNonce] = useState(0);

  // ---- Derived `repo` — keeps all downstream consumers unchanged ----
  const repo = useMemo(() => {
    const entry = contextRepos.find((r) => r.repoKey === activeRepoKey);
    return entry?.repo || null;
  }, [contextRepos, activeRepoKey]);

  const repoKey = activeRepoKey;

  // Convenient selectors
  const currentRepoState = repoKey ? repoStateByKey[repoKey] : null;

  const defaultBranch = currentRepoState?.defaultBranch || repo?.default_branch || "main";
  const currentBranch = currentRepoState?.currentBranch || defaultBranch;
  const sessionBranches = currentRepoState?.sessionBranches || [];
  const lastExecution = currentRepoState?.lastExecution || null;
  const pulseNonce = currentRepoState?.pulseNonce || 0;
  const chatByBranch = currentRepoState?.chatByBranch || {};

  // ---------------------------------------------------------------------------
  // Multi-repo context management
  // ---------------------------------------------------------------------------
  const addRepoToContext = useCallback((r) => {
    const key = makeRepoKey(r);
    if (!key) return;

    setContextRepos((prev) => {
      // Don't add duplicates
      if (prev.some((e) => e.repoKey === key)) {
        // Already in context — just activate it
        setActiveRepoKey(key);
        return prev;
      }
      const entry = { repoKey: key, repo: r, branch: r.default_branch || "main" };
      const next = [...prev, entry];
      return next;
    });
    setActiveRepoKey(key);
    setAddRepoOpen(false);
  }, []);

  const removeRepoFromContext = useCallback((key) => {
    setContextRepos((prev) => {
      const next = prev.filter((e) => e.repoKey !== key);
      // Reassign active if we removed the active one
      setActiveRepoKey((curActive) => {
        if (curActive === key) {
          return next.length > 0 ? next[0].repoKey : null;
        }
        return curActive;
      });
      return next;
    });
  }, []);

  const clearAllContext = useCallback(() => {
    setContextRepos([]);
    setActiveRepoKey(null);
  }, []);

  const handleContextBranchChange = useCallback((targetRepoKey, newBranch) => {
    // Update branch in contextRepos
    setContextRepos((prev) =>
      prev.map((e) =>
        e.repoKey === targetRepoKey ? { ...e, branch: newBranch } : e
      )
    );
    // Update branch in repoStateByKey
    setRepoStateByKey((prev) => {
      const cur = prev[targetRepoKey];
      if (!cur) return prev;
      return {
        ...prev,
        [targetRepoKey]: { ...cur, currentBranch: newBranch },
      };
    });
  }, []);

  // Init / reconcile repo state when active repo changes
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
  // Session management — every chat is backed by a Session (Claude Code parity)
  // ---------------------------------------------------------------------------

  // Guard against double-creation during concurrent send() calls
  const _creatingSessionRef = useRef(false);

  /**
   * ensureSession — Create a session on-demand (implicit).
   *
   * Called by ChatPanel before the first message is sent.  If a session
   * already exists it returns the current ID immediately.  Otherwise it
   * creates one, seeds the initial messages into chatBySession so the
   * useEffect reset doesn't wipe them, and returns the new ID.
   *
   * @param {string} [sessionName] — optional title (first user prompt, truncated)
   * @param {Array}  [seedMessages] — messages to pre-populate into the new session
   * @returns {Promise<string|null>} the session ID
   */
  const ensureSession = useCallback(async (sessionName, seedMessages) => {
    if (activeSessionId) return activeSessionId;
    if (!repo) return null;
    if (_creatingSessionRef.current) return null; // already in flight
    _creatingSessionRef.current = true;

    try {
      const token = localStorage.getItem("github_token");
      const headers = {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      };
      const res = await fetch("/api/sessions", {
        method: "POST",
        headers,
        body: JSON.stringify({
          repo_full_name: repoKey,
          branch: currentBranch,
          name: sessionName || undefined,
          repos: contextRepos.map((e) => ({
            full_name: e.repoKey,
            branch: e.branch,
            mode: e.repoKey === activeRepoKey ? "write" : "read",
          })),
          active_repo: activeRepoKey,
        }),
      });
      if (!res.ok) return null;
      const data = await res.json();
      const newId = data.session_id;

      // Seed the session's chat state BEFORE setting activeSessionId so
      // the ChatPanel useEffect sync picks up the messages instead of []
      if (seedMessages && seedMessages.length > 0) {
        setChatBySession((prev) => ({
          ...prev,
          [newId]: { messages: seedMessages, plan: null },
        }));
      }

      setActiveSessionId(newId);
      setSessionRefreshNonce((n) => n + 1);
      return newId;
    } catch (err) {
      console.warn("Failed to create session:", err);
      return null;
    } finally {
      _creatingSessionRef.current = false;
    }
  }, [activeSessionId, repo, repoKey, currentBranch, contextRepos, activeRepoKey]);

  // Explicit "New Session" button — clears chat and starts fresh
  const handleNewSession = async () => {
    // Clear the current session so ensureSession creates a new one
    setActiveSessionId(null);
    try {
      const token = localStorage.getItem("github_token");
      const headers = {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      };
      const res = await fetch("/api/sessions", {
        method: "POST",
        headers,
        body: JSON.stringify({
          repo_full_name: repoKey,
          branch: currentBranch,
          repos: contextRepos.map((e) => ({
            full_name: e.repoKey,
            branch: e.branch,
            mode: e.repoKey === activeRepoKey ? "write" : "read",
          })),
          active_repo: activeRepoKey,
        }),
      });
      if (!res.ok) return;
      const data = await res.json();
      setActiveSessionId(data.session_id);
      setSessionRefreshNonce((n) => n + 1);
      showToast("Session Created", `New session started.`);
    } catch (err) {
      console.warn("Failed to create session:", err);
    }
  };

  const handleSelectSession = (session) => {
    setActiveSessionId(session.id);
    if (session.branch && session.branch !== currentBranch) {
      handleBranchChange(session.branch);
    }
  };

  // When a session is deleted: if it was the active session, clear the
  // chat so the user returns to a fresh "new conversation" state.
  // Non-active session deletions only affect the sidebar (handled there).
  const handleDeleteSession = useCallback((deletedId) => {
    if (deletedId === activeSessionId) {
      setActiveSessionId(null);
      // Clean up the in-memory chat state for the deleted session
      setChatBySession((prev) => {
        const next = { ...prev };
        delete next[deletedId];
        return next;
      });
      // Also clear the branch-keyed chat (the persistence effect may have
      // written the first user message there before the session was created)
      if (repoKey) {
        setRepoStateByKey((prev) => {
          const cur = prev[repoKey];
          if (!cur) return prev;
          const branchKey = cur.currentBranch || cur.defaultBranch || defaultBranch;
          return {
            ...prev,
            [repoKey]: {
              ...cur,
              chatByBranch: {
                ...(cur.chatByBranch || {}),
                [branchKey]: { messages: [], plan: null },
              },
            },
          };
        });
      }
    }
  }, [activeSessionId, repoKey, defaultBranch]);

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
  // Session-scoped chat state: isolate messages per (session + branch) instead
  // of per-branch alone.  This prevents session A's messages from leaking into
  // session B when both sessions share the same branch.
  // ---------------------------------------------------------------------------
  const [chatBySession, setChatBySession] = useState({});

  const sessionChatState = useMemo(() => {
    if (!activeSessionId) {
      // No session — fall back to legacy branch-keyed chat
      return currentChatState;
    }
    return chatBySession[activeSessionId] || { messages: [], plan: null };
  }, [activeSessionId, chatBySession, currentChatState]);

  const updateSessionChat = (patch) => {
    if (activeSessionId) {
      setChatBySession((prev) => ({
        ...prev,
        [activeSessionId]: {
          ...(prev[activeSessionId] || { messages: [], plan: null }),
          ...patch,
        },
      }));
    } else {
      // No active session — use legacy branch-keyed persistence
      updateChatForCurrentBranch(patch);
    }
  };

  // ---------------------------------------------------------------------------
  // Branch change (manual — for active repo)
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

    // Also update contextRepos branch tracking
    setContextRepos((prev) =>
      prev.map((e) =>
        e.repoKey === repoKey ? { ...e, branch: nextBranch } : e
      )
    );

    if (nextBranch === defaultBranch) {
      showToast("New Session", `Switched to ${defaultBranch}. Chat cleared.`);
    } else {
      showToast("Context Switched", `Now viewing ${nextBranch}.`);
    }
  };

  // ---------------------------------------------------------------------------
  // Execution complete
  // ---------------------------------------------------------------------------
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
        next.sessionBranches = uniq([...(next.sessionBranches || []), branch]);
        next.currentBranch = branch;
        next.pulseNonce = (next.pulseNonce || 0) + 1;

        const existingTargetChat = next.chatByBranch[branch];
        const isExistingSession =
          existingTargetChat && (existingTargetChat.messages || []).length > 0;

        if (isExistingSession) {
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

        if (!next.chatByBranch[next.defaultBranch]) {
          next.chatByBranch[next.defaultBranch] = { messages: [], plan: null };
        }
      } else if (mode === "sticky") {
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
        const data = await safeFetchJSON(apiUrl("/api/auth/validate"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ access_token: token }),
        });
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
    clearAllContext();
  };

  if (isLoading)
    return (
      <div className="app-root">
        <div className="loading-spinner"></div>
      </div>
    );

  if (!isAuthenticated) return <LoginPage onAuthenticated={handleAuthenticated} />;

  const hasContext = contextRepos.length > 0;

  return (
    <div className="app-root">
      <div className="main-wrapper">
        <aside className="sidebar">
          {/* ---- Brand ---- */}
          <div className="logo-row">
            <div className="logo-square">GP</div>
            <div>
              <div className="logo-title">GitPilot</div>
              <div className="logo-subtitle">Agentic GitHub Copilot</div>
            </div>
          </div>

          {/* ---- Navigation ---- */}
          <div className="main-nav">
            <button
              className={"nav-btn" + (activePage === "workspace" ? " nav-btn-active" : "")}
              onClick={() => setActivePage("workspace")}
            >
              Workspace
            </button>
            <button
              className={"nav-btn" + (activePage === "flow" ? " nav-btn-active" : "")}
              onClick={() => setActivePage("flow")}
            >
              Agent Workflow
            </button>
            <button
              className={"nav-btn" + (activePage === "admin" ? " nav-btn-active" : "")}
              onClick={() => setActivePage("admin")}
            >
              Settings
            </button>
          </div>

          {/* ---- Repository Switcher (shown when no context) ---- */}
          {!hasContext && (
            <RepoSelector onSelect={(r) => addRepoToContext(r)} />
          )}

          {/* ---- Sessions ---- */}
          {repo && (
            <SessionSidebar
              repo={repo}
              activeSessionId={activeSessionId}
              onSelectSession={handleSelectSession}
              onNewSession={handleNewSession}
              onDeleteSession={handleDeleteSession}
              refreshNonce={sessionRefreshNonce}
            />
          )}

          {/* ---- User ---- */}
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
              <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
                {/* ---- Context Bar (single source of truth for repo selection) ---- */}
                <ContextBar
                  contextRepos={contextRepos}
                  activeRepoKey={activeRepoKey}
                  repoStateByKey={repoStateByKey}
                  onActivate={setActiveRepoKey}
                  onRemove={removeRepoFromContext}
                  onAdd={() => setAddRepoOpen(true)}
                  onBranchChange={handleContextBranchChange}
                />

                <div className="workspace-grid" style={{ flex: 1 }}>
                  <aside className="gp-context-column">
                    <ProjectContextPanel
                      repo={repo}
                      defaultBranch={defaultBranch}
                      currentBranch={currentBranch}
                      sessionBranches={sessionBranches}
                      onBranchChange={handleBranchChange}
                      pulseNonce={pulseNonce}
                      lastExecution={lastExecution}
                      onSettingsClick={() => setSettingsOpen(true)}
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
                      sessionChatState={sessionChatState}
                      onSessionChatStateChange={updateSessionChat}
                      sessionId={activeSessionId}
                      onEnsureSession={ensureSession}
                    />
                  </main>
                </div>
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

      {repo && (
        <ProjectSettingsModal
          owner={repo.full_name?.split("/")[0] || repo.owner}
          repo={repo.full_name?.split("/")[1] || repo.name}
          isOpen={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          activeEnvId={activeEnvId}
          onEnvChange={setActiveEnvId}
        />
      )}

      {/* Add Repo Modal */}
      <AddRepoModal
        isOpen={addRepoOpen}
        onSelect={addRepoToContext}
        onClose={() => setAddRepoOpen(false)}
        excludeKeys={contextRepos.map((e) => e.repoKey)}
      />

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
