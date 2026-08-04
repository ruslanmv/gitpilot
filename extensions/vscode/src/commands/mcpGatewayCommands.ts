/**
 * GitPilot — MCP Context Forge commands.
 *
 * Configuring a gateway from the editor, without ever putting a credential in
 * `settings.json`.
 *
 * Design notes, in the order they matter:
 *
 *   1. **A password is never a setting.** VS Code settings sync to other
 *      machines and are readable by any extension; workspace settings can even
 *      be committed. So the URL, sign-in method and admin email are settings —
 *      shareable, reviewable — while the password and API token are typed into
 *      a masked prompt and sent straight to the GitPilot server, which is the
 *      only place they are stored. Nothing secret stays in the editor.
 *
 *   2. **Test before save.** The wizard probes the real gateway and shows what
 *      it said, so a wrong password fails here rather than in the middle of a
 *      coding run.
 *
 *   3. **One authority.** These commands call the same endpoints the desktop
 *      and mobile UIs use, so a gateway configured in the editor is configured
 *      everywhere.
 */

import * as vscode from "vscode";
import { McpGatewayClient, McpGatewayUpdate } from "../api/mcpGatewayClient";

const CONFIG_SECTION = "gitpilot.mcp";

type AuthMode = "auto" | "none" | "token" | "login";

const AUTH_CHOICES: Array<{ label: string; description: string; mode: AuthMode }> = [
  { label: "Automatic", description: "Detect what the gateway accepts (recommended)", mode: "auto" },
  { label: "No credential", description: "The gateway runs with authentication disabled", mode: "none" },
  { label: "API token", description: "A Forge API token — revocable, best for shared gateways", mode: "token" },
  { label: "Email + password", description: "Sign in as a Forge admin", mode: "login" },
];

function settings() {
  return vscode.workspace.getConfiguration(CONFIG_SECTION);
}

/** Render a test result as one line a human can act on. */
function describe(result: { ok: boolean; detail: string; serverCount: number | null }): string {
  if (!result.ok) return result.detail;
  const servers = result.serverCount != null ? ` ${result.serverCount} server(s) visible.` : "";
  return `${result.detail}${servers}`;
}

