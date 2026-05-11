/**
 * GitPilot — VS Code commands that surface the new backend features
 * shipped in Phases 1–4 of the upgrade plan.
 *
 * Every command in this module is **additive**.  None of them rewrite
 * existing UI flows; they run the new CLI subcommands in an integrated
 * terminal (so the user can see real output and abort with Ctrl-C),
 * or open the new documentation in VS Code's preview pane.  When the
 * underlying CLI feature is gated behind a feature flag, the command
 * sets `GITPILOT_FLAGS` on the spawned terminal so the user does not
 * have to remember the env var.
 *
 * Commands added (each contributes one entry under `contributes.commands`
 * in package.json):
 *
 *   - gitpilot.runDoctor          — `gitpilot doctor` health check
 *   - gitpilot.runInitWizard      — `gitpilot init --wizard`
 *   - gitpilot.openApiStability   — opens docs/API_STABILITY.md
 *   - gitpilot.openPhaseRunbooks  — quick-pick over docs/PHASE*.md
 *   - gitpilot.showFeatureFlags   — quick-pick of toggleable flags
 *
 * The commands themselves are intentionally thin: they orchestrate VS
 * Code APIs (terminals, quick picks, document previews) against the
 * already-shipped CLI.  No new backend endpoints are required.
 */

import * as vscode from "vscode";
import * as path from "path";

// ---------------------------------------------------------------------------
// Feature flags catalogued from the backend (Phase 1–3 batches).
// Keeping the list in TypeScript lets the quick-pick stay snappy without
// a round-trip to the backend; mismatches with the backend would surface
// as "flag has no effect" rather than an error.
// ---------------------------------------------------------------------------

const KNOWN_FLAGS: ReadonlyArray<{ name: string; description: string }> = [
  { name: "error_envelope",  description: "Structured error responses (Phase 1-D)" },
  { name: "prompt_cache",    description: "Anthropic prompt-cache markers (Phase 2-A)" },
  { name: "lazy_tool_defs",  description: "Mode-policy-driven MCP tool pruning (Phase 2-B)" },
  { name: "context_cache",   description: "LRU memoisation of context packs (Phase 2-C)" },
  { name: "stream_v2",       description: "End-to-end SSE streaming route (Phase 2-D)" },
  { name: "ui_stream_v2",    description: "UI consumer for stream_v2 (Phase 2-D)" },
  { name: "model_warmup",    description: "1-token startup ping (Phase 2-E)" },
  { name: "init_wizard",     description: "Interactive first-run wizard (Phase 3-G)" },
];


// ---------------------------------------------------------------------------
// Public registration entry point.  Call once from extension.ts.
// ---------------------------------------------------------------------------

export function registerPhase4Commands(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("gitpilot.runDoctor", runDoctor),
    vscode.commands.registerCommand("gitpilot.runInitWizard", runInitWizard),
    vscode.commands.registerCommand("gitpilot.openApiStability", openApiStability),
    vscode.commands.registerCommand("gitpilot.openPhaseRunbooks", openPhaseRunbooks),
    vscode.commands.registerCommand("gitpilot.showFeatureFlags", showFeatureFlags),
  );
}


// ---------------------------------------------------------------------------
// gitpilot.runDoctor
// ---------------------------------------------------------------------------

async function runDoctor(): Promise<void> {
  const terminal = ensureTerminal("GitPilot · Doctor");
  terminal.show(true);
  // ``--offline`` keeps the check ≤ 100 ms when there is no network.
  terminal.sendText("gitpilot doctor --offline");
}


// ---------------------------------------------------------------------------
// gitpilot.runInitWizard
// ---------------------------------------------------------------------------

async function runInitWizard(): Promise<void> {
  const folder = currentFolderOrWarn();
  if (!folder) {
    return;
  }

  // The wizard refuses to run unless its flag is on.  Setting the env
  // var on the terminal removes one source of confusion for first-time
  // users while keeping the flag globally off.
  const env = { GITPILOT_FLAGS: "init_wizard=1" };
  const terminal = ensureTerminal("GitPilot · Init wizard", env);
  terminal.show(true);
  terminal.sendText(`gitpilot init --wizard ${quoteArg(folder)}`);
}


