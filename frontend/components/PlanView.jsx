import React from "react";

export default function PlanView({ plan }) {
  if (!plan) return null;

  // Calculate totals for each action type
  const totals = { CREATE: 0, MODIFY: 0, DELETE: 0 };
  plan.steps.forEach((step) => {
    step.files.forEach((file) => {
      totals[file.action] = (totals[file.action] || 0) + 1;
    });
  });

  return (
    <div className="plan-card">
      <div className="plan-header">
        <div className="plan-goal">Plan for: {plan.goal}</div>
        <div className="plan-summary">{plan.summary}</div>
      </div>

      {/* Totals summary */}
      <div className="plan-totals">
        {totals.CREATE > 0 && (
          <span className="plan-total plan-total-create">
            {totals.CREATE} file{totals.CREATE !== 1 ? "s" : ""} to create
          </span>
        )}
        {totals.MODIFY > 0 && (
          <span className="plan-total plan-total-modify">
            {totals.MODIFY} file{totals.MODIFY !== 1 ? "s" : ""} to modify
          </span>
        )}
        {totals.DELETE > 0 && (
          <span className="plan-total plan-total-delete">
            {totals.DELETE} file{totals.DELETE !== 1 ? "s" : ""} to delete
          </span>
        )}
      </div>

      {/* Steps list */}
      <ol className="plan-steps">
        {plan.steps.map((s) => (
          <li key={s.step_number} className="plan-step">
            <div className="plan-step-header">
              <strong>{s.title}</strong>
            </div>
            <div className="plan-step-description">{s.description}</div>

            {/* Files list with action pills */}
            {s.files && s.files.length > 0 && (
              <ul className="plan-files">
                {s.files.map((file, idx) => (
                  <li key={idx} className="plan-file">
                    <span className={`gp-pill gp-pill-${file.action.toLowerCase()}`}>
                      {file.action}
                    </span>
                    <code className="plan-file-path">{file.path}</code>
                  </li>
                ))}
              </ul>
            )}

            {/* Risks */}
            {s.risks && (
              <div className="plan-step-risks">
                <span className="plan-risk-label">⚠️ Risks:</span> {s.risks}
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}
