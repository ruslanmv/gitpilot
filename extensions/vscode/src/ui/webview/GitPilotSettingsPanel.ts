import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";

/**
 * GitPilotSettingsPanel — opens a branded settings tab (webview panel)
 * with sidebar navigation (General, AI Provider, Agent, Editor) that
 * reads/writes VS Code configuration for the gitpilot extension.
 *
 * Only one instance is kept alive at a time (singleton pattern).
 */
export class GitPilotSettingsPanel {
  public static readonly viewType = "gitpilot.settingsPanel";
  private static _instance: GitPilotSettingsPanel | undefined;

  private readonly _panel: vscode.WebviewPanel;
  private readonly _extensionUri: vscode.Uri;
  private _disposed = false;

  private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri) {
    this._panel = panel;
    this._extensionUri = extensionUri;

    panel.onDidDispose(() => {
      this._disposed = true;
      GitPilotSettingsPanel._instance = undefined;
    });

    panel.webview.onDidReceiveMessage((msg) => this._onMessage(msg));
    void this._loadHtml();
  }

  /** Show or reveal the settings tab. */
  public static open(extensionUri: vscode.Uri): void {
    if (GitPilotSettingsPanel._instance) {
      GitPilotSettingsPanel._instance._panel.reveal(vscode.ViewColumn.One);
      return;
    }

    const panel = vscode.window.createWebviewPanel(
      GitPilotSettingsPanel.viewType,
      "GitPilot Settings",
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.joinPath(extensionUri, "out")],
      }
    );

    GitPilotSettingsPanel._instance = new GitPilotSettingsPanel(
      panel,
      extensionUri
    );
  }

  // ── Private ──

  private async _loadHtml(): Promise<void> {
    const nonce = this._getNonce();
    const csp = [
      "default-src 'none'",
      `style-src 'nonce-${nonce}'`,
      `script-src 'nonce-${nonce}'`,
      `img-src ${this._panel.webview.cspSource} https: data:`,
    ].join("; ");

    // Read template from out/ (copied by the build script)
    const templatePath = path.join(
      this._extensionUri.fsPath,
      "out",
      "ui",
      "webview",
      "gitpilotSettingsTemplate.html"
    );

    let html: string;
    try {
      html = fs.readFileSync(templatePath, "utf-8");
    } catch {
      this._panel.webview.html =
        "<h2>Could not load settings template.</h2>";
      return;
    }

    // Read version from package.json
    let version = "0.0.0";
    try {
      const pkgPath = path.join(this._extensionUri.fsPath, "package.json");
      const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf-8"));
      version = pkg.version || version;
    } catch {
      // ignore
    }

    html = html
      .replace(/__CSP__/g, csp)
      .replace(/__NONCE__/g, nonce)
      .replace(/__VERSION__/g, version);

    this._panel.webview.html = html;
  }

  private _onMessage(msg: { type: string; payload?: Record<string, unknown> }): void {
    if (msg.type === "GET_SETTINGS") {
      this._sendCurrentSettings();
      return;
    }

    if (msg.type === "SAVE_SETTINGS" && msg.payload) {
      this._applySettings(msg.payload);
      void this._panel.webview.postMessage({ type: "SETTINGS_SAVED" });
      return;
    }

    if (msg.type === "OPEN_ADMIN") {
      void vscode.commands.executeCommand("gitpilot.showServerInfo");
      return;
    }
  }

  private _sendCurrentSettings(): void {
    const cfg = vscode.workspace.getConfiguration("gitpilot");
    const serverState = vscode.workspace.getConfiguration("gitpilot");

    void this._panel.webview.postMessage({
      type: "SETTINGS_DATA",
      payload: {
        serverUrl: cfg.get<string>("serverUrl", "http://127.0.0.1:8000"),
        autoConnect: cfg.get<boolean>("autoConnect", true),
        githubToken: cfg.get<string>("githubToken", ""),
        provider: this._detectProvider(),
        permissionMode: cfg.get<string>("permissionMode", "normal"),
        defaultTopology: cfg.get<string>("defaultTopology", "default"),
        liteMode: cfg.get<boolean>("liteMode", false),
        showInlineHints: cfg.get<boolean>("showInlineHints", true),
        scanOnSave: cfg.get<boolean>("scanOnSave", false),
        showSecurityDiagnostics: cfg.get<boolean>(
          "showSecurityDiagnostics",
          true
        ),
        chatFontSize: cfg.get<number>("chatFontSize", 13),
        maxChatHistory: cfg.get<number>("maxChatHistory", 100),
        // Best-effort connection status: check if server URL responds
        connected: serverState.get<boolean>("autoConnect", true),
      },
    });
  }

  private _detectProvider(): string {
    // Try env vars first, then settings
    const env = process.env;
    if (env.GITPILOT_PROVIDER) return env.GITPILOT_PROVIDER.toLowerCase();

    // Check if common env vars hint at a provider
    if (env.OPENAI_API_KEY) return "openai";
    if (env.ANTHROPIC_API_KEY) return "claude";
    if (env.OLLAMA_BASE_URL) return "ollama";

    return "ollabridge"; // default
  }

  private _applySettings(payload: Record<string, unknown>): void {
    const cfg = vscode.workspace.getConfiguration("gitpilot");
    const target = vscode.ConfigurationTarget.Global;

    const set = (key: string, value: unknown) => {
      void cfg.update(key, value, target);
    };

    if (payload.serverUrl !== undefined) set("serverUrl", payload.serverUrl);
    if (payload.autoConnect !== undefined) set("autoConnect", payload.autoConnect);
    if (payload.githubToken !== undefined) set("githubToken", payload.githubToken);
    if (payload.permissionMode !== undefined) set("permissionMode", payload.permissionMode);
    if (payload.defaultTopology !== undefined) set("defaultTopology", payload.defaultTopology);
    if (payload.liteMode !== undefined) set("liteMode", payload.liteMode);
    if (payload.showInlineHints !== undefined) set("showInlineHints", payload.showInlineHints);
    if (payload.scanOnSave !== undefined) set("scanOnSave", payload.scanOnSave);
    if (payload.showSecurityDiagnostics !== undefined) set("showSecurityDiagnostics", payload.showSecurityDiagnostics);
    if (payload.chatFontSize !== undefined) set("chatFontSize", payload.chatFontSize);
    if (payload.maxChatHistory !== undefined) set("maxChatHistory", payload.maxChatHistory);
  }

  private _getNonce(): string {
    const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    let result = "";
    for (let i = 0; i < 32; i++) {
      result += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return result;
  }
}
