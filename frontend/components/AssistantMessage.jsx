import React, { useState } from "react";
import { apiUrl } from "../utils/api.js";
import PlanView from "./PlanView.jsx";
import RunnableCodeBlock, { splitFences } from "./RunnableCodeBlock.jsx";
import ExecutionPlanCard from "./ExecutionPlanCard.jsx";

export default function AssistantMessage({
  answer,
  plan,
  executionLog,
  planStatus,
  owner,
  repo,
  onApproveExecution,
  nextActions,
  relatedPlan,
  diff,
  branch,
}) {
  // Approval-first sandbox: when the planner returns an execution_plan,
  // render the green ExecutionPlanCard instead of the orange Action Plan.
  const executionPlan = plan?.execution_plan || null;
  const [runResult, setRunResult] = useState(null);
  const [runError, setRunError] = useState(null);
  const [runBusy, setRunBusy] = useState(false);

  const approveExecution = async (ep) => {
    if (onApproveExecution) {
      onApproveExecution(ep, plan);
      return;
    }
    setRunBusy(true);
    setRunError(null);
    setRunResult(null);
    try {
      const body = ep.file
        ? { language: ep.language, code: null }
        : { language: ep.language, code: ep.inline_code, timeout_sec: ep.timeout_sec };
      const res = await fetch(apiUrl("/api/sandbox/run"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) setRunError(data.detail || `HTTP ${res.status}`);
      else setRunResult(data);
    } catch (e) { setRunError(e.message); }
    finally { setRunBusy(false); }
  };

  const hasFileActions = plan?.steps?.some((s) => s.files?.length > 0);
  const answerText = typeof answer === "string" ? answer.trim() : "";
  // Suppress the answer prose when it would just duplicate what the
  // plan card or success receipt already says.
  const showAnswerProse =
    answerText &&
    !executionLog &&
    !(plan && hasFileActions && !executionPlan && answerText === plan.summary);

  return (
    <div className="chat-message-ai assistant-card">
      {/* Free-form answer prose. Rendered without a section header so
          short answers feel like a chat reply, not a debug log. */}
      {showAnswerProse && (
        <div className="assistant-answer">
          {splitFences(answerText).map((seg, i) =>
            seg.type === "code" ? (
              <RunnableCodeBlock
                key={i}
                language={seg.language}
                code={seg.code}
                owner={owner}
                repo={repo}
              />
            ) : (
              <p key={i} className="assistant-answer__p">
                {seg.value}
              </p>
            ),
          )}
        </div>
      )}

      {/* Sandbox approval card. */}
      {executionPlan && (
        <div className="assistant-block">
          <ExecutionPlanCard
            plan={executionPlan}
            variant="full"
            busy={runBusy}
            onApprove={approveExecution}
            onCancel={() => { /* no-op */ }}
          />
          {runError && (
            <div className="assistant-run-error">Run error: {runError}</div>
          )}
          {runResult && (
            <pre className="assistant-run-output">
              {runResult.stdout || runResult.stderr || "(no output)"}
            </pre>
          )}
        </div>
      )}

      {/* Proposed execution plan — only when there are file changes
          and no execution log is present yet. */}
      {plan && hasFileActions && !executionPlan && !executionLog && (
        <div className="assistant-block">
          <PlanView plan={plan} planStatus={planStatus} />
        </div>
      )}

      {/* Completed execution receipt. */}
      {executionLog && (
        <SuccessReceipt
          executionLog={executionLog}
          relatedPlan={relatedPlan}
          owner={owner}
          repo={repo}
          branch={branch}
          diff={diff}
          nextActions={nextActions}
        />
      )}

      {/* Next actions when there is no execution log (e.g. simple answers). */}
      {!executionLog && Array.isArray(nextActions) && nextActions.length > 0 && (
        <div className="assistant-next-row">
          {nextActions.map((a, i) => (
            <NextActionButton key={i} action={a} />
          ))}
        </div>
      )}
    </div>
  );
}

function NextActionButton({ action }) {
  const onClick = () => {
    if (action.kind === "run_file" && action.payload?.file) {
      window.dispatchEvent(
        new CustomEvent("gitpilot:run-file", {
          detail: { path: action.payload.file },
        }),
      );
    } else if (action.kind === "open_workspace" && action.payload?.file) {
      window.dispatchEvent(
        new CustomEvent("gitpilot:open-workspace", {
          detail: { path: action.payload.file },
        }),
      );
    } else if (action.kind === "open_in_canvas" && action.payload?.file) {
      window.dispatchEvent(
        new CustomEvent("gitpilot:open-in-canvas", {
          detail: { path: action.payload.file },
        }),
      );
    }
  };

  const isPrimary = action.kind === "run_file";
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        "next-action-btn" +
        (isPrimary ? " next-action-btn--primary" : " next-action-btn--ghost")
      }
    >
      <span className="next-action-btn__glyph" aria-hidden="true">
        {action.kind === "run_file"
          ? "▶"
          : action.kind === "open_workspace"
          ? "📂"
          : "⊞"}
      </span>
      {action.label}
    </button>
  );
}

