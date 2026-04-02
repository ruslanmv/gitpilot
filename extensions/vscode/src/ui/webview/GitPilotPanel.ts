/**
 * GitPilot Redesign — State-Driven Webview Panel
 * Modern Copilot-style sidebar with compact topbar, provider strip,
 * context pills, chat thread, and quick actions.
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
  private _disposables: vscode.Disposable[] = [];

  constructor(
    private _extensionUri: vscode.Uri,
    private _stateStore: StateStore,
    private _onMessage: (msg: WebviewToExtensionMessage) => void
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

    // Listen for messages from webview
    this._disposables.push(
      webviewView.webview.onDidReceiveMessage((msg: WebviewToExtensionMessage) => {
        if (msg.type === "INIT") {
          this._syncState();
        } else {
          this._onMessage(msg);
        }
      })
    );

    // Listen for state changes
    this._disposables.push(
      this._stateStore.onDidChangeState(() => {
        this._syncState();
      })
    );

    // Set initial HTML
    webviewView.webview.html = this._getHtml();

    // Initial state sync
    this._syncState();
  }

  private _syncState(): void {
    if (this._bridge) {
      this._bridge.postMessage({
        type: "STATE_SYNC",
        payload: this._stateStore.state as GitPilotState,
      });
    }
  }

  postMessage(msg: ExtensionToWebviewMessage): void {
    this._bridge?.postMessage(msg);
  }

  private _getHtml(): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>GitPilot</title>
  <style>
    :root {
      --bg: var(--vscode-sideBar-background);
      --fg: var(--vscode-sideBar-foreground);
      --border: var(--vscode-panel-border);
      --input-bg: var(--vscode-input-background);
      --input-fg: var(--vscode-input-foreground);
      --input-border: var(--vscode-input-border);
      --btn-bg: var(--vscode-button-background);
      --btn-fg: var(--vscode-button-foreground);
      --btn-hover: var(--vscode-button-hoverBackground);
      --btn2-bg: var(--vscode-button-secondaryBackground);
      --btn2-fg: var(--vscode-button-secondaryForeground);
      --muted: var(--vscode-descriptionForeground);
      --panel: var(--vscode-editorWidget-background, var(--input-bg));
      --link: var(--vscode-textLink-foreground);
      --success: #4caf50;
      --warning: #ff9800;
      --error: #f44336;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--vscode-font-family);
      font-size: var(--vscode-font-size);
      color: var(--fg);
      background: var(--bg);
      padding: 10px;
      overflow-x: hidden;
    }

    .hidden { display: none !important; }
    #app { max-width: 100%; }

    /* ── Top Bar ────────────────────────────── */
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--panel);
      margin-bottom: 8px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }
    .avatar {
      width: 32px; height: 32px;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: var(--btn-bg);
      color: var(--btn-fg);
      font-size: 16px;
      flex: 0 0 auto;
    }
    .brand-copy { min-width: 0; }
    .brand-title { font-weight: 700; font-size: 13px; }
    .brand-subtitle {
      color: var(--muted);
      font-size: 10px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .topbar-actions { display: flex; gap: 4px; flex-shrink: 0; }
    .icon-btn, .chip-btn {
      border: 1px solid var(--border);
      background: transparent;
      color: var(--fg);
      border-radius: 6px;
      cursor: pointer;
      font-family: inherit;
      font-size: 11px;
      transition: background 0.12s, color 0.12s;
    }
    .icon-btn {
      width: 28px; height: 28px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 14px;
    }
    .chip-btn {
      padding: 0 8px;
      height: 28px;
      max-width: 140px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .icon-btn:hover, .chip-btn:hover {
      background: var(--btn-bg);
      color: var(--btn-fg);
    }

    /* ── Provider Strip ─────────────────────── */
    .provider-strip {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--panel);
      margin-bottom: 8px;
      cursor: pointer;
      transition: border-color 0.15s;
    }
    .provider-strip:hover { border-color: var(--btn-bg); }
    .status-dot {
      display: inline-block;
      width: 8px; height: 8px;
      border-radius: 50%;
      flex: 0 0 auto;
    }
    .status-ok { background: var(--success); }
    .status-warning { background: var(--warning); }
    .status-error { background: var(--error); }
    .status-unknown { background: #666; }
    .provider-copy { min-width: 0; flex: 1; }
    .provider-name { font-weight: 600; font-size: 12px; }
    .provider-model {
      color: var(--muted);
      font-size: 11px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    /* ── Mode Selector ──────────────────────── */
    .mode-selector {
      position: relative;
      margin-bottom: 8px;
    }
    .mode-trigger {
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 9px 12px;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--panel);
      color: var(--fg);
      cursor: pointer;
      font-family: inherit;
      font-size: 12px;
      text-align: left;
      transition: border-color 0.12s;
    }
    .mode-trigger:hover { border-color: var(--btn-bg); }
    .mode-trigger .mode-label {
      font-size: 10px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .4px;
    }
    .mode-trigger .mode-value { font-weight: 600; }
    .mode-trigger .chevron {
      font-size: 10px;
      color: var(--muted);
      flex: 0 0 auto;
    }
    .mode-dropdown {
      position: absolute;
      top: calc(100% + 4px);
      left: 0; right: 0;
      z-index: 20;
      max-height: 380px;
      overflow-y: auto;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--panel);
      box-shadow: 0 8px 24px rgba(0,0,0,0.25);
      padding: 6px;
    }
    .mode-group-header {
      font-size: 10px;
      font-weight: 700;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .4px;
      padding: 8px 8px 4px;
    }
    .mode-option {
      width: 100%;
      display: flex;
      align-items: flex-start;
      gap: 8px;
      padding: 8px 10px;
      border: 1px solid transparent;
      border-radius: 8px;
      background: transparent;
      color: var(--fg);
      cursor: pointer;
      text-align: left;
      font-family: inherit;
      font-size: 12px;
      margin-bottom: 2px;
      transition: background 0.1s;
    }
    .mode-option:hover { background: var(--vscode-list-hoverBackground, rgba(255,255,255,.05)); }
    .mode-option.active {
      border-color: var(--vscode-focusBorder);
      background: var(--vscode-list-hoverBackground, rgba(255,255,255,.05));
    }
    .mode-option .mode-emoji { font-size: 14px; flex: 0 0 auto; margin-top: 1px; }
    .mode-option .mode-info { min-width: 0; flex: 1; }
    .mode-option .mode-title { font-weight: 600; font-size: 12px; }
    .mode-option .mode-desc {
      font-size: 11px;
      color: var(--muted);
      line-height: 1.3;
      margin-top: 2px;
    }
    .mode-badge {
      font-size: 9px;
      padding: 1px 5px;
      border-radius: 999px;
      background: var(--vscode-badge-background, rgba(255,255,255,.1));
      color: var(--vscode-badge-foreground, var(--muted));
      flex: 0 0 auto;
      margin-top: 2px;
    }

    /* ── Context Pills ──────────────────────── */
    .pills {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      margin-bottom: 8px;
    }
    .pill {
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: transparent;
      font-size: 10px;
      color: var(--muted);
      white-space: nowrap;
    }

    /* ── Panels ─────────────────────────────── */
    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
      margin-bottom: 8px;
    }
    .section-title {
      font-weight: 600;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: .4px;
      margin-bottom: 8px;
      color: var(--muted);
    }

    /* ── Hero / Empty State ─────────────────── */
    .hero {
      text-align: center;
      padding: 24px 14px 18px;
    }
    .hero-icon {
      width: 56px; height: 56px;
      margin: 0 auto 10px;
      border-radius: 16px;
      background: var(--btn-bg);
      color: var(--btn-fg);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 28px;
    }
    .hero h3 { font-size: 17px; font-weight: 700; margin-bottom: 4px; }
    .hero h3 .accent { color: var(--link); }
    .hero p {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
      margin-bottom: 14px;
    }
    .hero .disclaimer {
      font-size: 10px;
      color: var(--muted);
      opacity: 0.6;
      margin-top: 14px;
    }

    /* ── Buttons ─────────────────────────────── */
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 7px 14px;
      border: none;
      border-radius: 6px;
      background: var(--btn-bg);
      color: var(--btn-fg);
      cursor: pointer;
      font-size: 12px;
      font-family: inherit;
      transition: background 0.12s;
    }
    .btn:hover { background: var(--btn-hover); }
    .btn:disabled { opacity: 0.4; cursor: not-allowed; }
    .btn-secondary {
      background: var(--btn2-bg, transparent);
      border: 1px solid var(--border);
      color: var(--btn2-fg, var(--fg));
    }
    .btn-secondary:hover {
      background: var(--btn-bg);
      color: var(--btn-fg);
    }
    .btn-row {
      display: flex;
      gap: 6px;
      justify-content: center;
    }

    /* ── Quick Actions ──────────────────────── */
    .quick-actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px;
    }
    .quick-action-btn {
      padding: 9px 10px;
      font-size: 11px;
      text-align: left;
      background: transparent;
      border: 1px solid var(--border);
      border-radius: 8px;
      color: var(--fg);
      cursor: pointer;
      font-family: inherit;
      transition: background 0.12s, border-color 0.12s;
    }
    .quick-action-btn:hover {
      background: var(--btn-bg);
      color: var(--btn-fg);
    }
    .quick-action-btn:disabled { opacity: 0.35; cursor: not-allowed; }

    /* ── Suggestion Chips ───────────────────── */
    .suggestions {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 10px;
    }
    .suggest-chip {
      padding: 6px 12px;
      border: 1px solid var(--border);
      border-radius: 999px;
      background: transparent;
      color: var(--fg);
      font-size: 11px;
      font-family: inherit;
      cursor: pointer;
      transition: background 0.12s, border-color 0.12s, color 0.12s;
      white-space: nowrap;
    }
    .suggest-chip:hover {
      background: var(--btn-bg);
      color: var(--btn-fg);
      border-color: var(--btn-bg);
    }

    /* ── Chat ────────────────────────────────── */
    .chat-shell {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
    }
    .chat-messages {
      max-height: 380px;
      overflow-y: auto;
      margin-bottom: 10px;
    }
    .chat-msg {
      padding: 8px 11px;
      margin: 6px 0;
      border-radius: 10px;
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .chat-msg.user {
      background: var(--btn-bg);
      color: var(--btn-fg);
      margin-left: 14%;
      border-bottom-right-radius: 3px;
    }
    .chat-msg.assistant {
      background: transparent;
      border: 1px solid var(--border);
      margin-right: 14%;
      border-bottom-left-radius: 3px;
    }
    .chat-msg.assistant code {
      background: var(--vscode-textCodeBlock-background, rgba(255,255,255,.06));
      padding: 1px 4px;
      border-radius: 3px;
      font-family: var(--vscode-editor-font-family);
      font-size: 0.92em;
    }
    .chat-msg.assistant pre {
      background: var(--vscode-textCodeBlock-background, rgba(255,255,255,.06));
      padding: 8px 10px;
      border-radius: 6px;
      overflow-x: auto;
      margin: 6px 0;
    }
    .chat-msg.assistant pre code { background: none; padding: 0; }
    .chat-input-row {
      display: flex;
      gap: 6px;
      align-items: flex-end;
    }
    .chat-input {
      flex: 1;
      padding: 9px 12px;
      background: var(--input-bg);
      color: var(--input-fg);
      border: 1px solid var(--input-border);
      border-radius: 8px;
      font-size: 12px;
      resize: vertical;
      min-height: 40px;
      max-height: 140px;
      font-family: inherit;
      line-height: 1.4;
      outline: none;
    }
    .chat-input:focus { border-color: var(--vscode-focusBorder); }
    .chat-input::placeholder { opacity: 0.5; }
    .input-help {
      color: var(--muted);
      font-size: 10px;
      margin-top: 5px;
      opacity: 0.65;
    }

    /* ── Error Banner ───────────────────────── */
    .error-banner {
      background: rgba(244, 67, 54, 0.1);
      border: 1px solid var(--error);
      border-radius: 8px;
      padding: 8px 12px;
      margin-bottom: 8px;
      font-size: 12px;
    }
  </style>
</head>
<body>
  <div id="app">
    <!-- ── Empty State (no folder open) ── -->
    <div id="empty-state" class="hidden">
      <div class="panel hero">
        <div class="hero-icon">&#129302;</div>
        <h3>Hi, I'm <span class="accent">GitPilot</span></h3>
        <p>Ask me to code, refactor, explain,<br>debug, or generate tests.</p>
        <div class="btn-row">
          <button class="btn" onclick="send({type:'OPEN_WORKSPACE'})">Open Folder</button>
          <button class="btn btn-secondary" onclick="send({type:'OPEN_LLM_SETTINGS'})">LLM Settings</button>
        </div>
        <p class="disclaimer">Users should independently verify accuracy of AI-generated content.</p>
      </div>
    </div>

    <!-- ── Main UI (folder open) ── -->
    <div id="main-ui" class="hidden">
      <div id="error-banner" class="error-banner hidden"></div>

      <!-- Top Bar -->
      <div class="topbar">
        <div class="brand">
          <div class="avatar">&#129302;</div>
          <div class="brand-copy">
            <div class="brand-title">GitPilot</div>
            <div class="brand-subtitle" id="top-subtitle">Ready</div>
          </div>
        </div>
        <div class="topbar-actions">
          <button class="chip-btn" id="model-btn" title="Change model" onclick="send({type:'OPEN_MODEL_SETUP'})">Model</button>
          <button class="icon-btn" title="LLM Settings" onclick="send({type:'OPEN_LLM_SETTINGS'})">&#9881;</button>
          <button class="icon-btn" title="Refresh" onclick="send({type:'REFRESH_STATUS'})">&#8635;</button>
        </div>
      </div>

      <!-- Provider Strip -->
      <div class="provider-strip" onclick="send({type:'OPEN_PROVIDER_SETUP'})">
        <span id="provider-dot" class="status-dot status-unknown"></span>
        <div class="provider-copy">
          <div class="provider-name" id="provider-name">Not configured</div>
          <div class="provider-model" id="provider-model">Choose a provider and model</div>
        </div>
      </div>

      <!-- Mode Selector -->
      <div class="mode-selector" id="mode-selector">
        <button class="mode-trigger" onclick="toggleModeDropdown()">
          <div>
            <div class="mode-label">Mode</div>
            <div class="mode-value" id="mode-value">&#10024; Auto</div>
          </div>
          <span class="chevron">&#9660;</span>
        </button>
        <div class="mode-dropdown hidden" id="mode-dropdown"></div>
      </div>

      <!-- Context Pills -->
      <div class="pills" id="context-pills">
        <span class="pill" id="pill-mode">Mode: &#8212;</span>
        <span class="pill" id="pill-folder">Folder: &#8212;</span>
        <span class="pill" id="pill-repo">Repo: &#8212;</span>
        <span class="pill" id="pill-branch">Branch: &#8212;</span>
        <span class="pill" id="pill-session">Session: &#8212;</span>
      </div>

      <!-- Quick Actions -->
      <div class="panel">
        <div class="section-title">Quick Actions</div>
        <div class="quick-actions" id="quick-actions"></div>
      </div>

      <!-- Chat -->
      <div class="chat-shell" id="chat-card">
        <div class="section-title">Chat</div>
        <div class="chat-messages" id="chat-messages">
          <div class="chat-msg assistant">What would you like to do with your code?</div>
        </div>
        <div class="suggestions" id="suggestions">
          <button class="suggest-chip" onclick="sendSuggestion('Explain this code')">Explain code</button>
          <button class="suggest-chip" onclick="sendSuggestion('Fix the issue in this code')">Fix issue</button>
          <button class="suggest-chip" onclick="sendSuggestion('Refactor this code to be cleaner')">Refactor</button>
          <button class="suggest-chip" onclick="sendSuggestion('Generate tests for this code')">Generate tests</button>
          <button class="suggest-chip" onclick="sendSuggestion('Review this code for issues')">Review</button>
        </div>
        <div class="chat-input-row">
          <textarea class="chat-input" id="chat-input" rows="2" placeholder="Describe what you want GitPilot to do..." onkeydown="handleChatKey(event)"></textarea>
          <button class="btn" id="chat-send-btn" onclick="sendChat()">Send</button>
        </div>
        <div class="input-help">Enter to send &#183; Shift+Enter for newline</div>
        <div id="chat-blocked" class="hidden" style="text-align:center;padding:12px;opacity:0.7;font-size:12px;">
          Chat unavailable. Complete setup first.
        </div>
      </div>
    </div>
  </div>

  <script>
    const vscode = acquireVsCodeApi();
    let currentState = null;
    let chatMessages = [];

    function send(msg) { vscode.postMessage(msg); }

    // ── Message handler ─────────────────────────
    window.addEventListener('message', function(event) {
      var msg = event.data;
      if (msg.type === 'STATE_SYNC') {
        currentState = msg.payload;
        render(currentState);
      } else if (msg.type === 'CHAT_RESPONSE') {
        chatMessages.push(msg.payload);
        renderChatMessages();
      } else if (msg.type === 'ERROR') {
        showError(msg.payload);
      }
    });

    // ── Main render ─────────────────────────────
    function render(state) {
      if (!state) return;

      var emptyState = document.getElementById('empty-state');
      var mainUi = document.getElementById('main-ui');

      if (!state.workspace.folderOpen) {
        emptyState.classList.remove('hidden');
        mainUi.classList.add('hidden');
        return;
      }

      emptyState.classList.add('hidden');
      mainUi.classList.remove('hidden');

      renderTopbar(state);
      renderProviderStrip(state);
      renderModeSelector(state);
      renderContextPills(state);
      renderQuickActions(state);
      renderChat(state);
    }

    // ── Top bar ─────────────────────────────────
    function renderTopbar(state) {
      var providerLabels = { openai: 'OpenAI', claude: 'Claude', watsonx: 'Watsonx', ollama: 'Ollama', ollabridge: 'OllaBridge' };
      var provider = state.provider.providerName ? (providerLabels[state.provider.providerName] || state.provider.providerName) : 'No provider';
      var model = state.provider.model || 'No model';
      var health = state.server.connected ? 'Connected' : 'Disconnected';

      document.getElementById('top-subtitle').textContent = provider + ' \\u00B7 ' + health;
      document.getElementById('model-btn').textContent = model !== 'No model' ? model : 'Model';
    }

    // ── Provider strip ──────────────────────────
    function renderProviderStrip(state) {
      var p = state.provider;
      var providerLabels = { openai: 'OpenAI', claude: 'Claude', watsonx: 'Watsonx', ollama: 'Ollama', ollabridge: 'OllaBridge' };
      document.getElementById('provider-name').textContent = p.providerName ? (providerLabels[p.providerName] || p.providerName) : 'Not configured';

      var parts = [];
      if (p.model) parts.push(p.model);
      if (p.source) parts.push('from ' + p.source);
      document.getElementById('provider-model').textContent = parts.length > 0 ? parts.join(' \\u00B7 ') : 'Choose a provider and model';

      var dot = document.getElementById('provider-dot');
      dot.className = 'status-dot';
      if (p.health === 'ok') dot.classList.add('status-ok');
      else if (p.health === 'warning') dot.classList.add('status-warning');
      else if (p.health === 'error') dot.classList.add('status-error');
      else dot.classList.add('status-unknown');
    }

    // ── Context pills ───────────────────────────
    function renderContextPills(state) {
      var modeLabels = { folder: 'Folder', local_git: 'Local Git', github: 'GitHub' };
      document.getElementById('pill-mode').textContent = 'Mode: ' + (modeLabels[state.workspace.mode] || '\\u2014');
      document.getElementById('pill-folder').textContent = state.workspace.folderName || 'No folder';
      document.getElementById('pill-repo').textContent = state.workspace.git.repoName || 'No repo';
      document.getElementById('pill-branch').textContent = state.workspace.git.branch || 'No branch';
      document.getElementById('pill-session').textContent = state.session.active ? (state.session.title || 'Active') : 'No session';
    }

    // ── Quick actions ───────────────────────────
    function renderQuickActions(state) {
      var actions = [
        { id: 'explain_project', label: 'Explain project', icon: '\\uD83D\\uDCD6' },
        { id: 'review_file', label: 'Review file', icon: '\\uD83D\\uDC41' },
        { id: 'fix_selection', label: 'Fix selection', icon: '\\uD83D\\uDD27' },
        { id: 'generate_tests', label: 'Generate tests', icon: '\\uD83E\\uDDEA' },
        { id: 'security_scan', label: 'Security scan', icon: '\\uD83D\\uDEE1' },
      ];
      var canRun = state.readiness.canRunProjectActions;
      document.getElementById('quick-actions').innerHTML = actions.map(function(a) {
        return '<button class="quick-action-btn" ' + (canRun ? '' : 'disabled') +
          ' onclick="send({type:\\'RUN_QUICK_ACTION\\',payload:{action:\\'' + a.id + '\\'}})">' +
          a.icon + ' ' + a.label + '</button>';
      }).join('');
    }

    // ── Chat ────────────────────────────────────
    function renderChat(state) {
      var canChat = state.readiness.canChat;
      var chatBlocked = document.getElementById('chat-blocked');
      var chatInput = document.getElementById('chat-input');
      var chatSendBtn = document.getElementById('chat-send-btn');

      if (!canChat) {
        chatInput.disabled = true;
        chatSendBtn.disabled = true;
        chatBlocked.classList.remove('hidden');
      } else {
        chatInput.disabled = false;
        chatSendBtn.disabled = false;
        chatBlocked.classList.add('hidden');
      }
    }

    function renderChatMessages() {
      var container = document.getElementById('chat-messages');
      if (chatMessages.length === 0) {
        container.innerHTML = '<div class="chat-msg assistant">What would you like to do with your code?</div>';
        return;
      }
      container.innerHTML = chatMessages.map(function(m) {
        return '<div class="chat-msg ' + m.role + '">' + renderMd(m.content) + '</div>';
      }).join('');
      container.scrollTop = container.scrollHeight;
    }

    function sendChat() {
      var input = document.getElementById('chat-input');
      var text = input.value.trim();
      if (!text) return;

      chatMessages.push({ id: Date.now().toString(), role: 'user', content: text, createdAt: new Date().toISOString() });
      renderChatMessages();
      input.value = '';
      send({ type: 'SEND_CHAT', payload: { text: text } });
      // Hide suggestion chips once the user starts chatting
      var chips = document.getElementById('suggestions');
      if (chips) chips.classList.add('hidden');
    }

    function handleChatKey(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendChat();
      }
    }

    function sendSuggestion(text) {
      var input = document.getElementById('chat-input');
      input.value = text;
      sendChat();
      // Hide suggestion chips after first use
      var chips = document.getElementById('suggestions');
      if (chips) chips.classList.add('hidden');
    }

    // ── Mode selector ───────────────────────────
    var WORKFLOW_OPTIONS = [
      { id: 'auto', emoji: '\\u2728', label: 'Auto', desc: 'GitPilot chooses the best workflow', group: 'auto' },
      { id: 'default', emoji: '\\uD83D\\uDD00', label: 'Dispatch', desc: 'Router dispatches to specialized agents', agents: 10, group: 'arch' },
      { id: 'gitpilot_code', emoji: '\\uD83E\\uDDE0', label: 'ReAct Loop', desc: 'Single agent with iterative reasoning', agents: 6, group: 'arch' },
      { id: 'lite_mode', emoji: '\\uD83D\\uDCA1', label: 'Lite Mode', desc: 'Optimized for small models under 7B', agents: 1, group: 'arch' },
      { id: 'feature_builder', emoji: '\\uD83D\\uDE80', label: 'Feature Builder', desc: 'Explore, plan, implement, review, PR', agents: 5, group: 'pipeline' },
      { id: 'bug_hunter', emoji: '\\uD83D\\uDC1B', label: 'Bug Hunter', desc: 'Diagnose, fix, verify, ship', agents: 4, group: 'pipeline' },
      { id: 'code_inspector', emoji: '\\uD83D\\uDD0D', label: 'Code Inspector', desc: 'Read-only analysis and review', agents: 2, group: 'pipeline' },
      { id: 'architect_mode', emoji: '\\uD83D\\uDCD0', label: 'Architect Mode', desc: 'Design plan without code changes', agents: 2, group: 'pipeline' },
      { id: 'quick_fix', emoji: '\\u26A1', label: 'Quick Fix', desc: 'Minimal edit, commit, done', agents: 2, group: 'pipeline' },
    ];
    var modeDropdownOpen = false;

    function toggleModeDropdown() {
      modeDropdownOpen = !modeDropdownOpen;
      var dd = document.getElementById('mode-dropdown');
      dd.classList.toggle('hidden', !modeDropdownOpen);
      if (modeDropdownOpen) renderModeDropdown();
    }

    function renderModeDropdown() {
      var dd = document.getElementById('mode-dropdown');
      var currentMode = (currentState && currentState.workflow) ? currentState.workflow.selectedMode : 'auto';
      var html = '';
      html += '<div class="mode-group-header">Recommended</div>';
      WORKFLOW_OPTIONS.filter(function(o) { return o.group === 'auto'; }).forEach(function(o) {
        html += buildModeOption(o, currentMode);
      });
      html += '<div class="mode-group-header">System Architectures</div>';
      WORKFLOW_OPTIONS.filter(function(o) { return o.group === 'arch'; }).forEach(function(o) {
        html += buildModeOption(o, currentMode);
      });
      html += '<div class="mode-group-header">Task Pipelines</div>';
      WORKFLOW_OPTIONS.filter(function(o) { return o.group === 'pipeline'; }).forEach(function(o) {
        html += buildModeOption(o, currentMode);
      });
      dd.innerHTML = html;
    }

    function buildModeOption(o, currentMode) {
      var active = o.id === currentMode ? ' active' : '';
      var badge = o.agents ? '<span class="mode-badge">' + o.agents + ' agents</span>' : '';
      return '<button class="mode-option' + active + '" onclick="selectWorkflowMode(\\'' + o.id + '\\')">' +
        '<span class="mode-emoji">' + o.emoji + '</span>' +
        '<div class="mode-info"><div class="mode-title">' + o.label + '</div><div class="mode-desc">' + o.desc + '</div></div>' +
        badge + '</button>';
    }

    function selectWorkflowMode(mode) {
      send({ type: 'SET_WORKFLOW_MODE', payload: { mode: mode } });
      modeDropdownOpen = false;
      document.getElementById('mode-dropdown').classList.add('hidden');
      // Update trigger label immediately
      var opt = WORKFLOW_OPTIONS.find(function(o) { return o.id === mode; });
      if (opt) {
        document.getElementById('mode-value').textContent = opt.emoji + ' ' + opt.label;
      }
    }

    function renderModeSelector(state) {
      if (!state.workflow) return;
      var mode = state.workflow.selectedMode || 'auto';
      var opt = WORKFLOW_OPTIONS.find(function(o) { return o.id === mode; });
      if (opt) {
        document.getElementById('mode-value').textContent = opt.emoji + ' ' + opt.label;
      }
    }

    // ── Error banner ────────────────────────────
    function showError(err) {
      var banner = document.getElementById('error-banner');
      banner.textContent = (err.title || 'Error') + ': ' + (err.message || 'Unknown error');
      banner.classList.remove('hidden');
      setTimeout(function() { banner.classList.add('hidden'); }, 8000);
    }

    // ── Simple markdown renderer ────────────────
    function escapeHtml(str) {
      var div = document.createElement('div');
      div.textContent = str;
      return div.innerHTML;
    }

    function renderMd(text) {
      var h = escapeHtml(text);
      // Code blocks
      h = h.replace(/\`\`\`(\\w*)\\n([\\s\\S]*?)\`\`\`/g, '<pre><code>$2</code></pre>');
      h = h.replace(/\`\`\`([\\s\\S]*?)\`\`\`/g, '<pre><code>$1</code></pre>');
      // Inline code
      h = h.replace(/\`([^\`]+)\`/g, '<code>$1</code>');
      // Bold
      h = h.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
      // Italic
      h = h.replace(/\\*(.+?)\\*/g, '<em>$1</em>');
      return h;
    }

    // Initialize
    send({ type: 'INIT' });
  </script>
</body>
</html>`;
  }

  dispose(): void {
    this._disposables.forEach((d) => d.dispose());
  }
}
