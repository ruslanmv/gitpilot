import { ChatClient } from "../api/chatClient";
import { StateStore } from "../core/stateStore";
import { ContextAssembler } from "../services/context/contextAssembler";
import { ProjectContextService } from "../services/context/projectContextService";
import { ProjectIndexCache } from "../services/context/projectIndexCache";
import { WorkingSetService } from "../services/context/workingSetService";
import { ErrorTranslator } from "../services/workspace/errorTranslator";
import { TaskMapper } from "../services/task/taskMapper";
import { TaskPlanner } from "../services/task/taskPlanner";
import type { ChatIntent, StructuredContextBundle, StructuredProjectContext, WorkspaceMode } from "../core/types";

export class ChatOrchestrator {
  constructor(
    private readonly chatClient: ChatClient,
    private readonly stateStore: StateStore,
    private readonly projectContextService: ProjectContextService,
    private readonly workingSetService: WorkingSetService,
    private readonly contextAssembler: ContextAssembler,
    private readonly projectIndexCache: ProjectIndexCache<StructuredProjectContext>,
    private readonly errorTranslator: ErrorTranslator,
    private readonly taskMapper = new TaskMapper(),
    private readonly taskPlanner = new TaskPlanner(),
  ) {}

  async buildStructuredContext(input: {
    rawText: string;
    intent: ChatIntent | string;
    workspaceRoot?: string;
    repoRoot?: string;
    repoName?: string;
    branch?: string;
    mode?: WorkspaceMode;
  }): Promise<StructuredContextBundle> {
    const resolvedMode: WorkspaceMode = input.mode || (input.repoRoot ? "local_git" : "folder");
    const cacheKey = [resolvedMode, input.workspaceRoot || "", input.repoRoot || "", input.repoName || "", input.branch || ""].join("::");

    let projectContext = this.projectIndexCache.get(cacheKey);
    if (!projectContext) {
      projectContext = this.projectContextService.build({
        workspaceRoot: input.workspaceRoot,
        repoRoot: input.repoRoot,
        repoName: input.repoName,
        branch: input.branch,
        mode: resolvedMode,
      });
      if (projectContext) this.projectIndexCache.set(cacheKey, projectContext);
    }

    const workingSet = this.workingSetService.build(input.workspaceRoot);
    const taskContext = this.contextAssembler.buildTaskContext({
      intent: String(input.intent),
      rawMessage: input.rawText,
      workingSet,
    });
    const legacy_prompt = this.contextAssembler.buildLegacyPrompt(projectContext, workingSet, taskContext, input.rawText);

    this.stateStore.updateProjectContextSummary({
      mode: projectContext?.mode,
      repoName: projectContext?.repoName,
      branch: projectContext?.branch,
      indexedFiles: projectContext?.treeSummary?.length,
      languages: projectContext?.languages,
      manifests: projectContext?.manifests,
      keyFiles: projectContext?.keyFiles,
      indexedAt: projectContext?.indexedAt,
    });

    return { project_context: projectContext, working_set: workingSet, task_context: taskContext, legacy_prompt };
  }

  async sendMessage(input: {
    sessionId: string;
    rawText: string;
    intent: ChatIntent | string;
    topologyId?: string;
    workspaceRoot?: string;
    repoRoot?: string;
    repoName?: string;
    branch?: string;
    mode?: WorkspaceMode;
  }) {
    const structuredContext = await this.buildStructuredContext(input);
    const provisionalPlan = this.taskPlanner.planFromPrompt(input.rawText);
    this.stateStore.updateActiveTask({
      title: input.rawText.slice(0, 80),
      intent: String(input.intent),
      status: "planning",
      summary: "Preparing repository-aware request.",
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      plan: provisionalPlan,
      filesInScope: this.taskMapper.filesInScopeFromPaths([
        structuredContext.working_set?.currentFile,
        ...(structuredContext.working_set?.relatedFiles || []),
      ].filter((v): v is string => Boolean(v))),
    });

    try {
      this.stateStore.setTaskStatus("generating");
      const response = await this.chatClient.sendMessage({
        session_id: input.sessionId,
        message: structuredContext.legacy_prompt,
        topology_id: input.topologyId,
        intent: input.intent,
        project_context: structuredContext.project_context,
        working_set: structuredContext.working_set,
        task_context: structuredContext.task_context,
      });

      const filesInScope = response.filesInScope || this.taskMapper.filesInScopeFromPaths(
        response.references?.map((ref) => ref.path) || structuredContext.working_set?.relatedFiles || []
      );
      const edits = response.edits || [];
      this.stateStore.updateActiveTask({
        title: input.rawText.slice(0, 80),
        intent: String(input.intent),
        status: edits.length ? "ready_to_apply" : response.plan ? "reviewing" : "done",
        summary: response.answer,
        updatedAt: new Date().toISOString(),
        plan: response.plan || provisionalPlan,
        filesInScope,
        changedFiles: this.taskMapper.changedFilesFromEdits(edits),
        edits,
      });
      return response;
    } catch (error) {
      this.stateStore.setTaskStatus("failed", this.errorTranslator.translate(error));
      throw error;
    }
  }
}
