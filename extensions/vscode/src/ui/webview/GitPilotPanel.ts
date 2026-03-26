/**
 * GitPilot Redesign — State-Driven Webview Panel
 * Replaces string-heavy HTML generation with state-driven rendering.
 */

import * as vscode from "vscode";
import { StateStore } from "../../core/stateStore";
import { WebviewBridge } from "./webviewBridge";
import {
  GitPilotState,
  WorkspaceMode,
  ExtensionToWebviewMessage,
  WebviewToExtensionMessage,
} from "../../core/types";
import { PROVIDER_LABELS, WORKSPACE_MODE_LABELS, QUICK_ACTIONS } from "../../core/constants";

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
      --badge-bg: var(--vscode-badge-background);
      --badge-fg: var(--vscode-badge-foreground);
      --success: #4caf50;
      --warning: #ff9800;
      --error: #f44336;
      --info: #2196f3;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: var(--vscode-font-family); font-size: var(--vscode-font-size); color: var(--fg); background: var(--bg); padding: 8px; }

    .card { background: var(--input-bg); border: 1px solid var(--border); border-radius: 6px; padding: 12px; margin-bottom: 8px; }
    .card-title { font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; opacity: 0.8; margin-bottom: 8px; }
    .card-row { display: flex; justify-content: space-between; align-items: center; padding: 2px 0; font-size: 12px; }
    .card-row .label { opacity: 0.7; }
    .card-row .value { font-weight: 500; }

    .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; }
    .status-ok { background: var(--success); }
    .status-warning { background: var(--warning); }
    .status-error { background: var(--error); }
    .status-unknown { background: #666; }

    .checklist-item { display: flex; align-items: center; padding: 3px 0; font-size: 12px; }
    .checklist-icon { margin-right: 6px; font-size: 14px; }
    .check-ok { color: var(--success); }
    .check-warn { color: var(--warning); }
    .check-fail { color: var(--error); }

    .btn { display: inline-flex; align-items: center; justify-content: center; padding: 6px 12px; border: none; border-radius: 4px; background: var(--btn-bg); color: var(--btn-fg); cursor: pointer; font-size: 12px; width: 100%; margin-top: 4px; }
    .btn:hover { background: var(--btn-hover); }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-secondary { background: transparent; border: 1px solid var(--border); color: var(--fg); }
    .btn-row { display: flex; gap: 4px; margin-top: 4px; }
    .btn-row .btn { width: auto; flex: 1; }

    .quick-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 4px; }
    .quick-action-btn { padding: 8px; font-size: 11px; text-align: left; background: var(--input-bg); border: 1px solid var(--border); border-radius: 4px; color: var(--fg); cursor: pointer; }
    .quick-action-btn:hover { background: var(--btn-bg); color: var(--btn-fg); }
    .quick-action-btn:disabled { opacity: 0.4; cursor: not-allowed; }

    .chat-container { display: flex; flex-direction: column; }
    .chat-messages { max-height: 300px; overflow-y: auto; margin-bottom: 8px; }
    .chat-msg { padding: 6px 8px; margin: 4px 0; border-radius: 6px; font-size: 12px; line-height: 1.4; }
    .chat-msg.user { background: var(--btn-bg); color: var(--btn-fg); margin-left: 20%; text-align: right; }
    .chat-msg.assistant { background: var(--input-bg); border: 1px solid var(--border); margin-right: 20%; }
    .chat-input-row { display: flex; gap: 4px; }
    .chat-input { flex: 1; padding: 6px 8px; background: var(--input-bg); color: var(--input-fg); border: 1px solid var(--input-border); border-radius: 4px; font-size: 12px; resize: none; font-family: inherit; }
    .chat-send-btn { padding: 6px 12px; }

    .mode-card { padding: 10px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 4px; cursor: pointer; }
    .mode-card:hover { border-color: var(--btn-bg); }
    .mode-card.active { border-color: var(--btn-bg); background: rgba(var(--btn-bg), 0.1); }
    .mode-card.disabled { opacity: 0.4; cursor: not-allowed; }
    .mode-card-title { font-weight: 600; font-size: 12px; }
    .mode-card-desc { font-size: 11px; opacity: 0.7; margin-top: 2px; }

    .provider-card { display: flex; align-items: center; gap: 8px; }
    .provider-name { font-weight: 600; }
    .provider-model { font-size: 11px; opacity: 0.7; }
    .provider-source { font-size: 10px; opacity: 0.5; }

    .empty-state { text-align: center; padding: 40px 20px; }
    .empty-state h3 { margin-bottom: 8px; }
    .empty-state p { opacity: 0.7; margin-bottom: 16px; font-size: 12px; }

    .error-banner { background: rgba(244, 67, 54, 0.1); border: 1px solid var(--error); border-radius: 6px; padding: 8px 12px; margin-bottom: 8px; font-size: 12px; }

    #app { max-width: 100%; }
    .hidden { display: none !important; }
  </style>