function SuccessReceipt({
  executionLog,
  relatedPlan,
  owner,
  repo,
  branch,
  diff,
  nextActions,
}) {
  const steps = executionLog?.steps || [];
  const totalSteps = steps.length;

  // Aggregate every file action across steps so "Files changed (N)" can
  // be a single top-level section instead of per-step duplication.
  const allFiles = [];
  if (relatedPlan?.steps?.length) {
    for (const s of relatedPlan.steps) {
      for (const f of s.files || []) {
        if (f.action !== "INDEX") allFiles.push(f);
      }
    }
  }
  const totalDuration = steps.reduce((acc, s) => {
    return acc + (s.executions || []).reduce(
      (a, ex) => a + (typeof ex.duration_ms === "number" ? ex.duration_ms : 0),
      0,
    );
  }, 0);

  // Short headline outcome lines (max 2): "Created X", "Modified Y".
  const headlines = [];
  for (const action of ["CREATE", "MODIFY", "DELETE"]) {
    for (const f of allFiles) {
      if (f.action === action && headlines.length < 2) {
        headlines.push({ verb: verbFor(action), path: f.path });
      }
    }
  }

  // The bottom-bar already owns Create PR. Only show a pointer text here
  // when there is a meaningful PR path (branch + file changes).
  const hasPRPath = Boolean(branch && allFiles.length > 0);

  // Filter out create-PR-style next-actions (handled by bottom bar);
  // keep ▶ run / 📂 open as inline buttons.
  const inlineNextActions = (nextActions || []).filter(
    (a) => a && a.kind && a.kind !== "create_pr",
  );

  const hasTechLog =
    totalDuration > 0 ||
    steps.some((s) => s.summary || (s.executions || []).length > 0);

  return (
    <div className="success-card">
      <div className="success-card__head">
        <div className="success-card__icon" aria-hidden="true">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" strokeWidth="2.4" strokeLinecap="round"
               strokeLinejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        </div>
        <div className="success-card__head-text">
          <div className="success-card__title">Execution completed</div>
          <div className="success-card__subtitle">
            Successfully executed {totalSteps} step{totalSteps === 1 ? "" : "s"}
            <span className="success-card__dot">·</span>
            <span className="success-card__time-inline">Just now</span>
          </div>
        </div>
        <span className="exec-status-badge exec-status-badge--ok success-card__badge">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" strokeWidth="3" strokeLinecap="round"
               strokeLinejoin="round" aria-hidden="true">
            <polyline points="20 6 9 17 4 12" />
          </svg>
          Executed
        </span>
      </div>

      {headlines.length > 0 && (
        <ul className="success-headlines">
          {headlines.map((h, i) => (
            <li key={i} className="success-headline">
              <span className="success-headline__verb">{h.verb}</span>
              <code className="success-headline__path">{h.path}</code>
            </li>
          ))}
        </ul>
      )}

      {(owner || branch) && (
        <div className="success-meta">
          {owner && repo && (
            <div className="success-meta__item">
              <span className="success-meta__label">Repository</span>
              <span className="success-meta__value">{owner}/{repo}</span>
            </div>
          )}
          {branch && (
            <div className="success-meta__item">
              <span className="success-meta__label">Branch</span>
              <span className="success-meta__value success-meta__value--mono">
                {branch}
              </span>
            </div>
          )}
        </div>
      )}

      {allFiles.length > 0 && (
        <div className="success-files">
          <div className="success-files__label">
            Files changed ({allFiles.length})
          </div>
          <ul className="success-action-list">
            {allFiles.map((f, i) => (
              <li key={i} className="success-action-row">
                <span className="success-action__check" aria-hidden="true">✓</span>
                <span
                  className={`exec-badge exec-badge--${f.action.toLowerCase()}`}
                >
                  {f.action}
                </span>
                <code className="success-action__path">{f.path}</code>
                <span className="success-action__meta">
                  {actionMeta(f.action)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {inlineNextActions.length > 0 && (
        <div className="success-next-row">
          {inlineNextActions.map((a, i) => (
            <NextActionButton key={i} action={a} />
          ))}
        </div>
      )}

      {hasTechLog && (
        <details className="success-techlog">
          <summary>
            View execution log
            {totalDuration > 0 && (
              <span className="success-techlog__time">
                · {(totalDuration / 1000).toFixed(1)}s
              </span>
            )}
          </summary>
          {steps.map((s) => (
            <div key={s.step_number} className="success-techlog__step">
              <div className="success-techlog__step-title">
                Step {s.step_number}
                {totalSteps > 1 ? ` of ${totalSteps}` : ""}
              </div>
              {s.summary && (
                <pre className="success-techlog__pre">{s.summary}</pre>
              )}
              {Array.isArray(s.executions) && s.executions.map((ex, i) => (
                <ExecutionCard key={i} ex={ex} />
              ))}
            </div>
          ))}
        </details>
      )}

      {hasPRPath && (
        <div className="success-hint">
          Next: Create a pull request when ready.
        </div>
      )}
    </div>
  );
}

function actionMeta(action) {
  switch (action) {
    case "READ":   return "Read-only";
    case "CREATE": return "Created";
    case "MODIFY": return "Modified";
    case "DELETE": return "Deleted";
    case "INDEX":  return "Indexed";
    default:       return "";
  }
}

function verbFor(action) {
  switch (action) {
    case "CREATE": return "Created";
    case "MODIFY": return "Modified";
    case "DELETE": return "Deleted";
    default:       return action;
  }
}

function ExecutionCard({ ex }) {
  const status = ex.status || "pending";
  const statusClass =
    status === "completed" ? "exec-card-inner--ok" :
    status === "failed"    ? "exec-card-inner--bad" :
    status === "skipped"   ? "exec-card-inner--warn" :
                             "exec-card-inner--info";
  return (
    <div className={`exec-card-inner ${statusClass}`}>
      <div className="exec-card-inner__head">
        <div>
          <strong className="exec-card-inner__path">{ex.path}</strong>
          {ex.sandbox && (
            <span className="exec-card-inner__sandbox">· {ex.sandbox}</span>
          )}
        </div>
        <span className="exec-card-inner__status">
          {status === "completed" && `Exit ${ex.exit_code} · ${ex.duration_ms} ms`}
          {status === "failed" && (typeof ex.exit_code === "number"
            ? `Failed · exit ${ex.exit_code}` : "Failed")}
          {status === "skipped" && "Skipped"}
          {status === "pending" && "Running…"}
        </span>
      </div>
      {ex.command && (
        <div className="exec-card-inner__cmd">$ {ex.command}</div>
      )}
      {ex.stdout && (
        <details open style={{ marginTop: 4 }}>
          <summary className="exec-card-inner__stdout-summary">stdout</summary>
          <pre className="exec-card-inner__pre">{ex.stdout}</pre>
        </details>
      )}
      {ex.stderr && (
        <details open={status === "failed"} style={{ marginTop: 4 }}>
          <summary className="exec-card-inner__stderr-summary">stderr</summary>
          <pre className="exec-card-inner__pre exec-card-inner__pre--err">
            {ex.stderr}
          </pre>
        </details>
      )}
      {ex.error && !ex.stderr && (
        <div className="exec-card-inner__error">{ex.error}</div>
      )}
      {ex.reason && (
        <div className="exec-card-inner__reason">{ex.reason}</div>
      )}
    </div>
  );
}
