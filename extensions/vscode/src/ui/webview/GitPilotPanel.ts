/**
 * GitPilot Redesign — State-Driven Webview Panel
 * Production-ready VS Code sidebar webview with:
 * - enterprise-grade top bar
 * - centered welcome/hero state
 * - provider/model visibility
 * - workflow selector
 * - quick actions
 * - improved chat UX
 * - safer CSP + nonce setup
 */

import * as vscode from "vscode";
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
      `default-src 'none'`,
      `img-src ${webview.cspSource} https: data:`,
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `font-src ${webview.cspSource}`,
      `script-src 'nonce-${nonce}'`,
    ].join("; ");

    return /* html */ `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta
    http-equiv="Content-Security-Policy"
    content="${csp}"
  />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>GitPilot</title>
  <style>
    :root {
      --bg: var(--vscode-sideBar-background);
      --fg: var(--vscode-sideBar-foreground);
      --muted: var(--vscode-descriptionForeground);
      --border: var(--vscode-panel-border);
      --panel: var(--vscode-editorWidget-background, var(--vscode-sideBar-background));
      --panel-2: var(--vscode-editor-background, var(--vscode-sideBar-background));

      --input-bg: var(--vscode-input-background);
      --input-fg: var(--vscode-input-foreground);
      --input-border: var(--vscode-input-border);

      --btn-bg: var(--vscode-button-background);
      --btn-fg: var(--vscode-button-foreground);
      --btn-hover: var(--vscode-button-hoverBackground);

      --btn2-bg: var(--vscode-button-secondaryBackground, transparent);
      --btn2-fg: var(--vscode-button-secondaryForeground, var(--fg));

      --hover: var(--vscode-list-hoverBackground, rgba(255,255,255,.06));
      --link: var(--vscode-textLink-foreground);
      --focus: var(--vscode-focusBorder);

      --success: #4caf50;
      --warning: #ff9800;
      --error: #f44336;

      --radius-sm: 8px;
      --radius-md: 12px;
      --radius-lg: 16px;
      --shadow: 0 10px 30px rgba(0, 0, 0, 0.22);
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    html, body {
      width: 100%;
      min-height: 100%;
    }

    body {
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      color: var(--fg);
      background: var(--bg);
      padding: 10px;
      overflow-x: hidden;
    }

    button,
    input,
    textarea {
      font: inherit;
    }

    button {
      outline: none;
    }

    button:focus-visible,
    textarea:focus-visible {
      outline: 1px solid var(--focus);
      outline-offset: 1px;
    }

    .hidden {
      display: none !important;
    }

    .app {
      display: flex;
      flex-direction: column;
      gap: 8px;
      width: 100%;
      max-width: 100%;
    }

    .surface {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      box-shadow: none;
    }

    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 12px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
      flex: 1;
    }

    .brand-avatar {
      width: 34px;
      height: 34px;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: linear-gradient(180deg, #39b9ff 0%, #1095df 100%);
      color: #fff;
      font-size: 17px;
      flex: 0 0 auto;
      box-shadow: 0 8px 20px rgba(57,185,255,.22);
    }

    .brand-copy {
      min-width: 0;
      flex: 1;
    }

    .brand-title {
      font-size: 13px;
      font-weight: 700;
      line-height: 1.2;
      letter-spacing: .1px;
    }

    .brand-subtitle {
      margin-top: 2px;
      color: var(--muted);
      font-size: 11px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .topbar-actions {
      display: flex;
      align-items: center;
      gap: 6px;
      flex: 0 0 auto;
    }

    .chip-btn,
    .icon-btn,
    .btn,
    .btn-secondary,
    .pill-action,
    .qa-btn,
    .suggest-chip,
    .mode-trigger,
    .mode-option,
    .provider-strip {
      transition:
        background-color .14s ease,
        color .14s ease,
        border-color .14s ease,
        transform .04s ease;
    }

    .chip-btn,
    .icon-btn {
      border: 1px solid var(--border);
      background: transparent;
      color: var(--fg);
      cursor: pointer;
      border-radius: var(--radius-sm);
    }

    .chip-btn:hover,
    .icon-btn:hover,
    .provider-strip:hover,
    .mode-trigger:hover,
    .qa-btn:hover,
    .suggest-chip:hover,
    .btn-secondary:hover {
      background: var(--hover);
      border-color: color-mix(in srgb, var(--border) 45%, var(--link));
    }

    .chip-btn:active,
    .icon-btn:active,
    .qa-btn:active,
    .suggest-chip:active,
    .mode-trigger:active,
    .provider-strip:active {
      transform: translateY(1px);
    }

    .chip-btn {
      height: 30px;
      max-width: 150px;
      padding: 0 10px;
      font-size: 11px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .icon-btn {
      width: 30px;
      height: 30px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 14px;
    }

    .provider-strip {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      cursor: pointer;
    }

    .status-dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      flex: 0 0 auto;
    }

    .status-ok { background: var(--success); }
    .status-warning { background: var(--warning); }
    .status-error { background: var(--error); }
    .status-unknown { background: #6b7280; }

    .provider-copy {
      min-width: 0;
      flex: 1;
    }

    .provider-name {
      font-size: 12px;
      font-weight: 700;
      line-height: 1.2;
    }

    .provider-model {
      margin-top: 2px;
      font-size: 11px;
      color: var(--muted);
      line-height: 1.3;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .mode-selector {
      position: relative;
    }

    .mode-trigger {
      width: 100%;
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      background: var(--panel);
      color: var(--fg);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 10px 12px;
      text-align: left;
    }

    .mode-trigger-copy {
      display: flex;
      flex-direction: column;
      gap: 3px;
      min-width: 0;
    }

    .mode-label {
      font-size: 10px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .5px;
    }

    .mode-value {
      font-size: 12px;
      font-weight: 700;
      line-height: 1.2;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .mode-chevron {
      color: var(--muted);
      font-size: 10px;
      flex: 0 0 auto;
    }

    .mode-dropdown {
      position: absolute;
      z-index: 20;
      top: calc(100% + 6px);
      left: 0;
      right: 0;
      max-height: 420px;
      overflow-y: auto;
      padding: 8px;
      border: 1px solid var(--border);
      border-radius: var(--radius-md);
      background: var(--panel);
      box-shadow: var(--shadow);
    }

    .mode-group-title {
      padding: 8px 8px 4px;
      font-size: 10px;
      font-weight: 700;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .5px;
    }

    .mode-option {
      width: 100%;
      display: flex;
      align-items: flex-start;
      gap: 9px;
      border: 1px solid transparent;
      border-radius: 10px;
      background: transparent;
      color: var(--fg);
      cursor: pointer;
      text-align: left;
      padding: 9px 10px;
      margin-bottom: 4px;
    }

    .mode-option:hover {
      background: var(--hover);
    }

    .mode-option.active {
      border-color: var(--focus);
      background: var(--hover);
    }

    .mode-emoji {
      font-size: 15px;
      line-height: 1;
      margin-top: 2px;
      flex: 0 0 auto;
    }

    .mode-info {
      min-width: 0;
      flex: 1;
    }

    .mode-title-row {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }

    .mode-title {
      font-size: 12px;
      font-weight: 700;
      line-height: 1.2;
    }

    .mode-desc {
      margin-top: 3px;
      font-size: 11px;
      color: var(--muted);
      line-height: 1.35;
    }

    .mode-badge {
      padding: 2px 6px;
      border-radius: 999px;
      font-size: 9px;
      line-height: 1.1;
      background: var(--vscode-badge-background, rgba(255,255,255,.08));
      color: var(--vscode-badge-foreground, var(--muted));
      flex: 0 0 auto;
    }

    .pills {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: transparent;
      font-size: 10px;
      color: var(--muted);
      white-space: nowrap;
      max-width: 100%;
    }

    .panel {
      padding: 12px;
    }

    .section-title {
      margin-bottom: 8px;
      font-size: 11px;
      font-weight: 700;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .5px;
    }

    .hero-shell {
      min-height: 320px;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 10px 0;
    }

    .hero {
      width: 100%;
      max-width: 380px;
      text-align: center;
      padding: 22px 14px 18px;
    }

    .hero-icon {
      width: 76px;
      height: 76px;
      margin: 0 auto 14px;
      border-radius: 24px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 36px;
      background: linear-gradient(180deg, #7f9cff 0%, #536dfe 100%);
      color: #fff;
      box-shadow: 0 18px 34px rgba(83,109,254,.22);
    }

    .hero-title {
      font-size: 28px;
      font-weight: 800;
      line-height: 1.1;
      letter-spacing: -.2px;
      margin-bottom: 8px;
    }

    .hero-title .accent {
      color: var(--link);
    }

    .hero-subtitle {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.55;
      margin-bottom: 16px;
    }

    .hero-cta-row {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }

    .hero-disclaimer {
      font-size: 10px;
      color: var(--muted);
      opacity: .72;
      line-height: 1.45;
    }

    .btn,
    .btn-secondary {
      min-height: 32px;
      padding: 0 14px;
      border-radius: 8px;
      font-size: 12px;
      cursor: pointer;
      border: 1px solid transparent;
    }

    .btn {
      background: var(--btn-bg);
      color: var(--btn-fg);
      border-color: transparent;
    }

    .btn:hover {
      background: var(--btn-hover);
    }

    .btn-secondary {
      background: var(--btn2-bg);
      color: var(--btn2-fg);
      border-color: var(--border);
    }

    .qa-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px;
    }

    .qa-btn {
      min-height: 40px;
      padding: 9px 10px;
      text-align: left;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: transparent;
      color: var(--fg);
      cursor: pointer;
      font-size: 11px;
      line-height: 1.35;
    }

    .qa-btn:disabled {
      opacity: .38;
      cursor: not-allowed;
    }

    .chat-shell {
      display: flex;
      flex-direction: column;
      min-height: 280px;
      padding: 12px;
    }

    .chat-header {
      margin-bottom: 8px;
    }

    .chat-messages {
      max-height: 380px;
      overflow-y: auto;
      padding-right: 2px;
      margin-bottom: 10px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .message {
      display: flex;
      flex-direction: column;
      gap: 4px;
      max-width: 100%;
    }

    .message.user {
      align-items: flex-end;
    }

    .message.assistant {
      align-items: flex-start;
    }

    .message-bubble {
      max-width: 86%;
      border-radius: 14px;
      padding: 9px 12px;
      font-size: 12px;
      line-height: 1.58;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .message.user .message-bubble {
      background: var(--btn-bg);
      color: var(--btn-fg);
      border-bottom-right-radius: 4px;
    }

    .message.assistant .message-bubble {
      background: transparent;
      border: 1px solid var(--border);
      border-bottom-left-radius: 4px;
    }

    .message-meta {
      font-size: 10px;
      color: var(--muted);
      padding: 0 2px;
    }

    .message-bubble code {
      background: var(--vscode-textCodeBlock-background, rgba(255,255,255,.06));
      border-radius: 4px;
      padding: 1px 4px;
      font-family: var(--vscode-editor-font-family);
      font-size: .92em;
    }

    .message-bubble pre {
      margin: 8px 0;
      padding: 10px;
      border-radius: 8px;
      background: var(--vscode-textCodeBlock-background, rgba(255,255,255,.06));
      overflow-x: auto;
    }

    .message-bubble pre code {
      background: transparent;
      padding: 0;
      border-radius: 0;
    }

    .workflow-hint {
      margin-bottom: 10px;
      padding: 8px 10px;
      border-radius: 10px;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--muted);
      font-size: 11px;
      line-height: 1.4;
    }

    .suggestions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 10px;
    }

    .suggest-chip {
      min-height: 30px;
      padding: 0 12px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: transparent;
      color: var(--fg);
      cursor: pointer;
      font-size: 11px;
      white-space: nowrap;
    }

    .composer {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-top: auto;
    }

    .composer-row {
      display: flex;
      align-items: flex-end;
      gap: 6px;
    }

    .composer-input {
      flex: 1;
      min-height: 44px;
      max-height: 140px;
      resize: vertical;
      border-radius: 10px;
      border: 1px solid var(--input-border);
      background: var(--input-bg);
      color: var(--input-fg);
      padding: 10px 12px;
      font-size: 12px;
      line-height: 1.45;
    }

    .composer-help {
      font-size: 10px;
      color: var(--muted);
      opacity: .76;
    }

    .chat-blocked {
      text-align: center;
      padding: 10px 12px;
      font-size: 12px;
      color: var(--muted);
    }

    .banner-error {
      padding: 9px 12px;
      border-radius: 10px;
      border: 1px solid var(--error);
      background: rgba(244, 67, 54, .1);
      color: var(--fg);
      font-size: 12px;
      line-height: 1.45;
    }

    .empty-chat {
      padding: 18px 10px;
      text-align: center;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }

    .spacer-2 {
      height: 2px;
    }

    @media (max-width: 420px) {
      .hero-title {
        font-size: 24px;
      }

      .qa-grid {
        grid-template-columns: 1fr;
      }

      .topbar {
        align-items: flex-start;
      }

      .message-bubble {
        max-width: 92%;
      }
    }
  </style>
</head>
<body>
  <div id="app" class="app">
    <div id="error-banner" class="banner-error hidden" role="alert"></div>

    <div id="workspace-empty" class="surface hero-shell hidden">
      <div class="hero">
        <div class="hero-icon">🤖</div>
        <div class="hero-title">Hi, I’m <span class="accent">GitPilot</span></div>
        <div class="hero-subtitle">
          Ask me to code, refactor, explain, debug, or generate tests.<br />
          Configure your model and open a workspace to get started.
        </div>
        <div class="hero-cta-row">
          <button class="btn" id="open-workspace-btn">Open Folder</button>
          <button class="btn-secondary" id="open-settings-btn">LLM Settings</button>
        </div>
        <div class="hero-disclaimer">
          Users should independently verify the accuracy of AI-generated output.
        </div>
      </div>
    </div>

    <div id="workspace-main" class="hidden">
      <div class="surface topbar">
        <div class="brand">
          <div class="brand-avatar">🤖</div>
          <div class="brand-copy">
            <div class="brand-title">GitPilot</div>
            <div class="brand-subtitle" id="top-subtitle">Ready</div>
          </div>
        </div>
        <div class="topbar-actions">
          <button class="chip-btn" id="model-btn" title="Change model">Model</button>
          <button class="icon-btn" id="llm-settings-btn" title="LLM Settings" aria-label="LLM Settings">⚙</button>
          <button class="icon-btn" id="refresh-btn" title="Refresh" aria-label="Refresh">↻</button>
        </div>
      </div>

      <div class="surface provider-strip" id="provider-strip" title="Change provider">
        <span id="provider-dot" class="status-dot status-unknown"></span>
        <div class="provider-copy">
          <div class="provider-name" id="provider-name">Not configured</div>
          <div class="provider-model" id="provider-model">Choose a provider and model</div>
        </div>
      </div>

      <div class="mode-selector">
        <button class="mode-trigger" id="mode-trigger" aria-expanded="false" aria-haspopup="listbox">
          <div class="mode-trigger-copy">
            <div class="mode-label">Workflow</div>
            <div class="mode-value" id="mode-value">✨ Auto</div>
          </div>
          <div class="mode-chevron">▼</div>
        </button>
        <div id="mode-dropdown" class="mode-dropdown hidden" role="listbox" aria-label="Workflow selector"></div>
      </div>

      <div class="pills" id="context-pills">
        <span class="pill" id="pill-mode">Mode: —</span>
        <span class="pill" id="pill-folder">Folder: —</span>
        <span class="pill" id="pill-repo">Repo: —</span>
        <span class="pill" id="pill-branch">Branch: —</span>
        <span class="pill" id="pill-session">Session: —</span>
      </div>

      <div class="surface panel">
        <div class="section-title">Quick Actions</div>
        <div class="qa-grid" id="quick-actions"></div>
      </div>

      <div class="surface chat-shell">
        <div class="chat-header">
          <div class="section-title">Chat</div>
        </div>

        <div id="workflow-hint" class="workflow-hint hidden"></div>

        <div id="chat-messages" class="chat-messages" aria-live="polite">
          <div class="empty-chat">What would you like GitPilot to help you with?</div>
        </div>

        <div id="suggestions" class="suggestions">
          <button class="suggest-chip" data-suggestion="Explain this code">Explain code</button>
          <button class="suggest-chip" data-suggestion="Fix the issue in this code">Fix issue</button>
          <button class="suggest-chip" data-suggestion="Refactor this code to be cleaner">Refactor</button>
          <button class="suggest-chip" data-suggestion="Generate tests for this code">Generate tests</button>
          <button class="suggest-chip" data-suggestion="Review this code for issues">Review</button>
        </div>

        <div class="composer">
          <div class="composer-row">
            <textarea
              id="chat-input"
              class="composer-input"
              rows="2"
              placeholder="Describe what you want GitPilot to do..."
            ></textarea>
            <button class="btn" id="send-btn">Send</button>
          </div>
          <div class="composer-help">Enter to send · Shift+Enter for newline</div>
          <div id="chat-blocked" class="chat-blocked hidden">
            Chat is unavailable until setup is complete.
          </div>
        </div>
      </div>
    </div>
  </div>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();

    /** @type {any} */
    let currentState = null;
    /** @type {Array<{id?: string, role: string, content: string, createdAt?: string}>} */
    let chatMessages = [];
    let modeDropdownOpen = false;

    const PROVIDER_LABELS = {
      openai: "OpenAI",
      claude: "Claude",
      watsonx: "Watsonx",
      ollama: "Ollama",
      ollabridge: "OllaBridge"
    };

    const WORKFLOW_OPTIONS = [
      { id: "auto", emoji: "✨", label: "Auto", desc: "GitPilot chooses the best workflow for the task", group: "auto" },
      { id: "default", emoji: "🔀", label: "Dispatch", desc: "Routes work to specialized agents based on task type", agents: 10, group: "arch" },
      { id: "gitpilot_code", emoji: "🧠", label: "ReAct Loop", desc: "Single main agent with iterative reasoning and subagents", agents: 6, group: "arch" },
      { id: "lite_mode", emoji: "💡", label: "Lite Mode", desc: "Optimized for smaller local models", agents: 1, group: "arch" },
      { id: "feature_builder", emoji: "🚀", label: "Feature Builder", desc: "Explore, plan, implement, review, and prepare PR", agents: 5, group: "pipeline" },
      { id: "bug_hunter", emoji: "🐛", label: "Bug Hunter", desc: "Diagnose, fix, verify, and prepare a hotfix", agents: 4, group: "pipeline" },
      { id: "code_inspector", emoji: "🔍", label: "Code Inspector", desc: "Read-only analysis and issue discovery", agents: 2, group: "pipeline" },
      { id: "architect_mode", emoji: "📐", label: "Architect Mode", desc: "Research and design without changing code", agents: 2, group: "pipeline" },
      { id: "quick_fix", emoji: "⚡", label: "Quick Fix", desc: "Minimal workflow for focused small edits", agents: 2, group: "pipeline" }
    ];

    function post(msg) {
      vscode.postMessage(msg);
    }

    function byId(id) {
      return document.getElementById(id);
    }

    function safeText(value, fallback = "—") {
      if (value === null || value === undefined || value === "") return fallback;
      return String(value);
    }

    function providerLabel(value) {
      return PROVIDER_LABELS[value] || safeText(value, "No provider");
    }

    function getWorkflowOption(id) {
      return WORKFLOW_OPTIONS.find(item => item.id === id) || WORKFLOW_OPTIONS[0];
    }

    function escapeHtml(str) {
      const div = document.createElement("div");
      div.textContent = String(str ?? "");
      return div.innerHTML;
    }

    function renderMarkdown(input) {
      let html = escapeHtml(input || "");

      html = html.replace(/\\\`\\\`\\\`(\\w*)\\n([\\s\\S]*?)\\\`\\\`\\\`/g, (_m, _lang, code) => {
        return "<pre><code>" + code + "</code></pre>";
      });

      html = html.replace(/\\\`([^\\\`]+)\\\`/g, "<code>$1</code>");
      html = html.replace(/\\*\\*(.+?)\\*\\*/g, "<strong>$1</strong>");
      html = html.replace(/\\*(.+?)\\*/g, "<em>$1</em>");

      return html;
    }

    function showError(title, message) {
      const banner = byId("error-banner");
      banner.textContent = (title || "Error") + ": " + (message || "Unknown error");
      banner.classList.remove("hidden");

      window.clearTimeout(showError._timer);
      showError._timer = window.setTimeout(() => {
        banner.classList.add("hidden");
      }, 8000);
    }

    function syncVisibility(state) {
      const folderOpen = !!state?.workspace?.folderOpen;
      byId("workspace-empty").classList.toggle("hidden", folderOpen);
      byId("workspace-main").classList.toggle("hidden", !folderOpen);
    }

    function renderTopbar(state) {
      const provider = providerLabel(state?.provider?.providerName);
      const model = safeText(state?.provider?.model, "Model");
      const connected = !!state?.server?.connected;

      byId("top-subtitle").textContent = provider + " · " + (connected ? "Connected" : "Disconnected");
      byId("model-btn").textContent = model === "—" ? "Model" : model;
    }

    function renderProviderStrip(state) {
      const providerName = state?.provider?.providerName;
      const model = state?.provider?.model;
      const source = state?.provider?.source;
      const health = state?.provider?.health;

      byId("provider-name").textContent = providerName ? providerLabel(providerName) : "Not configured";

      const parts = [];
      if (model) parts.push(String(model));
      if (source) parts.push("from " + String(source));
      byId("provider-model").textContent =
        parts.length > 0 ? parts.join(" · ") : "Choose a provider and model";

      const dot = byId("provider-dot");
      dot.className = "status-dot";
      if (health === "ok") dot.classList.add("status-ok");
      else if (health === "warning") dot.classList.add("status-warning");
      else if (health === "error") dot.classList.add("status-error");
      else dot.classList.add("status-unknown");
    }

    function renderWorkflowTrigger(state) {
      const selectedMode = state?.workflow?.selectedMode || "auto";
      const option = getWorkflowOption(selectedMode);
      byId("mode-value").textContent = option.emoji + " " + option.label;
    }

    function renderWorkflowHint(state) {
      const hint = byId("workflow-hint");
      const selectedMode = state?.workflow?.selectedMode;
      const effectiveMode = state?.workflow?.effectiveMode;
      const reason = state?.workflow?.reason;

      if (!selectedMode && !effectiveMode && !reason) {
        hint.classList.add("hidden");
        hint.textContent = "";
        return;
      }

      const selectedLabel = getWorkflowOption(selectedMode || "auto").label;
      const effectiveLabel = getWorkflowOption(effectiveMode || selectedMode || "auto").label;

      let text = "Selected workflow: " + selectedLabel;
      if (effectiveLabel && effectiveLabel !== selectedLabel) {
        text += " · Using " + effectiveLabel;
      }
      if (reason) {
        text += " · " + reason;
      }

      hint.textContent = text;
      hint.classList.remove("hidden");
    }

    function renderWorkflowDropdown(state) {
      const dropdown = byId("mode-dropdown");
      const selectedMode = state?.workflow?.selectedMode || "auto";

      const groups = [
        { key: "auto", label: "Recommended" },
        { key: "arch", label: "System Architectures" },
        { key: "pipeline", label: "Task Pipelines" }
      ];

      dropdown.innerHTML = groups.map(group => {
        const items = WORKFLOW_OPTIONS
          .filter(option => option.group === group.key)
          .map(option => {
            const active = option.id === selectedMode ? " active" : "";
            const badge = option.agents
              ? '<span class="mode-badge">' + option.agents + ' agents</span>'
              : "";
            return (
              '<button class="mode-option' + active + '" data-mode="' + option.id + '" role="option" aria-selected="' + (option.id === selectedMode ? "true" : "false") + '">' +
                '<span class="mode-emoji">' + option.emoji + "</span>" +
                '<div class="mode-info">' +
                  '<div class="mode-title-row">' +
                    '<span class="mode-title">' + escapeHtml(option.label) + "</span>" +
                    badge +
                  "</div>" +
                  '<div class="mode-desc">' + escapeHtml(option.desc) + "</div>" +
                "</div>" +
              "</button>"
            );
          })
          .join("");

        return (
          '<div class="mode-group-title">' + group.label + "</div>" +
          items
        );
      }).join("");

      dropdown.querySelectorAll("[data-mode]").forEach((el) => {
        el.addEventListener("click", () => {
          const mode = el.getAttribute("data-mode");
          if (!mode) return;
          post({ type: "SET_WORKFLOW_MODE", payload: { mode } });
          closeModeDropdown();
        });
      });
    }

    function openModeDropdown() {
      modeDropdownOpen = true;
      byId("mode-dropdown").classList.remove("hidden");
      byId("mode-trigger").setAttribute("aria-expanded", "true");
      renderWorkflowDropdown(currentState);
    }

    function closeModeDropdown() {
      modeDropdownOpen = false;
      byId("mode-dropdown").classList.add("hidden");
      byId("mode-trigger").setAttribute("aria-expanded", "false");
    }

    function toggleModeDropdown() {
      if (modeDropdownOpen) closeModeDropdown();
      else openModeDropdown();
    }

    function renderContextPills(state) {
      const modeLabels = {
        folder: "Folder",
        local_git: "Local Git",
        github: "GitHub"
      };

      byId("pill-mode").textContent = "Mode: " + safeText(modeLabels[state?.workspace?.mode], "—");
      byId("pill-folder").textContent = "Folder: " + safeText(state?.workspace?.folderName, "—");
      byId("pill-repo").textContent = "Repo: " + safeText(state?.workspace?.git?.repoName, "—");
      byId("pill-branch").textContent = "Branch: " + safeText(state?.workspace?.git?.branch, "—");
      byId("pill-session").textContent =
        "Session: " + (state?.session?.active ? safeText(state?.session?.title, "Active") : "Not started");
    }

    function renderQuickActions(state) {
      const canRun = !!state?.readiness?.canRunProjectActions;
      const actions = [
        { id: "explain_project", label: "📘 Explain project" },
        { id: "review_file", label: "👁 Review file" },
        { id: "fix_selection", label: "🔧 Fix selection" },
        { id: "generate_tests", label: "🧪 Generate tests" },
        { id: "security_scan", label: "🛡 Security scan" }
      ];

      const container = byId("quick-actions");
      container.innerHTML = actions.map(action => {
        return (
          '<button class="qa-btn" ' +
            (canRun ? "" : "disabled") +
            ' data-action="' + action.id + '">' +
            action.label +
          "</button>"
        );
      }).join("");

      container.querySelectorAll("[data-action]").forEach((el) => {
        el.addEventListener("click", () => {
          const action = el.getAttribute("data-action");
          if (!action) return;
          post({ type: "RUN_QUICK_ACTION", payload: { action } });
        });
      });
    }

    function renderChatAvailability(state) {
      const canChat = !!state?.readiness?.canChat;
      byId("chat-input").disabled = !canChat;
      byId("send-btn").disabled = !canChat;
      byId("chat-blocked").classList.toggle("hidden", canChat);
    }

    function renderChatMessages() {
      const container = byId("chat-messages");

      if (!chatMessages.length) {
        container.innerHTML = '<div class="empty-chat">What would you like GitPilot to help you with?</div>';
        return;
      }

      container.innerHTML = chatMessages.map((message) => {
        const role = message.role === "user" ? "user" : "assistant";
        const label = role === "user" ? "You" : "GitPilot";

        return (
          '<div class="message ' + role + '">' +
            '<div class="message-meta">' + label + "</div>" +
            '<div class="message-bubble">' + renderMarkdown(message.content || "") + "</div>" +
          "</div>"
        );
      }).join("");

      container.scrollTop = container.scrollHeight;
    }

    function pushUserMessage(text) {
      chatMessages.push({
        id: String(Date.now()),
        role: "user",
        content: text,
        createdAt: new Date().toISOString()
      });
      renderChatMessages();
    }

    function pushAssistantMessage(payload) {
      if (!payload) return;

      if (typeof payload === "string") {
        chatMessages.push({
          id: String(Date.now()),
          role: "assistant",
          content: payload,
          createdAt: new Date().toISOString()
        });
      } else {
        chatMessages.push({
          id: payload.id || String(Date.now()),
          role: payload.role || "assistant",
          content: payload.content || "",
          createdAt: payload.createdAt || new Date().toISOString()
        });
      }

      renderChatMessages();
    }

    function hideSuggestionsIfNeeded() {
      if (chatMessages.length > 0) {
        byId("suggestions").classList.add("hidden");
      } else {
        byId("suggestions").classList.remove("hidden");
      }
    }

    function sendChat() {
      const input = byId("chat-input");
      const text = String(input.value || "").trim();
      if (!text) return;

      pushUserMessage(text);
      hideSuggestionsIfNeeded();

      input.value = "";
      input.style.height = "";
      post({ type: "SEND_CHAT", payload: { text } });
    }

    function applySuggestion(text) {
      byId("chat-input").value = text;
      sendChat();
    }

    function autoResizeTextarea() {
      const input = byId("chat-input");
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 140) + "px";
    }

    function render(state) {
      if (!state) return;

      syncVisibility(state);

      if (!state?.workspace?.folderOpen) {
        return;
      }

      renderTopbar(state);
      renderProviderStrip(state);
      renderWorkflowTrigger(state);
      renderWorkflowHint(state);
      renderContextPills(state);
      renderQuickActions(state);
      renderChatAvailability(state);
      hideSuggestionsIfNeeded();
    }

    function bindStaticEvents() {
      byId("open-workspace-btn").addEventListener("click", () => {
        post({ type: "OPEN_WORKSPACE" });
      });

      byId("open-settings-btn").addEventListener("click", () => {
        post({ type: "OPEN_LLM_SETTINGS" });
      });

      byId("llm-settings-btn").addEventListener("click", () => {
        post({ type: "OPEN_LLM_SETTINGS" });
      });

      byId("model-btn").addEventListener("click", () => {
        post({ type: "OPEN_MODEL_SETUP" });
      });

      byId("refresh-btn").addEventListener("click", () => {
        post({ type: "REFRESH_STATUS" });
      });

      byId("provider-strip").addEventListener("click", () => {
        post({ type: "OPEN_PROVIDER_SETUP" });
      });

      byId("mode-trigger").addEventListener("click", () => {
        toggleModeDropdown();
      });

      document.addEventListener("click", (event) => {
        const trigger = byId("mode-trigger");
        const dropdown = byId("mode-dropdown");
        if (!modeDropdownOpen) return;

        const target = event.target;
        if (!(target instanceof Node)) return;

        if (!trigger.contains(target) && !dropdown.contains(target)) {
          closeModeDropdown();
        }
      });

      byId("send-btn").addEventListener("click", () => {
        sendChat();
      });

      byId("chat-input").addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          sendChat();
        }
      });

      byId("chat-input").addEventListener("input", () => {
        autoResizeTextarea();
      });

      document.querySelectorAll("[data-suggestion]").forEach((el) => {
        el.addEventListener("click", () => {
          const text = el.getAttribute("data-suggestion");
          if (!text) return;
          applySuggestion(text);
        });
      });
    }

    window.addEventListener("message", (event) => {
      const msg = event.data;

      if (!msg || typeof msg !== "object") return;

      if (msg.type === "STATE_SYNC") {
        currentState = msg.payload;
        render(currentState);
        return;
      }

      if (msg.type === "CHAT_RESPONSE") {
        pushAssistantMessage(msg.payload);
        hideSuggestionsIfNeeded();
        return;
      }

      if (msg.type === "ERROR") {
        showError(msg.payload?.title, msg.payload?.message);
      }
    });

    bindStaticEvents();
    post({ type: "INIT" });
  </script>
</body>
</html>`;
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