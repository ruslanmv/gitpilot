// frontend/utils/planKind.js
//
// Classify a /api/chat/plan response into the shape the chat should render.
// Extracted from ChatPanel so it can be exercised directly — this decision
// is the difference between showing a working plan and telling the user the
// model failed, and it is far too easy to get subtly wrong inline.
//
//   executable    — there is something to approve and run: a deterministic
//                   sandbox ExecutionPlan, or a step that CREATEs, MODIFYs,
//                   DELETEs or EXECUTEs a file.
//   informational — every step only READs, and the summary is a real answer.
//                   "What does this project do?" lands here: the READs were
//                   the research, the summary is the reply. No approval
//                   controls, because there is nothing to approve.
//   empty         — no steps, or nothing actionable and nothing said.
//                   Only this deserves the failure banner.

export const PLACEHOLDER_SUMMARY = "Here is the proposed plan for your request.";

/** File actions that mean "this plan does something". */
export const ACTIONABLE_FILE_ACTIONS = ["CREATE", "MODIFY", "DELETE", "EXECUTE"];

/** Steps live at the top level, or nested under `plan` on older responses. */
export function planSteps(data) {
  if (Array.isArray(data?.steps)) return data.steps;
  if (Array.isArray(data?.plan?.steps)) return data.plan.steps;
  return [];
}

/** The deterministic sandbox plan, if the backend attached one. */
export function executionPlanOf(data) {
  return data?.execution_plan || data?.plan?.execution_plan || null;
}

export function planSummary(data) {
  return data?.plan?.summary || data?.summary || data?.message || PLACEHOLDER_SUMMARY;
}

/**
 * @returns {"executable"|"informational"|"empty"}
 */
export function classifyPlan(data) {
  const steps = planSteps(data);

  // An ExecutionPlan is actionable by construction: the backend builds it in
  // pure Python from the resolved file path, never from model output. Its
  // presence is a stronger signal than anything in `steps`.
  const hasExecutable =
    Boolean(executionPlanOf(data)) ||
    steps.some(
      (s) =>
        Array.isArray(s?.files) &&
        s.files.some((f) => ACTIONABLE_FILE_ACTIONS.includes(f?.action)),
    );
  if (hasExecutable) return "executable";

  const isReadOnly =
    steps.length > 0 &&
    steps.every(
      (s) =>
        !Array.isArray(s?.files) ||
        s.files.length === 0 ||
        s.files.every((f) => f?.action === "READ"),
    );
  const summary = planSummary(data);
  const hasRealSummary = Boolean(summary) && summary !== PLACEHOLDER_SUMMARY;

  return isReadOnly && hasRealSummary ? "informational" : "empty";
}
