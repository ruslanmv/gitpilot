import React from "react";

/**
 * PlanView — enterprise "Proposed execution plan" card.
 *
 * One main card. Subtle internal spacing instead of bordered nested boxes.
 * Steps are rendered as a vertical numbered timeline; file actions become
 * compact pill badges (READ / CREATE / MODIFY / DELETE / INDEX).
 *
 * The card itself is borderless — the surrounding AssistantMessage
 * already provides the outer container.
 */
export default function PlanView({ plan, planStatus }) {
  if (!plan) return null;

  const totals = { CREATE: 0, MODIFY: 0, DELETE: 0, INDEX: 0, READ: 0 };
  plan.steps.forEach((step) => {
    step.files.forEach((file) => {
      totals[file.action] = (totals[file.action] || 0) + 1;
    });
  });

  return (
    <div className="exec-card exec-card--plan">
      <div className="exec-card__head">
        <div className="exec-card__icon exec-card__icon--plan" aria-hidden="true">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" strokeWidth="2" strokeLinecap="round"
               strokeLinejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="8" y1="13" x2="16" y2="13" />
            <line x1="8" y1="17" x2="13" y2="17" />
          </svg>
        </div>
        <div className="exec-card__head-text">
          <div className="exec-card__eyebrow">Proposed execution plan</div>
          <div className="exec-card__title">{plan.goal}</div>
        </div>

        <div className="exec-card__head-right">
          {planStatus === "executed" && (
            <span className="exec-status-badge exec-status-badge--ok">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="3" strokeLinecap="round"
                   strokeLinejoin="round" aria-hidden="true">
                <polyline points="20 6 9 17 4 12" />
              </svg>
              Executed
            </span>
          )}
          {planStatus === "rejected" && (
            <span className="exec-status-badge exec-status-badge--muted">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2.6" strokeLinecap="round"
                   strokeLinejoin="round" aria-hidden="true">
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
              Rejected
            </span>
          )}

          <div className="exec-card__totals">
            {totals.CREATE > 0 && (
              <span className="exec-total exec-total--create">
                {totals.CREATE} create
              </span>
            )}
            {totals.MODIFY > 0 && (
              <span className="exec-total exec-total--modify">
                {totals.MODIFY} modify
              </span>
            )}
            {totals.DELETE > 0 && (
              <span className="exec-total exec-total--delete">
                {totals.DELETE} delete
              </span>
            )}
            {totals.INDEX > 0 && (
              <span className="exec-total exec-total--index">
                {totals.INDEX === 1 ? "1 setup" : `${totals.INDEX} setup`}
              </span>
            )}
          </div>
        </div>
      </div>

      <ol className="exec-timeline">
        {plan.steps.map((s, idx) => (
          <li key={s.step_number} className="exec-step">
            <div className="exec-step__rail" aria-hidden="true">
              <div className="exec-step__num">{s.step_number}</div>
              {idx < plan.steps.length - 1 && (
                <div className="exec-step__line" />
              )}
            </div>
            <div className="exec-step__body">
              <div className="exec-step__title">{s.title}</div>
              {s.description && (
                <p className="exec-step__desc">{s.description}</p>
              )}

              {s.files && s.files.length > 0 && (
                <ul className="exec-actions">
                  {s.files.map((file, fi) => (
                    <li key={fi} className="exec-action-row">
                      <span
                        className={`exec-badge exec-badge--${file.action.toLowerCase()}`}
                      >
                        {file.action}
                      </span>
                      <code className="exec-action__path">
                        {file.action === "INDEX"
                          ? "Build semantic index for this repo"
                          : file.path}
                      </code>
                    </li>
                  ))}
                </ul>
              )}

              {s.files && s.files.some((f) => f.action === "INDEX") && (
                <div className="exec-notice">
                  One-time semantic index build (~80 MB model, ~30 s, ~12 MB on
                  disk). No cloud calls. Click <strong>Reject plan</strong> to
                  skip — you'll be offered the grep fallback.
                </div>
              )}

              {s.risks && (
                <div className="exec-risk">
                  <span aria-hidden="true">⚠</span>
                  <span>{s.risks}</span>
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
