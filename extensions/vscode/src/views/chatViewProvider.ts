/**
 * Chat Webview Provider — Full sidebar chat panel.
 *
 * Features:
 * - Markdown-rendered AI responses with syntax highlighting
 * - Action plan display with approve/reject buttons
 * - Skill invocation via /command syntax
 * - Session persistence
 * - File reference links (clickable to open in editor)
 * - Topology selector
 */
import * as vscode from 'vscode';
import { GitPilotApiClient } from '../api/client';
import { getWorkspaceContext } from '../utils/context';
import { getConfig } from '../utils/config';

interface ChatMessage {
    role: 'user' | 'assistant' | 'system';
    content: string;
    plan?: Array<{ step: number; action: string; file: string; description: string }>;
    timestamp: number;
}

export class ChatViewProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = 'gitpilot.chatView';
    private _view?: vscode.WebviewView;
    private _messages: ChatMessage[] = [];
    private _sessionId?: string;

    constructor(
        private readonly _extensionUri: vscode.Uri,
        private readonly _client: GitPilotApiClient,
    ) {}

    resolveWebviewView(
        webviewView: vscode.WebviewView,
        _context: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken,
    ): void {
        this._view = webviewView;
        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [vscode.Uri.joinPath(this._extensionUri, 'media')],
        };
        webviewView.webview.html = this._getHtml(webviewView.webview);

        webviewView.webview.onDidReceiveMessage(async (msg) => {
            switch (msg.type) {
                case 'sendMessage':
                    await this._handleUserMessage(msg.text);
                    break;
                case 'approvePlan':
                    await this._handleApprovePlan(msg.plan);
                    break;
                case 'openFile':
                    this._openFile(msg.path);
                    break;
                case 'setTopology':
                    await this._setTopology(msg.topologyId);
                    break;
                case 'newSession':
                    this._messages = [];
                    this._sessionId = undefined;
                    this._postMessage({ type: 'clearChat' });
                    break;
            }
        });

        // Send connection state
        this._postMessage({
            type: 'connectionState',
            connected: this._client.isConnected,
            serverUrl: this._client.serverUrl,
        });
    }

    public sendMessageFromCommand(text: string): void {
        if (this._view) {
            this._handleUserMessage(text);
        }
    }

    public sendCodeContext(code: string, action: 'explain' | 'review' | 'fix' | 'test'): void {
        const prompts: Record<string, string> = {
            explain: `Explain this code:\n\`\`\`\n${code}\n\`\`\``,
            review: `Review this code for issues, bugs, and improvements:\n\`\`\`\n${code}\n\`\`\``,
            fix: `Fix any bugs in this code:\n\`\`\`\n${code}\n\`\`\``,
            test: `Write unit tests for this code:\n\`\`\`\n${code}\n\`\`\``,
        };
        this._handleUserMessage(prompts[action]);
    }

    public updateConnectionState(connected: boolean): void {
        this._postMessage({
            type: 'connectionState',
            connected,
            serverUrl: this._client.serverUrl,
        });
    }

    private async _handleUserMessage(text: string): Promise<void> {
        const ctx = getWorkspaceContext();
        const userMsg: ChatMessage = { role: 'user', content: text, timestamp: Date.now() };
        this._messages.push(userMsg);
        this._postMessage({ type: 'addMessage', message: userMsg });
        this._postMessage({ type: 'setLoading', loading: true });

        try {
            // Check for skill invocation
            if (text.startsWith('/')) {
                const skillName = text.split(' ')[0].substring(1);
                const result = await this._client.invokeSkill(skillName, {
                    repo_owner: ctx.repoOwner,
                    repo_name: ctx.repoName,
                });
                const assistantMsg: ChatMessage = {
                    role: 'assistant',
                    content: result.result,
                    timestamp: Date.now(),
                };
                this._messages.push(assistantMsg);
                this._postMessage({ type: 'addMessage', message: assistantMsg });
            } else {
                // Regular chat — generate plan
                const response = await this._client.chatPlan(
                    ctx.repoOwner,
                    ctx.repoName,
                    text,
                    this._sessionId,
                );

                const assistantMsg: ChatMessage = {
                    role: 'assistant',
                    content: response.answer,
                    plan: response.plan?.length ? response.plan : undefined,
                    timestamp: Date.now(),
                };
                this._messages.push(assistantMsg);
                this._postMessage({ type: 'addMessage', message: assistantMsg });
            }
        } catch (err: any) {
            const errorMsg: ChatMessage = {
                role: 'system',
                content: `Error: ${err.message}`,
                timestamp: Date.now(),
            };
            this._messages.push(errorMsg);
            this._postMessage({ type: 'addMessage', message: errorMsg });
        } finally {
            this._postMessage({ type: 'setLoading', loading: false });
        }
    }

    private async _handleApprovePlan(plan: ChatMessage['plan']): Promise<void> {
        if (!plan) { return; }
        const ctx = getWorkspaceContext();
        this._postMessage({ type: 'setLoading', loading: true });

        try {
            const result = await this._client.chatExecute(
                ctx.repoOwner,
                ctx.repoName,
                plan,
                this._sessionId,
            );
            const msg: ChatMessage = {
                role: 'assistant',
                content: `Execution complete.\n\n**Files changed:** ${result.files_changed?.join(', ') || 'none'}\n${result.pr_url ? `\n**PR:** ${result.pr_url}` : ''}`,
                timestamp: Date.now(),
            };
            this._messages.push(msg);
            this._postMessage({ type: 'addMessage', message: msg });
        } catch (err: any) {
            this._postMessage({
                type: 'addMessage',
                message: { role: 'system', content: `Execution error: ${err.message}`, timestamp: Date.now() },
            });
        } finally {
            this._postMessage({ type: 'setLoading', loading: false });
        }
    }

    private _openFile(filePath: string): void {
        const folders = vscode.workspace.workspaceFolders;
        if (!folders) { return; }
        const uri = vscode.Uri.joinPath(folders[0].uri, filePath);
        vscode.window.showTextDocument(uri, { preview: true });
    }

    private async _setTopology(id: string): Promise<void> {
        try {
            await this._client.setTopology(id);
            vscode.window.showInformationMessage(`Agent topology set to: ${id}`);
        } catch (err: any) {
            vscode.window.showErrorMessage(`Failed to set topology: ${err.message}`);
        }
    }

    private _postMessage(msg: any): void {
        this._view?.webview.postMessage(msg);
    }

    private _getHtml(webview: vscode.Webview): string {
        const config = getConfig();
        const nonce = getNonce();
        return /*html*/ `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        :root {
            --font-size: ${config.chatFontSize}px;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: var(--vscode-font-family);
            font-size: var(--font-size);
            color: var(--vscode-foreground);
            background: var(--vscode-sideBar-background);
            display: flex;
            flex-direction: column;
            height: 100vh;
            overflow: hidden;
        }
        #header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            border-bottom: 1px solid var(--vscode-panel-border);
            flex-shrink: 0;
        }
        #header .status {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 11px;
            opacity: 0.7;
        }
        #header .status .dot {
            width: 8px; height: 8px; border-radius: 50%;
            background: var(--vscode-testing-iconFailed);
        }
        #header .status .dot.connected {
            background: var(--vscode-testing-iconPassed);
        }
        #header .actions { display: flex; gap: 4px; }
        #header .actions button {
            background: none; border: none; color: var(--vscode-foreground);
            cursor: pointer; padding: 2px 6px; border-radius: 3px; font-size: 12px;
        }
        #header .actions button:hover {
            background: var(--vscode-toolbar-hoverBackground);
        }
        #messages {
            flex: 1;
            overflow-y: auto;
            padding: 12px;
        }
        .message {
            margin-bottom: 16px;
            animation: fadeIn 0.2s ease-in;
        }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
        .message .role {
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            margin-bottom: 4px;
            opacity: 0.6;
        }
        .message.user .role { color: var(--vscode-textLink-foreground); }
        .message.assistant .role { color: var(--vscode-terminal-ansiGreen); }
        .message.system .role { color: var(--vscode-testing-iconFailed); }
        .message .content {
            line-height: 1.5;
            white-space: pre-wrap;
            word-break: break-word;
        }
        .message .content code {
            background: var(--vscode-textCodeBlock-background);
            padding: 1px 4px;
            border-radius: 3px;
            font-family: var(--vscode-editor-font-family);
            font-size: 0.9em;
        }
        .message .content pre {
            background: var(--vscode-textCodeBlock-background);
            padding: 8px 12px;
            border-radius: 4px;
            overflow-x: auto;
            margin: 8px 0;
        }
        .message .content pre code {
            background: none;
            padding: 0;
        }
        .plan-block {
            border: 1px solid var(--vscode-panel-border);
            border-radius: 6px;
            margin-top: 8px;
            overflow: hidden;
        }
        .plan-block .plan-header {
            background: var(--vscode-editor-background);
            padding: 8px 12px;
            font-weight: 600;
            font-size: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .plan-step {
            display: flex;
            gap: 8px;
            padding: 6px 12px;
            border-top: 1px solid var(--vscode-panel-border);
            font-size: 12px;
        }
        .plan-step .action-badge {
            padding: 1px 6px;
            border-radius: 3px;
            font-size: 10px;
            font-weight: 600;
            text-transform: uppercase;
            flex-shrink: 0;
        }
        .plan-step .action-badge.CREATE { background: #2ea04370; color: #3fb950; }
        .plan-step .action-badge.MODIFY { background: #d2992270; color: #e3b341; }
        .plan-step .action-badge.DELETE { background: #f8514970; color: #f85149; }
        .plan-step .action-badge.READ { background: #388bfd70; color: #58a6ff; }
        .plan-step .file-link {
            color: var(--vscode-textLink-foreground);
            cursor: pointer;
            text-decoration: underline;
        }
        .plan-actions {
            display: flex;
            gap: 8px;
            padding: 8px 12px;
            border-top: 1px solid var(--vscode-panel-border);
        }
        .plan-actions button {
            padding: 4px 16px;
            border-radius: 4px;
            border: none;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
        }
        .plan-actions .approve {
            background: var(--vscode-button-background);
            color: var(--vscode-button-foreground);
        }
        .plan-actions .approve:hover {
            background: var(--vscode-button-hoverBackground);
        }
        .plan-actions .reject {
            background: var(--vscode-button-secondaryBackground);
            color: var(--vscode-button-secondaryForeground);
        }
        #input-area {
            border-top: 1px solid var(--vscode-panel-border);
            padding: 8px 12px;
            flex-shrink: 0;
        }
        #input-area textarea {
            width: 100%;
            background: var(--vscode-input-background);
            color: var(--vscode-input-foreground);
            border: 1px solid var(--vscode-input-border);
            border-radius: 4px;
            padding: 8px;
            font-family: var(--vscode-font-family);
            font-size: var(--font-size);
            resize: none;
            outline: none;
            min-height: 60px;
            max-height: 200px;
        }
        #input-area textarea:focus {
            border-color: var(--vscode-focusBorder);
        }
        #input-area .hint {
            font-size: 11px;
            opacity: 0.5;
            margin-top: 4px;
        }
        .loading {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 0;
            font-size: 12px;
            opacity: 0.7;
        }
        .loading .spinner {
            width: 14px; height: 14px;
            border: 2px solid var(--vscode-foreground);
            border-top-color: transparent;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }
        .hidden { display: none !important; }
    </style>
</head>
<body>
    <div id="header">
        <div class="status">
            <span class="dot" id="statusDot"></span>
            <span id="statusText">Disconnected</span>
        </div>
        <div class="actions">
            <button onclick="newSession()" title="New Session">+</button>
        </div>
    </div>
    <div id="messages">
        <div class="message system">
            <div class="content">Welcome to GitPilot. Type a message or use /skill to invoke a skill.</div>
        </div>
    </div>
    <div id="loading" class="loading hidden">
        <div class="spinner"></div>
        <span>Thinking...</span>
    </div>
    <div id="input-area">
        <textarea id="input" placeholder="Ask GitPilot anything... (Ctrl+Enter to send)" rows="2"></textarea>
        <div class="hint">Ctrl+Enter to send · /skill to invoke · Shift+Enter for newline</div>
    </div>

    <script nonce="${nonce}">
        const vscode = acquireVsCodeApi();
        const messagesEl = document.getElementById('messages');
        const inputEl = document.getElementById('input');
        const loadingEl = document.getElementById('loading');
        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        let currentPlan = null;

        inputEl.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault();
                sendMessage();
            }
        });

        function sendMessage() {
            const text = inputEl.value.trim();
            if (!text) return;
            inputEl.value = '';
            inputEl.style.height = 'auto';
            vscode.postMessage({ type: 'sendMessage', text });
        }

        function newSession() {
            vscode.postMessage({ type: 'newSession' });
            messagesEl.innerHTML = '<div class="message system"><div class="content">New session started.</div></div>';
        }

        function approvePlan() {
            if (currentPlan) {
                vscode.postMessage({ type: 'approvePlan', plan: currentPlan });
                currentPlan = null;
            }
        }

        function openFile(path) {
            vscode.postMessage({ type: 'openFile', path });
        }

        function escapeHtml(str) {
            return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        }

        function renderMarkdown(text) {
            // Simple markdown: bold, italic, code blocks, inline code, links
            let html = escapeHtml(text);
            // Code blocks
            html = html.replace(/\`\`\`(\\w*)\n([\s\S]*?)\`\`\`/g, '<pre><code>$2</code></pre>');
            // Inline code
            html = html.replace(/\`([^\`]+)\`/g, '<code>$1</code>');
            // Bold
            html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
            // Italic
            html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
            return html;
        }

        function renderPlan(plan) {
            if (!plan || !plan.length) return '';
            currentPlan = plan;
            let html = '<div class="plan-block"><div class="plan-header"><span>Action Plan</span><span>' + plan.length + ' steps</span></div>';
            for (const step of plan) {
                html += '<div class="plan-step">' +
                    '<span class="action-badge ' + escapeHtml(step.action) + '">' + escapeHtml(step.action) + '</span>' +
                    '<span class="file-link" onclick="openFile(\'' + escapeHtml(step.file) + '\')">' + escapeHtml(step.file) + '</span>' +
                    '<span>' + escapeHtml(step.description) + '</span>' +
                    '</div>';
            }
            html += '<div class="plan-actions">' +
                '<button class="approve" onclick="approvePlan()">Approve & Execute</button>' +
                '<button class="reject" onclick="currentPlan=null">Dismiss</button>' +
                '</div></div>';
            return html;
        }

        window.addEventListener('message', (event) => {
            const msg = event.data;
            switch (msg.type) {
                case 'addMessage': {
                    const m = msg.message;
                    const div = document.createElement('div');
                    div.className = 'message ' + m.role;
                    div.innerHTML = '<div class="role">' + m.role + '</div>' +
                        '<div class="content">' + renderMarkdown(m.content) + '</div>' +
                        (m.plan ? renderPlan(m.plan) : '');
                    messagesEl.appendChild(div);
                    messagesEl.scrollTop = messagesEl.scrollHeight;
                    break;
                }
                case 'setLoading':
                    loadingEl.classList.toggle('hidden', !msg.loading);
                    inputEl.disabled = msg.loading;
                    break;
                case 'connectionState':
                    statusDot.className = 'dot' + (msg.connected ? ' connected' : '');
                    statusText.textContent = msg.connected ? 'Connected' : 'Disconnected';
                    break;
                case 'clearChat':
                    messagesEl.innerHTML = '<div class="message system"><div class="content">New session started.</div></div>';
                    break;
            }
        });

        // Auto-resize textarea
        inputEl.addEventListener('input', () => {
            inputEl.style.height = 'auto';
            inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
        });
    </script>
</body>
</html>`;
    }
}

function getNonce(): string {
    let text = '';
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    for (let i = 0; i < 32; i++) {
        text += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return text;
}
