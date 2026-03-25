// frontend/components/ChatPanel.jsx
import React, { useEffect, useRef, useState } from "react";
import AssistantMessage from "./AssistantMessage.jsx";
import DiffStats from "./DiffStats.jsx";
import DiffViewer from "./DiffViewer.jsx";
import CreatePRButton from "./CreatePRButton.jsx";
import StreamingMessage from "./StreamingMessage.jsx";
import { SessionWebSocket } from "../utils/ws.js";

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
}) {
  // Initialize state from props or defaults
  const [messages, setMessages] = useState(sessionChatState?.messages || []);
  const [goal, setGoal] = useState("");
  const [plan, setPlan] = useState(sessionChatState?.plan || null);

  const [loadingPlan, setLoadingPlan] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [status, setStatus] = useState("");

  // Claude-Code-on-Web: WebSocket streaming + diff + PR
  const [wsConnected, setWsConnected] = useState(false);
  const [streamingEvents, setStreamingEvents] = useState([]);
  const [diffData, setDiffData] = useState(null);
  const [showDiffViewer, setShowDiffViewer] = useState(false);
  const wsRef = useRef(null);

  // Ref mirrors streamingEvents so WS callbacks avoid stale closures
  const streamingEventsRef = useRef([]);
  useEffect(() => { streamingEventsRef.current = streamingEvents; }, [streamingEvents]);

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
          // avoid stale closure — streamingEvents state would be stale here)
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

    return () => {
      ws.close();
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
  // Persist a message to the backend session (fire-and-forget)
  // ---------------------------------------------------------------------------
  const persistMessage = (sid, role, content) => {
    if (!sid) return;
    fetch(`/api/sessions/${sid}/message`, {
      method: "POST",
      headers: getHeaders(),
      body: JSON.stringify({ role, content }),
    }).catch(() => {}); // best-effort
  };

  const send = async () => {
    if (!repo || !goal.trim()) return;

    const text = goal.trim();

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
      const res = await fetch("/api/chat/plan", {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify({
          repo_owner: repo.owner,
          repo_name: repo.name,
          goal: text,
          branch_name: effectiveBranch,
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to generate plan");

      setPlan(data);

      // Extract summary from nested plan structure or top-level
      const summary =
        data.plan?.summary || data.summary || data.message ||
        "Here is the proposed plan for your request.";

      // Assistant response (Answer + Action Plan)
      setMessages((prev) => [
        ...prev,
        {
          from: "ai",
          role: "assistant",
          answer: summary,
          content: summary,
          plan: data,
        },
      ]);

      // Persist assistant response to backend session
      persistMessage(sid, "assistant", summary);

      // Clear input only after success
      setGoal("");
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

      const res = await fetch("/api/chat/execute", {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify({
          repo_owner: repo.owner,
          repo_name: repo.name,
          plan,
          branch_name,
        }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Execution failed");

      setStatus(data.message || "Execution completed.");

      const completionMsg = {
        from: "ai",
        role: "assistant",
        answer: data.message || "Execution completed.",
        content: data.message || "Execution completed.",
        executionLog: data.executionLog,
      };

      // Show completion immediately (keeps old "Execution Log" section)
      setMessages((prev) => [...prev, completionMsg]);

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

          // Assistant message (Answer / Plan / Execution Log)
          return (
            <div key={idx}>
              <AssistantMessage
                answer={m.answer || m.content}
                plan={m.plan}
                executionLog={m.executionLog}
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

        {loadingPlan && streamingEvents.length === 0 && (
          <div className="chat-message-ai" style={{ color: "#A1A1AA", fontStyle: "italic", padding: "10px" }}>
            Thinking...
          </div>
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
            disabled={loadingPlan || executing}
          />

          {/* Always show both buttons (old UX) */}
          <button
            className="chat-btn primary"
            type="button"
            onClick={send}
            disabled={loadingPlan || executing || !goal.trim()}
          >
            {loadingPlan ? "Planning..." : wsConnected ? "Send" : "Generate plan"}
          </button>

          <button
            className="chat-btn secondary"
            type="button"
            onClick={execute}
            disabled={!plan || executing || loadingPlan}
          >
            {executing ? "Executing..." : "Approve & execute"}
          </button>

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

        {/* WebSocket connection indicator */}
        {sessionId && (
          <div style={{ marginTop: 6, display: "flex", alignItems: "center", gap: 8 }}>
            <span className="ws-indicator">
              <span className="ws-dot" style={{
                backgroundColor: wsConnected ? "#10B981" : "#EF4444",
              }} />
              {wsConnected ? "Live" : "Connecting..."}
            </span>
          </div>
        )}
      </div>

      {/* Diff Viewer overlay */}
      {showDiffViewer && (
        <DiffViewer
          diff={diffData}
          onClose={() => setShowDiffViewer(false)}
        />
      )}
    </div>
  );
}
