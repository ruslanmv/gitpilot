import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import StartupScreen from "./components/StartupScreen.jsx";
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
import UserMenu from "./components/UserMenu.jsx";
import AboutModal from "./components/AboutModal.jsx";
import {
  WorkspaceModesTab,
  SecurityTab,
  IntegrationsTab,
  MCPServersTab,
  SkillsTab,
  SessionsTab,
  AdvancedTab,
} from "./components/AdminTabs";
import { apiUrl, safeFetchJSON, fetchStatus } from "./utils/api.js";
import { initApp } from "./utils/appInit.js";

function makeRepoKey(repo) {
  if (!repo) return null;
  return repo.full_name || `${repo.owner}/${repo.name}`;
}

function uniq(arr) {
  return Array.from(new Set((arr || []).filter(Boolean)));
}

function getProviderLabel(status) {
  if (!status) return "Checking...";
  return (
    status?.provider?.name ||
    status?.provider_name ||
    status?.provider?.provider ||
    "Checking..."
  );
}

function getBackendVersion(status) {
  if (!status) return "Checking...";
  return status?.version || status?.app_version || "Checking...";
}

export default function App() {
  const frontendVersion = __APP_VERSION__ || "unknown";

  // ---- Multi-repo context state ----
  const [contextRepos, setContextRepos] = useState([]);
  // Each entry: { repoKey: "owner/repo", repo: {...}, branch: "main" }
  const [activeRepoKey, setActiveRepoKey] = useState(null);
  const [addRepoOpen, setAddRepoOpen] = useState(false);

  const [activePage, setActivePage] = useState("workspace");
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [userInfo, setUserInfo] = useState(null);

  // Startup / enterprise loader state
  const [startupPhase, setStartupPhase] = useState("booting");
  const [startupStatusMessage, setStartupStatusMessage] = useState("Starting application...");
  const [startupDetailMessage, setStartupDetailMessage] = useState(
    "Initializing authentication, provider, and workspace context."
  );
  const [startupStatusSnapshot, setStartupStatusSnapshot] = useState(null);

  // Repo + Session State Machine
  const [repoStateByKey, setRepoStateByKey] = useState({});
  const [toast, setToast] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [adminTab, setAdminTab] = useState("overview");
  const [adminStatus, setAdminStatus] = useState(null);

  // Fetch admin status when overview tab is active
  useEffect(() => {
    if (activePage === "admin" && adminTab === "overview") {
      fetchStatus()
        .then((data) => setAdminStatus(data))
        .catch(() => setAdminStatus(null));
    }
  }, [activePage, adminTab]);

  // Claude-Code-on-Web: Session sidebar + Environment state
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [activeEnvId, setActiveEnvId] = useState("default");
  const [sessionRefreshNonce, setSessionRefreshNonce] = useState(0);

  // Sidebar collapse state (persisted in localStorage)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem("gitpilot_sidebar_collapsed") === "true";
    } catch {
      return false;
    }
  });

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("gitpilot_sidebar_collapsed", String(next));
      } catch {}
      return next;
    });
  }, []);

  // Keyboard shortcut: Cmd/Ctrl + B to toggle sidebar
  useEffect(() => {
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "b") {
        e.preventDefault();
        toggleSidebar();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [toggleSidebar]);

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
      if (prev.some((e) => e.repoKey === key)) {
        setActiveRepoKey(key);
        return prev;
      }
      const entry = { repoKey: key, repo: r, branch: r.default_branch || "main" };
      return [...prev, entry];
    });

    setActiveRepoKey(key);
    setAddRepoOpen(false);
  }, []);

  const removeRepoFromContext = useCallback((key) => {
    setContextRepos((prev) => {
      const next = prev.filter((e) => e.repoKey !== key);
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
    setContextRepos((prev) =>
      prev.map((e) =>
        e.repoKey === targetRepoKey ? { ...e, branch: newBranch } : e
      )
    );

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

  const _creatingSessionRef = useRef(false);

  const [chatBySession, setChatBySession] = useState({});

  const ensureSession = useCallback(
    async (sessionName, seedMessages) => {
      if (activeSessionId) return activeSessionId;
      if (!repo) return null;
      if (_creatingSessionRef.current) return null;
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
    },
    [activeSessionId, repo, repoKey, currentBranch, contextRepos, activeRepoKey]
  );

  const handleNewSession = async () => {
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
      showToast("Session Created", "New session started.");
    } catch (err) {
      console.warn("Failed to create session:", err);
    }
  };

  /**
   * Convert a backend Message object to the frontend chat UI shape.
   * Backend:  { role: "user|assistant|system", content: "...", timestamp, metadata }
   * Frontend: { from: "user|ai", role: "user|assistant|system", content, answer, ... }
   */
  const normalizeBackendMessage = (m) => {
    const role = m.role || "assistant";
    const content = m.content || "";
    if (role === "user") {
      return { from: "user", role: "user", content, text: content };
    }
    if (role === "system") {
      return { from: "ai", role: "system", content };
    }
    // assistant
    return {
      from: "ai",
      role: "assistant",
      content,
      answer: content,
      // Preserve any structured metadata the backend stored (plan, diff, etc.)
      ...(m.metadata && typeof m.metadata === "object" ? m.metadata : {}),
    };
  };

  /**
   * Fetch persisted messages for a session from the backend.
   * Returns an array of normalized frontend messages (ready for ChatPanel),
   * or an empty array on failure.
   */
  const fetchSessionMessages = useCallback(async (sessionId) => {
    if (!sessionId) return [];
    try {
      const token = localStorage.getItem("github_token");
      const headers = { "Content-Type": "application/json" };
      if (token) headers["Authorization"] = `Bearer ${token}`;

      const res = await fetch(apiUrl(`/api/sessions/${sessionId}/messages`), {
        headers,
      });
      if (!res.ok) {
        console.warn(`[fetchSessionMessages] ${res.status} for ${sessionId}`);
        return [];
      }
      const data = await res.json();
      const backendMessages = Array.isArray(data.messages) ? data.messages : [];
      return backendMessages.map(normalizeBackendMessage);
    } catch (err) {
      console.warn(`[fetchSessionMessages] Failed to fetch ${sessionId}:`, err);
      return [];
    }
  }, []);

  /**
   * Handle click on a session in the sidebar.
   *
   * Critical ordering: we must hydrate chatBySession BEFORE setting
   * activeSessionId, because ChatPanel's session-sync useEffect reads
   * sessionChatState only when sessionId changes (it does NOT depend on
   * chatBySession to avoid prop/state loops). If we set activeSessionId
   * first, ChatPanel would see an empty messages array, then our async
   * hydration would complete but ChatPanel wouldn't re-sync.
   */
  // Resolve the branch we should jump to when reopening a session.
  // Preference order:
  //   1. session.repos[i].branch for the active_repo (multi-repo)
  //   2. session.branch (legacy single-repo field)
  // Returns ``null`` when nothing is recorded.
  const resolveSessionBranch = (session) => {
    if (!session) return null;
    if (Array.isArray(session.repos) && session.repos.length > 0) {
      const target =
        session.repos.find(
          (r) => session.active_repo && r?.full_name === session.active_repo,
        ) || session.repos[0];
      if (target?.branch) return target.branch;
    }
    return session.branch || null;
  };

  // Probe whether a branch still exists on GitHub.  We deliberately
  // reuse the existing tree endpoint instead of adding a new one — a
  // 200 means the ref resolves, anything else (most importantly 404)
  // means the branch is gone or otherwise unreachable.  Failure
  // degrades to "branch unknown" so a transient network blip falls
  // back gracefully rather than misleading the user.
  const probeBranchExists = async (repoFullName, branch) => {
    if (!repoFullName || !branch) return false;
    try {
      const token = localStorage.getItem("github_token");
      const headers = {};
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const res = await fetch(
        apiUrl(
          `/api/repos/${repoFullName}/tree?ref=${encodeURIComponent(branch)}`,
        ),
        { headers },
      );
      return res.ok;
    } catch {
      return false;
    }
  };

  const handleSelectSession = useCallback(async (session) => {
    // 1. Fetch persisted messages first
    const messages = await fetchSessionMessages(session.id);

    // 2. Seed the chat cache (ChatPanel will read this via sessionChatState)
    setChatBySession((prev) => ({
      ...prev,
      [session.id]: {
        ...(prev[session.id] || { plan: null }),
        messages,
      },
    }));

    // 3. NOW activate the session — ChatPanel's sync effect will read
    //    the hydrated messages from chatBySession[session.id]
    setActiveSessionId(session.id);

    // 4. Jump to the branch this session last published to, but verify
    //    it still exists on GitHub first.  When the branch was deleted
    //    (rebased away, merged-and-pruned, …) fall back to the
    //    repository's default branch and tell the user what happened —
    //    silently landing on the default would mask data loss.
    const target = resolveSessionBranch(session);
    if (target && target !== currentBranch) {
      const repoFullName =
        session.repo ||
        (Array.isArray(session.repos) && session.repos[0]?.full_name);
      const exists = await probeBranchExists(repoFullName, target);
      if (exists) {
        handleBranchChange(target);
      } else {
        const fallback = defaultBranch || "main";
        showToast(
          "Branch not found",
          `'${target}' was not found on GitHub. Switched to ${fallback}.`,
        );
        if (fallback !== currentBranch) handleBranchChange(fallback);
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchSessionMessages, currentBranch, defaultBranch]);

  const handleDeleteSession = useCallback(
    (deletedId) => {
      if (deletedId === activeSessionId) {
        setActiveSessionId(null);

        setChatBySession((prev) => {
          const next = { ...prev };
          delete next[deletedId];
          return next;
        });

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
    },
    [activeSessionId, repoKey, defaultBranch]
  );

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

  const sessionChatState = useMemo(() => {
    if (!activeSessionId) {
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

      if (nextBranch === cur.defaultBranch) {
        nextState.chatByBranch = {
          ...nextState.chatByBranch,
          [nextBranch]: { messages: [], plan: null },
        };
      }

      return { ...prev, [repoKey]: nextState };
    });

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
    completionMsg,
    sourceBranch,
  }) => {
    if (!repoKey || !branch) return;

    // Clear the session-keyed chat cache's ``plan`` AND append the
    // completion message synchronously, before any branch change can
    // trigger ChatPanel's session-sync effect.  Two bugs need to be
    // fixed in the same write:
    //
    // 1. Stale plan: without clearing, the sync effect re-reads the
    //    old approved plan and restores the Approve & execute / Reject
    //    plan buttons, enabling accidental double-execution.
    //
    // 2. Wiped completion: in hard-switch mode the sync effect runs
    //    BEFORE the persistence effect (declared earlier in
    //    ChatPanel), so it overwrites local ``messages`` with
    //    ``sessionChatState.messages`` — which doesn't yet contain
    //    completionMsg.  The user's "Answer / Execution Log" block
    //    then vanishes from the session view.
    //
    // By appending normalizedCompletion here, sessionChatState already
    // carries the completion when the sync effect reads it.  No
    // duplicate is introduced: local ``messages`` already has the same
    // entry, so the subsequent persistence pass is a no-op write.
    if (activeSessionId) {
      const normalizedCompletion =
        completionMsg &&
        (completionMsg.answer || completionMsg.content || completionMsg.executionLog)
          ? {
              from: completionMsg.from || "ai",
              role: completionMsg.role || "assistant",
              answer: completionMsg.answer,
              content: completionMsg.content,
              executionLog: completionMsg.executionLog,
              diff: completionMsg.diff,
            }
          : null;
      setChatBySession((prev) => {
        const existing = prev[activeSessionId];
        if (!existing) return prev;
        const noPlanChange = existing.plan == null;
        if (noPlanChange && !normalizedCompletion) return prev;
        return {
          ...prev,
          [activeSessionId]: {
            ...existing,
            messages: normalizedCompletion
              ? [...(existing.messages || []), normalizedCompletion]
              : existing.messages,
            plan: null,
          },
        };
      });
    }

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
        completionMsg &&
        (completionMsg.answer || completionMsg.content || completionMsg.executionLog)
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
            (cur.chatByBranch && cur.chatByBranch[prevBranchKey]) || {
              messages: [],
              plan: null,
            };

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
  // Auth & startup render
  // ---------------------------------------------------------------------------
  useEffect(() => {
    checkAuthentication();
  }, []);

  const checkAuthentication = async () => {
    setStartupPhase("booting");
    setStartupStatusMessage("Starting application...");
    setStartupDetailMessage(
      "Initializing authentication, provider, and workspace context."
    );

    try {
      setStartupPhase("checking-backend");
      setStartupStatusMessage("Connecting to backend...");
      setStartupDetailMessage(
        "Waiting for the server to be ready. This may take a few seconds on first start."
      );

      // Single-source-of-truth init: combines /api/status + /api/auth/status
      // in one request. Runs exactly once per page load (StrictMode-safe).
      const initResult = await initApp();
      const status = initResult.status;
      if (status) {
        setStartupStatusSnapshot(status);
        setAdminStatus(status);
      }

      const token = localStorage.getItem("github_token");
      const user = localStorage.getItem("github_user");

      if (token && user) {
        setStartupPhase("validating-auth");
        setStartupStatusMessage("Validating authentication...");
        setStartupDetailMessage(
          "Restoring your GitHub session and confirming access."
        );

        try {
          const data = await safeFetchJSON(apiUrl("/api/auth/validate"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ access_token: token }),
            timeout: 20000, // 20s — first-load GitHub API validation can be slow
          });

          if (data.authenticated) {
            setStartupPhase("restoring-session");
            setStartupStatusMessage("Restoring workspace...");
            setStartupDetailMessage(
              "Loading user profile, reconnecting provider state, and preparing the workspace."
            );

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

      setStartupPhase("ready");
      setStartupStatusMessage("Preparing sign-in...");
      setStartupDetailMessage(
        "GitPilot is ready. Please authenticate to continue."
      );

      setIsAuthenticated(false);
      setIsLoading(false);
    } catch (err) {
      console.error(err);
      setStartupPhase("fallback");
      setStartupStatusMessage("Starting application...");
      setStartupDetailMessage(
        "Continuing with basic startup while backend status is still loading."
      );
      setIsAuthenticated(false);
      setIsLoading(false);
    }
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

  if (isLoading) {
    return (
      <StartupScreen
        appName="GitPilot"
        subtitle="Enterprise Workspace Copilot"
        frontendVersion={frontendVersion}
        backendVersion={getBackendVersion(startupStatusSnapshot)}
        provider={getProviderLabel(startupStatusSnapshot)}
        statusMessage={startupStatusMessage}
        detailMessage={startupDetailMessage}
        phase={startupPhase}
      />
    );
  }

  if (!isAuthenticated) {
    return (
      <LoginPage
        onAuthenticated={handleAuthenticated}
        backendReady={!!startupStatusSnapshot}
      />
    );
  }

  const hasContext = contextRepos.length > 0;

  return (
    <div className="app-root">
      <div className="main-wrapper">
        <aside className={`sidebar${sidebarCollapsed ? " sidebar--collapsed" : ""}`}>
          <div
            className="sidebar-top-row"
          >
            <div
              className="logo-row"
              onClick={sidebarCollapsed ? toggleSidebar : undefined}
              style={sidebarCollapsed ? { cursor: "pointer" } : undefined}
            >
              <div className="logo-square">GP</div>
              {!sidebarCollapsed && (
                <div>
                  <div className="logo-title">GitPilot</div>
                  <div className="logo-subtitle">Agentic GitHub Copilot</div>
                </div>
              )}
            </div>

            {!sidebarCollapsed && (
              <button
                className="sidebar-toggle-btn"
                onClick={toggleSidebar}
                title="Collapse sidebar (Ctrl+B)"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                  <path
                    d="M10 3L5 8L10 13"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
            )}
          </div>

          <div className="main-nav">
            <button
              className={"nav-btn" + (activePage === "workspace" ? " nav-btn-active" : "")}
              onClick={() => setActivePage("workspace")}
              title="Workspace"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <rect x="2" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3" />
                <rect x="9" y="2" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3" />
                <rect x="2" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3" />
                <rect x="9" y="9" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3" />
              </svg>
              {!sidebarCollapsed && <span>Workspace</span>}
            </button>

            <button
              className={"nav-btn" + (activePage === "flow" ? " nav-btn-active" : "")}
              onClick={() => setActivePage("flow")}
              title="Agent Workflow"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <circle cx="4" cy="4" r="2" stroke="currentColor" strokeWidth="1.3" />
                <circle cx="12" cy="4" r="2" stroke="currentColor" strokeWidth="1.3" />
                <circle cx="8" cy="12" r="2" stroke="currentColor" strokeWidth="1.3" />
                <path d="M5.5 5.5L7 10.5" stroke="currentColor" strokeWidth="1.3" />
                <path d="M10.5 5.5L9 10.5" stroke="currentColor" strokeWidth="1.3" />
              </svg>
              {!sidebarCollapsed && <span>Agent Workflow</span>}
            </button>

            <button
              className={"nav-btn" + (activePage === "admin" ? " nav-btn-active" : "")}
              onClick={() => setActivePage("admin")}
              title="Admin"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path
                  d="M8 2C8 2 9.5 4 9.5 6C9.5 6.8 9.2 7.5 8.7 8L10 14H6L7.3 8C6.8 7.5 6.5 6.8 6.5 6C6.5 4 8 2 8 2Z"
                  stroke="currentColor"
                  strokeWidth="1.3"
                  strokeLinejoin="round"
                />
                <circle cx="8" cy="6" r="1.5" stroke="currentColor" strokeWidth="1.3" />
              </svg>
              {!sidebarCollapsed && <span>Admin</span>}
            </button>
          </div>

          {!sidebarCollapsed && (
            <>
              {!hasContext && (
                <RepoSelector onSelect={(r) => addRepoToContext(r)} />
              )}

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
            </>
          )}

          {userInfo && (
            <div className="user-profile">
              <UserMenu
                userInfo={userInfo}
                sidebarCollapsed={sidebarCollapsed}
                onOpenSettings={() => {
                  setActivePage("admin");
                  setAdminTab("advanced");
                }}
                onOpenAbout={() => setAboutOpen(true)}
                onLogout={handleLogout}
              />
            </div>
          )}
        </aside>

        <main className="workspace">
          {activePage === "admin" && (
            <div style={{ padding: "24px", maxWidth: "960px", margin: "0 auto" }}>
              <div style={{ display: "flex", gap: "8px", marginBottom: "24px", flexWrap: "wrap" }}>
                {["overview", "providers", "workspace-modes", "integrations", "mcp-servers", "sessions", "skills", "security", "advanced"].map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setAdminTab(tab)}
                    style={{
                      padding: "8px 16px",
                      borderRadius: "6px",
                      border: adminTab === tab ? "1px solid #3B82F6" : "1px solid #333",
                      background: adminTab === tab ? "#1e3a5f" : "#1a1b26",
                      color: adminTab === tab ? "#93c5fd" : "#a0a0b0",
                      cursor: "pointer",
                      fontSize: "13px",
                      textTransform: "capitalize",
                    }}
                  >
                    {tab.replace("-", " ")}
                  </button>
                ))}
              </div>

              {adminTab === "overview" && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "16px" }}>
                  <div style={{ background: "#1a1b26", borderRadius: "8px", padding: "16px", border: "1px solid #2a2b36" }}>
                    <div style={{ fontSize: "12px", opacity: 0.6, marginBottom: "8px" }}>Server</div>
                    <div style={{ fontSize: "16px", fontWeight: 600 }}>
                      {adminStatus?.server_ready ? "Connected" : "Checking..."}
                    </div>
                    <div style={{ fontSize: "12px", opacity: 0.5 }}>127.0.0.1:8000</div>
                  </div>

                  <div style={{ background: "#1a1b26", borderRadius: "8px", padding: "16px", border: "1px solid #2a2b36" }}>
                    <div style={{ fontSize: "12px", opacity: 0.6, marginBottom: "8px" }}>Provider</div>
                    <div style={{ fontSize: "16px", fontWeight: 600 }}>
                      {adminStatus?.provider?.name || "Loading..."}
                    </div>
                    <div style={{ fontSize: "12px", opacity: 0.5 }}>
                      {adminStatus?.provider?.configured
                        ? `${adminStatus.provider.model || "Ready"}`
                        : "Not configured"}
                    </div>
                  </div>

                  <div style={{ background: "#1a1b26", borderRadius: "8px", padding: "16px", border: "1px solid #2a2b36" }}>
                    <div style={{ fontSize: "12px", opacity: 0.6, marginBottom: "8px" }}>Workspace Modes</div>
                    <div style={{ fontSize: "12px" }}>
                      Folder: {adminStatus?.workspace?.folder_mode_available ? "Yes" : "—"}
                    </div>
                    <div style={{ fontSize: "12px" }}>
                      Local Git: {adminStatus?.workspace?.local_git_available ? "Yes" : "—"}
                    </div>
                    <div style={{ fontSize: "12px" }}>
                      GitHub: {adminStatus?.workspace?.github_mode_available ? "Yes" : "Optional"}
                    </div>
                  </div>

                  <div style={{ background: "#1a1b26", borderRadius: "8px", padding: "16px", border: "1px solid #2a2b36" }}>
                    <div style={{ fontSize: "12px", opacity: 0.6, marginBottom: "8px" }}>GitHub</div>
                    <div style={{ fontSize: "14px" }}>
                      {adminStatus?.github?.connected ? "Connected" : "Optional"}
                    </div>
                    <div style={{ fontSize: "12px", opacity: 0.5 }}>
                      {adminStatus?.github?.username || "Not linked"}
                    </div>
                  </div>

                  <div style={{ background: "#1a1b26", borderRadius: "8px", padding: "16px", border: "1px solid #2a2b36" }}>
                    <div style={{ fontSize: "12px", opacity: 0.6, marginBottom: "8px" }}>Sessions</div>
                    <div style={{ fontSize: "14px" }}>—</div>
                  </div>

                  <div style={{ background: "#1a1b26", borderRadius: "8px", padding: "16px", border: "1px solid #2a2b36" }}>
                    <div style={{ fontSize: "12px", opacity: 0.6, marginBottom: "8px" }}>Get Started</div>
                    <button
                      onClick={() => setAdminTab("providers")}
                      style={{
                        padding: "6px 12px",
                        background: "#3B82F6",
                        color: "#fff",
                        border: "none",
                        borderRadius: "4px",
                        cursor: "pointer",
                        fontSize: "12px",
                        marginRight: "4px",
                      }}
                    >
                      Configure Provider
                    </button>
                  </div>
                </div>
              )}

              {adminTab === "providers" && (
                <div>
                  <h3 style={{ marginBottom: "16px" }}>AI Providers</h3>
                  <LlmSettings />
                </div>
              )}

              {adminTab === "workspace-modes" && (
                <WorkspaceModesTab
                  showToast={showToast}
                  onSessionStarted={(result) => {
                    setActiveSessionId(result.session_id);
                    setSessionRefreshNonce((n) => n + 1);
                    setActivePage("workspace");
                  }}
                />
              )}

              {adminTab === "integrations" && (
                <IntegrationsTab
                  userInfo={userInfo}
                  onDisconnect={handleLogout}
                  showToast={showToast}
                />
              )}

              {adminTab === "mcp-servers" && (
                <MCPServersTab showToast={showToast} />
              )}

              {adminTab === "security" && (
                <SecurityTab showToast={showToast} />
              )}

              {adminTab === "sessions" && (
                <SessionsTab
                  showToast={showToast}
                  onSelectSession={(s) => {
                    handleSelectSession(s);
                    setActivePage("workspace");
                  }}
                />
              )}

              {adminTab === "skills" && <SkillsTab showToast={showToast} />}

              {adminTab === "advanced" && (
                <AdvancedTab
                  showToast={showToast}
                  onOpenFullSettings={() => setSettingsOpen(true)}
                />
              )}
            </div>
          )}

          {activePage === "flow" && <FlowViewer />}

          {activePage === "workspace" &&
            (repo ? (
              <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
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

      <AddRepoModal
        isOpen={addRepoOpen}
        onSelect={addRepoToContext}
        onClose={() => setAddRepoOpen(false)}
        excludeKeys={contextRepos.map((e) => e.repoKey)}
      />

      <AboutModal
        isOpen={aboutOpen}
        onClose={() => setAboutOpen(false)}
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