</head>
<body>
  <div id="app">
    <!-- Empty State: No Folder -->
    <div id="empty-state" class="empty-state hidden">
      <h3>GitPilot</h3>
      <p>Open a folder to start using GitPilot</p>
      <button class="btn" onclick="send({type:'OPEN_SETTINGS'})">Open Folder</button>
      <button class="btn btn-secondary" style="margin-top:4px" onclick="send({type:'OPEN_PROVIDER_SETUP'})">Open Provider Setup</button>
    </div>

    <!-- Main UI -->
    <div id="main-ui" class="hidden">
      <!-- Error Banner -->
      <div id="error-banner" class="error-banner hidden"></div>

      <!-- Workspace Context Card -->
      <div class="card" id="workspace-card">
        <div class="card-title">Workspace</div>
        <div class="card-row"><span class="label">Mode</span><span class="value" id="ws-mode">—</span></div>
        <div class="card-row"><span class="label">Folder</span><span class="value" id="ws-folder">—</span></div>
        <div class="card-row"><span class="label">Repo</span><span class="value" id="ws-repo">—</span></div>
        <div class="card-row"><span class="label">Branch</span><span class="value" id="ws-branch">—</span></div>
        <div class="card-row"><span class="label">Provider</span><span class="value" id="ws-provider">—</span></div>
        <div class="card-row"><span class="label">Server</span><span class="value" id="ws-server">—</span></div>
        <div class="card-row"><span class="label">Session</span><span class="value" id="ws-session">—</span></div>
        <div class="btn-row">
          <button class="btn btn-secondary" onclick="send({type:'REFRESH_STATUS'})">Refresh</button>
          <button class="btn btn-secondary" onclick="toggleModeSelector()">Change Mode</button>
        </div>
      </div>

      <!-- Setup Status Card -->
      <div class="card" id="setup-card">
        <div class="card-title">Setup Status</div>
        <div id="setup-checklist"></div>
        <div id="setup-cta"></div>
      </div>

      <!-- Provider Card -->
      <div class="card" id="provider-card">
        <div class="card-title">AI Provider</div>
        <div class="provider-card">
          <span id="provider-dot" class="status-dot status-unknown"></span>
          <div>
            <div class="provider-name" id="provider-name">—</div>
            <div class="provider-model" id="provider-model">—</div>
            <div class="provider-source" id="provider-source">—</div>
          </div>
        </div>
        <button class="btn btn-secondary" style="margin-top:8px" onclick="send({type:'OPEN_PROVIDER_SETUP'})">Change Provider</button>
      </div>

      <!-- Mode Selector (hidden by default) -->
      <div class="card hidden" id="mode-selector">
        <div class="card-title">Choose how to work</div>
        <div class="mode-card" id="mode-folder" onclick="selectMode('folder')">
          <div class="mode-card-title">Folder Mode</div>
          <div class="mode-card-desc">Use local files only</div>
        </div>
        <div class="mode-card" id="mode-local_git" onclick="selectMode('local_git')">
          <div class="mode-card-title">Local Git Mode</div>
          <div class="mode-card-desc">Use repo + branch context</div>
        </div>
        <div class="mode-card" id="mode-github" onclick="selectMode('github')">
          <div class="mode-card-title">GitHub Mode</div>
          <div class="mode-card-desc">Use PRs, issues, remote workflows</div>
        </div>
      </div>

      <!-- Quick Actions Card -->
      <div class="card" id="quick-actions-card">
        <div class="card-title">Quick Actions</div>
        <div class="quick-actions" id="quick-actions"></div>
      </div>

      <!-- Chat Panel -->
      <div class="card" id="chat-card">
        <div class="card-title">Chat</div>
        <div class="chat-container">
          <div class="chat-messages" id="chat-messages">
            <div class="chat-msg assistant">Ask about this repository...</div>
          </div>
          <div class="chat-input-row">
            <textarea class="chat-input" id="chat-input" rows="2" placeholder="Type a message..." onkeydown="handleChatKey(event)"></textarea>
            <button class="btn chat-send-btn" id="chat-send-btn" onclick="sendChat()">Send</button>
          </div>
        </div>
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
    let modeSelectorOpen = false;

    function send(msg) { vscode.postMessage(msg); }

    // State sync handler
    window.addEventListener('message', event => {
      const msg = event.data;
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

    function render(state) {
      if (!state) return;

      const emptyState = document.getElementById('empty-state');
      const mainUi = document.getElementById('main-ui');

      if (!state.workspace.folderOpen) {
        emptyState.classList.remove('hidden');
        mainUi.classList.add('hidden');
        return;
      }

      emptyState.classList.add('hidden');
      mainUi.classList.remove('hidden');

      renderWorkspaceCard(state);
      renderSetupCard(state);
      renderProviderCard(state);
      renderQuickActions(state);
      renderChat(state);
    }

    function renderWorkspaceCard(state) {
      const modeLabels = { folder: 'Folder Mode', local_git: 'Local Git Mode', github: 'GitHub Mode' };
      document.getElementById('ws-mode').textContent = modeLabels[state.workspace.mode] || '—';
      document.getElementById('ws-folder').textContent = state.workspace.folderName || '—';
      document.getElementById('ws-repo').textContent = state.workspace.git.repoName || '—';
      document.getElementById('ws-branch').textContent = state.workspace.git.branch || '—';
      document.getElementById('ws-provider').textContent =
        state.provider.providerName ? (state.provider.providerName.charAt(0).toUpperCase() + state.provider.providerName.slice(1)) : '—';
      document.getElementById('ws-server').innerHTML =
        state.server.connected
          ? '<span class="status-dot status-ok"></span>Connected'
          : '<span class="status-dot status-error"></span>Disconnected';
      document.getElementById('ws-session').textContent =
        state.session.active ? (state.session.title || 'Active') : 'Not started';
    }

    function renderSetupCard(state) {
      const checklist = document.getElementById('setup-checklist');
      const items = [
        { ok: state.server.connected, label: 'Server connected' },
        { ok: state.provider.configured, label: 'Provider configured', warn: false },
        { ok: state.workspace.folderOpen, label: 'Folder open' },
        { ok: state.workspace.git.isGitRepo, label: 'Git repository detected', warn: true },
        { ok: state.session.active, label: 'Session active', warn: true },
      ];

      checklist.innerHTML = items.map(item => {
        const icon = item.ok ? '✓' : (item.warn ? '⚠' : '✗');
        const cls = item.ok ? 'check-ok' : (item.warn ? 'check-warn' : 'check-fail');
        return '<div class="checklist-item"><span class="checklist-icon ' + cls + '">' + icon + '</span>' + item.label + '</div>';
      }).join('');

      // CTA
      const ctaDiv = document.getElementById('setup-cta');
      const r = state.readiness;
      if (r.primaryCta) {
        const actionMap = {
          start_folder: "send({type:'START_SESSION',payload:{mode:'folder'}})",
          start_local_git: "send({type:'START_SESSION',payload:{mode:'local_git'}})",
          connect_github: "send({type:'OPEN_SETTINGS'})",
          open_provider_setup: "send({type:'OPEN_PROVIDER_SETUP'})",
          retry: "send({type:'REFRESH_STATUS'})",
        };
        ctaDiv.innerHTML = '<button class="btn" onclick="' + (actionMap[r.primaryCta.id] || '') + '">' + r.primaryCta.label + '</button>';
      } else {
        ctaDiv.innerHTML = '';
      }
    }

    function renderProviderCard(state) {
      const p = state.provider;
      const providerLabels = { openai: 'OpenAI', claude: 'Claude', watsonx: 'Watsonx', ollama: 'Ollama', ollabridge: 'OllaBridge' };
      document.getElementById('provider-name').textContent = p.providerName ? (providerLabels[p.providerName] || p.providerName) : 'Not configured';
      document.getElementById('provider-model').textContent = p.model || 'No model selected';
      document.getElementById('provider-source').textContent = p.source ? ('from ' + p.source) : '';

      const dot = document.getElementById('provider-dot');
      dot.className = 'status-dot';
      if (p.health === 'ok') dot.classList.add('status-ok');
      else if (p.health === 'warning') dot.classList.add('status-warning');
      else if (p.health === 'error') dot.classList.add('status-error');
      else dot.classList.add('status-unknown');
    }

    function renderQuickActions(state) {
      const actions = [
        { id: 'explain_project', label: 'Explain project' },
        { id: 'review_file', label: 'Review file' },
        { id: 'fix_selection', label: 'Fix selection' },
        { id: 'generate_tests', label: 'Generate tests' },
        { id: 'security_scan', label: 'Security scan' },
      ];
      const canRun = state.readiness.canRunProjectActions;
      document.getElementById('quick-actions').innerHTML = actions.map(a =>
        '<button class="quick-action-btn" ' + (canRun ? '' : 'disabled') +
        ' onclick="send({type:\\'RUN_QUICK_ACTION\\',payload:{action:\\'' + a.id + '\\'}})">' + a.label + '</button>'
      ).join('');
    }

    function renderChat(state) {
      const canChat = state.readiness.canChat;
      const chatCard = document.getElementById('chat-card');
      const chatBlocked = document.getElementById('chat-blocked');
      const chatInput = document.getElementById('chat-input');
      const chatSendBtn = document.getElementById('chat-send-btn');

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
      const container = document.getElementById('chat-messages');
      if (chatMessages.length === 0) {
        container.innerHTML = '<div class="chat-msg assistant">Ask about this repository...</div>';
        return;
      }
      container.innerHTML = chatMessages.map(m =>
        '<div class="chat-msg ' + m.role + '">' + escapeHtml(m.content) + '</div>'
      ).join('');
      container.scrollTop = container.scrollHeight;
    }

    function sendChat() {
      const input = document.getElementById('chat-input');
      const text = input.value.trim();
      if (!text) return;

      chatMessages.push({ id: Date.now().toString(), role: 'user', content: text, createdAt: new Date().toISOString() });
      renderChatMessages();
      input.value = '';
      send({ type: 'SEND_CHAT', payload: { text } });
    }

    function handleChatKey(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendChat();
      }
    }

    function toggleModeSelector() {
      modeSelectorOpen = !modeSelectorOpen;
      document.getElementById('mode-selector').classList.toggle('hidden', !modeSelectorOpen);
    }

    function selectMode(mode) {
      send({ type: 'CHANGE_MODE', payload: { mode } });
      modeSelectorOpen = false;
      document.getElementById('mode-selector').classList.add('hidden');
    }

    function showError(err) {
      const banner = document.getElementById('error-banner');
      banner.textContent = (err.title || 'Error') + ': ' + (err.message || 'Unknown error');
      banner.classList.remove('hidden');
      setTimeout(() => banner.classList.add('hidden'), 8000);
    }

    function escapeHtml(str) {
      const div = document.createElement('div');
      div.textContent = str;
      return div.innerHTML;
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
