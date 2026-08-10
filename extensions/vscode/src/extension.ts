/**
 * GitPilot VS Code Extension — Main Entry Point
 *
 * Production-ready activation flow with:
 * - stable command registration for Quick Actions
 * - workspace/repo-aware chat context
 * - setup wizard for first-run UX
 * - automatic local provider bootstrap
 * - automatic session creation
 * - deterministic "Explain project" behavior
 * - safer message handling and diagnostics
 * - state-driven chat synchronization for the Workspace webview
 * - reduced duplicate refresh/bootstrap churn
 */

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

import { GitPilotApiClient } from "./api/client";
import { StatusClient } from "./api/statusClient";
import { SessionClient } from "./api/sessionClient";
import { ChatClient } from "./api/chatClient";
import { SettingsClient } from "./api/settingsClient";
import { RepoClient } from "./api/repoClient";

import { getConfig, onConfigChange } from "./utils/config";
import { getWorkspaceContext, ensureGitRepo } from "./utils/context";
import { StatusBarManager } from "./utils/statusBar";

import { ChatViewProvider } from "./views/chatViewProvider";
import { SessionsTreeProvider } from "./tree/sessionsTreeProvider";
import { SkillsTreeProvider } from "./tree/skillsTreeProvider";

import { SecurityDiagnosticsProvider } from "./providers/securityDiagnostics";
import { GitPilotCodeLensProvider } from "./providers/codeLensProvider";
import { GitPilotCodeActionProvider } from "./providers/codeActionProvider";

import { AgentFlowPanel } from "./panels/agentFlowPanel";

import { registerChatCommands } from "./commands/chat";
import { registerReviewCommands } from "./commands/review";
import { registerSecurityCommands } from "./commands/security";
import { registerSkillCommands } from "./commands/skills";
import { registerServerCommands } from "./commands/server";
import { registerGitCommands } from "./commands/git";
import { registerWorkspaceCommands } from "./commands/workspaceCommands";
import { registerSetupCommands } from "./commands/setupCommands";
import { registerProviderCommands } from "./commands/providerCommands";
import { registerMcpGatewayCommands } from "./commands/mcpGatewayCommands";
import { McpGatewayClient } from "./api/mcpGatewayClient";
import { McpClient } from "./api/mcpClient";
import { McpForgeInstaller } from "./services/mcp/McpForgeInstaller";
import { registerSessionCommands } from "./commands/sessionCommands";
import { registerChatCommandsV2 } from "./commands/chatCommands";
import { registerPhase4Commands } from "./commands/phase4Commands";

import { StateStore } from "./core/stateStore";
import { Checkpoint, CheckpointClient } from "./api/checkpointClient";
import { DiagnosticsService } from "./services/diagnostics/DiagnosticsService";
import { GitPilotEvents } from "./core/events";

import { GitPilotServerController } from "./services/server/GitPilotServerController";
import { GitPilotNavView } from "./ui/webview/GitPilotNavView";
import { WorkspaceResolver } from "./services/workspace/workspaceResolver";
import { GitContextService } from "./services/workspace/gitContextService";
import { ModeResolver } from "./services/workspace/modeResolver";
import { ReadinessEvaluator } from "./services/workspace/readinessEvaluator";
import { SessionCoordinator } from "./services/workspace/sessionCoordinator";
import { ErrorTranslator } from "./services/workspace/errorTranslator";

import { GitPilotPanel } from "./ui/webview/GitPilotPanel";

import { ProjectContextService } from "./services/context/projectContextService";
import { WorkingSetService } from "./services/context/workingSetService";
import { ContextAssembler } from "./services/context/contextAssembler";
import { ProjectIndexCache } from "./services/context/projectIndexCache";

import {
  detectIntent,
  buildIntentPrefix,
} from "./services/chat/intentDetector";

import type {
  ExtensionToWebviewMessage,
  WorkspaceMode,
  StructuredProjectContext,
  StructuredWorkingSet,
  StructuredTaskContext,
  PlanSummary,
  ProposedEdit,
} from "./core/types";

type DisposableLike = { dispose(): void };

type QuickActionId =
  | "explain_project"
  | "review_file"
  | "fix_selection"
  | "generate_tests"
  | "security_scan";

type FileSummary = {
  path: string;
  type: "file" | "dir";
};

type ProjectSnapshot = {
  repoName: string;
  branch?: string;
  repoRoot?: string;
  workspaceFolder?: string;
  languages: string[];
  manifests: string[];
  keyFiles: string[];
  readmePreview?: string;
  tree: FileSummary[];
};

type StructuredContextBundle = {
  project_context?: StructuredProjectContext;
  working_set?: StructuredWorkingSet;
  task_context?: StructuredTaskContext;
  legacy_prompt: string;
};

type ChatMessageState = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  createdAt: string;
  plan?: PlanSummary;
};

const OUTPUT_CHANNEL_NAME = "GitPilot";
const PROJECT_TREE_LIMIT = 250;
const README_PREVIEW_MAX_CHARS = 4000;
const FILE_READ_MAX_CHARS = 12000;

const QUICK_ACTION_COMMANDS: ReadonlyArray<{
  id: string;
  action: QuickActionId;
}> = [
  { id: "gitpilot.explain_project", action: "explain_project" },
  { id: "gitpilot.review_file", action: "review_file" },
  { id: "gitpilot.fix_selection", action: "fix_selection" },
  { id: "gitpilot.generate_tests", action: "generate_tests" },
  { id: "gitpilot.security_scan", action: "security_scan" },
];

