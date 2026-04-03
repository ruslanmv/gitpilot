import type { ProjectContext } from "./projectContextService";
import type { WorkingSet } from "./workingSetService";

export type TaskContext = {
  intent: string;
  scope: "workspace" | "selection" | "file";
  summary: string;
};

export class ContextAssembler {
  buildTaskContext(input: {
    intent: string;
    rawMessage: string;
    workingSet?: WorkingSet;
  }): TaskContext {
    const scope: "workspace" | "selection" | "file" = input.workingSet?.currentSelection
      ? "selection"
      : input.workingSet?.currentFile
      ? "file"
      : "workspace";

    const summaryParts = [
      `intent=${input.intent}`,
      scope !== "workspace" ? `scope=${scope}` : undefined,
      input.workingSet?.currentFile ? `current_file=${input.workingSet.currentFile}` : undefined,
    ].filter(Boolean);

    return {
      intent: input.intent,
      scope,
      summary: `${summaryParts.join(" ")} request=${input.rawMessage}`,
    };
  }

  buildLegacyPrompt(project?: ProjectContext, working?: WorkingSet, task?: TaskContext, userMessage?: string): string {
    const sections: string[] = [];

    if (project) {
      sections.push([
        "Project context:",
        project.repoName ? `Repo: ${project.repoName}` : undefined,
        project.branch ? `Branch: ${project.branch}` : undefined,
        project.mode ? `Mode: ${project.mode}` : undefined,
        project.languages.length ? `Languages: ${project.languages.join(", ")}` : undefined,
        project.manifests.length ? `Manifests: ${project.manifests.join(", ")}` : undefined,
        project.keyFiles.length ? `Key files: ${project.keyFiles.join(", ")}` : undefined,
        project.treeSummary.length ? `Tree:\n${project.treeSummary.map((e) => `- ${e.type}: ${e.path}`).join("\n")}` : undefined,
        project.readmePreview ? `README preview:\n${project.readmePreview}` : undefined,
      ].filter(Boolean).join("\n"));
    }

    if (working) {
      sections.push([
        "Working set:",
        working.currentFile ? `Current file: ${working.currentFile}` : undefined,
        working.languageId ? `Language: ${working.languageId}` : undefined,
        working.currentSelection ? `Selection:\n\`\`\`\n${working.currentSelection}\n\`\`\`` : undefined,
        working.openTabs.length ? `Open tabs: ${working.openTabs.join(", ")}` : undefined,
        working.relatedFiles.length ? `Related files: ${working.relatedFiles.join(", ")}` : undefined,
      ].filter(Boolean).join("\n"));
    }

    if (task) {
      sections.push(`Task context:\n${task.summary}`);
    }

    sections.push(`User request:\n${userMessage || ""}`);

    return sections.join("\n\n---\n\n");
  }
}
