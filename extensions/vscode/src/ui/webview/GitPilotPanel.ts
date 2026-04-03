import * as vscode from "vscode";
import * as fs from "fs";
import * as path from "path";
import { StateStore } from "../../core/stateStore";
import { WebviewBridge } from "./webviewBridge";
import {
  GitPilotState,
  ExtensionToWebviewMessage,
  WebviewToExtensionMessage,
} from "../../core/types";

export class GitPilotPanel implements vscode.WebviewViewProvider {
  public static readonly viewType = "gitpilot.chatView";

  private _view?: vscode.WebviewView;
  private _bridge?: WebviewBridge;
  private readonly _disposables: vscode.Disposable[] = [];

  constructor(
    private readonly _extensionUri: vscode.Uri,
    private readonly _stateStore: StateStore,
    private readonly _onMessage: (msg: WebviewToExtensionMessage) => void
  ) {}

  resolveWebviewView(
    webviewView: vscode.WebviewView,
    _context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ): void {
    this._view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [this._extensionUri],
    };

    this._bridge = new WebviewBridge(webviewView.webview);

    const onReceive = webviewView.webview.onDidReceiveMessage(
      (msg: WebviewToExtensionMessage) => {
        if (msg.type === "INIT") {
          this._syncState();
          return;
        }
        this._onMessage(msg);
      }
    );

    const onStateChange = this._stateStore.onDidChangeState(() => {
      this._syncState();
    });

    const onDispose = webviewView.onDidDispose(() => {
      this.dispose();
    });

    this._disposables.push(onReceive, onStateChange, onDispose);
    webviewView.webview.html = this._getHtml(webviewView.webview);
    this._syncState();
  }

  postMessage(msg: ExtensionToWebviewMessage): void {
    this._bridge?.postMessage(msg);
  }

  dispose(): void {
    while (this._disposables.length) {
      const disposable = this._disposables.pop();
      try {
        disposable?.dispose();
      } catch {
        // no-op
      }
    }
  }

  private _syncState(): void {
    if (!this._bridge) {
      return;
    }

    this._bridge.postMessage({
      type: "STATE_SYNC",
      payload: this._stateStore.state as GitPilotState,
    });
  }

  private _getHtml(webview: vscode.Webview): string {
    const nonce = this._getNonce();
    const csp = [
      "default-src 'none'",
      `img-src ${webview.cspSource} https: data:`,
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `font-src ${webview.cspSource}`,
      `script-src 'nonce-${nonce}'`,
    ].join('; ');

    const templatePath = path.join(
      this._extensionUri.fsPath,
      'src',
      'ui',
      'webview',
      'gitpilotWorkspaceTemplate.html'
    );
    const template = fs.readFileSync(templatePath, 'utf8');
    return template.replace('__CSP__', csp).replace('__NONCE__', nonce);
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
