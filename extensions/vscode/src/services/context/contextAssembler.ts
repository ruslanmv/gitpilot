import type {
  StructuredProjectContext,
  StructuredWorkingSet,
  StructuredTaskContext,
  StructuredContextBundle,
  ChatScope,
} from "../../core/types";

export type TaskContext = StructuredTaskContext;
export type ProjectContext = StructuredProjectContext;
export type WorkingSet = StructuredWorkingSet;

export class ContextAssembler {
  buildTaskContext(input: {
    intent: string;
    rawMessage: string;
    workingSet?: WorkingSet;
  }): TaskContext {
    const scope: ChatScope = input.workingSet?.currentSelection
      ? "selection"
      : input.workingSet?.currentFile
        ? "file"
        : "workspace";

    const summaryParts = [
      `intent=${input.intent}`,
      scope !== "workspace" ? `scope=${scope}` : undefined,
      input.workingSet?.currentFile
        ? `current_file=${input.workingSet.currentFile}`
        : undefined,
    ].filter(Boolean);

    return {
      intent: input.intent,
      scope,
      summary: `${summaryParts.join(" ")} request=${input.rawMessage}`,
    };
  }

  buildStructuredContextBundle(input: {
    project_context?: ProjectContext;
    working_set?: WorkingSet;
    task_context?: TaskContext;
    userMessage: string;
  }): StructuredContextBundle {
    return {
      project_context: input.project_context,
      working_set: input.working_set,
      task_context: input.task_context,
      legacy_prompt: this.buildLegacyPrompt(
        input.project_context,
        input.working_set,
        input.task_context,
        input.userMessage
      ),
    };
  }

  buildLegacyPrompt(
    project?: ProjectContext,
    working?: WorkingSet,
    task?: TaskContext,
    userMessage?: string
  ): string {
    const sections: string[] = [];

    if (project) {
      sections.push(
        [
          "Project context:",
          project.repoName ? `Repo: ${project.repoName}` : undefined,
          project.branch ? `Branch: ${project.branch}` : undefined,
          project.mode ? `Mode: ${project.mode}` : undefined,
          project.languages?.length
            ? `Languages: ${project.languages.join(", ")}`
            : undefined,
          project.manifests?.length
            ? `Manifests: ${project.manifests.join(", ")}`
            : undefined,
          project.keyFiles?.length
            ? `Key files: ${project.keyFiles.join(", ")}`
            : undefined,
          project.treeSummary?.length
            ? `Tree:\n${project.treeSummary
                .map((entry) => `- ${entry.type}: ${entry.path}`)
                .join("\n")}`
            : undefined,
          project.readmePreview
            ? `README preview:\n${project.readmePreview}`
            : undefined,
        ]
          .filter(Boolean)
          .join("\n")
      );
    }

    if (working) {
      sections.push(
        [
          "Working set:",
          working.currentFile ? `Current file: ${working.currentFile}` : undefined,
          working.languageId ? `Language: ${working.languageId}` : undefined,
          working.currentSelection
            ? `Selection:\n\`\`\`\n${working.currentSelection}\n\`\`\``
            : undefined,
          working.openTabs?.length
            ? `Open tabs: ${working.openTabs.join(", ")}`
            : undefined,
          working.relatedFiles?.length
            ? `Related files: ${working.relatedFiles.join(", ")}`
            : undefined,
        ]
          .filter(Boolean)
          .join("\n")
      );
    }

    if (task?.summary) {
      sections.push(`Task context:\n${task.summary}`);
    }

    sections.push(`User request:\n${userMessage || ""}`);

    return sections.join("\n\n---\n\n");
  }
}
