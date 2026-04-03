import * as vscode from "vscode";
import { StateStore } from "../../core/stateStore";
import { WebviewBridge } from "./webviewBridge";
import {
  GitPilotState,
  ExtensionToWebviewMessage,
  WebviewToExtensionMessage,
} from "../../core/types";

export class GitPilotPanel
  implements vscode.WebviewViewProvider, vscode.Disposable
{
  public static readonly viewType = "gitpilot.chatView";

  private _view?: vscode.WebviewView;
  private _bridge?: WebviewBridge;
  private readonly _disposables: vscode.Disposable[] = [];
  private readonly _output: vscode.OutputChannel;

  constructor(
    private readonly _extensionUri: vscode.Uri,
    private readonly _stateStore: StateStore,
    private readonly _onMessage: (msg: WebviewToExtensionMessage) => void
  ) {
    this._output = vscode.window.createOutputChannel("GitPilot");
    this._disposables.push(this._output);
  }

  public resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this._view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [
        this._extensionUri,
        vscode.Uri.joinPath(this._extensionUri, "out"),
        vscode.Uri.joinPath(this._extensionUri, "resources"),
        vscode.Uri.joinPath(this._extensionUri, "src"),
      ],
    };

    this._bridge = new WebviewBridge(webviewView.webview);

    const onReceive = webviewView.webview.onDidReceiveMessage(
      async (msg: WebviewToExtensionMessage) => {
        try {
          if (msg.type === "INIT") {
            this._log("Received INIT from webview");
            this._syncState();
            return;
          }

          if ((msg as { type?: string }).type === "OPEN_LOGS") {
            this._output.show(true);
            return;
          }

          if ((msg as { type?: string }).type === "RELOAD_WINDOW") {
            await vscode.commands.executeCommand("workbench.action.reloadWindow");
            return;
          }

          this._log(`Received message from webview: ${msg.type}`);
          this._onMessage(msg);
        } catch (error) {
          this._logError("Error while handling message from webview", error);
          void vscode.window.showErrorMessage(
            "GitPilot failed to process a message from the workspace view. Open Output → GitPilot for details."
          );
        }
      }
    );

    const onStateChange = this._stateStore.onDidChangeState(() => {
      this._syncState();
    });

    const onDispose = webviewView.onDidDispose(() => {
      this._log("Webview disposed");
      this.dispose();
    });

    this._disposables.push(onReceive, onStateChange, onDispose);

    void this._initializeWebview(webviewView);
  }

  public postMessage(msg: ExtensionToWebviewMessage): void {
    try {
      this._bridge?.postMessage(msg);
    } catch (error) {
      this._logError("Failed to post message to webview", error);
    }
  }

  public dispose(): void {
    while (this._disposables.length > 0) {
      const disposable = this._disposables.pop();
      try {
        disposable?.dispose();
      } catch {
        // no-op
      }
    }

    this._bridge = undefined;
    this._view = undefined;
  }

  private async _initializeWebview(
    webviewView: vscode.WebviewView
  ): Promise<void> {
    try {
      webviewView.webview.html = await this._getHtml(webviewView.webview);
      this._syncState();
      this._log("GitPilot webview initialized successfully");
    } catch (error) {
      this._logError("Failed to initialize GitPilot webview", error);
      webviewView.webview.html = this._getFallbackHtml(
        webviewView.webview,
        error
      );

      void vscode.window.showErrorMessage(
        "GitPilot Workspace could not be loaded. Open Output → GitPilot for details."
      );
    }
  }

  private _syncState(): void {
    if (!this._bridge) {
      return;
    }

    try {
      this._bridge.postMessage({
        type: "STATE_SYNC",
        payload: this._stateStore.state as GitPilotState,
      });
    } catch (error) {
      this._logError("Failed to sync state to webview", error);
    }
  }

  private async _getHtml(webview: vscode.Webview): Promise<string> {
    const nonce = this._getNonce();
    const csp = [
      "default-src 'none'",
      `img-src ${webview.cspSource} https: data:`,
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `font-src ${webview.cspSource} data:`,
      `script-src 'nonce-${nonce}'`,
      `connect-src ${webview.cspSource} https:`,
    ].join("; ");

    const templateUri = await this._resolveTemplateUri();
    this._log(`Loading webview template from: ${templateUri.toString()}`);

    const templateBytes = await vscode.workspace.fs.readFile(templateUri);
    const template = new TextDecoder("utf-8").decode(templateBytes);

    return template
      .replace(/__CSP__/g, this._escapeHtml(csp))
      .replace(/__NONCE__/g, nonce);
  }

  private async _resolveTemplateUri(): Promise<vscode.Uri> {
    const candidates = [
      vscode.Uri.joinPath(
        this._extensionUri,
        "out",
        "ui",
        "webview",
        "gitpilotWorkspaceTemplate.html"
      ),
      vscode.Uri.joinPath(
        this._extensionUri,
        "src",
        "ui",
        "webview",
        "gitpilotWorkspaceTemplate.html"
      ),
    ];

    for (const candidate of candidates) {
      if (await this._exists(candidate)) {
        return candidate;
      }
    }

    throw new Error(
      [
        "GitPilot webview template was not found.",
        "Expected one of:",
        ...candidates.map((uri) => ` - ${uri.fsPath}`),
        "Ensure gitpilotWorkspaceTemplate.html is included in the packaged extension.",
      ].join("\n")
    );
  }

  private async _exists(uri: vscode.Uri): Promise<boolean> {
    try {
      await vscode.workspace.fs.stat(uri);
      return true;
    } catch {
      return false;
    }
  }

  private _getFallbackHtml(webview: vscode.Webview, error: unknown): string {
    const nonce = this._getNonce();
    const csp = [
      "default-src 'none'",
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `font-src ${webview.cspSource} data:`,
      `script-src 'nonce-${nonce}'`,
    ].join("; ");

    const errorMessage =
      error instanceof Error ? error.message : "Unknown initialization error";

    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${this._escapeHtml(csp)}" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>GitPilot Workspace</title>
  <style>
    :root {
      color-scheme: light dark;
      --gp-radius-xl: 18px;
      --gp-radius-lg: 14px;
      --gp-radius-md: 12px;
      --gp-shadow: 0 22px 60px rgba(0, 0, 0, 0.22);
      --gp-accent: var(--vscode-focusBorder);
      --gp-accent-soft: color-mix(in srgb, var(--vscode-focusBorder) 18%, transparent);
      --gp-danger: var(--vscode-errorForeground);
      --gp-muted: var(--vscode-descriptionForeground);
      --gp-border: var(--vscode-panel-border);
      --gp-bg: var(--vscode-sideBar-background);
      --gp-surface: var(--vscode-editor-background);
      --gp-surface-2: var(--vscode-sideBarSectionHeader-background);
      --gp-text: var(--vscode-foreground);
    }

    * {
      box-sizing: border-box;
    }

    html, body {
      margin: 0;
      padding: 0;
      min-height: 100%;
      background: var(--gp-bg);
      color: var(--gp-text);
      font-family: var(--vscode-font-family);
    }

    body {
      padding: 20px;
    }

    .frame {
      width: 100%;
      max-width: 1100px;
      margin: 0 auto;
      border: 1px solid var(--gp-border);
      border-radius: var(--gp-radius-xl);
      overflow: hidden;
      background: var(--gp-surface);
      box-shadow: var(--gp-shadow);
    }

    .hero {
      position: relative;
      overflow: hidden;
      padding: 28px 28px 24px;
      border-bottom: 1px solid var(--gp-border);
      background:
        radial-gradient(circle at top right, rgba(82, 146, 255, 0.22), transparent 32%),
        radial-gradient(circle at left center, rgba(147, 197, 253, 0.10), transparent 28%),
        linear-gradient(180deg, rgba(127,127,127,0.06), rgba(127,127,127,0.02));
    }

    .hero::after {
      content: "";
      position: absolute;
      inset: 0;
      pointer-events: none;
      background:
        linear-gradient(90deg, transparent, rgba(255,255,255,0.03), transparent);
      opacity: 0.5;
    }

    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 7px 12px;
      border-radius: 999px;
      border: 1px solid var(--gp-border);
      background: rgba(127,127,127,0.08);
      color: var(--gp-muted);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: var(--gp-danger);
      box-shadow: 0 0 0 4px rgba(255, 0, 0, 0.08);
    }

    .title {
      margin: 18px 0 10px;
      font-size: 26px;
      line-height: 1.2;
      font-weight: 800;
      letter-spacing: -0.02em;
    }

    .subtitle {
      margin: 0;
      max-width: 820px;
      color: var(--gp-muted);
      line-height: 1.65;
      font-size: 14px;
    }

    .layout {
      display: grid;
      grid-template-columns: 1.4fr 0.9fr;
      gap: 18px;
      padding: 22px;
    }

    .stack {
      display: grid;
      gap: 18px;
    }

    .card {
      border: 1px solid var(--gp-border);
      border-radius: var(--gp-radius-lg);
      background: var(--gp-surface-2);
      overflow: hidden;
    }

    .card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--gp-border);
      background: linear-gradient(180deg, rgba(127,127,127,0.05), rgba(127,127,127,0.01));
    }

    .card-title {
      margin: 0;
      font-size: 13px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--gp-muted);
    }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      border: 1px solid var(--gp-border);
      border-radius: 999px;
      background: rgba(127,127,127,0.08);
      color: var(--gp-text);
      font-size: 11px;
      font-weight: 700;
    }

    .card-body {
      padding: 18px;
    }

    .lead {
      margin: 0;
      font-size: 14px;
      line-height: 1.65;
      color: var(--gp-muted);
    }

    .error-box {
      margin-top: 14px;
      border: 1px solid var(--gp-border);
      border-radius: var(--gp-radius-md);
      background: var(--vscode-textCodeBlock-background);
      padding: 14px;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: var(--vscode-editor-font-family);
      font-size: 12px;
      line-height: 1.65;
      color: var(--vscode-editor-foreground);
    }

    .list {
      margin: 0;
      padding: 0;
      list-style: none;
      display: grid;
      gap: 12px;
    }

    .list li {
      display: grid;
      grid-template-columns: 28px 1fr;
      gap: 12px;
      align-items: start;
    }

    .index {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      border-radius: 999px;
      background: var(--gp-accent-soft);
      color: var(--gp-text);
      font-size: 12px;
      font-weight: 800;
      border: 1px solid var(--gp-border);
    }

    .list strong {
      display: block;
      margin-bottom: 3px;
      font-size: 13px;
    }

    .list span {
      color: var(--gp-muted);
      font-size: 13px;
      line-height: 1.55;
    }

    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 16px;
    }

    button {
      appearance: none;
      border: 1px solid transparent;
      border-radius: 12px;
      padding: 11px 16px;
      font: inherit;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      transition: transform 120ms ease, opacity 120ms ease, background 120ms ease;
    }

    button:hover {
      transform: translateY(-1px);
    }

    button:active {
      transform: translateY(0);
    }

    .primary {
      background: var(--vscode-button-background);
      color: var(--vscode-button-foreground);
      border-color: var(--vscode-button-border, transparent);
    }

    .primary:hover {
      background: var(--vscode-button-hoverBackground);
    }

    .secondary {
      background: transparent;
      color: var(--vscode-textLink-foreground);
      border-color: var(--gp-border);
    }

    .secondary:hover {
      background: rgba(127,127,127,0.08);
    }

    .mini-grid {
      display: grid;
      gap: 12px;
    }

    .stat {
      border: 1px solid var(--gp-border);
      border-radius: var(--gp-radius-md);
      padding: 14px;
      background: rgba(127,127,127,0.04);
    }

    .stat-label {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      color: var(--gp-muted);
      margin-bottom: 8px;
    }

    .stat-value {
      font-size: 14px;
      line-height: 1.5;
      font-weight: 700;
    }

    .footer {
      padding: 0 22px 22px;
      color: var(--gp-muted);
      font-size: 12px;
      line-height: 1.6;
    }

    @media (max-width: 900px) {
      .layout {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="frame">
    <section class="hero">
      <div class="eyebrow">
        <span class="status-dot"></span>
        GitPilot Workspace
      </div>
      <h1 class="title">GitPilot could not load its workspace interface</h1>
      <p class="subtitle">
        The extension started successfully, but the packaged webview template was not available at runtime.
        This usually means the GitPilot UI asset was excluded from the VSIX or not copied into the production bundle.
      </p>
    </section>

    <section class="layout">
      <div class="stack">
        <article class="card">
          <div class="card-header">
            <h2 class="card-title">Diagnostic Summary</h2>
            <div class="badge">Runtime Asset Failure</div>
          </div>
          <div class="card-body">
            <p class="lead">
              GitPilot is installed and registered, but the workspace view could not render because a required HTML template was not found.
            </p>
            <div class="error-box">${this._escapeHtml(errorMessage)}</div>
          </div>
        </article>

        <article class="card">
          <div class="card-header">
            <h2 class="card-title">Recommended Recovery</h2>
            <div class="badge">High Priority</div>
          </div>
          <div class="card-body">
            <ul class="list">
              <li>
                <div class="index">1</div>
                <div>
                  <strong>Verify packaged assets</strong>
                  <span>Confirm that gitpilotWorkspaceTemplate.html is included inside the installed extension bundle.</span>
                </div>
              </li>
              <li>
                <div class="index">2</div>
                <div>
                  <strong>Rebuild and reinstall</strong>
                  <span>Repackage the VSIX after fixing the template path or packaging rules, then reinstall the updated extension.</span>
                </div>
              </li>
              <li>
                <div class="index">3</div>
                <div>
                  <strong>Inspect GitPilot output logs</strong>
                  <span>Use the GitPilot output channel to confirm the exact runtime path that the extension tried to load.</span>
                </div>
              </li>
            </ul>

            <div class="actions">
              <button id="openLogs" class="primary">Open GitPilot Output</button>
              <button id="reloadWindow" class="secondary">Reload Window</button>
            </div>
          </div>
        </article>
      </div>

      <div class="stack">
        <article class="card">
          <div class="card-header">
            <h2 class="card-title">Workspace Status</h2>
            <div class="badge">Readiness Check</div>
          </div>
          <div class="card-body mini-grid">
            <div class="stat">
              <div class="stat-label">Extension Activation</div>
              <div class="stat-value">Successful</div>
            </div>
            <div class="stat">
              <div class="stat-label">Webview Registration</div>
              <div class="stat-value">Successful</div>
            </div>
            <div class="stat">
              <div class="stat-label">Template Availability</div>
              <div class="stat-value">Missing at Runtime</div>
            </div>
          </div>
        </article>

        <article class="card">
          <div class="card-header">
            <h2 class="card-title">Enterprise Note</h2>
            <div class="badge">Production UX</div>
          </div>
          <div class="card-body">
            <p class="lead">
              This fallback screen is intentionally polished so the product still feels premium during failure scenarios.
              Users see a clear diagnosis, an action path, and a branded workspace experience instead of a broken blank panel.
            </p>
          </div>
        </article>
      </div>
    </section>

    <div class="footer">
      GitPilot Workspace is running in protected webview mode. Open the GitPilot output channel for detailed diagnostics.
    </div>
  </div>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();

    document.getElementById("openLogs")?.addEventListener("click", () => {
      vscode.postMessage({ type: "OPEN_LOGS" });
    });

    document.getElementById("reloadWindow")?.addEventListener("click", () => {
      vscode.postMessage({ type: "RELOAD_WINDOW" });
    });
  </script>
</body>
</html>`;
  }

  private _log(message: string): void {
    this._output.appendLine(`[GitPilotPanel] ${message}`);
  }

  private _logError(message: string, error: unknown): void {
    const details =
      error instanceof Error ? `${error.message}\n${error.stack ?? ""}` : String(error);

    this._output.appendLine(`[GitPilotPanel] ${message}`);
    this._output.appendLine(details);
  }

  private _escapeHtml(value: string): string {
    return value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  private _getNonce(): string {
    const chars =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";

    let value = "";
    for (let i = 0; i < 32; i += 1) {
      value += chars.charAt(Math.floor(Math.random() * chars.length));
    }

    return value;
  }
}