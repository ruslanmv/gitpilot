import React from "react";
import PlanView from "./PlanView.jsx";

export default function AssistantMessage({ answer, plan, executionLog }) {
  return (
    <div className="chat-message-ai">
      {/* Answer section */}
      <section className="gp-section gp-section-answer">
        <header className="gp-section-header">
          <h3>Answer</h3>
        </header>
        <div className="gp-section-content">
          <p>{answer}</p>
        </div>
      </section>

      {/* Action Plan section */}
      {plan && (
        <section className="gp-section gp-section-plan">
          <header className="gp-section-header">
            <h3>Action plan</h3>
          </header>
          <div className="gp-section-content">
            <PlanView plan={plan} />
          </div>
        </section>
      )}

      {/* Execution Log section (shown after execution) */}
      {executionLog && (
        <section className="gp-section gp-section-execution">
          <header className="gp-section-header">
            <h3>Execution log</h3>
          </header>
          <div className="gp-section-content">
            <ul className="execution-steps">
              {executionLog.steps.map((s) => (
                <li key={s.step_number} className="execution-step">
                  <span className="execution-step-number">Step {s.step_number}</span>
                  <span className="execution-step-summary">{s.summary}</span>
                </li>
              ))}
            </ul>
          </div>
        </section>
      )}
    </div>
  );
}
