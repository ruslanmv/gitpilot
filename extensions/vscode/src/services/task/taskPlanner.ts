import type { PlanSummary, PlanStepSummary } from "../../core/types";

export class TaskPlanner {
  planFromPrompt(prompt: string): PlanSummary {
    const normalized = prompt.trim();
    const goal = normalized.split("\n")[0].slice(0, 120) || "Workspace task";
    const lower = normalized.toLowerCase();

    const steps: PlanStepSummary[] = [
      {
        step: 1,
        title: "Inspect workspace context",
        action: "inspect",
        description: "Inspect the repository, active files, and surrounding context.",
        status: "ready",
      },
      {
        step: 2,
        title: this.pickScopeTitle(lower),
        action: "scope",
        description: "Identify the files in scope and verify the implementation path.",
        status: "pending",
      },
      {
        step: 3,
        title: this.pickChangeTitle(lower),
        action: "edit",
        description: "Prepare targeted changes with minimal risk and explicit diffs.",
        status: "pending",
      },
      {
        step: 4,
        title: this.pickValidationTitle(lower),
        action: "validate",
        description: "Validate edge cases, tests, and consistency across related files.",
        status: "pending",
      },
      {
        step: 5,
        title: "Summarize outcome",
        action: "summarize",
        description: "Present the result, changed files, and any remaining follow-up work.",
        status: "pending",
      },
    ];

    return {
      goal,
      summary: "GitPilot will inspect the workspace, scope the relevant files, prepare changes, validate the result, and summarize the outcome.",
      steps,
    };
  }

  refreshPlan(previous: PlanSummary | undefined, prompt: string): PlanSummary {
    if (!previous || !previous.steps.length) {
      return this.planFromPrompt(prompt);
    }
    return {
      ...previous,
      summary: previous.summary || "Plan refreshed for the current task.",
      steps: previous.steps.map((step, index) => ({
        ...step,
        step: index + 1,
        status: index === 0 ? "ready" : step.status === "applied" ? "applied" : "pending",
      })),
    };
  }

  private pickScopeTitle(lower: string): string {
    if (lower.includes("test")) return "Identify test targets";
    if (lower.includes("auth")) return "Trace auth flow";
    if (lower.includes("ui") || lower.includes("panel") || lower.includes("webview")) return "Locate UI surfaces";
    return "Identify files in scope";
  }

  private pickChangeTitle(lower: string): string {
    if (lower.includes("refactor")) return "Refactor targeted modules";
    if (lower.includes("fix")) return "Apply targeted fix";
    if (lower.includes("test")) return "Add or update tests";
    return "Prepare code changes";
  }

  private pickValidationTitle(lower: string): string {
    if (lower.includes("security")) return "Review security edges";
    if (lower.includes("test")) return "Verify test coverage";
    return "Review edge cases";
  }
}
