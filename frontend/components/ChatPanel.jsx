// frontend/components/ChatPanel.jsx
import React, { useEffect, useRef, useState } from "react";
import AssistantMessage from "./AssistantMessage.jsx";

// Helper to get headers (inline safety if utility is missing)
const getHeaders = () => ({
  "Content-Type": "application/json",
  Authorization: `Bearer ${localStorage.getItem("github_token") || ""}`,
});

export default function ChatPanel({
  repo,
  defaultBranch = "main",
  currentBranch, // ✅ do NOT default here; parent must pass the real one
  onExecutionComplete,
  sessionChatState,
  onSessionChatStateChange,
}) {
  // Initialize state from props or defaults
  const [messages, setMessages] = useState(sessionChatState?.messages || []);
  const [goal, setGoal] = useState("");
  const [plan, setPlan] = useState(sessionChatState?.plan || null);

  const [loadingPlan, setLoadingPlan] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [status, setStatus] = useState("");

  const messagesEndRef = useRef(null);
  const prevMsgCountRef = useRef((sessionChatState?.messages || []).length);

  // ---------------------------------------------------------------------------
  // 1) SESSION SYNC: Restore chat ONLY when branch changes
  // IMPORTANT: Do NOT depend on sessionChatState here (prevents prop/state loop)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    const nextMessages = sessionChatState?.messages || [];
    const nextPlan = sessionChatState?.plan || null;

    setMessages(nextMessages);
    setPlan(nextPlan);

    // Reset transient UI state on branch switch
    setGoal("");
    setStatus("");
    setLoadingPlan(false);
    setExecuting(false);

    // Update msg count tracker so auto-scroll doesn't "jump" on switch
    prevMsgCountRef.current = nextMessages.length;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentBranch]);

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
    const curCount = messages.length;
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
  }, [messages.length]);

  // ---------------------------------------------------------------------------
  // HANDLERS
  // ---------------------------------------------------------------------------
  const send = async () => {
    if (!repo || !goal.trim()) return;

    const text = goal.trim();

    // Optimistic update (old UX: user bubble appears immediately)
    const userMsg = { from: "user", role: "user", text, content: text };
    setMessages((prev) => [...prev, userMsg]);

    setLoadingPlan(true);
    setStatus("");
    setPlan(null);

    // ✅ Guard: never send null/undefined branch_name
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

      // Assistant response (Answer + Action Plan)
      setMessages((prev) => [
        ...prev,
        {
          from: "ai",
          role: "assistant",
          answer: data.summary || "Here is the proposed plan for your request.",
          content: data.summary || "Here is the proposed plan for your request.",
          plan: data,
        },
      ]);

      // Clear input only after success (old behavior)
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

      // Show completion immediately (keeps old “Execution Log” section)
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

        .chat-input-row { display: flex; gap: 10px; align-items: center; }

        .chat-input {
          flex: 1;
          background: #18181B;
          border: 1px solid #27272A;
          color: white;
          padding: 10px 12px;
          border-radius: 8px;
          outline: none;
          font-size: 14px;
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
            </div>
          );
        })}

        {loadingPlan && (
          <div className="chat-message-ai" style={{ color: "#A1A1AA", fontStyle: "italic", padding: "10px" }}>
            Thinking...
          </div>
        )}

        {!messages.length && !plan && !loadingPlan && (
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

      <div className="chat-input-box">
        {status && (
          <div style={{ fontSize: 11, color: "#ffb3b7", marginBottom: 8 }}>
            {status}
          </div>
        )}

        <div className="chat-input-row">
          <input
            className="chat-input"
            placeholder="Describe the change you want to make..."
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
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
            {loadingPlan ? "Planning..." : "Generate plan"}
          </button>

          <button
            className="chat-btn secondary"
            type="button"
            onClick={execute}
            disabled={!plan || executing || loadingPlan}
          >
            {executing ? "Executing..." : "Approve & execute"}
          </button>
        </div>
      </div>
    </div>
  );
}
