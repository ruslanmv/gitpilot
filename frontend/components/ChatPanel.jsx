import React, { useState, useRef, useEffect } from "react";
import AssistantMessage from "./AssistantMessage.jsx";

// Helper to get headers (inline safety if utility is missing)
const getHeaders = () => ({
  "Content-Type": "application/json",
  "Authorization": `Bearer ${localStorage.getItem("github_token") || ""}`,
});

export default function ChatPanel({
  repo,
  defaultBranch = "main",
  currentBranch = "main",
  onExecutionComplete,
  sessionChatState,
  onSessionChatStateChange,
}) {
  // Initialize state from props or defaults
  // We use a ref to track if we've initialized to avoid overwriting state on every render
  const [messages, setMessages] = useState(sessionChatState?.messages || []);
  const [goal, setGoal] = useState("");
  const [plan, setPlan] = useState(sessionChatState?.plan || null);
  
  const [loadingPlan, setLoadingPlan] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [status, setStatus] = useState("");
  const messagesEndRef = useRef(null);

  // ---------------------------------------------------------------------------
  // 1. SESSION SYNC: Restore chat ONLY when branch changes
  // ---------------------------------------------------------------------------
  useEffect(() => {
    // When the branch changes, we load the new session state from the parent.
    // This is the "Switch" action.
    if (sessionChatState) {
        setMessages(sessionChatState.messages || []);
        setPlan(sessionChatState.plan || null);
        setGoal("");
        setStatus("");
        setLoadingPlan(false);
        setExecuting(false);
    }
  }, [currentBranch]); // Only trigger on branch switch, NOT on sessionChatState change

  // ---------------------------------------------------------------------------
  // 2. PERSISTENCE: Save chat to Parent (Debounced or Conditional)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    // Only update parent if local state differs effectively or acts as a "save"
    // We avoid circular dependency by NOT depending on sessionChatState here.
    if (typeof onSessionChatStateChange === "function") {
      // Prevents wiping parent state if local state is empty on mount
      if (messages.length > 0 || plan) {
         // Create a stable object to compare or just push up
         onSessionChatStateChange({ messages, plan });
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, plan]); 

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, plan, loadingPlan, executing]);

  // ---------------------------------------------------------------------------
  // HANDLERS
  // ---------------------------------------------------------------------------

  const send = async () => {
    if (!goal.trim()) return;
    
    // Normalize message structure
    const userMsg = { from: "user", text: goal.trim(), role: "user", content: goal.trim() };
    
    // Optimistic update
    const nextMessages = [...messages, userMsg];
    setMessages(nextMessages);
    
    setLoadingPlan(true);
    setStatus("");
    setPlan(null);
    setGoal(""); 

    try {
      const res = await fetch("/api/chat/plan", {
        method: "POST",
        headers: getHeaders(),
        body: JSON.stringify({
          repo_owner: repo.owner,
          repo_name: repo.name,
          goal: userMsg.content,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to generate plan");

      setPlan(data);
      
      // Add AI response about the plan
      setMessages(prev => [...prev, {
        from: "ai",
        role: "assistant",
        answer: data.summary || "Here is the proposed plan for your request.",
        content: data.summary || "Here is the proposed plan for your request.",
        plan: data // Store plan in message for history context
      }]);

    } catch (err) {
      console.error(err);
      setStatus(String(err.message || err));
      setMessages(prev => [...prev, { 
          from: "ai", role: "system", 
          text: `Error: ${err.message}`, content: `Error: ${err.message}` 
      }]);
    } finally {
      setLoadingPlan(false);
    }
  };

  const execute = async () => {
    if (!plan) return;
    setExecuting(true);
    setStatus("");

    try {
      // LOGIC: Sticky vs Hard Switch
      // If we are on the default branch, we send 'undefined' so backend creates a NEW branch.
      // If we are already on an AI branch, we send that name so backend commits to IT.
      const branch_name = currentBranch === defaultBranch ? undefined : currentBranch;

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
      
      // Add AI completion message with Execution Log *BEFORE* notifying parent
      // This ensures the logs are part of the state that gets saved to the NEW branch
      const completionMsg = {
          from: "ai",
          role: "assistant",
          answer: data.message || "Execution completed.",
          content: data.message || "Execution completed.",
          executionLog: data.executionLog, // CRITICAL: Pass logs to AssistantMessage
      };

      setMessages(prev => [...prev, completionMsg]);
      setPlan(null); // Clear active plan UI (it's now in history)

      // CRITICAL: Inform App.jsx to update global state
      // This triggers the branch switch in App.jsx.
      // App.jsx will then take the *current* chat state (which now includes the logs)
      // and copy it to the new branch bucket.
      if (typeof onExecutionComplete === "function") {
        onExecutionComplete({ 
            branch: data.branch || data.branch_name, 
            mode: data.mode,
            commit_url: data.commit_url || data.html_url,
            message: data.message
        });
      }

    } catch (err) {
      console.error(err);
      setStatus(String(err.message || err));
    } finally {
      setExecuting(false);
    }
  };

  // ---------------------------------------------------------------------------
  // RENDER
  // ---------------------------------------------------------------------------
  return (
    <div className="chat-container">
      {/* Inject minimal styles for success message support */}
      <style>{`
        .chat-container { display: flex; flex-direction: column; height: 100%; }
        .chat-messages { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px; }
        
        .chat-message-user { 
            align-self: flex-end; 
            background: #27272A; 
            color: #fff; 
            padding: 12px 16px; 
            border-radius: 8px; 
            max-width: 85%; 
            font-size: 14px; 
            line-height: 1.5;
        }
        
        /* Success System Message Styling */
        .chat-msg-success { 
            align-self: flex-start; 
            width: 100%; 
            background: rgba(16, 185, 129, 0.1); 
            border: 1px solid rgba(16, 185, 129, 0.2); 
            color: #D1FAE5;
            padding: 12px 16px;
            border-radius: 8px;
            display: flex; gap: 12px;
            font-size: 14px;
        }
        .success-icon { font-size: 18px; }
        .success-link { display: inline-block; margin-top: 6px; font-weight: 600; color: #34D399; text-decoration: none; }
        .success-link:hover { text-decoration: underline; }

        .chat-input-box { padding: 16px; border-top: 1px solid #27272A; background: #131316; }
        .chat-input-row { display: flex; gap: 10px; }
        .chat-input { flex: 1; background: #18181B; border: 1px solid #27272A; color: white; padding: 10px; border-radius: 6px; outline: none; }
        .chat-btn { background: #EDEDED; color: black; border: none; padding: 0 16px; border-radius: 6px; font-weight: 600; cursor: pointer; }
        .chat-btn.secondary { background: transparent; border: 1px solid #3F3F46; color: #A1A1AA; }
        .chat-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        
        .chat-empty-state { text-align: center; color: #52525B; margin-top: 40px; font-size: 14px; }
      `}</style>

      <div className="chat-messages">
        {messages.map((m, idx) => {
          // 1. Success Message (Injected by App.jsx logic)
          if (m.isSuccess) {
            return (
              <div key={idx} className="chat-msg-success">
                <div className="success-icon">🚀</div>
                <div>
                  <div style={{whiteSpace: 'pre-wrap'}}>{m.content}</div>
                  {m.link && (
                    <a href={m.link} target="_blank" rel="noreferrer" className="success-link">
                      View Changes on GitHub &rarr;
                    </a>
                  )}
                </div>
              </div>
            );
          }

          // 2. User Message
          if (m.from === "user" || m.role === "user") {
            return (
              <div key={idx} className="chat-message-user">
                <span>{m.text || m.content}</span>
              </div>
            );
          }

          // 3. AI / Assistant Message (Uses the nice UI component)
          return (
            <div key={idx}>
              <AssistantMessage
                answer={m.answer || m.content}
                plan={m.plan}
                executionLog={m.executionLog} // Preserves the logs
              />
            </div>
          );
        })}

        {/* Loading Indicator */}
        {loadingPlan && (
             <div className="chat-message-ai" style={{color: '#A1A1AA', fontStyle: 'italic', padding: '10px'}}>
                Thinking...
             </div>
        )}

        {/* Empty State */}
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
          <div style={{ fontSize: 11, color: "#ffb3b7", marginBottom: 8 }}>{status}</div>
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
            disabled={loadingPlan || executing}
          />
          <button
            className="chat-btn"
            type="button"
            onClick={send}
            disabled={loadingPlan || executing || !!plan} // Disable send if plan is pending
          >
            {loadingPlan ? "Planning..." : "Generate plan"}
          </button>
          
          {/* Show Execute button only if a plan is pending approval */}
          {plan && (
              <button
                className="chat-btn secondary"
                type="button"
                onClick={execute}
                disabled={executing}
              >
                {executing ? "Executing..." : "Approve & execute"}
              </button>
          )}
        </div>
      </div>
    </div>
  );
}