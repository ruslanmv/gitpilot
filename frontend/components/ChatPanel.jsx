import { resolveBackendUrl } from "../utils/backend.js";
import { apiUrl } from "../utils/api.js";
// frontend/components/ChatPanel.jsx
import React, { useEffect, useRef, useState } from "react";
import AssistantMessage from "./AssistantMessage.jsx";
import ThinkingIndicator from "./ThinkingIndicator.jsx";
import ContextMeter from "./ContextMeter.jsx";
import TasksPanel from "./TasksPanel.jsx";
import DiffStats from "./DiffStats.jsx";
import DiffViewer from "./DiffViewer.jsx";
import CreatePRButton from "./CreatePRButton.jsx";
import StreamingMessage from "./StreamingMessage.jsx";
import SandboxCanvas from "./SandboxCanvas.jsx";
import FilePreviewPanel from "./FilePreviewPanel.jsx";
import { SessionWebSocket } from "../utils/ws.js";

// Map a file extension to the canonical sandbox language tag.  Used
// when "Open in Canvas" needs to seed SandboxCanvas with the right
// language hint pulled straight from a repo file path.
const _LANG_FROM_EXT = {
  py: "python", js: "javascript", mjs: "javascript", cjs: "javascript",
  sh: "bash", bash: "bash",
};
function languageFromPath(path) {
  if (!path || !path.includes(".")) return "python";
  return _LANG_FROM_EXT[path.split(".").pop().toLowerCase()] || "python";
}

