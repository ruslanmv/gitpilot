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
import { registerSessionCommands } from "./commands/sessionCommands";
import { registerChatCommandsV2 } from "./commands/chatCommands";

import { StateStore } from "./core/stateStore";
import { GitPilotEvents } from "./core/events";

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
  const repoClient = new RepoClient(client);

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
    gitpilotPanel.postMessage(message);
  };

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
      return;
    }

    autoBootstrapInFlight = true;

    try {
      output.appendLine(`[GitPilot] Bootstrap check: ${reason}`);

      await vscode.commands.executeCommand("gitpilot.refreshStatus");

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
        await vscode.commands.executeCommand("gitpilot.refreshStatus");
      }

      state = stateStore.state;

      if (
        state.readiness.canChat &&
        !state.session.active &&
        state.session.status !== "creating"
      ) {
        output.appendLine("[GitPilot] Auto-starting session...");
        await sessionCoordinator.ensureActiveSession();
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
    if (
      !stateStore.state.session.sessionId &&
      stateStore.state.readiness.canChat
    ) {
      await sessionCoordinator.ensureActiveSession();
      await vscode.commands.executeCommand("gitpilot.refreshStatus");
    }
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

  const sendChatToBackend = async (rawText: string): Promise<void> => {
    const text = rawText.trim();
    if (!text) {
      return;
    }

    const sessionId = await ensureSessionReady();

    if (!sessionId) {
      postErrorToPanel({
        code: "SESSION_NOT_READY",
        title: "GitPilot is finishing setup",
        message: "A session is being prepared. Please try again in a moment.",
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

      const response = await chatClientV2.sendMessage({
        session_id: sessionId,
        message: enrichedMessage,
        topology_id: topologyId,
        intent,
        project_context: structuredContext.project_context,
        working_set: structuredContext.working_set,
        task_context: structuredContext.task_context,
      });

      postMessageToPanel({
        type: "CHAT_RESPONSE",
        payload: {
          id: response.message_id || Date.now().toString(),
          role: "assistant",
          content: response.answer,
          createdAt: new Date().toISOString(),
          plan: response.plan,
        },
      });
    } catch (error: unknown) {
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

    if (connected) {
      sessionsTree.refresh();
      skillsTree.refresh();
      void refreshStatusAndBootstrap("client-connected");
    }
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
            await vscode.commands.executeCommand(
              "workbench.action.openSettings",
              "@ext:ruslanmv.gitpilot-vscode"
            );
            return;

          case "OPEN_WORKSPACE":
            await vscode.commands.executeCommand(
              "workbench.action.files.openFolder"
            );
            return;

          case "OPEN_ADMIN_UI":
            await vscode.commands.executeCommand("gitpilot.showServerInfo");
            return;

          case "OPEN_PROVIDER_SETUP":
            await vscode.commands.executeCommand("gitpilot.selectProviderV2");
            return;

          case "OPEN_MODEL_SETUP":
            await vscode.commands.executeCommand("gitpilot.selectModelV2");
            return;

          case "OPEN_LLM_SETTINGS":
            await vscode.commands.executeCommand("gitpilot.openLlmSettings");
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

          case "REGENERATE_TASK_PLAN":
            await vscode.commands.executeCommand("gitpilot.regenerateTaskPlan");
            return;

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

  context.subscriptions.push(
    gitpilotPanel,
    vscode.window.registerWebviewViewProvider(
      GitPilotPanel.viewType,
      gitpilotPanel
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

  registerChatCommands(context, client, legacyChatProvider);
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
  registerSessionCommands(context, stateStore, sessionCoordinator);
  registerChatCommandsV2(context, stateStore, chatClientV2);

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
    await vscode.commands.executeCommand("gitpilot.chatView.focus");
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
    vscode.window.showInformationMessage(
      "Apply Proposed Changes is registered, but the patch-apply pipeline is not fully wired in this file yet."
    );
  });

  registerCommand("gitpilot.regenerateTaskPlan", async () => {
    vscode.window.showInformationMessage(
      "Regenerate Task Plan is registered. Trigger task replanning from the Workspace Copilot flow."
    );
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
    void client.connect().then((connected) => {
      if (connected) {
        output.appendLine(
          `[GitPilot] Connected to server at ${config.serverUrl}`
        );
        void refreshStatusAndBootstrap("auto-connect");
      } else {
        output.appendLine(
          `[GitPilot] Auto-connect failed for ${config.serverUrl}`
        );
      }
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