// ---------------------------------------------------------------------------
// gitpilot.openApiStability  /  gitpilot.openPhaseRunbooks
// ---------------------------------------------------------------------------

async function openApiStability(): Promise<void> {
  await openDocFromRepo("docs/API_STABILITY.md");
}


async function openPhaseRunbooks(): Promise<void> {
  const options: Array<{ label: string; doc: string }> = [
    { label: "Phase 1 — Foundations",          doc: "docs/PHASE1.md" },
    { label: "Phase 2 — Performance",          doc: "docs/PHASE2.md" },
    { label: "Phase 3-G — First-run wizard",   doc: "docs/PHASE3_G.md" },
    { label: "Phase 4 — Quality safety net",   doc: "docs/PHASE4.md" },
    { label: "Upgrade catalogue (all phases)", doc: "docs/UPGRADES.md" },
    { label: "Public API contract",            doc: "docs/API_STABILITY.md" },
  ];
  const pick = await vscode.window.showQuickPick(
    options.map((o) => ({ label: o.label, description: o.doc })),
    { placeHolder: "Open a GitPilot phase runbook" },
  );
  if (pick) {
    await openDocFromRepo(pick.description as string);
  }
}


// ---------------------------------------------------------------------------
// gitpilot.showFeatureFlags
// ---------------------------------------------------------------------------

async function showFeatureFlags(): Promise<void> {
  const items = KNOWN_FLAGS.map((f) => ({
    label: f.name,
    description: f.description,
  }));
  const pick = await vscode.window.showQuickPick(items, {
    placeHolder: "Pick a flag to copy a sample GITPILOT_FLAGS env var to your clipboard",
  });
  if (!pick) {
    return;
  }
  const value = `${pick.label}=1`;
  await vscode.env.clipboard.writeText(`GITPILOT_FLAGS="${value}"`);
  vscode.window.showInformationMessage(
    `Copied to clipboard: GITPILOT_FLAGS="${value}".  Restart \`gitpilot serve\` for it to apply.`,
  );
}


// ---------------------------------------------------------------------------
// Helpers — kept private to this module
// ---------------------------------------------------------------------------

function ensureTerminal(
  name: string,
  env?: Record<string, string>,
): vscode.Terminal {
  const existing = vscode.window.terminals.find((t) => t.name === name);
  if (existing) {
    return existing;
  }
  return vscode.window.createTerminal({ name, env });
}


function currentFolderOrWarn(): string | undefined {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || folders.length === 0) {
    vscode.window.showWarningMessage(
      "Open a folder in VS Code before running the GitPilot wizard.",
    );
    return undefined;
  }
  return folders[0]!.uri.fsPath;
}


function quoteArg(arg: string): string {
  // Cheap shell-escape — wraps the path in double quotes and escapes
  // any embedded double quote.  Sufficient for VS Code workspace
  // paths on Linux / macOS / Windows.
  return `"${arg.replace(/"/g, '\\"')}"`;
}


async function openDocFromRepo(relativePath: string): Promise<void> {
  const repoRoot = await findRepoRoot();
  if (!repoRoot) {
    vscode.window.showWarningMessage(
      "GitPilot docs are not part of the open workspace.  " +
        "Clone https://github.com/ruslanmv/gitpilot to access the runbooks.",
    );
    return;
  }
  const docPath = path.join(repoRoot, relativePath);
  const uri = vscode.Uri.file(docPath);
  try {
    await vscode.commands.executeCommand("markdown.showPreview", uri);
  } catch {
    // Fallback when the markdown preview extension is not available.
    await vscode.window.showTextDocument(uri, { preview: true });
  }
}


async function findRepoRoot(): Promise<string | undefined> {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders) {
    return undefined;
  }
  for (const folder of folders) {
    const candidate = folder.uri.fsPath;
    // Look for `docs/UPGRADES.md` as the canonical marker that we're
    // inside a GitPilot clone (the file ships from Phase 1 onward).
    try {
      const probe = vscode.Uri.file(path.join(candidate, "docs", "UPGRADES.md"));
      await vscode.workspace.fs.stat(probe);
      return candidate;
    } catch {
      // not this folder
    }
  }
  return undefined;
}
