// frontend/components/TasksPanel.jsx
//
// Right-sidebar Tasks panel — Claude Code-style trace of every AI
// invocation in the active session.  Trigger is a small ⊞ icon next
// to the context meter; clicking it opens a popover anchored to the
// composer rail.
//
// V1 contract (simplest cut):
//   - One task per top-level user action (Plan, Execute).
//   - Lazy fetch on open + manual ↻ refresh.  Zero idle traffic.
//   - No cost row.  Token counts shown only when the provider exposes
//     them; otherwise "—".
//
// GitPilot brand orange #D95C3D is used only for the running-state
// dot — completed is slate, failed is the existing red.  No new deps;
// inline styles + scoped <style> block, same pattern as ContextMeter.

import React, { useEffect, useRef, useState } from "react";
import { apiUrl } from "../utils/api.js";

const GITPILOT_ORANGE = "#D95C3D";
const SUCCESS_GREEN = "#10B981";
const FAIL_RED = "#EF4444";
const DIM = "#9aa0b4";

const fmtMs = (ms) => {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s - m * 60);
  return `${m} m ${rem.toString().padStart(2, "0")} s`;
};

const fmtTokens = (n) => {
  if (n == null) return "—";
  if (n < 1000) return `${n}`;
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
};

function StatusGlyph({ status }) {
  if (status === "running") {
    return (
      <span
        aria-label="running"
        title="running"
        style={{
          display: "inline-block",
          width: 10,
          height: 10,
          borderRadius: "50%",
          background: GITPILOT_ORANGE,
          animation: "gitpilot-tasks-pulse 1.2s ease-in-out infinite",
        }}
      />
    );
  }
  if (status === "failed") {
    return (
      <span aria-label="failed" title="failed" style={{ color: FAIL_RED, fontSize: 13 }}>
        ✕
      </span>
    );
  }
  return (
    <span aria-label="completed" title="completed" style={{ color: SUCCESS_GREEN, fontSize: 13 }}>
      ✓
    </span>
  );
}

function TaskRow({ task }) {
  const kindLabel = task.kind ? task.kind[0].toUpperCase() + task.kind.slice(1) : "—";
  const parts = [kindLabel];
  parts.push(fmtMs(task.duration_ms));
  if (task.prompt_tokens != null || task.completion_tokens != null) {
    const t = (task.prompt_tokens || 0) + (task.completion_tokens || 0);
    if (t > 0) parts.push(`${fmtTokens(t)} tokens`);
  }
  parts.push(task.status === "completed" ? "✓ completed" : task.status);
  return (
    <div className="ctx-task-row">
      <div className="ctx-task-glyph">
        <StatusGlyph status={task.status} />
      </div>
      <div className="ctx-task-body">
        <div className="ctx-task-title" title={task.title}>
          {task.title || "(untitled)"}
        </div>
        <div className="ctx-task-meta">{parts.join(" · ")}</div>
        {task.error && (
          <div className="ctx-task-err" title={task.error}>
            {task.error}
          </div>
        )}
      </div>
    </div>
  );
}