export function activate(context: vscode.ExtensionContext): void {
  const config = getConfig();
  const output = vscode.window.createOutputChannel(OUTPUT_CHANNEL_NAME);

  output.appendLine("[GitPilot] Activating extension...");

  const client = new GitPilotApiClient(config.serverUrl, config.githubToken);
  const statusBar = new StatusBarManager();

  const legacyChatProvider = new ChatViewProvider(context.extensionUri, client);

  const sessionsTree = new SessionsTreeProvider(client);
  const skillsTree = new SkillsTreeProvider(client);
  const securityProvider = new SecurityDiagnosticsProvider(client);
  const codeLensProvider = new GitPilotCodeLensProvider(config.showInlineHints);

  const stateStore = new StateStore();
  const events = new GitPilotEvents();

  const statusClient = new StatusClient(client);
  const sessionClient = new SessionClient(client);
  const chatClientV2 = new ChatClient(client);
  const settingsClient = new SettingsClient(client);
  // Checkpoints are what make Agent mode a choice rather than a one-way door.
  const checkpointClient = new CheckpointClient(client);

  /**
   * The answer to "it says connected, so why did that not work?".
   *
   * Logs every request, notices a backend built from a different commit than
   * the extension, and can print the whole picture on demand.
   */
  const diagnostics = new DiagnosticsService(
    client,
    output,
    context.extension.packageJSON.version as string
  );
  context.subscriptions.push(diagnostics);

  context.subscriptions.push(
    vscode.commands.registerCommand("gitpilot.diagnostics", async () => {
    const report = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Window, title: "Collecting GitPilot diagnostics\u2026" },
      () => diagnostics.report()
    );

    // A document, not the Output channel: it can be selected, copied into an
    // issue, and read without scrolling past everything else logged today.
    const doc = await vscode.workspace.openTextDocument({
      content: report,
      language: "markdown",
    });
      await vscode.window.showTextDocument(doc, { preview: false });
    })
  );
  const repoClient = new RepoClient(client);

  // Owns the local `gitpilot serve` process so the settings page can recover
  // from a stopped backend without sending the user to a terminal.
  const serverController = new GitPilotServerController(client, output);

  // MCP: attaching servers augments what the agents can do, and the installer
  // brings up a Context Forge to attach them to.
  const mcpClient = new McpClient(client);
  const forgeInstaller = new McpForgeInstaller(output);

  /** Open the branded settings tab, wired to the live backend clients. */
  const openSettingsPanel = async (): Promise<void> => {
    const { GitPilotSettingsPanel } = await import(
      "./ui/webview/GitPilotSettingsPanel"
    );
    GitPilotSettingsPanel.open({
      extensionUri: context.extensionUri,
      client,
      settingsClient,
      serverController,
      mcpClient,
      forgeInstaller,
    });
  };

  const workspaceResolver = new WorkspaceResolver();
  const gitContextService = new GitContextService();
  const modeResolver = new ModeResolver();
  const readinessEvaluator = new ReadinessEvaluator();
  const errorTranslator = new ErrorTranslator();

  const projectContextService = new ProjectContextService();
  const workingSetService = new WorkingSetService();
  const contextAssembler = new ContextAssembler();
  const projectIndexCache = new ProjectIndexCache<StructuredProjectContext>();

  const sessionCoordinator = new SessionCoordinator(
    sessionClient,
    stateStore,
    errorTranslator
  );

  void repoClient;
  void events;

  context.subscriptions.push(
    output,
    statusBar,
    securityProvider,
    stateStore,
    events,
    workspaceResolver,
    serverController,
    vscode.commands.registerCommand("gitpilot.openSettings", openSettingsPanel),
    {
      dispose: () => {
        try {
          client.dispose();
        } catch {
          // no-op
        }
      },
    } satisfies DisposableLike
  );

  let autoBootstrapInFlight = false;
  let projectSnapshotCache: ProjectSnapshot | undefined;
  let projectSnapshotCacheKey: string | undefined;

  let gitpilotPanel!: GitPilotPanel;

  const clearProjectCaches = (): void => {
    projectSnapshotCache = undefined;
    projectSnapshotCacheKey = undefined;

    const maybeClear = projectIndexCache as unknown as { clear?: () => void };
    if (typeof maybeClear.clear === "function") {
      maybeClear.clear();
    }
  };

  const postMessageToPanel = (message: ExtensionToWebviewMessage): void => {
    if (!gitpilotPanel) {
      return;
    }
    gitpilotPanel.postMessage(message);
  };

  /**
   * Workspace files, for `@` completion and the attach picker.
   *
   * Cached for a few seconds: `@` filters on every keystroke, and re-globbing
   * a large repository per character is the difference between a dropdown that
   * feels instant and one that stutters.
   */
  const FILE_INDEX_TTL_MS = 15_000;
  const FILE_INDEX_EXCLUDE =
    "**/{node_modules,.git,dist,out,build,.venv,venv,__pycache__,.next,target,vendor}/**";
  let fileIndexCache: { at: number; files: string[] } | undefined;

  const listWorkspaceFiles = async (): Promise<string[]> => {
    if (fileIndexCache && Date.now() - fileIndexCache.at < FILE_INDEX_TTL_MS) {
      return fileIndexCache.files;
    }

    const uris = await vscode.workspace.findFiles("**/*", FILE_INDEX_EXCLUDE, 3000);
    const files = uris
      .map((uri) => vscode.workspace.asRelativePath(uri, false))
      .sort((a, b) => a.localeCompare(b));

    fileIndexCache = { at: Date.now(), files };
    return files;
  };

  /**
   * Mirror the editor selection into the composer.
   *
   * A single-line caret is not a selection worth attaching — pushing
   * "user.ts:42" on every cursor move would make the chip flicker for no
   * benefit — so only a real range counts. The body travels with it, capped,
   * so the model sees the code rather than a coordinate.
   */
  const SELECTION_TEXT_LIMIT = 4000;

  const publishEditorContext = (): void => {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.uri.scheme !== "file" || editor.selection.isEmpty) {
      postMessageToPanel({ type: "EDITOR_CONTEXT", payload: undefined });
      return;
    }

    const sel = editor.selection;
    const start = sel.start.line + 1;
    const end = sel.end.line + 1;
    const text = editor.document.getText(sel);

    postMessageToPanel({
      type: "EDITOR_CONTEXT",
      payload: {
        file: vscode.workspace.asRelativePath(editor.document.uri, false),
        range: start === end ? `${start}` : `${start}-${end}`,
        text:
          text.length > SELECTION_TEXT_LIMIT
            ? `${text.slice(0, SELECTION_TEXT_LIMIT)}\n… (${text.length - SELECTION_TEXT_LIMIT} more characters)`
            : text,
      },
    });
  };

  context.subscriptions.push(
    vscode.window.onDidChangeTextEditorSelection(publishEditorContext),
    vscode.window.onDidChangeActiveTextEditor(publishEditorContext),
    // A saved file can change what is worth attaching, and the index is
    // cheap to drop.
    vscode.workspace.onDidCreateFiles(() => { fileIndexCache = undefined; }),
    vscode.workspace.onDidDeleteFiles(() => { fileIndexCache = undefined; })
  );

  const postErrorToPanel = (payload: {
    code: string;
    title: string;
    message: string;
    recoverable?: boolean;
  }): void => {
    postMessageToPanel({
      type: "ERROR",
      payload: {
        recoverable: true,
        ...payload,
      },
    });
  };

  const appendOutputError = (prefix: string, error: unknown): void => {
    output.appendLine(`${prefix}: ${String(error)}`);
  };

  const currentWorkspaceRoot = (): string | undefined => {
    const editorPath = vscode.window.activeTextEditor?.document?.uri?.fsPath;
    if (editorPath) {
      const folder = vscode.workspace.getWorkspaceFolder(
        vscode.Uri.file(editorPath)
      );
      if (folder?.uri.fsPath) {
        return folder.uri.fsPath;
      }
    }

    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  };

  const isWorkspaceTrusted = (): boolean => {
    try {
      return vscode.workspace.isTrusted;
    } catch {
      return true;
    }
  };

  const findGitRoot = (startPath?: string): string | undefined => {
    if (!startPath || !fs.existsSync(startPath)) {
      return undefined;
    }

    let current: string;
    try {
      current = fs.statSync(startPath).isDirectory()
        ? startPath
        : path.dirname(startPath);
    } catch {
      return undefined;
    }

    while (true) {
      const dotGit = path.join(current, ".git");
      if (fs.existsSync(dotGit)) {
        return current;
      }

      const parent = path.dirname(current);
      if (parent === current) {
        return undefined;
      }

      current = parent;
    }
  };

  const safeReadTextFile = (
    filePath: string,
    maxChars: number
  ): string | undefined => {
    try {
      if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
        return undefined;
      }

      const raw = fs.readFileSync(filePath, "utf8");
      if (raw.length <= maxChars) {
        return raw;
      }

      return `${raw.slice(0, maxChars)}\n\n...[truncated]`;
    } catch {
      return undefined;
    }
  };

  const listProjectTree = (
    root: string,
    limit = PROJECT_TREE_LIMIT
  ): FileSummary[] => {
    const results: FileSummary[] = [];
    const ignoredNames = new Set([
      ".git",
      "node_modules",
      ".next",
      ".turbo",
      ".venv",
      "venv",
      "__pycache__",
      "dist",
      "build",
      ".idea",
      ".vscode-test",
      ".pytest_cache",
      ".mypy_cache",
      ".ruff_cache",
      ".cache",
      "coverage",
      ".DS_Store",
    ]);

    const walk = (dir: string): void => {
      if (results.length >= limit) {
        return;
      }

      let entries: fs.Dirent[] = [];
      try {
        entries = fs.readdirSync(dir, { withFileTypes: true });
      } catch {
        return;
      }

      entries.sort((a, b) => {
        if (a.isDirectory() && !b.isDirectory()) {
          return -1;
        }
        if (!a.isDirectory() && b.isDirectory()) {
          return 1;
        }
        return a.name.localeCompare(b.name);
      });

      for (const entry of entries) {
        if (results.length >= limit) {
          return;
        }

        if (ignoredNames.has(entry.name)) {
          continue;
        }

        const abs = path.join(dir, entry.name);
        const rel = path.relative(root, abs).replace(/\\/g, "/");

        if (!rel) {
          continue;
        }

        if (entry.isDirectory()) {
          results.push({ path: `${rel}/`, type: "dir" });
          walk(abs);
        } else if (entry.isFile()) {
          results.push({ path: rel, type: "file" });
        }
      }
    };

    walk(root);
    return results;
  };

  const detectProjectLanguages = (tree: FileSummary[]): string[] => {
    const exts = new Set<string>();

    for (const entry of tree) {
      if (entry.type !== "file") {
        continue;
      }

      const ext = path.extname(entry.path).toLowerCase();
      if (ext) {
        exts.add(ext);
      }
    }

    const map: Record<string, string> = {
      ".ts": "TypeScript",
      ".tsx": "TypeScript React",
      ".js": "JavaScript",
      ".jsx": "JavaScript React",
      ".py": "Python",
      ".go": "Go",
      ".rs": "Rust",
      ".java": "Java",
      ".kt": "Kotlin",
      ".php": "PHP",
      ".rb": "Ruby",
      ".cs": "C#",
      ".cpp": "C++",
      ".c": "C",
      ".h": "C/C++ Header",
      ".swift": "Swift",
      ".scala": "Scala",
      ".r": "R",
      ".lua": "Lua",
      ".sql": "SQL",
      ".sh": "Shell",
      ".yaml": "YAML",
      ".yml": "YAML",
      ".json": "JSON",
      ".md": "Markdown",
    };

    const langs = new Set<string>();
    for (const ext of exts) {
      const language = map[ext];
      if (language) {
        langs.add(language);
      }
    }

    return [...langs].sort((a, b) => a.localeCompare(b));
  };

  const findManifestFiles = (tree: FileSummary[]): string[] => {
    const known = new Set([
      "package.json",
      "package-lock.json",
      "pnpm-lock.yaml",
      "yarn.lock",
      "tsconfig.json",
      "pyproject.toml",
      "requirements.txt",
      "poetry.lock",
      "Pipfile",
      "Cargo.toml",
      "Cargo.lock",
      "go.mod",
      "go.sum",
      "pom.xml",
      "build.gradle",
      "build.gradle.kts",
      "composer.json",
      "Gemfile",
      "Dockerfile",
      "docker-compose.yml",
      "docker-compose.yaml",
      "README.md",
      "README.rst",
      ".env.example",
      ".github/workflows",
    ]);

    return tree
      .map((entry) => entry.path.replace(/\/$/, ""))
      .filter((p) => known.has(p) || p.startsWith(".github/workflows/"))
      .slice(0, 40);
  };

  const findKeyFiles = (tree: FileSummary[]): string[] => {
    const priority = [
      "README.md",
      "README.rst",
      "src/",
      "app/",
      "server/",
      "backend/",
      "frontend/",
      "api/",
      "tests/",
      "__tests__/",
      "docs/",
      ".github/workflows/",
      "package.json",
      "pyproject.toml",
      "Cargo.toml",
      "go.mod",
      "pom.xml",
      "Dockerfile",
    ];

    const paths = tree.map((entry) => entry.path);
    const selected: string[] = [];

    for (const wanted of priority) {
      const match = paths.find((p) => p === wanted || p.startsWith(wanted));
      if (match && !selected.includes(match)) {
        selected.push(match);
      }
    }

    for (const p of paths) {
      if (selected.length >= 30) {
        break;
      }
      if (!selected.includes(p)) {
        selected.push(p);
      }
    }

    return selected;
  };

  const getProjectSnapshot = async (
    forceRefresh = false
  ): Promise<ProjectSnapshot | undefined> => {
    const workspaceRoot = currentWorkspaceRoot();
    const repoRoot = findGitRoot(workspaceRoot);
    const cacheKey = `${workspaceRoot || ""}::${repoRoot || ""}`;

    if (
      !forceRefresh &&
      projectSnapshotCache &&
      projectSnapshotCacheKey === cacheKey
    ) {
      return projectSnapshotCache;
    }

    const baseRoot = repoRoot || workspaceRoot;
    if (!baseRoot) {
      projectSnapshotCache = undefined;
      projectSnapshotCacheKey = undefined;
      return undefined;
    }

    const tree = listProjectTree(baseRoot, PROJECT_TREE_LIMIT);
    const readmePathCandidates = [
      path.join(baseRoot, "README.md"),
      path.join(baseRoot, "README.rst"),
      path.join(baseRoot, "readme.md"),
    ];

    const readmePath = readmePathCandidates.find((candidate) =>
      fs.existsSync(candidate)
    );

    const readmePreview = readmePath
      ? safeReadTextFile(readmePath, README_PREVIEW_MAX_CHARS)
      : undefined;

    const snapshot: ProjectSnapshot = {
      repoName:
        stateStore.state.workspace.git.repoName ||
        path.basename(baseRoot) ||
        "current-project",
      branch: stateStore.state.workspace.git.branch || undefined,
      repoRoot,
      workspaceFolder: workspaceRoot,
      languages: detectProjectLanguages(tree),
      manifests: findManifestFiles(tree),
      keyFiles: findKeyFiles(tree),
      readmePreview,
      tree,
    };

    projectSnapshotCache = snapshot;
    projectSnapshotCacheKey = cacheKey;
    return snapshot;
  };

  const getCurrentFileContext = (): {
    fileName?: string;
    languageId?: string;
    selectionText?: string;
    fullText?: string;
  } => {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      return {};
    }

    const document = editor.document;
    const selectionText = editor.selection?.isEmpty
      ? ""
      : document.getText(editor.selection);
    const fullTextRaw = document.getText();
    const fullText =
      fullTextRaw.length <= FILE_READ_MAX_CHARS
        ? fullTextRaw
        : `${fullTextRaw.slice(0, FILE_READ_MAX_CHARS)}\n\n...[truncated]`;

    return {
      fileName: document.fileName,
      languageId: document.languageId,
      selectionText: selectionText || undefined,
      fullText,
    };
  };

  const buildStructuredContext = async (
    rawText: string,
    intent: string
  ): Promise<StructuredContextBundle> => {
    const workspaceRoot = currentWorkspaceRoot();
    const repoRoot = findGitRoot(workspaceRoot);
    const state = stateStore.state;
    const sessionMode = state.session.mode;
    const resolvedMode: WorkspaceMode =
      sessionMode || (repoRoot ? "local_git" : "folder");

    const repoName =
      state.workspace.git.repoName ||
      state.workspace.folderName ||
      (repoRoot ? path.basename(repoRoot) : undefined);

    const cacheKey = [
      resolvedMode ?? "",
      workspaceRoot ?? "",
      repoRoot ?? "",
      repoName ?? "",
      state.workspace.git.branch ?? "",
    ].join("::");

    let projectContext = projectIndexCache.get(cacheKey);
    if (!projectContext) {
      projectContext = projectContextService.build({
        workspaceRoot,
        repoRoot,
        repoName,
        branch: state.workspace.git.branch,
        mode:
          resolvedMode === "github"
            ? "github"
            : repoRoot
              ? "local_git"
              : "folder",
      });

      if (projectContext) {
        projectIndexCache.set(cacheKey, projectContext);
      }
    }

    const workingSet = workingSetService.build(workspaceRoot);
    const taskContext = contextAssembler.buildTaskContext({
      intent,
      rawMessage: rawText,
      workingSet,
    });

    const legacy_prompt = contextAssembler.buildLegacyPrompt(
      projectContext,
      workingSet,
      taskContext,
      rawText
    );

    return {
      project_context: projectContext,
      working_set: workingSet,
      task_context: taskContext,
      legacy_prompt,
    };
  };

  const toPlanSummary = (plan: unknown): PlanSummary | undefined => {
    if (!plan || typeof plan !== "object") {
      return undefined;
    }

    const raw = plan as {
      goal?: unknown;
      summary?: unknown;
      steps?: unknown;
    };

    const rawSteps = Array.isArray(raw.steps) ? raw.steps : [];

    const normalizePlanStepStatus = (
      status: unknown
    ): PlanSummary["steps"][number]["status"] => {
      if (status === "failed" || status === "applied" || status === "pending" || status === "ready") {
        return status;
      }
      return "pending";
    };

    return {
      goal:
        typeof raw.goal === "string" && raw.goal.trim()
          ? raw.goal
          : "Complete the requested task",
      summary:
        typeof raw.summary === "string" && raw.summary.trim()
          ? raw.summary
          : "GitPilot generated a task plan.",
      steps: rawSteps.map((step, index) => {
        const item =
          step && typeof step === "object"
            ? (step as {
                id?: unknown;
                title?: unknown;
                description?: unknown;
                status?: unknown;
                file?: unknown;
                action?: unknown;
              })
            : {};

        return {
          step: index + 1,
          id:
            typeof item.id === "string" && item.id.trim()
              ? item.id
              : `step-${index + 1}`,
          title:
            typeof item.title === "string" && item.title.trim()
              ? item.title
              : typeof item.action === "string" && item.action.trim()
                ? item.action
                : `Step ${index + 1}`,
          description:
            typeof item.description === "string"
              ? item.description
              : typeof item.file === "string"
                ? item.file
                : "Planned task step",
          status: normalizePlanStepStatus(item.status),
          file:
            typeof item.file === "string" && item.file.trim()
              ? item.file
              : undefined,
          action:
            typeof item.action === "string" && item.action.trim()
              ? item.action
              : "planned_action",
        };
      }),
    };
  };

  const appendChatMessageToState = (message: ChatMessageState): void => {
    const store = stateStore as unknown as {
      appendChatMessage?: (msg: ChatMessageState) => void;
      updateChat?: (patch: { messages: ChatMessageState[] }) => void;
      state: {
        chat?: {
          messages?: ChatMessageState[];
        };
      };
    };

    if (typeof store.appendChatMessage === "function") {
      store.appendChatMessage(message);
      return;
    }

    const currentMessages = store.state.chat?.messages ?? [];
    if (typeof store.updateChat === "function") {
      store.updateChat({
        messages: [...currentMessages, message],
      });
      return;
    }

    output.appendLine(
      "[GitPilot] Warning: StateStore has no appendChatMessage/updateChat method."
    );
  };

  const syncResponsePlanToState = (response: {
    answer?: string;
    plan?: unknown;
    message_id?: string;
  }): void => {
    const normalizedPlan = toPlanSummary(response.plan);
    if (!normalizedPlan) {
      return;
    }

    const currentTask = stateStore.state.activeTask || {};

    stateStore.updateActiveTask({
      ...currentTask,
      title: currentTask.title || normalizedPlan.goal || "Task in progress",
      summary:
        currentTask.summary ||
        normalizedPlan.summary ||
        "GitPilot generated a plan and is preparing scoped changes.",
      status: currentTask.status === "idle" ? "planning" : currentTask.status,
      plan: normalizedPlan,
    });
  };

  const registerCommand = (
    commandId: string,
    handler: (...args: unknown[]) => unknown | Promise<unknown>
  ): void => {
    context.subscriptions.push(
      vscode.commands.registerCommand(commandId, async (...args: unknown[]) => {
        output.appendLine(`[GitPilot] Command invoked: ${commandId}`);
        await handler(...args);
      })
    );
  };

  const logCommandAvailability = async (): Promise<void> => {
    try {
      const commands = await vscode.commands.getCommands(true);
      for (const cmd of QUICK_ACTION_COMMANDS) {
        if (!commands.includes(cmd.id)) {
          output.appendLine(
            `[GitPilot] Missing command registration: ${cmd.id}`
          );
        }
      }
    } catch (error: unknown) {
      appendOutputError(
        "[GitPilot] Failed to inspect command registry",
        error
      );
    }
  };

  const refreshStatusAndBootstrap = async (reason: string): Promise<void> => {
    if (autoBootstrapInFlight) {
      output.appendLine(
        `[GitPilot] Bootstrap skipped (already running): ${reason}`
      );
      return;
    }

    autoBootstrapInFlight = true;

    try {
      output.appendLine(`[GitPilot] Bootstrap check: ${reason}`);

      await vscode.commands.executeCommand("gitpilot.refreshStatus");

      let needsFinalRefresh = false;
      let state = stateStore.state;

      if (!state.workspace.folderOpen) {
        return;
      }

      const autoProvider =
        await settingsClient.bootstrapLocalProviderDefaults();

      if (autoProvider.changed) {
        output.appendLine(
          `[GitPilot] Auto-configured provider=${autoProvider.provider} model=${autoProvider.model}`
        );
        needsFinalRefresh = true;
      }

      if (needsFinalRefresh) {
        await vscode.commands.executeCommand("gitpilot.refreshStatus");
        state = stateStore.state;
        needsFinalRefresh = false;
      }

      if (
        state.readiness.canChat &&
        !state.session.active &&
        state.session.status !== "creating"
      ) {
        output.appendLine("[GitPilot] Auto-starting session...");
        await sessionCoordinator.ensureActiveSession();
        needsFinalRefresh = true;
      }

      if (needsFinalRefresh) {
        await vscode.commands.executeCommand("gitpilot.refreshStatus");
      }

      await getProjectSnapshot(true);
    } catch (error: unknown) {
      appendOutputError(`[GitPilot] Bootstrap error (${reason})`, error);
    } finally {
      autoBootstrapInFlight = false;
    }
  };

  const ensureSessionReady = async (): Promise<string | undefined> => {
    const state = stateStore.state;

    // Fast path: session already exists
    if (state.session.sessionId) {
      return state.session.sessionId;
    }

    // If readiness hasn't been evaluated yet (first click, cold start),
    // wait for the status refresh to complete instead of returning
    // undefined immediately. This prevents losing the user's first
    // click on WSL where /api/status takes 10+ seconds.
    if (!state.readiness.canChat) {
      output.appendLine(
        "[GitPilot] Chat not ready yet — waiting for status refresh..."
      );
      try {
        await vscode.commands.executeCommand("gitpilot.refreshStatus");
      } catch {
        // ignore — readiness will be re-evaluated below
      }
    }

    // Re-check after refresh
    const refreshedState = stateStore.state;
    if (!refreshedState.readiness.canChat) {
      output.appendLine(
        "[GitPilot] Still not ready to chat after refresh"
      );
      return undefined;
    }

    if (!refreshedState.session.active && refreshedState.session.status !== "creating") {
      await sessionCoordinator.ensureActiveSession();
    }

    await vscode.commands.executeCommand("gitpilot.refreshStatus");
    return stateStore.state.session.sessionId;
  };

  const runSetupWizard = async (): Promise<void> => {
    const workspaceRoot = currentWorkspaceRoot();
    const repoRoot = findGitRoot(workspaceRoot);
    const trusted = isWorkspaceTrusted();

    const choices: Array<
      vscode.QuickPickItem & { run: () => Promise<void> }
    > = [];

    if (!trusted) {
      choices.push({
        label: "Trust this workspace",
        description: "Required for full repo-aware features",
        run: async () => {
          await vscode.commands.executeCommand("workbench.trust.manage");
        },
      });
    }

    if (!workspaceRoot) {
      choices.push({
        label: "Open a folder",
        description: "Select the project folder to use with GitPilot",
        run: async () => {
          await vscode.commands.executeCommand(
            "workbench.action.files.openFolder"
          );
        },
      });
    }

    if (workspaceRoot && !repoRoot) {
      choices.push({
        label: "Initialize Git repository",
        description: "Set up this folder as a Git repository",
        run: async () => {
          await ensureGitRepo();
        },
      });
    }

    choices.push(
      {
        label: "Select provider",
        description:
          "Choose Ollama, OpenAI, Claude-compatible, or another provider",
        run: async () => {
          await vscode.commands.executeCommand("gitpilot.selectProviderV2");
        },
      },
      {
        label: "Select model",
        description: "Choose the model used for chat and quick actions",
        run: async () => {
          await vscode.commands.executeCommand("gitpilot.selectModelV2");
        },
      },
      {
        label: "Refresh project scan",
        description: "Rebuild repo-aware context for the current workspace",
        run: async () => {
          clearProjectCaches();
          await getProjectSnapshot(true);
          vscode.window.showInformationMessage(
            "GitPilot project scan refreshed."
          );
        },
      }
    );

    const picked = await vscode.window.showQuickPick(choices, {
      placeHolder: "GitPilot Setup Wizard — choose the next step",
      title: "GitPilot Setup Wizard",
      matchOnDescription: true,
    });

    if (!picked) {
      return;
    }

    try {
      await picked.run();
      await refreshStatusAndBootstrap("setup-wizard");
      vscode.window.showInformationMessage("GitPilot setup step completed.");
    } catch (error: unknown) {
      appendOutputError("[GitPilot] Setup wizard action failed", error);
      vscode.window.showErrorMessage(errorTranslator.translate(error));
    }
  };

  // ── V2 streaming support (additive) ──────────────────────────────────
  // AbortController for the current SSE stream. Allows CANCEL_TASK to
  // terminate a running agent request immediately.
  let activeStreamAbort: AbortController | null = null;

  /**
   * Consume the backend SSE stream and forward each event to the webview.
   * Returns the accumulated assistant text, or null if the stream failed
   * (in which case the caller should fall back to batch mode).
   */
  const sendChatStreaming = async (
    serverUrl: string,
    sessionId: string,
    message: string,
    intent: string | undefined,
  ): Promise<string | null> => {
    activeStreamAbort = new AbortController();
    const signal = activeStreamAbort.signal;

    // Why this path gave up is the single most useful thing to know when a
    // question produces no answer, and until now none of it was recorded:
    // three different failures all returned a bare `null` and the log said
    // only "streaming unavailable". Each one now names itself, with the
    // event tally that distinguishes "server refused" from "server answered
    // with nothing" — which are the same value here and completely
    // different problems.
    const startedAt = Date.now();
    const seen: Record<string, number> = {};
    const elapsed = () => `${((Date.now() - startedAt) / 1000).toFixed(1)}s`;
    output.appendLine(
      `[GitPilot] stream → POST /api/v2/chat/stream session=${sessionId} intent=${intent ?? "none"} chars=${message.length}`
    );

    let res: Response;
    try {
      res = await fetch(`${serverUrl}/api/v2/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          message,
          permission_mode: ({ auto: "auto", ask: "normal", plan: "plan" } as Record<string, string>)[stateStore.state.executionMode] || "normal",
        }),
        signal,
      });
    } catch (err: unknown) {
      // Server doesn't support v2 or network error — fall back
      output.appendLine(
        `[GitPilot] stream ✗ unreachable after ${elapsed()} (${err instanceof Error ? err.message : String(err)}) → batch`
      );
      return null;
    }

    if (!res.ok || !res.body) {
      output.appendLine(
        `[GitPilot] stream ✗ HTTP ${res.status}${res.body ? "" : " (no body)"} after ${elapsed()} → batch`
      );
      return null;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let fullText = "";
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";

        for (const part of parts) {
          if (!part.startsWith("data: ")) continue;
          let event: Record<string, unknown>;
          try {
            event = JSON.parse(part.slice(6));
          } catch {
            continue;
          }

          const type = String(event.type || "");
          seen[type] = (seen[type] || 0) + 1;

          if (type === "text_delta") {
            fullText += String(event.text || "");
            postMessageToPanel({
              type: "CHAT_STREAM_CHUNK",
              payload: { content: String(event.text || "") },
            } as ExtensionToWebviewMessage);
          } else if (type === "tool_start") {
            postMessageToPanel({
              type: "AGENT_TOOL_ACTIVITY",
              payload: {
                id: String(event.tool_id || event.id || ""),
                name: String(event.name || ""),
                status: "running" as const,
                args: (event.arguments || {}) as Record<string, unknown>,
              },
            } as ExtensionToWebviewMessage);
          } else if (type === "tool_result") {
            postMessageToPanel({
              type: "AGENT_TOOL_ACTIVITY",
              payload: {
                id: String(event.tool_id || event.id || ""),
                name: String(event.name || ""),
                status: event.is_error ? "failed" as const : "completed" as const,
                result: String(event.result || "").slice(0, 500),
                is_error: Boolean(event.is_error),
              },
            } as ExtensionToWebviewMessage);
          } else if (type === "approval_needed") {
            postMessageToPanel({
              type: "TOOL_APPROVAL_REQUEST",
              payload: {
                id: String(event.request_id || ""),
                tool: String(event.tool || ""),
                args: (event.arguments || {}) as Record<string, unknown>,
                summary: String(event.summary || ""),
                diffPreview: event.diff_preview ? String(event.diff_preview) : undefined,
                riskLevel: (String(event.risk_level || "medium")) as "low" | "medium" | "high",
              },
            } as ExtensionToWebviewMessage);
          } else if (type === "plan_step") {
            postMessageToPanel({
              type: "PLAN_STEP_UPDATE",
              payload: {
                stepIndex: Number(event.step_index || 0),
                stepTitle: String(event.title || ""),
                action: String(event.action || ""),
                status: String(event.status || "pending"),
              },
            } as ExtensionToWebviewMessage);
          } else if (type === "terminal_output") {
            postMessageToPanel({
              type: "TERMINAL_OUTPUT",
              payload: {
                stream: String(event.stream || "stdout") as "stdout" | "stderr" | "exit",
                text: String(event.text || ""),
              },
            } as ExtensionToWebviewMessage);
          } else if (type === "terminal_exit") {
            postMessageToPanel({
              type: "TERMINAL_OUTPUT",
              payload: {
                stream: "exit" as const,
                text: `\n[Process exited with code ${event.exit_code ?? -1}]`,
                exitCode: Number(event.exit_code ?? -1),
              },
            } as ExtensionToWebviewMessage);
          } else if (type === "test_result") {
            postMessageToPanel({
              type: "TEST_RESULT",
              payload: {
                framework: String(event.framework || "unknown"),
                passed: Number(event.passed || 0),
                failed: Number(event.failed || 0),
                skipped: Number(event.skipped || 0),
                exitCode: Number(event.exit_code || 0),
              },
            } as ExtensionToWebviewMessage);
          } else if (type === "diagnostics") {
            postMessageToPanel({
              type: "DIAGNOSTICS_RESULT",
              payload: {
                errors: Number(event.errors || 0),
                warnings: Number(event.warnings || 0),
                entries: Array.isArray(event.entries) ? event.entries as Array<{ file: string; line: number; severity: string; message: string }> : [],
              },
            } as ExtensionToWebviewMessage);
          } else if (type === "status_change" && event.status !== "keepalive") {
            // Update task status in state store
            const statusMap: Record<string, string> = {
              planning: "planning",
              generating: "generating",
              executing: "generating",
              reviewing: "reviewing",
              done: "done",
            };
            const mapped = statusMap[String(event.status)] || undefined;
            if (mapped) {
              stateStore.updateActiveTask({
                ...(stateStore.state.activeTask || {}),
                status: mapped as any,
              });
            }
          } else if (type === "done") {
            postMessageToPanel({
              type: "CHAT_STREAM_END",
              payload: { usage: (event.usage || {}) as any },
            } as ExtensionToWebviewMessage);
          } else if (type === "error") {
            postMessageToPanel({
              type: "ERROR",
              payload: {
                code: "AGENT_ERROR",
                title: "Agent Error",
                message: String(event.error || "Unknown error"),
                recoverable: Boolean(event.recoverable ?? true),
              },
            } as ExtensionToWebviewMessage);
          }
        }
      }
    } catch (err: unknown) {
      if (signal.aborted) {
        output.appendLine("[GitPilot] SSE stream cancelled by user");
      } else {
        output.appendLine(`[GitPilot] SSE stream error: ${err}`);
      }
    } finally {
      activeStreamAbort = null;
    }

    const tally = Object.entries(seen)
      .map(([k, v]) => `${k}=${v}`)
      .join(" ") || "no events";

    if (!fullText) {
      // An empty stream is the backend's way of saying "this session is not
      // one I can plan for — use the batch endpoint". It is a normal
      // handover for folder-only sessions, not a fault, so it is logged as
      // a route rather than an error. The tally is what tells the two apart
      // when it is a fault: `done=1` alone is the handover, whereas a
      // `status_change` run that produced no text is a real failure to
      // generate.
      output.appendLine(
        `[GitPilot] stream → empty after ${elapsed()} (${tally}); backend handed off → batch`
      );
      return null;
    }

    output.appendLine(
      `[GitPilot] stream ✓ ${fullText.length} chars in ${elapsed()} (${tally})`
    );
    return fullText;
  };

  const sendChatToBackend = async (rawText: string): Promise<void> => {
    const text = rawText.trim();
    if (!text) {
      return;
    }

    const userMessage: ChatMessageState = {
      id: `user:${Date.now()}`,
      role: "user",
      content: text,
      createdAt: new Date().toISOString(),
    };

    appendChatMessageToState(userMessage);

    const currentTask = stateStore.state.activeTask || {};
    stateStore.updateActiveTask({
      ...currentTask,
      status:
        currentTask.status && currentTask.status !== "idle"
          ? currentTask.status
          : "planning",
      title: currentTask.title || "Working on your request",
      summary:
        currentTask.summary || "GitPilot is analyzing the repository context.",
    });

    const sessionId = await ensureSessionReady();

    if (!sessionId) {
      // Reset task status to idle so the webview clears the thinking
      // bubble. Without this, the animation stays stuck forever
      // because the early return skips the normal done/failed path.
      stateStore.updateActiveTask({
        ...(stateStore.state.activeTask || {}),
        status: "idle",
      });
      postErrorToPanel({
        code: "SESSION_NOT_READY",
        title: "GitPilot is finishing setup",
        message:
          "The backend is still starting up. Please try again in a few seconds.",
      });
      return;
    }

    try {
      const { intent, cleanMessage } = detectIntent(text);
      const normalizedMessage = cleanMessage || text;
      const intentPrefix = buildIntentPrefix(intent);

      const structuredContext = await buildStructuredContext(
        normalizedMessage,
        intent
      );

      const enrichedMessage = intentPrefix
        ? `[${intentPrefix}]\n\n${structuredContext.legacy_prompt}`
        : structuredContext.legacy_prompt;

      const workflowMode = stateStore.state.workflow.selectedMode;
      const topologyId =
        workflowMode && workflowMode !== "auto" ? workflowMode : undefined;

      // ── Try V2 SSE streaming first, fall back to batch ──
      const serverUrl = client.serverUrl;
      const streamedText = await sendChatStreaming(
        serverUrl,
        sessionId,
        enrichedMessage,
        intent
      );

      if (streamedText !== null) {
        // Streaming succeeded — finalize state
        const assistantMessage: ChatMessageState = {
          id: `assistant:${Date.now()}`,
          role: "assistant",
          content: streamedText,
          createdAt: new Date().toISOString(),
        };
        appendChatMessageToState(assistantMessage);

        stateStore.updateActiveTask({
          ...(stateStore.state.activeTask || {}),
          status: "done",
          summary: streamedText.slice(0, 500) || "GitPilot completed the request.",
        });

        output.appendLine(
          `[GitPilot] Streamed response received (${streamedText.length} chars). intent=${intent} session=${sessionId}`
        );
      } else {
        // Streaming produced nothing — the real answer comes from the batch
        // endpoint. Keep the thinking state up for it: this call is the slow
        // one, routinely 20-40s against a local model.
        //
        // The backend no longer reports "done" for a stream that did no
        // work, so this is a continuation rather than the repair of a
        // premature completion. It stays explicit because the stream may
        // still have advanced the status to "planning" before handing off.
        const batchStartedAt = Date.now();
        output.appendLine("[GitPilot] batch → POST /api/chat/send");

        stateStore.updateActiveTask({
          ...(stateStore.state.activeTask || {}),
          status: "generating",
          title: "Working on your request",
          summary: "",
        });

        const response = await chatClientV2.sendMessage({
          session_id: sessionId,
          message: enrichedMessage,
          topology_id: topologyId,
          intent,
          project_context: structuredContext.project_context,
          working_set: structuredContext.working_set,
          task_context: structuredContext.task_context,
        });

        const normalizedPlan = toPlanSummary(response.plan);

        const assistantMessage: ChatMessageState = {
          id: response.message_id || `assistant:${Date.now()}`,
          role: "assistant",
          content: response.answer,
          createdAt: new Date().toISOString(),
          plan: normalizedPlan,
        };

        appendChatMessageToState(assistantMessage);
        syncResponsePlanToState(response);

        // Extract proposed file edits from the backend response.
        // The backend now parses code blocks in the LLM answer and
        // returns them as structured ProposedEdit objects, enabling
        // the VS Code "Apply Patch" button to write files to disk.
        const responseEdits: ProposedEdit[] = Array.isArray(
          (response as unknown as Record<string, unknown>).edits
        )
          ? ((response as unknown as Record<string, unknown>).edits as ProposedEdit[])
          : [];

        const hasEdits = responseEdits.length > 0;

        const updatedTask = stateStore.state.activeTask || {};
        stateStore.updateActiveTask({
          ...updatedTask,
          status: normalizedPlan || hasEdits ? "ready_to_apply" : "done",
          edits: hasEdits ? responseEdits : updatedTask.edits,
          summary:
            normalizedPlan?.summary ||
            (hasEdits ? `${responseEdits.length} file(s) ready to apply.` : "") ||
            updatedTask.summary ||
            "GitPilot completed the request.",
        });

        output.appendLine(
          `[GitPilot] batch ✓ ${(response.answer || "").length} chars in ` +
            `${((Date.now() - batchStartedAt) / 1000).toFixed(1)}s ` +
            `(plan=${normalizedPlan ? "yes" : "no"} edits=${responseEdits.length}) ` +
            `intent=${intent} session=${sessionId}`
        );
      }
    } catch (error: unknown) {
      appendOutputError("[GitPilot] Chat request failed", error);

      stateStore.updateActiveTask({
        ...(stateStore.state.activeTask || {}),
        status: "failed",
      });

      postErrorToPanel({
        code: "CHAT_ERROR",
        title: "Chat Error",
        message: errorTranslator.translate(error),
      });
    }
  };

  const buildQuickActionPrompt = async (
    action: QuickActionId
  ): Promise<string | undefined> => {
    const ctx = getCurrentFileContext();
    const snapshot = await getProjectSnapshot(false);
    const repoName =
      snapshot?.repoName ||
      stateStore.state.workspace.git.repoName ||
      stateStore.state.workspace.folderName ||
      "current repository";

    switch (action) {
      case "explain_project":
        return [
          `Explain the current project "${repoName}".`,
          "Use the repository tree, README, manifests, and current workspace context.",
          "Describe:",
          "1. the project purpose",
          "2. the main modules and folders",
          "3. the architecture and runtime flow",
          "4. the developer workflow",
          "5. how a new developer should get started quickly",
          "Do not say that project context is missing unless the repo snapshot is genuinely empty.",
        ].join("\n");

      case "review_file": {
        if (!ctx.fileName) {
          vscode.window.showWarningMessage("Open a file first.");
          return undefined;
        }

        const fullText = ctx.fullText;

        return [
          "Review the current file for bugs, maintainability issues, risky code paths, and refactoring opportunities.",
          `File: ${ctx.fileName}`,
          ctx.selectionText
            ? `Focus especially on this selected code:\n\`\`\`${ctx.languageId || ""}\n${ctx.selectionText}\n\`\`\``
            : fullText
              ? `Review the full file content:\n\`\`\`${ctx.languageId || ""}\n${fullText}\n\`\`\``
              : "Review the full file using available workspace context.",
          "Be concrete and prioritize the most important issues first.",
        ].join("\n\n");
      }

      case "fix_selection": {
        if (!ctx.fileName) {
          vscode.window.showWarningMessage("Open a file first.");
          return undefined;
        }
        if (!ctx.selectionText?.trim()) {
          vscode.window.showWarningMessage("Select some code first.");
          return undefined;
        }

        return [
          `Fix the selected code in ${ctx.fileName}.`,
          "Explain the issue briefly, then provide the corrected version.",
          `Selected code:\n\`\`\`${ctx.languageId || ""}\n${ctx.selectionText}\n\`\`\``,
        ].join("\n\n");
      }

      case "generate_tests": {
        if (!ctx.fileName) {
          return [
            "Generate useful automated tests for the current project.",
            "Focus on realistic edge cases, failure paths, and key business logic.",
            "Infer the likely testing framework from the repository manifests and structure.",
          ].join("\n\n");
        }

        const fullText = ctx.fullText;

        return [
          `Generate useful automated tests for ${ctx.fileName}.`,
          "Focus on realistic edge cases, failure paths, and core business logic.",
          ctx.selectionText
            ? `Prioritize this selected code:\n\`\`\`${ctx.languageId || ""}\n${ctx.selectionText}\n\`\`\``
            : fullText
              ? `Use this file as the primary context:\n\`\`\`${ctx.languageId || ""}\n${fullText}\n\`\`\``
              : "Use the current file as the primary context.",
        ].join("\n\n");
      }

      case "security_scan":
        return [
          `Review the current project "${repoName}" for security issues.`,
          "Check for secrets exposure, unsafe code, risky defaults, insecure dependencies, insecure auth flows, injection risks, and attack surfaces.",
          "Prioritize findings by severity and explain concrete remediation steps.",
        ].join("\n\n");

      default:
        return undefined;
    }
  };

  client.onStateChange((connectionState) => {
    const connected = connectionState === "connected";

    statusBar.update(connectionState);
    legacyChatProvider.updateConnectionState(connected);
    stateStore.updateServer({ connected });

    if (!connected) {
      return;
    }

    sessionsTree.refresh();
    skillsTree.refresh();

    void refreshStatusAndBootstrap("client-connected");
  });

  gitpilotPanel = new GitPilotPanel(
    context.extensionUri,
    stateStore,
    async (msg) => {
      try {
        switch (msg.type) {
          case "INIT":
            void refreshStatusAndBootstrap("webview-init");
            return;

          case "START_SESSION":
            await sessionCoordinator.startSession(
              msg.payload.mode as WorkspaceMode
            );
            await vscode.commands.executeCommand("gitpilot.refreshStatus");
            return;

          case "CHANGE_MODE":
            stateStore.updateWorkspace({ mode: msg.payload.mode });
            clearProjectCaches();
            return;

          case "SEND_CHAT":
            await sendChatToBackend(msg.payload.text);
            return;

          case "REQUEST_FILE_INDEX":
            postMessageToPanel({
              type: "FILE_INDEX",
              payload: { files: await listWorkspaceFiles() },
            });
            return;

          case "PICK_CONTEXT_FILE": {
            const files = await listWorkspaceFiles();
            if (files.length === 0) {
              void vscode.window.showInformationMessage(
                "No files in this workspace to attach."
              );
              return;
            }
            const picked = await vscode.window.showQuickPick(files, {
              title: "Attach a file to the context",
              placeHolder: "Type to filter…",
            });
            if (picked) {
              postMessageToPanel({
                type: "ATTACH_CONTEXT_FILE",
                payload: { path: picked },
              });
            }
            return;
          }

          case "RUN_QUICK_ACTION": {
            const prompt = await buildQuickActionPrompt(
              msg.payload.action as QuickActionId
            );

            if (!prompt) {
              return;
            }

            await sendChatToBackend(prompt);
            return;
          }

          case "OPEN_SETTINGS":
            await openSettingsPanel();
            return;

          case "OPEN_WORKSPACE":
            await vscode.commands.executeCommand(
              "workbench.action.files.openFolder"
            );
            return;

          // Provider, model and admin entry points all land on the same
          // integrated settings page. Nothing here opens a browser.
          case "OPEN_ADMIN_UI":
          case "OPEN_PROVIDER_SETUP":
          case "OPEN_MODEL_SETUP":
          case "OPEN_LLM_SETTINGS":
            await openSettingsPanel();
            return;

          case "SET_WORKFLOW_MODE": {
            const selectedMode = msg.payload.mode;

            stateStore.updateWorkflow({
              selectedMode,
              source: selectedMode === "auto" ? "auto" : "user",
            });

            if (selectedMode !== "auto") {
              try {
                await client.setTopology(selectedMode);
              } catch {
                output.appendLine(
                  `[GitPilot] Warning: failed to persist topology "${selectedMode}" to backend.`
                );
              }
            }

            return;
          }

          case "SET_EXECUTION_MODE": {
            const execMode = msg.payload.mode;
            output.appendLine(`[GitPilot] Execution mode set to: ${execMode}`);
            stateStore.setExecutionMode(execMode);

            // Persist to VS Code settings
            const modeMap: Record<string, string> = { auto: "auto", ask: "normal", plan: "plan" };
            void vscode.workspace.getConfiguration("gitpilot").update(
              "permissionMode", modeMap[execMode] || "normal",
              vscode.ConfigurationTarget.Global
            );

            // Sync to backend
            try {
              const serverUrl = client.serverUrl;
              void fetch(`${serverUrl}/api/permissions/mode`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ mode: modeMap[execMode] || "normal" }),
              });
            } catch {
              output.appendLine("[GitPilot] Warning: failed to sync mode to backend");
            }
            return;
          }

          case "REFRESH_STATUS":
            await vscode.commands.executeCommand("gitpilot.refreshStatus");
            return;

          case "OPEN_SETUP_WIZARD":
            await vscode.commands.executeCommand("gitpilot.setupWizard");
            return;

          case "REFRESH_PROJECT_CONTEXT":
            await vscode.commands.executeCommand(
              "gitpilot.refreshProjectContext"
            );
            return;

          case "OPEN_CHANGED_FILE": {
            const folderPath = stateStore.state.workspace.folderPath;
            if (!folderPath) {
              vscode.window.showWarningMessage(
                "No workspace folder is currently open."
              );
              return;
            }

            const fileUri = vscode.Uri.file(
              path.join(folderPath, msg.payload.path)
            );
            await vscode.commands.executeCommand("vscode.open", fileUri);
            return;
          }

          case "OPEN_CHANGED_DIFF":
            await vscode.commands.executeCommand(
              "gitpilot.openChangedDiff",
              msg.payload.path
            );
            return;

          case "APPLY_PROPOSED_CHANGES":
            await vscode.commands.executeCommand("gitpilot.applyProposedChanges");
            return;

          case "REVERT_PROPOSED_CHANGES":
            await vscode.commands.executeCommand("gitpilot.revertProposedChanges");
            return;

          case "REWIND":
            await vscode.commands.executeCommand("gitpilot.rewind");
            return;

          case "REVEAL_FILE": {
            const folderPath = currentWorkspaceRoot();
            if (!folderPath) {
              postErrorToPanel({
                code: "NO_WORKSPACE_ROOT",
                title: "Reveal Failed",
                message: "No workspace folder is currently open.",
              });
              return;
            }

            const fileUri = vscode.Uri.file(
              path.join(folderPath, msg.payload.path)
            );
            await vscode.commands.executeCommand("revealInExplorer", fileUri);
            return;
          }

          case "REGENERATE_TASK_PLAN":
            await vscode.commands.executeCommand("gitpilot.regenerateTaskPlan");
            return;

          // ── V2 streaming messages ──
          case "TOOL_APPROVAL_RESPONSE": {
            const { id: approvalId, approved, scope } = msg.payload;
            output.appendLine(
              `[GitPilot] Approval response: ${approvalId} approved=${approved} scope=${scope}`
            );
            // Forward to backend approval endpoint
            try {
              const serverUrl = client.serverUrl;
              const sessionId = stateStore.state.session.sessionId || "";
              void fetch(`${serverUrl}/api/v2/approval/respond`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  session_id: sessionId,
                  request_id: approvalId,
                  approved,
                  scope: scope || "once",
                }),
              });
            } catch (err) {
              output.appendLine(`[GitPilot] Approval forward failed: ${err}`);
            }
            return;
          }

          case "CANCEL_TASK":
            output.appendLine("[GitPilot] Cancel task requested");
            if (activeStreamAbort) {
              activeStreamAbort.abort();
              activeStreamAbort = null;
            }
            stateStore.updateActiveTask({
              ...(stateStore.state.activeTask || {}),
              status: "failed",
              error: "Cancelled by user",
            });
            return;

          case "NEW_SESSION": {
            output.appendLine("[GitPilot] New session requested — clearing state");
            // Cancel any active stream
            if (activeStreamAbort) {
              activeStreamAbort.abort();
              activeStreamAbort = null;
            }
            // Reset stateStore chat + task but keep provider/workspace config
            stateStore.clearTaskState();
            if (stateStore.state.chat) {
              stateStore.state.chat.messages = [];
            }
            // Force a fresh session on the backend for the next message
            stateStore.updateSession({ sessionId: "", status: "idle" });
            // Notify webview — STATE_SYNC will fire via onDidChangeState
            output.appendLine("[GitPilot] State cleared, ready for new chat");
            return;
          }

          case "APPROVE_PLAN": {
            output.appendLine("[GitPilot] Plan approved by user — executing");
            // The plan is already stored in stateStore.activeTask.plan
            // Trigger execution by sending the plan to the executor
            const plan = stateStore.state.activeTask?.plan;
            if (plan) {
              stateStore.updateActiveTask({
                ...(stateStore.state.activeTask || {}),
                status: "generating",
              });
              // The approval is the trigger; there is no other stream to
              // delegate to, which is what the old catch here assumed.
              await vscode.commands.executeCommand("gitpilot.executeApprovedPlan");
            }
            return;
          }

          case "REJECT_PLAN": {
            output.appendLine("[GitPilot] Plan rejected by user");
            stateStore.updateActiveTask({
              ...(stateStore.state.activeTask || {}),
              status: "idle",
            });
            return;
          }

          default: {
            const exhaustiveCheck: never = msg;
            void exhaustiveCheck;
            return;
          }
        }
      } catch (error: unknown) {
        appendOutputError("[GitPilot] Webview message handling error", error);

        postErrorToPanel({
          code: "WEBVIEW_ACTION_ERROR",
          title: "Action Error",
          message: errorTranslator.translate(error),
        });
      }
    }
  );

  // The sidebar is navigation only; the workspace panel below it keeps every
  // feature and animation it already had.
  const navView = new GitPilotNavView({
    extensionUri: context.extensionUri,
    client,
    sessionClient,
    stateStore,
    serverController,
  });

  /**
   * The editor is the product surface, and there is exactly one of it.
   *
   * Empty, the tab is GitPilot Home — brand, "What are we building?", one
   * composer. Send a message and the same tab is the conversation. Two
   * surfaces each with their own composer only ever raised the question of
   * which one was real.
   */
  const openChatTab = async (): Promise<void> => {
    gitpilotPanel.openInEditor();
  };

  context.subscriptions.push(
    gitpilotPanel,
    vscode.commands.registerCommand("gitpilot.openChatTab", openChatTab),
    // Home and Chat are the same tab at two moments, so they open the same way.
    vscode.commands.registerCommand("gitpilot.openHome", openChatTab),
    vscode.window.registerWebviewViewProvider(
      GitPilotNavView.viewType,
      navView
    )
  );

  context.subscriptions.push(
    vscode.window.registerTreeDataProvider(
      "gitpilot.sessionsView",
      sessionsTree
    ),
    vscode.window.registerTreeDataProvider("gitpilot.skillsView", skillsTree)
  );

  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider(
      { scheme: "file" },
      codeLensProvider
    ),
    vscode.languages.registerCodeActionsProvider(
      { scheme: "file" },
      new GitPilotCodeActionProvider(),
      {
        providedCodeActionKinds:
          GitPilotCodeActionProvider.providedCodeActionKinds,
      }
    )
  );

  registerChatCommands(context, client, legacyChatProvider, {
    stateStore,
    sessionCoordinator,
    modeResolver,
    /**
     * Start the new task from a genuinely clean panel.
     *
     * Aborting first matters: a run still streaming into the old conversation
     * would carry on writing into the new one, because the panel starts a
     * fresh streaming node the moment a chunk arrives with none open.
     */
    resetPanel: () => {
      if (activeStreamAbort) {
        activeStreamAbort.abort();
        activeStreamAbort = null;
      }
      postMessageToPanel({ type: "SESSION_RESET" });
    },
  });
  registerReviewCommands(context, legacyChatProvider);
  registerSecurityCommands(context, securityProvider);
  registerSkillCommands(context, client, legacyChatProvider);
  registerServerCommands(context, client, legacyChatProvider);
  registerGitCommands(context, client, legacyChatProvider);

  registerWorkspaceCommands(
    context,
    stateStore,
    workspaceResolver,
    gitContextService,
    modeResolver,
    readinessEvaluator,
    statusClient
  );
  registerSetupCommands(context, stateStore);
  registerProviderCommands(context, stateStore, settingsClient);
  registerMcpGatewayCommands(context, new McpGatewayClient(client));
  registerSessionCommands(context, stateStore, sessionCoordinator);
  registerChatCommandsV2(context, stateStore, chatClientV2);
  // Phase 1–4 backend feature commands (doctor, wizard, runbooks, flags).
  registerPhase4Commands(context);

  registerCommand("gitpilot.showAgentFlow", () => {
    AgentFlowPanel.show(client, context.extensionUri);
  });

  registerCommand("gitpilot.refreshSessions", () => {
    sessionsTree.refresh();
  });

  registerCommand("gitpilot.refreshSkills", () => {
    skillsTree.refresh();
  });

  registerCommand("gitpilot.runCommand", async () => {
    const command = await vscode.window.showInputBox({
      prompt: "Enter command to run via GitPilot",
      placeHolder: "e.g. npm test",
      ignoreFocusOut: true,
    });

    if (!command) {
      return;
    }

    legacyChatProvider.sendMessageFromCommand(`Run this command: ${command}`);
    await vscode.commands.executeCommand("gitpilot.openChatTab");
  });

  registerCommand("gitpilot.setupWizard", async () => {
    await runSetupWizard();
  });

  registerCommand("gitpilot.refreshProjectContext", async () => {
    clearProjectCaches();
    await getProjectSnapshot(true);
    vscode.window.showInformationMessage("GitPilot project context refreshed.");
  });

  registerCommand("gitpilot.openChangedDiff", async (relativePath?: unknown) => {
    const folderPath = stateStore.state.workspace.folderPath;
    if (!folderPath || typeof relativePath !== "string" || !relativePath) {
      vscode.window.showWarningMessage(
        "Unable to open diff. No workspace folder or target file was provided."
      );
      return;
    }

    const filePath = path.join(folderPath, relativePath);
    const fileUri = vscode.Uri.file(filePath);
    await vscode.commands.executeCommand("vscode.open", fileUri);
  });

  registerCommand("gitpilot.applyProposedChanges", async () => {
    const task = stateStore.state.activeTask;
    const edits = task?.edits || [];
    const folderPath = stateStore.state.workspace.folderPath;

    if (!edits.length) {
      vscode.window.showInformationMessage("No proposed changes to apply.");
      return;
    }

    if (!folderPath) {
      vscode.window.showWarningMessage("No workspace folder open.");
      return;
    }

    try {
      stateStore.setTaskStatus("applying");
      const { PatchApplier } = await import("./services/patch/patchApplier");
      const patchApplier = new PatchApplier();
      const result = await patchApplier.apply(folderPath, edits);

      if (result.success) {
        // Post-apply: refresh project context so the tree/index
        // reflects the newly created/modified files, and clear
        // pending edits so the "Apply Patch" button disappears.
        stateStore.updateActiveTask({
          ...(stateStore.state.activeTask || {}),
          edits: [],
          status: "done",
          summary: `Applied ${result.appliedFiles?.length ?? edits.length} file(s). Context refreshing...`,
        });

        // Refresh project context in background
        void vscode.commands.executeCommand("gitpilot.refreshProjectContext");

        // Open the first applied file so the user sees the result
        if (result.appliedFiles?.length) {
          const firstFile = result.appliedFiles[0] as unknown;
          const firstPath = typeof firstFile === "string"
            ? firstFile
            : (firstFile as { path?: string })?.path || "";
          const fileUri = vscode.Uri.file(path.join(folderPath, firstPath));
          void vscode.commands.executeCommand("vscode.open", fileUri);
        }

        output.appendLine(
          `[GitPilot] Apply success: ${result.appliedFiles?.length ?? 0} files written`
        );
        vscode.window.showInformationMessage(
          `GitPilot applied ${result.appliedFiles?.length ?? edits.length} file(s) successfully.`
        );
      } else {
        stateStore.setTaskStatus("failed");
        output.appendLine("[GitPilot] Apply reported failure");
      }
    } catch (err) {
      stateStore.setTaskStatus("failed");
      appendOutputError("[GitPilot] Apply failed", err);
      vscode.window.showErrorMessage(`Failed to apply changes: ${err}`);
    }
  });

  /**
   * Put the workspace and the conversation back to a checkpoint.
   *
   * GitPilot snapshots before every mutating tool call, so this is the undo
   * that makes Agent mode a choice rather than a one-way door. Both halves go
   * back together — restoring files under a transcript that still describes
   * the work would leave the model reasoning about edits that no longer exist.
   */
  const rewindToCheckpoint = async (checkpoint: Checkpoint): Promise<void> => {
    const sessionId = stateStore.state.session.sessionId;
    if (!sessionId) {
      return;
    }

    const scope = checkpoint.has_files
      ? "This restores your files and rewinds the conversation."
      : "This workspace was too large to snapshot, so only the conversation rewinds. Your files are left alone.";

    const confirm = await vscode.window.showWarningMessage(
      `Rewind to "${checkpoint.description}"?`,
      { modal: true, detail: `${scope}\n\nWork done after this point is discarded.` },
      "Rewind"
    );
    if (confirm !== "Rewind") {
      return;
    }

    try {
      const result = await checkpointClient.rewind(sessionId, checkpoint.id);

      stateStore.setChatMessages(
        (result.messages || []).map((m, index) => ({
          id: `${sessionId}-rewind-${index}`,
          role: m.role === "user" || m.role === "assistant" ? m.role : "system",
          content: m.content ?? "",
          createdAt: m.timestamp || new Date().toISOString(),
        }))
      );
      // The plan, diff and changed-file list all described work that has just
      // been undone; leaving them on screen would be a lie.
      stateStore.clearTaskState();

      void vscode.commands.executeCommand("gitpilot.refreshProjectContext");
      vscode.window.showInformationMessage(
        checkpoint.has_files
          ? `Rewound to "${checkpoint.description}".`
          : `Rewound the conversation to "${checkpoint.description}". Files were not snapshotted.`
      );
    } catch (err) {
      appendOutputError("[GitPilot] Rewind failed", err);
      vscode.window.showErrorMessage(`Could not rewind: ${err}`);
    }
  };

  /** "2:34 PM" today, otherwise a short date — same rule as the sidebar. */
  const checkpointWhen = (iso: string): string => {
    const then = new Date(iso);
    if (Number.isNaN(then.getTime())) {
      return "";
    }
    const sameDay = then.toDateString() === new Date().toDateString();
    return sameDay
      ? then.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
      : then.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };

  const pickCheckpoint = async (): Promise<Checkpoint | undefined> => {
    const sessionId = stateStore.state.session.sessionId;
    if (!sessionId) {
      vscode.window.showInformationMessage(
        "No active GitPilot session, so there is nothing to rewind."
      );
      return undefined;
    }

    let checkpoints: Checkpoint[];
    try {
      checkpoints = await checkpointClient.list(sessionId);
    } catch (err) {
      appendOutputError("[GitPilot] Could not list checkpoints", err);
      vscode.window.showErrorMessage(`Could not load checkpoints: ${err}`);
      return undefined;
    }

    if (checkpoints.length === 0) {
      vscode.window.showInformationMessage(
        "No checkpoints yet. GitPilot takes one before every change it makes."
      );
      return undefined;
    }

    const picked = await vscode.window.showQuickPick(
      checkpoints.map((cp) => ({
        label: cp.description || cp.tool_name,
        description: checkpointWhen(cp.timestamp),
        detail: cp.has_files
          ? `Restores files · rewinds to message ${cp.message_index}`
          : "Conversation only — the workspace was too large to snapshot",
        checkpoint: cp,
      })),
      { title: "Rewind GitPilot", placeHolder: "Pick a point to go back to" }
    );

    return picked?.checkpoint;
  };

  registerCommand("gitpilot.rewind", async () => {
    const checkpoint = await pickCheckpoint();
    if (checkpoint) {
      await rewindToCheckpoint(checkpoint);
    }
  });

  /**
   * Undo the last thing GitPilot changed.
   *
   * The Revert button has been in the chat panel all along, wired to a command
   * that was never registered — so it silently did nothing. With checkpoints
   * it has an honest meaning: go back to the snapshot taken before the most
   * recent change.
   */
  registerCommand("gitpilot.revertProposedChanges", async () => {
    const task = stateStore.state.activeTask;

    // Changes that were only ever proposed are still just a pending edit list;
    // dropping it is the whole revert, and no checkpoint is needed.
    if ((task?.edits || []).length > 0 && task?.status !== "applying") {
      stateStore.updateActiveTask({
        ...(stateStore.state.activeTask || {}),
        edits: [],
        changedFiles: [],
        status: "idle",
        summary: "Proposed changes discarded.",
      });
      vscode.window.showInformationMessage("Discarded the proposed changes.");
      return;
    }

    const sessionId = stateStore.state.session.sessionId;
    if (!sessionId) {
      vscode.window.showInformationMessage("Nothing to revert.");
      return;
    }

    let checkpoints: Checkpoint[];
    try {
      checkpoints = await checkpointClient.list(sessionId);
    } catch (err) {
      appendOutputError("[GitPilot] Could not list checkpoints", err);
      vscode.window.showErrorMessage(`Could not load checkpoints: ${err}`);
      return;
    }

    const latest = checkpoints.find((cp) => cp.has_files) || checkpoints[0];
    if (!latest) {
      vscode.window.showInformationMessage(
        "No checkpoint to revert to — GitPilot has not changed anything in this session."
      );
      return;
    }

    await rewindToCheckpoint(latest);
  });

  /**
   * Run the plan the user just approved.
   *
   * "Approve & Execute" dispatched this command, which nobody had registered.
   * The `catch` around the dispatch logged "delegated to active stream" and
   * moved on — so the task sat at `generating` while nothing executed. There
   * is no other stream to delegate to; the approval *is* the trigger.
   */
  registerCommand("gitpilot.executeApprovedPlan", async () => {
    const plan = stateStore.state.activeTask?.plan;
    const steps = plan?.steps || [];

    if (steps.length === 0) {
      vscode.window.showInformationMessage("No approved plan to execute.");
      stateStore.setTaskStatus("idle");
      return;
    }

    const numbered = steps
      .map((step, index) => {
        const detail =
          typeof step === "string"
            ? step
            : step.title || step.description || step.action || "";
        return `${index + 1}. ${detail}`;
      })
      .filter((line) => line.trim().length > 3)
      .join("\n");

    await sendChatToBackend(
      `[Execute approved plan] The plan below was approved. Carry it out step by step.\n\n${numbered}`
    );
  });

  registerCommand("gitpilot.regenerateTaskPlan", async () => {
    const task = stateStore.state.activeTask;
    if (!task?.title && !task?.summary) {
      vscode.window.showInformationMessage("No active task to regenerate a plan for.");
      return;
    }

    const prompt = task.title || task.summary || "Regenerate the current task plan";
    stateStore.setTaskStatus("planning");
    await sendChatToBackend(`[Regenerate plan] ${prompt}`);
  });

  registerCommand("gitpilot.explain_project", async () => {
    const prompt = await buildQuickActionPrompt("explain_project");
    if (prompt) {
      await sendChatToBackend(prompt);
    }
  });

  registerCommand("gitpilot.review_file", async () => {
    const prompt = await buildQuickActionPrompt("review_file");
    if (prompt) {
      await sendChatToBackend(prompt);
    }
  });

  registerCommand("gitpilot.fix_selection", async () => {
    const prompt = await buildQuickActionPrompt("fix_selection");
    if (prompt) {
      await sendChatToBackend(prompt);
    }
  });

  registerCommand("gitpilot.generate_tests", async () => {
    const prompt = await buildQuickActionPrompt("generate_tests");
    if (prompt) {
      await sendChatToBackend(prompt);
    }
  });

  registerCommand("gitpilot.security_scan", async () => {
    const prompt = await buildQuickActionPrompt("security_scan");
    if (prompt) {
      await sendChatToBackend(prompt);
    }
  });

  context.subscriptions.push(
    onConfigChange((newConfig) => {
      output.appendLine("[GitPilot] Configuration updated.");

      client.setServerUrl(newConfig.serverUrl);
      client.setToken(newConfig.githubToken);
      codeLensProvider.setEnabled(newConfig.showInlineHints);

      if (newConfig.scanOnSave) {
        securityProvider.enableScanOnSave();
      }

      void refreshStatusAndBootstrap("config-change");
    })
  );

  if (config.scanOnSave) {
    securityProvider.enableScanOnSave();
  }

  if (config.autoConnect) {
    // Connecting once and giving up was the whole procedure. Now a server
    // that is merely slow to start gets waited for, and one that is not
    // running yet is watched for — so starting `gitpilot serve` in a terminal
    // is enough on its own, with no trip back here to click Reconnect.
    // The listener is registered before connect() resolves, so it would also
    // see the first success. One bootstrap per arrival, not two.
    let bootstrapped = false;
    const bootstrapOnce = (reason: string): void => {
      if (bootstrapped) {
        return;
      }
      bootstrapped = true;
      void refreshStatusAndBootstrap(reason);
    };

    context.subscriptions.push(
      client.onStateChange((state) => {
        if (state === "connected") {
          bootstrapOnce("reconnected");
        } else if (state === "disconnected") {
          // Ready to bootstrap again when it comes back.
          bootstrapped = false;
        }
      })
    );

    void client.connect().then((connected) => {
      if (connected) {
        output.appendLine(
          `[GitPilot] Connected to server at ${config.serverUrl}`
        );
        bootstrapOnce("auto-connect");
        return;
      }

      const probe = client.lastProbe;
      output.appendLine(
        `[GitPilot] Auto-connect failed for ${config.serverUrl}` +
          (probe ? ` (${probe.outcome} after ${probe.elapsedMs}ms)` : "")
      );
      client.startAutoReconnect();
    });
  }

  void initializeWorkspaceState({
    output,
    stateStore,
    workspaceResolver,
    gitContextService,
  }).then(async () => {
    clearProjectCaches();
    await getProjectSnapshot(true);
    void refreshStatusAndBootstrap("initial-workspace-sync");
  });

  context.subscriptions.push(
    workspaceResolver.onDidChange(async (info) => {
      stateStore.updateWorkspace({
        folderOpen: info.folderOpen,
        folderPath: info.folderPath,
        folderName: info.folderName,
      });

      clearProjectCaches();

      // ── Project isolation: when the workspace folder changes,
      // clear the chat and session so the new project starts fresh.
      // Without this, chat messages from the previous project
      // persist and confuse the user.
      stateStore.clearTaskState();
      if (stateStore.state.chat) {
        stateStore.state.chat.messages = [];
      }
      stateStore.updateSession({ sessionId: "", status: "idle" });
      output.appendLine(
        `[GitPilot] Workspace changed to "${info.folderName || "(none)"}" — session reset for project isolation`
      );

      if (info.folderPath) {
        try {
          const gitContext = await gitContextService.detect(info.folderPath);
          stateStore.updateWorkspace({ git: gitContext });
        } catch (error: unknown) {
          appendOutputError("[GitPilot] Failed to refresh git context", error);
        }
      }

      await getProjectSnapshot(true);
      await refreshStatusAndBootstrap("workspace-change");
    })
  );

  const workspaceContext = getWorkspaceContext();

  if (workspaceContext.workspaceRoot && !workspaceContext.isGitRepo) {
    output.appendLine(
      "[GitPilot] Folder mode detected. Git initialization is available from the Setup Wizard."
    );
  }

  if (workspaceContext.workspaceRoot) {
    if (workspaceContext.isGitRepo) {
      output.appendLine(
        `[GitPilot] Workspace: ${workspaceContext.repoOwner}/${workspaceContext.repoName} (branch: ${workspaceContext.branch || "HEAD"})`
      );
    } else {
      output.appendLine(
        `[GitPilot] Workspace: ${workspaceContext.workspaceRoot} (no git repo detected)`
      );
    }
  }

  if (!isWorkspaceTrusted()) {
    output.appendLine(
      "[GitPilot] Workspace is not trusted. Some features may be limited."
    );
  }

  void logCommandAvailability();

  /*
   * Open GitPilot only into an empty editor area.
   *
   * The landing page is the product's front door, but a front door that opens
   * on top of the file someone was reading is an interruption. So it appears
   * on a fresh window and stays out of the way otherwise — the sidebar's
   * New Task and the command palette are always there.
   */
  const editorIsEmpty = vscode.window.tabGroups.all.every(
    (group) => group.tabs.length === 0
  );
  if (
    editorIsEmpty &&
    vscode.workspace.getConfiguration("gitpilot").get<boolean>("showHomeOnStartup", true)
  ) {
    void openChatTab();
  }

  output.appendLine("[GitPilot] Extension activated.");
}

export function deactivate(): void {
  // VS Code disposes registered resources automatically.
}

async function initializeWorkspaceState(args: {
  output: vscode.OutputChannel;
  stateStore: StateStore;
  workspaceResolver: WorkspaceResolver;
  gitContextService: GitContextService;
}): Promise<void> {
  const { output, stateStore, workspaceResolver, gitContextService } = args;

  const workspaceInfo = workspaceResolver.resolve();

  stateStore.updateWorkspace({
    folderOpen: workspaceInfo.folderOpen,
    folderPath: workspaceInfo.folderPath,
    folderName: workspaceInfo.folderName,
  });

  if (!workspaceInfo.folderPath) {
    return;
  }

  try {
    const gitContext = await gitContextService.detect(workspaceInfo.folderPath);
    stateStore.updateWorkspace({ git: gitContext });
  } catch (error: unknown) {
    output.appendLine(
      `[GitPilot] Failed to detect initial git context: ${String(error)}`
    );
  }
}