// Helper to get headers (inline safety if utility is missing)
const getHeaders = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${localStorage.getItem("github_token") || ""}`,
});

export default function ChatPanel({
  repo,
  defaultBranch = "main",
  currentBranch, // do NOT default here; parent must pass the real one
  onExecutionComplete,
  sessionChatState,
  onSessionChatStateChange,
  sessionId,
  onEnsureSession,
  canChat = true, // readiness gate: false disables composer and shows blocker
  chatBlocker = null, // { message: string, cta?: string, onCta?: () => void }
}) {
  // Initialize state from props or defaults
  const [messages, setMessages] = useState(sessionChatState?.messages || []);
  const [goal, setGoal] = useState("");
  const [plan, setPlan] = useState(sessionChatState?.plan || null);

  const [loadingPlan, setLoadingPlan] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [status, setStatus] = useState("");
  // Batch B9 — populated when a plan whose first step was INDEX is
  // rejected.  Lets us render a small "Run with grep instead?" prompt
  // so the user doesn't have to retype the goal.
  const [retryAfterIndexReject, setRetryAfterIndexReject] = useState(null);

  // Claude-Code-on-Web: WebSocket streaming + diff + PR
  const [wsConnected, setWsConnected] = useState(false);
  const [streamingEvents, setStreamingEvents] = useState([]);
  const [diffData, setDiffData] = useState(null);
  const [showDiffViewer, setShowDiffViewer] = useState(false);
  // SandboxCanvas state — opened by the "Open in Canvas" CTA on
  // post-CREATE next_actions and ExecutionCard footers. ``canvasSpec``
  // is { filename, language, code } or null when closed.
  const [canvasSpec, setCanvasSpec] = useState(null);
  const [canvasError, setCanvasError] = useState(null);
  // FilePreviewPanel state — opened by clicking a file row in the
  // sidebar (gitpilot:open-file).  Read-first surface; users can pick
  // "Prepare Run" / "Open Workspace" / "Ask GitPilot" from there.
  const [previewPath, setPreviewPath] = useState(null);
  const [previewContent, setPreviewContent] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState(null);
  const [previewErrorCode, setPreviewErrorCode] = useState(null);
  // "preview" (narrow drawer) or "workspace" (wide editor).
  const [previewMode, setPreviewMode] = useState("preview");
  const wsRef = useRef(null);

  // Ref mirrors streamingEvents so WS callbacks avoid stale closures
  const streamingEventsRef = useRef([]);
  useEffect(() => { streamingEventsRef.current = streamingEvents; }, [streamingEvents]);

  // Tracks files that were just CREATE'd / MODIFY'd by a fresh execution.
  // Used to (a) auto-retry once on 404 (GitHub contents API has brief
  // eventual-consistency lag) and (b) classify the file viewer's empty
  // state as "still syncing" instead of a generic 404.
  const fileWasJustCreatedRef = useRef(new Set());
  const fileWasJustDeletedRef = useRef(new Set());

  // Skip the session-sync useEffect reset when we just created a session
  // (the parent already seeded the messages into chatBySession)
  const skipNextSyncRef = useRef(false);

  const messagesEndRef = useRef(null);
  const prevMsgCountRef = useRef((sessionChatState?.messages || []).length);

  // ---------------------------------------------------------------------------
  // WebSocket connection management
  // ---------------------------------------------------------------------------
  useEffect(() => {
    // Clean up previous connection
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
      setWsConnected(false);
    }

    if (!sessionId) return;

    // Wait for backend to be reachable before opening WebSocket.
    // Without this, the WS connects immediately on session creation
    // and fails repeatedly with "closed before established" when the
    // backend is still starting up (common on WSL cold start).
    let cancelled = false;
    const backendUrl = resolveBackendUrl();
    const pingUrl = backendUrl ? `${backendUrl}/api/ping` : '/api/ping';
    const waitForBackend = async () => {
      for (let i = 0; i < 10 && !cancelled; i++) {
        try {
          const res = await fetch(pingUrl, { method: 'GET', signal: AbortSignal.timeout(2000) });
          if (res.ok) return true;
        } catch { /* retry */ }
        await new Promise(r => setTimeout(r, 1500));
      }
      return false;
    };

    waitForBackend().then((ok) => {
      if (cancelled || !ok) return;
      connectWs();
    });

    function connectWs() {
    const ws = new SessionWebSocket(sessionId, {
      onConnect: () => setWsConnected(true),
      onDisconnect: () => setWsConnected(false),
      onMessage: (data) => {
        if (data.type === "agent_message") {
          setStreamingEvents((prev) => [...prev, data]);
        } else if (data.type === "tool_use" || data.type === "tool_result") {
          setStreamingEvents((prev) => [...prev, data]);
        } else if (data.type === "diff_update") {
          setDiffData(data.stats || data);
        } else if (data.type === "session_restored") {
          // Session loaded
        }
      },
      onStatusChange: (newStatus) => {
        if (newStatus === "waiting") {
          // Always clear loading state when agent finishes
          setLoadingPlan(false);

          // Consolidate streaming events into a chat message (use ref to
          // avoid stale closure — streamingEvents state would be stale here).
          //
          // We also commit the FINAL consolidated text to the backend session
          // here.  Previously this branch never called persistMessage, so the
          // assistant turn looked correct in the live view but vanished on the
          // next session reload — the canonical "streaming truncation" symptom.
          const events = streamingEventsRef.current;
          if (events.length > 0) {
            const textParts = events
              .filter((e) => e.type === "agent_message")
              .map((e) => e.content);
            if (textParts.length > 0) {
              const consolidated = {
                from: "ai",
                role: "assistant",
                answer: textParts.join(""),
                content: textParts.join(""),
              };
              setMessages((prev) => [...prev, consolidated]);
              persistMessage(sessionId, "assistant", consolidated.content);
            }
            setStreamingEvents([]);
          }
        }
      },
      onError: (err) => {
        console.warn("[ws] Error:", err);
        setLoadingPlan(false);
      },
    });

    ws.connect();
    wsRef.current = ws;
    } // end connectWs

    return () => {
      cancelled = true;
      if (wsRef.current) wsRef.current.close();
    };
  }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------------------------------------------------------------------
  // 1) SESSION SYNC: Restore chat when branch, repo, OR session changes
  // IMPORTANT: Do NOT depend on sessionChatState here (prevents prop/state loop)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    // When send() just created a session, the parent seeded the messages
    // into chatBySession already.  Skip the reset so we don't wipe
    // the optimistic user message that was already rendered.
    if (skipNextSyncRef.current) {
      skipNextSyncRef.current = false;
      return;
    }

    const nextMessages = sessionChatState?.messages || [];
    const nextPlan = sessionChatState?.plan || null;

    setMessages(nextMessages);
    setPlan(nextPlan);

    // Reset transient UI state on branch/repo/session switch
    setGoal("");
    setStatus("");
    setLoadingPlan(false);
    setExecuting(false);
    setStreamingEvents([]);
    setDiffData(null);

    // Update msg count tracker so auto-scroll doesn't "jump" on switch
    prevMsgCountRef.current = nextMessages.length;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentBranch, repo?.full_name, sessionId]);

  // ---------------------------------------------------------------------------
  // 1b) FILE ▶ RUN: listen for run-file events from the sidebar.
  // ---------------------------------------------------------------------------
  //
  // FileTree dispatches ``gitpilot:run-file`` with the clicked file's
  // path.  We turn that into a normal chat message ("run <path>")
  // which goes through /api/chat/plan, hits the deterministic
  // short-circuit, and renders an ExecutionPlanCard — exactly the
  // same flow as typing the command. One handler, one approval surface,
  // zero duplicated logic.
  useEffect(() => {
    const onRunFile = (e) => {
      const path = e?.detail?.path;
      if (!path || !repo) return;
      send({ goal: `run ${path}` });
    };
    // "Open in Canvas" handler — fetches the file's content from the
    // active branch and opens SandboxCanvas seeded with it.  Logs a
    // friendly error banner when the fetch fails so a misconfigured
    // token / wrong branch doesn't silently swallow the click.
    const onOpenInCanvas = async (e) => {
      const path = e?.detail?.path;
      if (!path || !repo) return;
      setCanvasError(null);
      const branch = currentBranch || "HEAD";
      try {
        const url = `/api/repos/${repo.owner}/${repo.name}/file`
                  + `?path=${encodeURIComponent(path)}`
                  + `&ref=${encodeURIComponent(branch)}`;
        const res = await fetch(apiUrl(url), { headers: getHeaders() });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          setCanvasError(data.detail || `Could not load ${path} (HTTP ${res.status})`);
          // Still open the canvas with empty content so the user can
          // paste something — better than nothing happening on click.
          setCanvasSpec({
            filename: path, language: languageFromPath(path), code: "",
          });
          return;
        }
        setCanvasSpec({
          filename: path,
          language: languageFromPath(path),
          code: data.content || "",
        });
      } catch (err) {
        setCanvasError(err?.message || "Could not load file for Canvas");
        setCanvasSpec({
          filename: path, language: languageFromPath(path), code: "",
        });
      }
    };
    // "Open file" — clicking a file row in the sidebar mounts the
    // read-first FilePreviewPanel.  Calmer than dropping straight
    // into Canvas: the user sees the file, can pick "Prepare Run"
    // when they're ready (or "Open Workspace" for a wider editing
    // surface).  The ``mode`` detail toggles the panel's geometry:
    //   "preview"   ── narrow right drawer for a quick look
    //   "workspace" ── wide right-side editor for serious review
    const openFile = async (path, mode = "preview") => {
      if (!path || !repo) return;
      setPreviewPath(path);
      setPreviewMode(mode);
      setPreviewContent(null);
      setPreviewError(null);
      setPreviewErrorCode(null);
      setPreviewLoading(true);
      // Tell the sidebar which file is currently focused so it can
      // light up the row with the ◄ marker.
      try {
        window.dispatchEvent(new CustomEvent("gitpilot:file-opened", { detail: { path } }));
      } catch (_e) { /* old browser */ }
      const branch = currentBranch || "HEAD";
      const fetchOnce = async () => {
        const url = `/api/repos/${repo.owner}/${repo.name}/file`
                  + `?path=${encodeURIComponent(path)}`
                  + `&ref=${encodeURIComponent(branch)}`;
        const res = await fetch(apiUrl(url), { headers: getHeaders() });
        const data = await res.json().catch(() => ({}));
        return { res, data };
      };
      try {
        let { res, data } = await fetchOnce();
        // Auto-retry once on 404 for recently created files — GitHub
        // contents API has brief eventual-consistency lag after a
        // freshly published commit.
        if (res.status === 404 && fileWasJustCreatedRef.current?.has(path)) {
          await new Promise((r) => setTimeout(r, 900));
          ({ res, data } = await fetchOnce());
        }
        if (!res.ok) {
          setPreviewError(data.detail || `HTTP ${res.status}`);
          setPreviewErrorCode(res.status);
        } else {
          setPreviewContent(data.content || "");
        }
      } catch (err) {
        setPreviewError(err?.message || "Could not load file");
        setPreviewErrorCode(null);
      } finally {
        setPreviewLoading(false);
      }
    };
    const onOpenFile      = (e) => openFile(e?.detail?.path, "preview");
    const onOpenWorkspace = (e) => openFile(e?.detail?.path, "workspace");
    // "Ask GitPilot" — seed the chat input with a contextual question
    // about the clicked file.  Pure additive: focuses the input and
    // pre-fills it; the user can edit or send as-is.
    const onAskAboutFile = (e) => {
      const path = e?.detail?.path;
      if (!path) return;
      setGoal(`Tell me about ${path}.`);
      const ta = document.querySelector(".chat-input");
      if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
    };
    window.addEventListener("gitpilot:run-file", onRunFile);
    window.addEventListener("gitpilot:open-in-canvas", onOpenInCanvas);
    window.addEventListener("gitpilot:open-file", onOpenFile);
    window.addEventListener("gitpilot:open-workspace", onOpenWorkspace);
    window.addEventListener("gitpilot:ask-about-file", onAskAboutFile);
    return () => {
      window.removeEventListener("gitpilot:run-file", onRunFile);
      window.removeEventListener("gitpilot:open-in-canvas", onOpenInCanvas);
      window.removeEventListener("gitpilot:open-file", onOpenFile);
      window.removeEventListener("gitpilot:open-workspace", onOpenWorkspace);
      window.removeEventListener("gitpilot:ask-about-file", onAskAboutFile);
    };
    // ``send`` is stable enough across renders for this use case —
    // we don't want to re-bind on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repo?.full_name, currentBranch, sessionId]);

  // ---------------------------------------------------------------------------
  // 2) PERSISTENCE: Save chat to Parent (no loop now because sync only on branch)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (typeof onSessionChatStateChange === "function") {
      // Avoid wiping parent state on mount
      if (messages.length > 0 || plan) {
        onSessionChatStateChange({ messages, plan });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, plan]);

  // ---------------------------------------------------------------------------
  // 3) AUTO-SCROLL: Only scroll when a message is appended (reduces flicker)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const curCount = messages.length + streamingEvents.length;
    const prevCount = prevMsgCountRef.current;

    // Only scroll when new messages are added
    if (curCount > prevCount) {
      prevMsgCountRef.current = curCount;
      requestAnimationFrame(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
      });
    } else {
      prevMsgCountRef.current = curCount;
    }
  }, [messages.length, streamingEvents.length]);

  // ---------------------------------------------------------------------------
  // HANDLERS
  // ---------------------------------------------------------------------------
  // ---------------------------------------------------------------------------
  // Persist a message to the backend session (fire-and-forget).
  //
  // The fourth argument carries the *structured* payload of the assistant
  // response — the Action Plan, the Execution Log, diff stats, etc. The
  // backend stores it on Message.metadata; on session reload App.jsx
  // spreads metadata back into the local message via normalizeBackendMessage,
  // so the same AssistantMessage renderer can re-draw the Plan / Steps /
  // Create buttons identically to the live view.
  //
  // Before this fix the structured payload was dropped at persist time —
  // the session reloaded as raw text, and the UI degraded to a plain
  // paragraph. This is the canonical "state loss during hydration" bug.
  // ---------------------------------------------------------------------------
  const persistMessage = (sid, role, content, metadata = null) => {
    if (!sid) return;
    const body = { role, content };
    if (metadata && typeof metadata === "object" && Object.keys(metadata).length > 0) {
      body.metadata = metadata;
    }
    fetch(apiUrl(`/api/sessions/${sid}/message`), {
      method: "POST",
      headers: getHeaders(),
      body: JSON.stringify(body),
    }).catch(() => {}); // best-effort
  };

  // Pick the structured fields a message can carry across a reload.
  // Keep this in one place so every call-site stores the same shape and
  // the renderer never has to guess.
  const pickAssistantMetadata = (m) => {
    if (!m || typeof m !== "object") return null;
    const meta = {};
    if (m.plan)         meta.plan         = m.plan;
    if (m.executionLog) meta.executionLog = m.executionLog;
    if (m.diff)         meta.diff         = m.diff;
    if (m.actions)      meta.actions      = m.actions;
    if (m.nextActions)  meta.nextActions  = m.nextActions;
    if (m.branch)       meta.branch       = m.branch;
    // Informational plans (READ-only answers to "what does X do?" style
    // questions) carry no Approve/Reject controls — pin the flag so the
    // session reload re-renders the same shape.
    if (m.informational) meta.informational = true;
    return Object.keys(meta).length > 0 ? meta : null;
  };

  const send = async (overrides = {}) => {
    if (!repo) return;
    // Allow callers (e.g. the "Retry with grep" button on a rejected
    // INDEX plan) to drive send() with a fixed goal and a router flag.
    const overrideGoal = overrides.goal;
    const force_no_rag = Boolean(overrides.force_no_rag);
    const sourceText = overrideGoal != null ? overrideGoal : goal;
    if (!sourceText || !sourceText.trim()) return;

    const text = sourceText.trim();

    // Clear input immediately (Claude Code behavior) — but only when
    // the user typed; programmatic retries leave the input alone.
    if (overrideGoal == null) setGoal("");
    // Reset textarea height
    const ta = document.querySelector(".chat-input");
    if (ta) ta.style.height = "40px";

    // Optimistic update (user bubble appears immediately)
    const userMsg = { from: "user", role: "user", text, content: text };
    setMessages((prev) => [...prev, userMsg]);

    setLoadingPlan(true);
    setStatus("");
    setPlan(null);
    setStreamingEvents([]);

    // ------- Implicit session creation (Claude Code parity) -------
    // Every chat must be backed by a session.  If none exists yet,
    // create one on-demand before sending the plan request.
    let sid = sessionId;
    if (!sid && typeof onEnsureSession === "function") {
      // Derive a short title from the first message
      const sessionName = text.length > 60 ? text.slice(0, 57) + "..." : text;

      // Tell the sync useEffect to skip the reset that would otherwise
      // wipe the optimistic user message when activeSessionId changes.
      skipNextSyncRef.current = true;

      sid = await onEnsureSession(sessionName, [userMsg]);
      if (!sid) {
        // Session creation failed — continue without session
        skipNextSyncRef.current = false;
      }
    }

    // Persist user message to backend session
    persistMessage(sid, "user", text);

    // Always use HTTP for plan generation (the original reliable flow).
    // WebSocket is only used for real-time streaming feedback display.
    const effectiveBranch = currentBranch || defaultBranch || "HEAD";

    try {
      // Timeout after 5 minutes (CrewAI agent can be slow with small models)
      const planController = new AbortController();
      const planTimer = setTimeout(() => planController.abort(), 300000);

      let res;
      try {
        res = await fetch(apiUrl("/api/chat/plan"), {
          method: "POST",
          headers: getHeaders(),
          body: JSON.stringify({
            repo_owner: repo.owner,
            repo_name: repo.name,
            goal: text,
            branch_name: effectiveBranch,
            // Lets the backend record this plan as a Task on the
            // session so the right-sidebar Tasks panel can trace it.
            session_id: sid,
            // Batch B9 — set on the "Retry with grep" path after the
            // user rejects an INDEX-plan.  Tells the router to
            // suppress RAG / INDEX recommendations.
            force_no_rag,
          }),
          signal: planController.signal,
        });
      } catch (fetchErr) {
        if (fetchErr.name === "AbortError") {
          throw new Error("Request timed out after 5 minutes. The LLM may be too slow. Try a faster model.");
        }
        throw fetchErr;
      } finally {
        clearTimeout(planTimer);
      }

      let data;
      try {
        data = await res.json();
      } catch {
        throw new Error(`Server error (${res.status}). The LLM may have returned an invalid response. Try a different model or enable Lite Mode in Settings.`);
      }
      if (!res.ok) {
        const detail = data?.detail || data?.error || data?.message || "";
        // Friendly message for common LLM failures
        if (detail.includes("None or empty") || detail.includes("Invalid response from LLM")) {
          throw new Error(
            "The LLM returned an empty response. This often happens with small models (deepseek, qwen 0.5b). " +
            "Try a larger model (llama3, qwen2.5:7b) or enable Lite Mode in Settings."
          );
        }
        throw new Error(detail || "Failed to generate plan");
      }

      // Classify the plan into one of three kinds so we can render the
      // right shape — not just "valid or banner":
      //
      // * executable    — at least one CREATE/MODIFY/DELETE → plan card
      //                   with Approve & execute / Reject controls.
      // * informational — every file is READ (or no files at all on a
      //                   step that still has a meaningful description)
      //                   AND the summary is a real answer, not the
      //                   placeholder.  This is what happens when the
      //                   user asks "what do you think about this
      //                   project?" — the planner correctly READs the
      //                   relevant files and the summary IS the answer.
      //                   Render the summary as a normal assistant
      //                   message; do not show plan controls.
      // * empty         — no steps OR no actionable signal at all →
      //                   honest failure banner.
      //
      // Before this classifier the second case was treated as the
      // third, surfacing "I couldn't produce a plan" on perfectly
      // valid READ-only plans.
      const planSteps = Array.isArray(data?.steps)
        ? data.steps
        : Array.isArray(data?.plan?.steps)
        ? data.plan.steps
        : [];
      const PLACEHOLDER_SUMMARY = "Here is the proposed plan for your request.";
      const summary =
        data.plan?.summary || data.summary || data.message || PLACEHOLDER_SUMMARY;
      const hasExecutable = planSteps.some(
        (s) =>
          Array.isArray(s?.files) &&
          s.files.some((f) => ["CREATE", "MODIFY", "DELETE"].includes(f?.action)),
      );
      const isReadOnly =
        planSteps.length > 0 &&
        !hasExecutable &&
        planSteps.every(
          (s) =>
            !Array.isArray(s?.files) ||
            s.files.length === 0 ||
            s.files.every((f) => f?.action === "READ"),
        );
      const hasRealSummary = Boolean(summary) && summary !== PLACEHOLDER_SUMMARY;
      const planKind = hasExecutable
        ? "executable"
        : isReadOnly && hasRealSummary
        ? "informational"
        : "empty";

      if (planKind === "executable") {
        setPlan(data);
        const assistantMsg = {
          from: "ai",
          role: "assistant",
          answer: summary,
          content: summary,
          plan: data,
        };
        setMessages((prev) => [...prev, assistantMsg]);
        persistMessage(sid, "assistant", summary, pickAssistantMetadata(assistantMsg));
      } else if (planKind === "informational") {
        // The summary is the answer.  No plan card, no Approve/Reject —
        // there is nothing to execute.  We deliberately do NOT attach
        // ``plan: data`` here so AssistantMessage renders this turn
        // exactly like a chat reply.
        setPlan(null);
        const assistantMsg = {
          from: "ai",
          role: "assistant",
          answer: summary,
          content: summary,
          informational: true,
        };
        setMessages((prev) => [...prev, assistantMsg]);
        persistMessage(sid, "assistant", summary, pickAssistantMetadata(assistantMsg));
      } else {
        // empty — be honest about what we know.  The earlier wording
        // ("got stuck reading the same file twice") was a guess from
        // an older bug; for the cases that actually still hit this
        // branch the real signal is just "no actionable steps".
        setPlan(null);
        const failureText =
          "The model returned an empty plan. Try rephrasing more concretely, " +
          "or pick a stronger model in Settings → Provider.";
        const failureMsg = {
          from: "ai",
          role: "system",
          content: failureText,
        };
        setMessages((prev) => [...prev, failureMsg]);
        persistMessage(sid, "system", failureText);
        setStatus("No actionable plan produced.");
        return;
      }
    } catch (err) {
      const msg = String(err?.message || err);
      console.error(err);
      setStatus(msg);
      setMessages((prev) => [
        ...prev,
        { from: "ai", role: "system", content: `Error: ${msg}` },
      ]);
    } finally {
      setLoadingPlan(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Reject the active plan — minimal first cut.
  //
  // Industry rule we follow from the start: never write to disk on a path the
  // user did not approve.  Rejecting is the cheapest expression of that —
  // discard the proposed plan locally, leave the workspace untouched, record
  // the rejection in chat history so the user sees it after a session reload.
  //
  // No backend endpoint is needed yet because plans are not persisted as
  // first-class objects today; they ride along on the assistant message's
  // metadata.  When we later add per-plan state tracking, this handler will
  // also POST /api/chat/plan/{id}/reject — leaving that for a follow-up.
  // ---------------------------------------------------------------------------
  const rejectPlan = () => {
    if (!plan || executing) return;

    // Batch B9 — if the rejected plan contained an INDEX step, the
    // user is implicitly saying "I don't want to build the semantic
    // index right now".  Stash the original goal so we can offer a
    // one-click "retry with grep" path on the next render.
    const hadIndexStep = Array.isArray(plan?.steps) &&
      plan.steps.some((s) =>
        Array.isArray(s?.files) && s.files.some((f) => f?.action === "INDEX"),
      );
    const rejectedGoal = plan?.goal || "";

    setPlan(null);
    setStatus("Plan rejected. No files were changed.");

    const rejectionMsg = {
      from: "ai",
      role: "system",
      content: "Plan rejected. No files were changed.",
    };
    setMessages((prev) => [...prev, rejectionMsg]);

    if (sessionId) {
      persistMessage(sessionId, "system", rejectionMsg.content);
    }

    if (hadIndexStep && rejectedGoal) {
      setRetryAfterIndexReject({ goal: rejectedGoal });
    } else {
      setRetryAfterIndexReject(null);
    }
  };

  const execute = async () => {
    if (!repo || !plan) return;

    setExecuting(true);
    setStatus("");

    try {
      // Guard: currentBranch might be missing if parent didn't pass it yet
      const safeCurrent = currentBranch || defaultBranch || "HEAD";
      const safeDefault = defaultBranch || "main";

      // Sticky vs Hard Switch:
      // - If on default branch -> undefined (backend creates new branch)
      // - If already on AI branch -> currentBranch (backend updates existing)
      const branch_name = safeCurrent === safeDefault ? undefined : safeCurrent;

      const res = await fetch(apiUrl("/api/chat/execute"), {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify({
          repo_owner: repo.owner,
          repo_name: repo.name,
          plan,
          branch_name,
          // Lets the backend persist the new branch on the session
          // record so reopening this session lands on the published
          // branch, not the one it was created on.
          session_id: sessionId,
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Execution failed");

      setStatus(data.message || "Execution completed.");

      // Track files touched by this execution so the file viewer can
      // give "still syncing" / "deleted" classifications and so the
      // sidebar refreshes off the freshly-pushed branch tree.
      if (plan?.steps) {
        for (const step of plan.steps) {
          for (const f of step.files || []) {
            if (f.action === "CREATE" || f.action === "MODIFY") {
              fileWasJustCreatedRef.current.add(f.path);
            } else if (f.action === "DELETE") {
              fileWasJustDeletedRef.current.add(f.path);
            }
          }
        }
      }
      // Forget the marker after 30 s so older "syncing" badges don't
      // stick around forever.
      window.setTimeout(() => {
        fileWasJustCreatedRef.current.clear();
        fileWasJustDeletedRef.current.clear();
      }, 30000);

      // Ask the sidebar's file tree to refetch off the newly-published
      // branch. Fires after a small delay so GitHub's contents API has
      // a chance to catch up.
      window.setTimeout(() => {
        try {
          window.dispatchEvent(new CustomEvent("gitpilot:refresh-tree"));
        } catch (_e) { /* old browser */ }
      }, 600);

      const completionMsg = {
        from: "ai",
        role: "assistant",
        answer: data.message || "Execution completed.",
        content: data.message || "Execution completed.",
        executionLog: data.executionLog,
        diff: data.diff,
        // Backend-suggested follow-ups (e.g. "Run demo.py" after CREATE
        // of a runnable file).  Rendered as a button row in the
        // completion message — one click, no typing.
        nextActions: data.next_actions,
        branch: data.branch || data.branch_name,
      };

      // Show completion immediately (keeps old "Execution Log" section)
      setMessages((prev) => [...prev, completionMsg]);

      // Persist the execution log + diff alongside the message text so
      // the History view re-renders the green "Execution Log" panel and
      // the "View diff" affordance.  Without this, reloading the session
      // shows just the one-line "Execution completed." summary.
      persistMessage(
        sessionId,
        "assistant",
        completionMsg.content,
        pickAssistantMetadata(completionMsg),
      );

      // Clear active plan UI
      setPlan(null);

      // Pass completionMsg upward for seeding branch history
      if (typeof onExecutionComplete === "function") {
        onExecutionComplete({
          branch: data.branch || data.branch_name,
          mode: data.mode,
          commit_url: data.commit_url || data.html_url,
          message: data.message,
          completionMsg,
          sourceBranch: safeCurrent,
        });
      }
    } catch (err) {
      console.error(err);
      setStatus(String(err?.message || err));
    } finally {
      setExecuting(false);
    }
  };

  // ---------------------------------------------------------------------------
  // RENDER
  // ---------------------------------------------------------------------------
  const isOnSessionBranch = currentBranch && currentBranch !== defaultBranch;

  return (
    <div className="chat-container">
      <style>{`
        .chat-container { display: flex; flex-direction: column; height: 100%; }

        .chat-messages {
          flex: 1; overflow-y: auto;
          padding: 20px;
          display: flex; flex-direction: column; gap: 16px;
        }

        .chat-message-user {
          align-self: flex-end;
          background: #27272A;
          color: #fff;
          padding: 12px 16px;
          border-radius: 10px;
          max-width: 85%;
          font-size: 14px;
          line-height: 1.5;
        }

        /* Success System Message Styling */
        .chat-msg-success {
          align-self: flex-start;
          width: 100%;
          background: rgba(16, 185, 129, 0.10);
          border: 1px solid rgba(16, 185, 129, 0.20);
          color: #D1FAE5;
          padding: 12px 16px;
          border-radius: 10px;
          display: flex;
          gap: 12px;
          font-size: 14px;
        }
        .success-icon { font-size: 18px; }
        .success-link {
          display: inline-block;
          margin-top: 6px;
          font-weight: 600;
          color: #34D399;
          text-decoration: none;
        }
        .success-link:hover { text-decoration: underline; }

        .chat-input-box {
          padding: 16px;
          border-top: 1px solid #27272A;
          background: #131316;
        }

        .chat-input-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }

        .chat-input {
          flex: 1;
          min-width: 200px;
          background: #18181B;
          border: 1px solid #27272A;
          color: white;
          padding: 10px 12px;
          border-radius: 8px;
          outline: none;
          font-size: 14px;
          font-family: inherit;
          resize: none;
          min-height: 40px;
          max-height: 160px;
          line-height: 1.4;
        }

        /* Enterprise controls (restored) */
        .chat-btn {
          height: 38px;
          padding: 0 14px;
          border-radius: 8px;
          font-weight: 700;
          cursor: pointer;
          border: 1px solid transparent;
          font-size: 13px;
          white-space: nowrap;
        }

        /* Orange primary (old style) */
        .chat-btn.primary { background: #D95C3D; color: #fff; }
        .chat-btn.primary:hover { filter: brightness(0.98); }
        .chat-btn.primary:disabled { opacity: 0.55; cursor: not-allowed; }

        /* Secondary outline */
        .chat-btn.secondary {
          background: transparent;
          border: 1px solid #3F3F46;
          color: #A1A1AA;
        }
        .chat-btn.secondary:hover { background: rgba(255,255,255,0.04); }
        .chat-btn.secondary:disabled { opacity: 0.55; cursor: not-allowed; }

        .chat-empty-state {
          text-align: center;
          color: #52525B;
          margin-top: 40px;
          font-size: 14px;
        }

        /* WebSocket connection indicator */
        .ws-indicator {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          font-size: 10px;
          color: #71717A;
          padding: 2px 6px;
          border-radius: 4px;
          background: rgba(24, 24, 27, 0.6);
        }
        .ws-dot {
          width: 6px;
          height: 6px;
          border-radius: 50%;
        }

        @keyframes blink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0; }
        }
      `}</style>

      <div className="chat-messages">
        {messages.map((m, idx) => {
          // Success message (App.jsx injected)
          if (m.isSuccess) {
            return (
              <div key={idx} className="chat-msg-success">
                <div className="success-icon">🚀</div>
                <div>
                  <div style={{ whiteSpace: "pre-wrap" }}>{m.content}</div>
                  {m.link && (
                    <a href={m.link} target="_blank" rel="noreferrer" className="success-link">
                      View Changes on GitHub &rarr;
                    </a>
                  )}
                </div>
              </div>
            );
          }

          // User message
          if (m.from === "user" || m.role === "user") {
            return (
              <div key={idx} className="chat-message-user">
                <span>{m.text || m.content}</span>
              </div>
            );
          }

          // Assistant message (Answer / Plan / Execution Log).
          //
          // Lifecycle audit signal: if this message carries a plan, look
          // ahead in the timeline for any subsequent message that
          // records an execution log (=> the plan was approved+executed)
          // or a system "Plan rejected" entry (=> the plan was
          // rejected).  The status is rendered as a small green/grey
          // badge next to the Action Plan header so users can tell at a
          // glance — in history — whether a previous plan was acted on.
          let planStatus = null;
          if (m.plan) {
            const after = messages.slice(idx + 1);
            if (after.some((later) => later.executionLog)) {
              planStatus = "executed";
            } else if (
              after.some(
                (later) =>
                  later.role === "system" &&
                  typeof later.content === "string" &&
                  later.content.includes("Plan rejected"),
              )
            ) {
              planStatus = "rejected";
            }
          }

          // Find the plan that was approved for this completion, so the
          // success receipt can label actions (READ/CREATE/...) instead of
          // showing only an opaque execution dump.
          let linkedPlan = null;
          if (m.executionLog) {
            for (let i = idx - 1; i >= 0; i--) {
              if (messages[i].plan?.steps) {
                linkedPlan = messages[i].plan;
                break;
              }
            }
          }

          return (
            <div key={idx}>
              <AssistantMessage
                answer={m.answer || m.content}
                plan={m.plan}
                executionLog={m.executionLog}
                planStatus={planStatus}
                owner={repo?.owner}
                repo={repo?.name}
                onApproveExecution={() => execute()}
                nextActions={m.nextActions}
                relatedPlan={linkedPlan}
                diff={m.diff}
                branch={m.branch || currentBranch}
              />
              {/* Diff stats indicator (Claude-Code-on-Web parity) */}
              {m.diff && (
                <DiffStats diff={m.diff} onClick={() => {
                  setDiffData(m.diff);
                  setShowDiffViewer(true);
                }} />
              )}
            </div>
          );
        })}

        {/* Streaming events (real-time agent output) */}
        {streamingEvents.length > 0 && (
          <div>
            <StreamingMessage events={streamingEvents} />
          </div>
        )}

        {/* Enterprise Pulse — agentic thinking state shown after the user
            hits Send and before the first streamed/planned chunk arrives.
            Falls back gracefully to nothing once streamingEvents start
            flowing in (StreamingMessage takes over the live feedback). */}
        {loadingPlan && streamingEvents.length === 0 && (
          <ThinkingIndicator />
        )}

        {/* Live execution status — visible in the chat timeline while
            ``executing`` is true, sits between the Action Plan card and
            where the Execution Log (green panel in AssistantMessage)
            will land once the backend returns.  Removes the "did the
            app freeze?" feeling caused by only the bottom button
            saying "Executing…".

            Reuses the ThinkingIndicator with execution-specific labels.
            When the executor finishes, ``setExecuting(false)`` removes
            this bubble and the completionMsg lands in the timeline as
            a normal assistant message with its green Execution Log
            block — already rendered by AssistantMessage today. */}
        {executing && (
          <ThinkingIndicator
            labels={[
              "Executing plan",
              "Applying changes",
              "Verifying result",
            ]}
          />
        )}

        {!messages.length && !plan && !loadingPlan && streamingEvents.length === 0 && (
          <div className="chat-empty-state">
            <div className="chat-empty-icon">💬</div>
            <p>Tell GitPilot what you want to do with this repository.</p>
            <p style={{ fontSize: 12, color: "#676883", marginTop: 4 }}>
              It will propose a safe step-by-step plan before any execution.
            </p>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Batch B9 — post-Reject "retry with grep" prompt.  Renders
          only when the user rejected a plan whose first step was an
          INDEX action.  One click re-issues the same goal with
          force_no_rag so the router falls back to grep. */}
      {retryAfterIndexReject && !loadingPlan && (
        <div
          style={{
            padding: "10px 16px",
            borderTop: "1px solid #27272A",
            background: "rgba(217, 92, 61, 0.06)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            flexWrap: "wrap",
          }}
        >
          <span style={{ fontSize: 13, color: "#D4D4D8" }}>
            Index skipped.  Run the same goal with grep instead?
          </span>
          <span style={{ display: "inline-flex", gap: 8 }}>
            <button
              type="button"
              className="chat-btn primary"
              onClick={() => {
                const g = retryAfterIndexReject.goal;
                setRetryAfterIndexReject(null);
                send({ goal: g, force_no_rag: true });
              }}
            >
              Yes, use grep
            </button>
            <button
              type="button"
              className="chat-btn ghost"
              onClick={() => setRetryAfterIndexReject(null)}
              style={{
                color: "#9CA3AF",
                borderColor: "rgba(156, 163, 175, 0.35)",
                background: "transparent",
              }}
            >
              No, dismiss
            </button>
          </span>
        </div>
      )}

      {/* Diff stats bar (when agent has made changes) */}
      {diffData && (
        <div style={{
          padding: "8px 16px",
          borderTop: "1px solid #27272A",
          background: "#18181B",
        }}>
          <DiffStats diff={diffData} onClick={() => setShowDiffViewer(true)} />
        </div>
      )}

      <div className="chat-input-box">
        {/* Readiness blocker banner */}
        {!canChat && chatBlocker && (
          <div style={{
            fontSize: 12,
            color: "#F59E0B",
            background: "rgba(245, 158, 11, 0.08)",
            border: "1px solid rgba(245, 158, 11, 0.2)",
            borderRadius: 6,
            padding: "8px 12px",
            marginBottom: 8,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}>
            <span>{chatBlocker.message || "Chat is not ready yet."}</span>
            {chatBlocker.cta && chatBlocker.onCta && (
              <button
                type="button"
                onClick={chatBlocker.onCta}
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#F59E0B",
                  background: "transparent",
                  border: "1px solid rgba(245, 158, 11, 0.3)",
                  borderRadius: 4,
                  padding: "2px 8px",
                  cursor: "pointer",
                }}
              >
                {chatBlocker.cta}
              </button>
            )}
          </div>
        )}
        {status && (
          <div style={{ fontSize: 11, color: "#ffb3b7", marginBottom: 8 }}>
            {status}
          </div>
        )}

        <div className="chat-input-row">
          <textarea
            className="chat-input"
            placeholder={wsConnected ? "Send feedback or instructions..." : "Describe the change you want to make..."}
            value={goal}
            rows={1}
            onChange={(e) => {
              setGoal(e.target.value);
              e.target.style.height = "40px";
              e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px";
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (!loadingPlan && !executing) send();
              }
            }}
            disabled={!canChat || loadingPlan || executing}
          />

          {/* Always show both buttons (old UX) */}
          <button
            className="chat-btn primary"
            type="button"
            onClick={send}
            disabled={!canChat || loadingPlan || executing || !goal.trim()}
          >
            {loadingPlan ? "Planning..." : wsConnected ? "Send" : "Generate plan"}
          </button>

          {/* Approve & execute — visible only while a plan is awaiting
              approval, or while an execution is already in flight (so
              the user sees the "Executing…" label, not a missing
              button).  Previously this was always rendered with
              ``disabled={!plan}``, which meant after a successful
              execute() the button stayed on screen as a dimmed ghost
              and a second click could trigger a duplicate run —
              causing the executor to re-write the same file with the
              same content (~50 s of wasted LLM time per accidental
              click).  Hiding the button entirely once ``plan`` is
              null makes the bug impossible. */}
          {(plan || executing) && (
            <button
              className="chat-btn secondary"
              type="button"
              onClick={execute}
              disabled={executing || loadingPlan}
            >
              {executing ? "Executing..." : "Approve & execute"}
            </button>
          )}

          {/* Reject plan — same visibility window as Approve. */}
          {plan && !executing && !loadingPlan && (
            <button
              className="chat-btn ghost"
              type="button"
              onClick={rejectPlan}
              title="Discard this plan. No files will be changed."
              style={{
                color: "#F87171",
                borderColor: "rgba(248, 113, 113, 0.35)",
                background: "transparent",
              }}
            >
              Reject plan
            </button>
          )}

          {/* Create PR button (Claude-Code-on-Web parity) */}
          {isOnSessionBranch && (
            <CreatePRButton
              repo={repo}
              sessionId={sessionId}
              branch={currentBranch}
              defaultBranch={defaultBranch}
              disabled={executing || loadingPlan}
            />
          )}
        </div>

        {/* WebSocket connection indicator + context-window meter */}
        <div style={{ marginTop: 6, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <span>
            {sessionId && (
              <span className="ws-indicator">
                <span className="ws-dot" style={{
                  backgroundColor: wsConnected ? "#10B981" : "#EF4444",
                }} />
                {wsConnected ? "Live" : "Connecting..."}
              </span>
            )}
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <TasksPanel sessionId={sessionId} />
            <ContextMeter sessionId={sessionId} />
          </span>
        </div>
      </div>

      {/* Diff Viewer overlay */}
      {showDiffViewer && (
        <DiffViewer
          diff={diffData}
          onClose={() => setShowDiffViewer(false)}
        />
      )}

      {/* FilePreviewPanel — read-first viewer.  Opens on a file row
          click in the sidebar.  Header carries Prepare Run (runnable
          only), Open Workspace, and an overflow menu. */}
      {previewPath && (
        <FilePreviewPanel
          path={previewPath}
          content={previewContent}
          loading={previewLoading}
          error={previewError}
          errorCode={previewErrorCode}
          notFoundKind={
            fileWasJustDeletedRef.current.has(previewPath)
              ? "deleted"
              : fileWasJustCreatedRef.current.has(previewPath)
                ? "syncing"
                : "unavailable"
          }
          mode={previewMode}
          branch={currentBranch}
          onModeChange={setPreviewMode}
          onRefreshTree={() => {
            try {
              window.dispatchEvent(new CustomEvent("gitpilot:refresh-tree"));
            } catch (_e) { /* old browser */ }
          }}
          onRetry={() => {
            const p = previewPath;
            const m = previewMode;
            setPreviewPath(null);
            // Fire the same window event we listened to — keeps the
            // retry path identical to the original load and lets any
            // future side-effects (e.g. analytics) see one event class.
            setTimeout(() => window.dispatchEvent(new CustomEvent(
              m === "workspace" ? "gitpilot:open-workspace" : "gitpilot:open-file",
              { detail: { path: p } },
            )), 0);
          }}
          onClose={() => {
            try {
              window.dispatchEvent(new CustomEvent("gitpilot:file-closed"));
            } catch (_e) {/* old browser */}
            setPreviewPath(null);
            setPreviewContent(null);
            setPreviewError(null);
            setPreviewErrorCode(null);
          }}
        />
      )}

      {/* SandboxCanvas overlay — opened by "Open in Canvas" next_action
          buttons and ExecutionCard footers via the
          gitpilot:open-in-canvas window event. */}
      {canvasSpec && (
        <SandboxCanvas
          initialLanguage={canvasSpec.language}
          initialCode={canvasSpec.code}
          filename={canvasSpec.filename}
          onClose={() => { setCanvasSpec(null); setCanvasError(null); }}
        />
      )}
      {canvasError && canvasSpec && (
        <div style={{
          position: "fixed", bottom: 16, right: 16, zIndex: 110,
          padding: "8px 12px", maxWidth: 380, fontSize: 12,
          color: "#fca5a5", background: "#3d1111",
          border: "1px solid #7f1d1d", borderRadius: 6,
        }}>
          {canvasError}
        </div>
      )}
    </div>
  );
}