export default function TasksPanel({ sessionId = null }) {
  const [open, setOpen] = useState(false);
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const popoverRef = useRef(null);
  const triggerRef = useRef(null);

  const fetchTasks = async () => {
    if (!sessionId) {
      setTasks([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(apiUrl(`/api/sessions/${encodeURIComponent(sessionId)}/tasks`));
      if (!r.ok) {
        if (r.status === 404) {
          setError("disabled");
          setTasks([]);
        } else {
          setError(`http ${r.status}`);
        }
      } else {
        const data = await r.json();
        setTasks(Array.isArray(data.tasks) ? data.tasks : []);
      }
    } catch (e) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (open) fetchTasks();
  }, [open, sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    setTasks([]);
  }, [sessionId]);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (
        popoverRef.current &&
        !popoverRef.current.contains(e.target) &&
        triggerRef.current &&
        !triggerRef.current.contains(e.target)
      ) {
        setOpen(false);
      }
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (error === "disabled") return null;

  const running = tasks.filter((t) => t.status === "running");
  const completed = tasks.filter((t) => t.status !== "running");

  return (
    <span className="gitpilot-tasks-panel" style={{ position: "relative", display: "inline-flex" }}>
      <style>{`
        @keyframes gitpilot-tasks-pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.35; }
        }
        .gitpilot-tasks-panel .tasks-trigger {
          background: transparent;
          border: 1px solid rgba(255,255,255,0.12);
          color: ${DIM};
          width: 22px;
          height: 22px;
          border-radius: 4px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          font-size: 12px;
          line-height: 1;
          cursor: pointer;
          padding: 0;
          transition: color 120ms ease, border-color 120ms ease;
        }
        .gitpilot-tasks-panel .tasks-trigger:hover,
        .gitpilot-tasks-panel .tasks-trigger:focus-visible {
          color: #e5e7eb;
          border-color: rgba(255,255,255,0.28);
          outline: none;
        }
        .gitpilot-tasks-panel .tasks-trigger[data-running="1"] { color: ${GITPILOT_ORANGE}; border-color: ${GITPILOT_ORANGE}55; }
        .gitpilot-tasks-panel .tasks-popover {
          position: absolute;
          right: 0;
          bottom: calc(100% + 8px);
          width: 380px;
          max-height: 480px;
          overflow-y: auto;
          background: #1a1c25;
          border: 1px solid rgba(255,255,255,0.10);
          border-radius: 8px;
          box-shadow: 0 8px 24px rgba(0,0,0,0.45);
          padding: 12px 14px;
          z-index: 50;
          font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
        }
        .gitpilot-tasks-panel .tasks-popover h4 {
          margin: 0 0 8px 0;
          font-size: 12px;
          font-weight: 600;
          letter-spacing: 0.04em;
          text-transform: uppercase;
          color: ${DIM};
        }
        .gitpilot-tasks-panel .tasks-section {
          margin-bottom: 12px;
        }
        .gitpilot-tasks-panel .tasks-section-label {
          font-size: 11px;
          color: ${DIM};
          margin-bottom: 6px;
        }
        .gitpilot-tasks-panel .ctx-task-row {
          display: flex;
          align-items: flex-start;
          gap: 10px;
          padding: 8px 10px;
          border: 1px solid rgba(255,255,255,0.06);
          border-radius: 6px;
          margin-bottom: 6px;
        }
        .gitpilot-tasks-panel .ctx-task-glyph {
          flex: 0 0 16px;
          padding-top: 2px;
          display: flex;
          justify-content: center;
        }
        .gitpilot-tasks-panel .ctx-task-body { flex: 1; min-width: 0; }
        .gitpilot-tasks-panel .ctx-task-title {
          font-size: 13px;
          color: #e5e7eb;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .gitpilot-tasks-panel .ctx-task-meta {
          font-size: 11px;
          color: ${DIM};
          font-variant-numeric: tabular-nums;
          margin-top: 2px;
        }
        .gitpilot-tasks-panel .ctx-task-err {
          font-size: 11px;
          color: ${FAIL_RED};
          margin-top: 4px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .gitpilot-tasks-panel .tasks-footer {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-top: 8px;
          font-size: 11px;
          color: ${DIM};
        }
        .gitpilot-tasks-panel .tasks-refresh {
          background: transparent;
          border: 1px solid rgba(255,255,255,0.14);
          color: #cbd1e3;
          font-size: 11px;
          padding: 2px 8px;
          border-radius: 4px;
          cursor: pointer;
        }
        .gitpilot-tasks-panel .tasks-refresh:hover { color: #fff; border-color: rgba(255,255,255,0.3); }
        .gitpilot-tasks-panel .tasks-refresh:disabled { opacity: 0.5; cursor: default; }
        .gitpilot-tasks-panel .tasks-empty {
          font-size: 12px;
          color: ${DIM};
          text-align: center;
          padding: 20px 0;
        }
      `}</style>

      <button
        ref={triggerRef}
        type="button"
        className="tasks-trigger"
        aria-label="Tasks"
        aria-haspopup="dialog"
        aria-expanded={open}
        data-running={running.length > 0 ? "1" : "0"}
        onClick={() => setOpen((v) => !v)}
        title="Tasks"
      >
        {/* Simple grid glyph to match the screenshot's column icon. */}
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
          <rect x="1" y="1" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.4" />
          <rect x="9" y="1" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.4" />
          <rect x="1" y="9" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.4" />
          <rect x="9" y="9" width="6" height="6" rx="1" stroke="currentColor" strokeWidth="1.4" />
        </svg>
      </button>

      {open && (
        <div
          ref={popoverRef}
          className="tasks-popover"
          role="dialog"
          aria-label="Tasks panel"
        >
          <h4>Tasks</h4>

          {!sessionId && (
            <div className="tasks-empty">Start a chat to see tasks here.</div>
          )}

          {sessionId && loading && tasks.length === 0 && (
            <div className="tasks-empty">Loading…</div>
          )}

          {sessionId && error && error !== "disabled" && (
            <div className="tasks-empty" style={{ color: "#ffb3b7" }}>
              Couldn't load: {error}
            </div>
          )}

          {sessionId && !loading && !error && tasks.length === 0 && (
            <div className="tasks-empty">No tasks yet.</div>
          )}

          {running.length > 0 && (
            <div className="tasks-section">
              <div className="tasks-section-label">In flight</div>
              {running.map((t) => <TaskRow key={t.id} task={t} />)}
            </div>
          )}

          {completed.length > 0 && (
            <div className="tasks-section">
              <div className="tasks-section-label">
                Completed ({completed.length})
              </div>
              {completed
                .slice()
                .reverse()
                .map((t) => <TaskRow key={t.id} task={t} />)}
            </div>
          )}

          {sessionId && (
            <div className="tasks-footer">
              <span>One row per AI invocation.</span>
              <button
                type="button"
                className="tasks-refresh"
                onClick={fetchTasks}
                disabled={loading}
                aria-label="Refresh tasks"
              >
                {loading ? "…" : "↻ refresh"}
              </button>
            </div>
          )}
        </div>
      )}
    </span>
  );
}
