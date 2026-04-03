import type { PlanSummary } from "../../core/types";

export class TaskPlanner {
  planFromPrompt(prompt: string): PlanSummary {
    const goal = prompt.split("
")[0].slice(0, 120) || "Workspace task";
    return {
      goal,
      summary: "Investigate the workspace, identify relevant files, propose minimal safe changes, and summarize the result.",
      steps: [
        { step: 1, title: "Inspect context", action: "inspect", description: "Inspect project context and current working set.", status: "ready" },
        { step: 2, title: "Identify files", action: "scope", description: "Identify files in scope for the request.", status: "pending" },
        { step: 3, title: "Generate changes", action: "generate", description: "Generate patch proposals or code recommendations.", status: "pending" },
        { step: 4, title: "Review", action: "review", description: "Review the proposed changes and summarize risks.", status: "pending" },
      ],
    };
  }
}
