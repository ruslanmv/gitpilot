import React, { useState, useRef, useEffect } from "react";
import AssistantMessage from "./AssistantMessage.jsx";
import { getAuthHeaders } from "../utils/api.js";

export default function ChatPanel({
  repo,
  defaultBranch = "main",
  currentBranch = "main",
  onExecutionComplete,
  // Session-aware chat persistence
  sessionChatState,
  onSessionChatStateChange,
}) {
  const [messages, setMessages] = useState(sessionChatState?.messages || []);
  const [goal, setGoal] = useState("");
  const [plan, setPlan] = useState(sessionChatState?.plan || null);
  const [loadingPlan, setLoadingPlan] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [status, setStatus] = useState("");
  const messagesEndRef = useRef(null);

  // ---------------------------------------------------------------------------
  // Session State Machine: save/restore chat per branch
  // - Switching to production branch resets chat (fresh start).
  // - Switching back to AI branch restores previous chat.
  // ---------------------------------------------------------------------------
  useEffect(() => {
    // Apply incoming session state when branch changes.
    // Note: we intentionally keep the input box (goal) cleared on context switches.
    const nextMessages = sessionChatState?.messages || [];
    const nextPlan = sessionChatState?.plan || null;
    setMessages(nextMessages);
    setPlan(nextPlan);
    setGoal("");
    setStatus("");
    setLoadingPlan(false);
    setExecuting(false);
  }, [currentBranch]);

  // Persist on change
  useEffect(() => {
    if (typeof onSessionChatStateChange !== "function") return;
    onSessionChatStateChange({ messages, plan });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, plan]);

  // Auto-scroll to bottom when new messages are added
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = async () => {
    if (!goal.trim()) return;
    const userMsg = { from: "user", text: goal.trim() };
    setMessages((msgs) => [...msgs, userMsg]);
    setLoadingPlan(true);
    setStatus("");
    setPlan(null);

    try {
      const res = await fetch("/api/chat/plan", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getAuthHeaders(), // Inject Authorization header
        },
        body: JSON.stringify({
          repo_owner: repo.owner,
          repo_name: repo.name,
          goal: goal.trim(),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to generate plan");

      setPlan(data);
      setMessages((msgs) => [
        ...msgs,
        {
          from: "ai",
          answer: data.summary || "Here is the proposed plan for your request.",
          plan: data,
        },
      ]);
      setGoal("");
    } catch (err) {
      console.error(err);
      setStatus(String(err.message || err));
    } finally {
      setLoadingPlan(false);
    }
  };

  const execute = async () => {
    if (!plan) return;
    setExecuting(true);
    setStatus("");
    try {
      // Sticky context: if the user is browsing a non-default branch, keep committing there.
      // Hard switch: if the user is on defaultBranch, allow backend to create a new AI branch.
      const branch_name =
        currentBranch && currentBranch !== defaultBranch ? currentBranch : undefined;

      const res = await fetch("/api/chat/execute", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...getAuthHeaders(), // Inject Authorization header
        },
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

      // Inform App about branch context changes.
      // If we did NOT pass a branch_name, the backend may have created a new branch.
      const executedBranch = data.branch || branch_name;
      const mode = data.mode || (branch_name ? "sticky" : "hard-switch");
      if (typeof onExecutionComplete === "function") {
        onExecutionComplete({ branch: executedBranch, mode });
      }

      setMessages((msgs) => [
        ...msgs,
        {
          from: "ai",
          answer: data.message || "Execution completed.",
          executionLog: data.executionLog,
        },
      ]);
    } catch (err) {
      console.error(err);
      setStatus(String(err.message || err));
    } finally {
      setExecuting(false);
    }
  };

  return (
    <div className="chat-container">
      <div className="chat-messages">
        {messages.map((m, idx) => (
          <div key={idx}>
            {m.from === "user" ? (
              <div className="chat-message-user">
                <span>{m.text}</span>
              </div>
            ) : (
              <AssistantMessage
                answer={m.answer}
                plan={m.plan}
                executionLog={m.executionLog}
              />
            )}
          </div>
        ))}
        {!messages.length && (
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
          <div style={{ fontSize: 11, color: "#ffb3b7" }}>{status}</div>
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
                if (!loadingPlan) send();
              }
            }}
          />
          <button
            className="chat-btn"
            type="button"
            onClick={send}
            disabled={loadingPlan}
          >
            {loadingPlan ? "Planning..." : "Generate plan"}
          </button>
          <button
            className="chat-btn secondary"
            type="button"
            onClick={execute}
            disabled={!plan || executing}
          >
            {executing ? "Executing..." : "Approve & execute"}
          </button>
        </div>
      </div>
    </div>
  );
}