export function registerMcpGatewayCommands(
  context: vscode.ExtensionContext,
  gatewayClient: McpGatewayClient
): void {
  /**
   * The guided path: URL → sign-in method → credential → test → save.
   * Each step can be cancelled with Escape, and nothing is written until the
   * end, so abandoning halfway leaves the previous gateway untouched.
   */
  const configure = vscode.commands.registerCommand("gitpilot.configureMcpGateway", async () => {
    let current;
    try {
      current = await gatewayClient.get();
    } catch (err) {
      vscode.window.showErrorMessage(
        `GitPilot: could not read the MCP gateway settings — is the GitPilot server running? (${err})`
      );
      return;
    }

    const url = await vscode.window.showInputBox({
      title: "MCP Context Forge — gateway URL",
      prompt: "Where is the gateway? Its servers become tools your coding agents can call.",
      value: current.savedUrl || current.url,
      placeHolder: "http://localhost:4444",
      ignoreFocusOut: true,
      validateInput: (value) =>
        !value.trim() || /^https?:\/\//i.test(value.trim())
          ? undefined
          : "Enter a URL starting with http:// or https://",
    });
    if (url === undefined) return; // cancelled

    const picked = await vscode.window.showQuickPick(
      AUTH_CHOICES.map((c) => ({
        label: c.label,
        description: c.description,
        picked: c.mode === current.authMode,
        mode: c.mode,
      })),
      { title: "MCP Context Forge — sign-in method", ignoreFocusOut: true }
    );
    if (!picked) return;

    const update: McpGatewayUpdate = { url: url.trim(), authMode: picked.mode };

    if (picked.mode === "auto" || picked.mode === "login") {
      const email = await vscode.window.showInputBox({
        title: "MCP Context Forge — admin email",
        value: current.email,
        placeHolder: "admin@example.com",
        ignoreFocusOut: true,
      });
      if (email === undefined) return;
      update.email = email.trim();

      const password = await vscode.window.showInputBox({
        title: "MCP Context Forge — admin password",
        prompt: current.hasPassword
          ? "Leave blank to keep the password already saved."
          : "Stored by the GitPilot server, never in VS Code settings.",
        password: true,
        ignoreFocusOut: true,
      });
      if (password === undefined) return;
      if (password) update.password = password;
    }

    if (picked.mode === "auto" || picked.mode === "token") {
      const token = await vscode.window.showInputBox({
        title: "MCP Context Forge — API token",
        prompt: current.hasApiToken
          ? "Leave blank to keep the token already saved."
          : "Optional. Stored by the GitPilot server, never in VS Code settings.",
        password: true,
        ignoreFocusOut: true,
      });
      if (token === undefined) return;
      if (token) update.apiToken = token;
    }

    // Prove it before committing.
    const result = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: "Testing the MCP gateway…" },
      () => gatewayClient.test(update)
    );

    if (!result.ok) {
      const choice = await vscode.window.showWarningMessage(
        `MCP gateway: ${describe(result)}`,
        { modal: false },
        "Save anyway",
        "Edit"
      );
      if (choice === "Edit") {
        await vscode.commands.executeCommand("gitpilot.configureMcpGateway");
        return;
      }
      if (choice !== "Save anyway") return;
    }

    const saved = await gatewayClient.save(update);
    // Mirror the non-secret parts into settings so they are visible and
    // shareable; the credential stays server-side.
    await settings().update("gatewayUrl", saved.savedUrl || "", vscode.ConfigurationTarget.Global);
    await settings().update("authMode", saved.authMode, vscode.ConfigurationTarget.Global);
    await settings().update("adminEmail", saved.email || "", vscode.ConfigurationTarget.Global);

    const next = await vscode.window.showInformationMessage(
      result.ok
        ? `MCP gateway saved: ${saved.url}. ${describe(result)}`
        : `MCP gateway saved: ${saved.url} (not verified).`,
      "Sync servers now"
    );
    if (next === "Sync servers now") {
      await vscode.commands.executeCommand("gitpilot.syncMcpServers");
    }
  });

  /** Check the saved gateway without changing anything. */
  const test = vscode.commands.registerCommand("gitpilot.testMcpGateway", async () => {
    try {
      const result = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Testing the MCP gateway…" },
        () => gatewayClient.test({})
      );
      if (result.ok) {
        vscode.window.showInformationMessage(`MCP gateway: ${describe(result)}`);
      } else {
        const choice = await vscode.window.showWarningMessage(
          `MCP gateway: ${describe(result)}`,
          "Configure"
        );
        if (choice === "Configure") {
          await vscode.commands.executeCommand("gitpilot.configureMcpGateway");
        }
      }
    } catch (err) {
      vscode.window.showErrorMessage(`GitPilot: the gateway test could not run (${err})`);
    }
  });

  /** Pull the gateway's registry so the agents can see its tools. */
  const sync = vscode.commands.registerCommand("gitpilot.syncMcpServers", async () => {
    try {
      const report = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: "Syncing MCP servers…" },
        () => gatewayClient.sync()
      );
      if (report.forge_unreachable) {
        const choice = await vscode.window.showWarningMessage(
          `MCP sync failed: ${report.error || "the gateway is unreachable"}`,
          "Configure gateway"
        );
        if (choice === "Configure gateway") {
          await vscode.commands.executeCommand("gitpilot.configureMcpGateway");
        }
        return;
      }
      const added = report.added?.length || 0;
      const kept = report.kept?.length || 0;
      vscode.window.showInformationMessage(
        `MCP servers synced — ${added} added, ${kept} already known.`
      );
    } catch (err) {
      vscode.window.showErrorMessage(`GitPilot: the MCP sync could not run (${err})`);
    }
  });

  /** Remove a stored credential without touching the rest of the connection. */
  const signOut = vscode.commands.registerCommand("gitpilot.signOutMcpGateway", async () => {
    const choice = await vscode.window.showWarningMessage(
      "Remove the saved MCP gateway credentials? The gateway URL is kept.",
      { modal: true },
      "Remove"
    );
    if (choice !== "Remove") return;
    try {
      await gatewayClient.save({ clearPassword: true, clearApiToken: true });
      vscode.window.showInformationMessage("MCP gateway credentials removed.");
    } catch (err) {
      vscode.window.showErrorMessage(`GitPilot: could not remove the credentials (${err})`);
    }
  });

  context.subscriptions.push(configure, test, sync, signOut);

  /**
   * Settings are the shareable half of the connection, so an edit there should
   * take effect without also opening the wizard. Secrets are never read from
   * settings — only pushed to the server, which remains their only home.
   */
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration(async (event) => {
      if (!event.affectsConfiguration(CONFIG_SECTION)) return;
      const config = settings();
      const update: McpGatewayUpdate = {
        url: config.get<string>("gatewayUrl", ""),
        authMode: config.get<string>("authMode", "auto"),
        email: config.get<string>("adminEmail", ""),
      };
      if (!update.url && !update.email) return; // nothing meaningful to push
      try {
        await gatewayClient.save(update);
      } catch {
        // The server may simply not be running yet; the wizard will sync it later.
      }
    })
  );
}
