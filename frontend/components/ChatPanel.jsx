import React, { useState, useRef, useEffect } from "react";
import AssistantMessage from "./AssistantMessage.jsx";

export default function ChatPanel({ repo }) {
  const [messages, setMessages] = useState([]);
  const [goal, setGoal] = useState("");
  const [plan, setPlan] = useState(null);
  const [loadingPlan, setLoadingPlan] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [status, setStatus] = useState("");
  const messagesEndRef = useRef(null);

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
        headers: { "Content-Type": "application/json" },
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
      const res = await fetch("/api/chat/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          repo_owner: repo.owner,
          repo_name: repo.name,
          plan,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Execution failed");
      setStatus(data.message || "Execution completed.");
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