import * as vscode from "vscode";
import { StateStore } from "../core/stateStore";
import type { ExtensionToWebviewMessage, GitPilotState, WebviewToExtensionMessage } from "../core/types";

export class WorkspaceCopilotPanel implements vscode.Disposable {
  private static current: WorkspaceCopilotPanel | undefined;
  private readonly panel: vscode.WebviewPanel;
  private readonly disposables: vscode.Disposable[] = [];

  static show(
    extensionUri: vscode.Uri,
    stateStore: StateStore,
    onMessage: (msg: WebviewToExtensionMessage) => void,
  ): WorkspaceCopilotPanel {
    if (WorkspaceCopilotPanel.current) {
      WorkspaceCopilotPanel.current.panel.reveal(vscode.ViewColumn.Beside);
      return WorkspaceCopilotPanel.current;
    }
    WorkspaceCopilotPanel.current = new WorkspaceCopilotPanel(extensionUri, stateStore, onMessage);
    return WorkspaceCopilotPanel.current;
  }

  private constructor(
    extensionUri: vscode.Uri,
    private readonly stateStore: StateStore,
    private readonly onMessage: (msg: WebviewToExtensionMessage) => void,
  ) {
    this.panel = vscode.window.createWebviewPanel(
      "gitpilot.workspaceCopilot",
      "GitPilot Workspace",
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [extensionUri] },
    );

    this.panel.webview.html = this.getHtml();
    this.disposables.push(
      this.panel.webview.onDidReceiveMessage((msg) => this.onMessage(msg as WebviewToExtensionMessage)),
      this.stateStore.onDidChangeState(() => this.syncState()),
      this.panel.onDidDispose(() => this.dispose()),
    );
    this.syncState();
  }

  postMessage(message: ExtensionToWebviewMessage): void {
    void this.panel.webview.postMessage(message);
  }

  private syncState(): void {
    void this.panel.webview.postMessage({ type: "STATE_SYNC", payload: this.stateStore.state } satisfies ExtensionToWebviewMessage);
  }

  private getHtml(): string {
    const nonce = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<style>
body{font-family:var(--vscode-font-family);background:var(--vscode-editor-background);color:var(--vscode-foreground);padding:10px}
.gp-root{display:flex;flex-direction:column;gap:10px}
.gp-section{background:var(--vscode-sideBar-background);border:1px solid var(--vscode-panel-border);border-radius:12px;padding:12px}
.gp-title-row{display:flex;justify-content:space-between;gap:12px}
.gp-title-row h1{font-size:16px;margin:0 0 4px 0}.gp-title-row p,.gp-meta,.gp-summary{color:var(--vscode-descriptionForeground);font-size:12px;margin:2px 0}
.gp-task-title{font-weight:700;font-size:14px;margin-bottom:4px}
.gp-plan,.gp-list{margin:0;padding-left:18px}
.gp-plan li,.gp-list li{margin:8px 0;display:flex;justify-content:space-between;gap:8px}.gp-list li span{overflow:hidden;text-overflow:ellipsis}
.gp-actions-inline{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}.gp-header-actions{display:flex;gap:8px}
button{background:var(--vscode-button-background);color:var(--vscode-button-foreground);border:none;border-radius:8px;padding:6px 10px;cursor:pointer}
button:hover{background:var(--vscode-button-hoverBackground)}
textarea{width:100%;min-height:72px;background:var(--vscode-input-background);color:var(--vscode-input-foreground);border:1px solid var(--vscode-input-border);border-radius:8px;padding:8px;resize:vertical}
</style>
</head>
<body>
<div id="app" class="gp-root"></div>
<script nonce="${nonce}">
const vscode = acquireVsCodeApi();
const app = document.getElementById('app');
function esc(value){return String(value ?? '').replace(/[&<>\"]/g, (ch)=>({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[ch]));}
function section(title, body){return '<section class="gp-section"><h2>'+title+'</h2>'+body+'</section>';}
function renderState(state){
  const providerLine = [state.provider.providerName || 'Provider unknown', state.server.connected ? 'Connected' : 'Disconnected', state.provider.model || 'Model unset'].join(' · ');
  const repoLine = [state.projectContextSummary.repoName || state.workspace.folderName || 'No repo', state.projectContextSummary.branch ? 'Branch: '+state.projectContextSummary.branch : '', 'Workflow: '+state.workflow.selectedMode].filter(Boolean).join(' · ');
  const activeTaskTitle = state.activeTask.title || 'No active task';
  const activeTaskStatus = state.activeTask.status || 'idle';
  const planSteps = (state.activeTask.plan?.steps || []).map((step)=>'<li><strong>'+esc(step.title)+'</strong><span>'+esc(step.description)+'</span></li>').join('') || '<li><strong>No plan yet</strong><span>Ask GitPilot to inspect, review, or change the workspace.</span></li>';
  const files = (state.activeTask.filesInScope || []).map((item)=>'<li><span>'+esc(item.path)+'</span><div><button data-open-file="'+esc(item.path)+'">Open</button></div></li>').join('') || '<li><span>No files in scope yet</span></li>';
  const changes = (state.activeTask.changedFiles || []).map((item)=>'<li><span>'+esc(item.status.toUpperCase()+' '+item.path)+'</span><div><button data-open-file="'+esc(item.path)+'">Open</button><button data-open-diff="'+esc(item.path)+'">Diff</button></div></li>').join('') || '<li><span>No proposed changes yet</span></li>';
  app.innerHTML = [
    '<section class="gp-section gp-header"><div class="gp-title-row"><div><h1>GitPilot Workspace</h1><p>'+esc(providerLine)+'</p><p>'+esc(repoLine)+'</p></div><div class="gp-header-actions"><button data-action="OPEN_SETUP_WIZARD">Setup</button><button data-action="REFRESH_PROJECT_CONTEXT">↻</button></div></div></section>',
    section('Active Task','<div class="gp-task-title">'+esc(activeTaskTitle)+'</div><div class="gp-meta">Status: '+esc(activeTaskStatus)+'</div><div class="gp-summary">'+esc(state.activeTask.summary || 'GitPilot is ready for the next workspace task.')+'</div>'),
    section('Plan','<ol class="gp-plan">'+planSteps+'</ol>'),
    section('Files in Scope','<ul class="gp-list">'+files+'</ul>'),
    section('Changes','<ul class="gp-list">'+changes+'</ul>'),
    section('Chat','<div class="gp-summary">'+esc(state.activeTask.summary || 'What would you like GitPilot to do?')+'</div><textarea id="gp-chat-input" placeholder="Describe the task..."></textarea><div class="gp-actions-inline"><button data-action="SEND_CHAT">Send</button></div>'),
    section('Actions','<div class="gp-actions-inline"><button data-action="APPLY_PROPOSED_CHANGES">Apply Patch</button><button data-action="REFRESH_PROJECT_CONTEXT">Refresh Context</button><button data-action="OPEN_SETUP_WIZARD">Setup Wizard</button></div>')
  ].join('');

  app.querySelectorAll('[data-action="SEND_CHAT"]').forEach((btn) => btn.addEventListener('click', () => {
    const text = document.getElementById('gp-chat-input').value || '';
    vscode.postMessage({ type: 'SEND_CHAT', payload: { text } });
  }));
  app.querySelectorAll('[data-action]').forEach((btn) => {
    const action = btn.getAttribute('data-action');
    if (!action || action === 'SEND_CHAT') return;
    btn.addEventListener('click', () => vscode.postMessage({ type: action }));
  });
  app.querySelectorAll('[data-open-file]').forEach((btn) => btn.addEventListener('click', () => vscode.postMessage({ type: 'OPEN_CHANGED_FILE', payload: { path: btn.getAttribute('data-open-file') } })));
  app.querySelectorAll('[data-open-diff]').forEach((btn) => btn.addEventListener('click', () => vscode.postMessage({ type: 'OPEN_CHANGED_DIFF', payload: { path: btn.getAttribute('data-open-diff') } })));
}
window.addEventListener('message', (event) => { const msg = event.data; if (msg.type === 'STATE_SYNC') renderState(msg.payload); });
vscode.postMessage({ type: 'INIT' });
</script>
</body></html>`;
  }

  dispose(): void {
    WorkspaceCopilotPanel.current = undefined;
    while (this.disposables.length) {
      const disposable = this.disposables.pop();
      try { disposable?.dispose(); } catch { /* no-op */ }
    }
  }
}
