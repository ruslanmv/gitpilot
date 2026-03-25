/**
 * Chat Webview Provider — GitPilot Enterprise AI Assistant sidebar.
 *
 * All AI/LLM orchestration is delegated to the GitPilot server.
 * The server handles LLM selection (OllaBridge, OpenAI, Claude, Ollama,
 * Watsonx), agentic workflows (CrewAI), and multi-agent planning.
 *
 * The VS Code extension provides:
 * - Modern chatbot UI with markdown rendering
 * - Streaming response display with animated cursor
 * - Action plan display with approve/execute buttons
 * - Skill invocation via /command syntax
 * - Session persistence
 * - File reference links (clickable to open in editor)
 * - Active provider/model indicator in header
 * - Connection status indicator
 * - Cancel button to stop generation
 */
import * as vscode from 'vscode';
import { GitPilotApiClient } from '../api/client';
import { getWorkspaceContext, ensureGitRepo } from '../utils/context';
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
    private _abortController?: AbortController;
    private _activeProvider = '';
    private _activeModel = '';
    private _ollaBridgeHealthy = false;

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
                case 'cancel':
                    this._abortController?.abort();
                    this._postMessage({ type: 'setGenerating', generating: false });
                    break;
                case 'openFile':
                    this._openFile(msg.path);
                    break;
                case 'newSession':
                    this._messages = [];
                    this._sessionId = undefined;
                    this._postMessage({ type: 'clearChat' });
                    break;
                case 'selectProvider':
                    vscode.commands.executeCommand('gitpilot.selectProvider');
                    break;
                case 'selectModel':
                    vscode.commands.executeCommand('gitpilot.selectModel');
                    break;
                case 'exportChat':
                    this._exportChat();
                    break;
                case 'copyCode':
                    if (msg.code) {
                        vscode.env.clipboard.writeText(msg.code);
                        vscode.window.showInformationMessage('Code copied to clipboard');
                    }
                    break;
            }
        });

        // Send initial state
        this._postMessage({
            type: 'connectionState',
            connected: this._client.isConnected,
            serverUrl: this._client.serverUrl,
        });

        // Fetch active provider/model on load
        if (this._client.isConnected) {
            this._fetchProviderState();
        }
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
        if (connected) {
            this._fetchProviderState();
            this.checkOllaBridgeHealth();
        }
    }

    /** Called by commands when provider/model changes on the server. */
    public refreshProviderState(): void {
        this._fetchProviderState();
    }

    /** Load a saved session's messages into the chat UI. */
    public async loadSessionIntoChat(sessionId: string): Promise<void> {
        try {
            const session = await this._client.getSession(sessionId);
            this._sessionId = sessionId;
            this._messages = [];
            this._postMessage({ type: 'clearChat' });
            for (const msg of session.messages || []) {
                const chatMsg: ChatMessage = {
                    role: msg.role as ChatMessage['role'],
                    content: msg.content,
                    timestamp: Date.now(),
                };
                this._messages.push(chatMsg);
                this._postMessage({ type: 'addMessage', message: chatMsg });
            }
        } catch (err: any) {
            vscode.window.showErrorMessage(`Failed to load session: ${err.message}`);
        }
    }

    /** Check OllaBridge gateway health and update provider bar. */
    public async checkOllaBridgeHealth(): Promise<void> {
        try {
            const settings = await this._client.getSettings();
            if (settings.provider !== 'ollabridge') { return; }
            const cfg = settings.ollabridge || {};
            const health = await this._client.getOllaBridgeHealth(cfg.base_url || undefined);
            this._ollaBridgeHealthy = health.status === 'ok';
            this._postMessage({ type: 'ollaBridgeHealth', healthy: this._ollaBridgeHealthy });
        } catch {
            this._ollaBridgeHealthy = false;
            this._postMessage({ type: 'ollaBridgeHealth', healthy: false });
        }
    }

    // ── Server communication (all AI delegated to server) ──────

    private async _handleUserMessage(text: string): Promise<void> {
        if (!this._client.isConnected) {
            this._postMessage({
                type: 'addMessage',
                message: {
                    role: 'system',
                    content: `Not connected to GitPilot server at ${this._client.serverUrl}.\nMake sure the server is running (\`gitpilot serve\`) and try reconnecting.`,
                    timestamp: Date.now(),
                },
            });
            return;
        }

        const ctx = getWorkspaceContext();

        // If no git repo, offer to create one (important for version control)
        if (ctx.workspaceRoot && !ctx.isGitRepo) {
            const created = await ensureGitRepo();
            // Re-read context if repo was created
            if (created) {
                const freshCtx = getWorkspaceContext();
                ctx.repoOwner = freshCtx.repoOwner;
                ctx.repoName = freshCtx.repoName;
                ctx.branch = freshCtx.branch;
                ctx.isGitRepo = freshCtx.isGitRepo;
            }
        }

        // Show user message immediately
        const userMsg: ChatMessage = { role: 'user', content: text, timestamp: Date.now() };
        this._messages.push(userMsg);
        this._postMessage({ type: 'addMessage', message: userMsg });
        this._postMessage({ type: 'setGenerating', generating: true });

        this._abortController = new AbortController();

        try {
            // Skill invocation via /command syntax
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
                // Regular chat — delegate to server's agentic AI pipeline
                this._postMessage({ type: 'startAssistantMessage' });

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

                // Stream the text for a smooth typing effect
                this._postMessage({ type: 'streamText', text: response.answer });
                this._postMessage({ type: 'endAssistantMessage' });

                // If the server returned an action plan, display it
                if (assistantMsg.plan && assistantMsg.plan.length > 0) {
                    this._postMessage({ type: 'showPlan', plan: assistantMsg.plan });
                }
            }
        } catch (err: any) {
            this._postMessage({ type: 'endAssistantMessage' });
            const errorMsg: ChatMessage = {
                role: 'system',
                content: `Error: ${err.message}`,
                timestamp: Date.now(),
            };
            this._messages.push(errorMsg);
            this._postMessage({ type: 'addMessage', message: errorMsg });
        } finally {
            this._postMessage({ type: 'setGenerating', generating: false });
            this._abortController = undefined;
        }
    }

    private async _handleApprovePlan(plan: ChatMessage['plan']): Promise<void> {
        if (!plan) { return; }
        const ctx = getWorkspaceContext();
        this._postMessage({ type: 'setGenerating', generating: true });
        this._postMessage({ type: 'startAssistantMessage' });

        try {
            const result = await this._client.chatExecute(
                ctx.repoOwner,
                ctx.repoName,
                plan,
                this._sessionId,
            );

            const content = `Execution complete.\n\n**Files changed:** ${result.files_changed?.join(', ') || 'none'}${result.commit_sha ? `\n**Commit:** \`${result.commit_sha}\`` : ''}${result.pr_url ? `\n**PR:** ${result.pr_url}` : ''}`;

            this._postMessage({ type: 'streamText', text: content });
            this._postMessage({ type: 'endAssistantMessage' });

            // Show diff stats if available
            if (result.diff_stats) {
                this._postMessage({ type: 'showDiffStats', stats: result.diff_stats });
            }

            // Show execution log if available
            if (result.execution_log && result.execution_log.length > 0) {
                this._postMessage({ type: 'showExecutionLog', log: result.execution_log });
            }

            const msg: ChatMessage = {
                role: 'assistant',
                content,
                timestamp: Date.now(),
            };
            this._messages.push(msg);
        } catch (err: any) {
            this._postMessage({ type: 'endAssistantMessage' });
            this._postMessage({
                type: 'addMessage',
                message: { role: 'system', content: `Execution error: ${err.message}`, timestamp: Date.now() },
            });
        } finally {
            this._postMessage({ type: 'setGenerating', generating: false });
        }
    }

    // ── Export chat ─────────────────────────────────────────────

    private _exportChat(): void {
        if (this._messages.length === 0) {
            vscode.window.showInformationMessage('No messages to export');
            return;
        }
        const text = this._messages.map(m => {
            const ts = new Date(m.timestamp).toLocaleTimeString();
            const role = m.role === 'user' ? 'You' : m.role === 'assistant' ? 'GitPilot' : 'System';
            return `[${ts}] ${role}:\n${m.content}`;
        }).join('\n\n---\n\n');
        vscode.env.clipboard.writeText(text);
        vscode.window.showInformationMessage('Chat exported to clipboard');
    }

    // ── Provider state sync ────────────────────────────────────

    private async _fetchProviderState(): Promise<void> {
        try {
            const settings = await this._client.getSettings();
            const provider = settings.provider || '';
            const providerCfg = settings[provider] || {};
            const model = providerCfg.model || providerCfg.model_id || '';
            this._activeProvider = provider;
            this._activeModel = model;
            this._postMessage({ type: 'providerState', provider, model });
        } catch {
            // Not critical — header just won't show provider info
        }
    }

    // ── Helpers ────────────────────────────────────────────────

    private _openFile(filePath: string): void {
        const folders = vscode.workspace.workspaceFolders;
        if (!folders) { return; }
        const uri = vscode.Uri.joinPath(folders[0].uri, filePath);
        vscode.window.showTextDocument(uri, { preview: true });
    }

    private _postMessage(msg: any): void {
        this._view?.webview.postMessage(msg);
    }

    // ── Webview HTML ───────────────────────────────────────────

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
        :root { --font-size: ${config.chatFontSize}px; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: var(--vscode-font-family);
            font-size: var(--font-size);
            color: var(--vscode-foreground);
            background: var(--vscode-sideBar-background);
            display: flex; flex-direction: column; height: 100vh; overflow: hidden;
        }

        /* ── Header ─────────────────────────────────────── */
        #header {
            display: flex; align-items: center; justify-content: space-between;
            padding: 8px 12px;
            border-bottom: 1px solid var(--vscode-panel-border);
            flex-shrink: 0;
        }
        #header .brand { font-weight: 700; font-size: 13px; display: flex; align-items: center; gap: 6px; }
        #header .status { display: flex; align-items: center; gap: 5px; font-size: 10px; opacity: 0.6; }
        .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--vscode-testing-iconFailed); }
        .dot.ok { background: var(--vscode-testing-iconPassed); }
        #header .actions { display: flex; gap: 2px; }
        #header .actions button {
            background: none; border: none; color: var(--vscode-foreground);
            cursor: pointer; padding: 3px 7px; border-radius: 4px; font-size: 13px;
        }
        #header .actions button:hover { background: var(--vscode-toolbar-hoverBackground); }

        /* ── Provider bar ───────────────────────────────── */
        #provider-bar {
            display: flex; align-items: center; gap: 6px;
            padding: 4px 12px;
            border-bottom: 1px solid var(--vscode-panel-border);
            font-size: 11px; opacity: 0.7; flex-shrink: 0;
            background: var(--vscode-editor-background);
        }
        #provider-bar .provider-btn {
            background: none; border: 1px solid var(--vscode-panel-border);
            color: var(--vscode-foreground); cursor: pointer;
            padding: 2px 8px; border-radius: 4px; font-size: 11px;
        }
        #provider-bar .provider-btn:hover {
            background: var(--vscode-toolbar-hoverBackground);
            border-color: var(--vscode-focusBorder);
        }

        /* ── Messages area ──────────────────────────────── */
        #messages { flex: 1; overflow-y: auto; padding: 12px; }
        .msg { margin-bottom: 14px; animation: fadeIn 0.15s ease-in; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(3px); } to { opacity: 1; } }

        .msg.user .bubble {
            background: var(--vscode-button-background);
            color: var(--vscode-button-foreground);
            padding: 8px 12px; border-radius: 12px 12px 2px 12px;
            display: inline-block; max-width: 92%;
            float: right; clear: both;
        }

        .msg.assistant .bubble {
            background: var(--vscode-editor-background);
            border: 1px solid var(--vscode-panel-border);
            padding: 10px 14px; border-radius: 2px 12px 12px 12px;
            line-height: 1.55; max-width: 100%;
        }
        .msg.assistant .bubble code {
            background: var(--vscode-textCodeBlock-background);
            padding: 1px 5px; border-radius: 3px;
            font-family: var(--vscode-editor-font-family); font-size: 0.9em;
        }
        .msg.assistant .bubble pre {
            background: var(--vscode-textCodeBlock-background);
            padding: 10px 12px; border-radius: 6px;
            overflow-x: auto; margin: 8px 0;
        }
        .msg.assistant .bubble pre code { background: none; padding: 0; }

        .msg.system .bubble {
            background: rgba(255,100,100,0.08);
            border: 1px solid rgba(255,100,100,0.15);
            color: var(--vscode-errorForeground, #f48771);
            padding: 8px 12px; border-radius: 6px; font-size: 12px;
        }

        /* ── Plan block ─────────────────────────────────── */
        .plan-block {
            border: 1px solid var(--vscode-panel-border);
            border-radius: 6px; margin: 10px 0; overflow: hidden;
        }
        .plan-header {
            background: var(--vscode-editor-background);
            padding: 8px 12px; font-weight: 600; font-size: 12px;
            display: flex; justify-content: space-between; align-items: center;
        }
        .plan-step {
            display: flex; gap: 8px; padding: 6px 12px;
            border-top: 1px solid var(--vscode-panel-border); font-size: 12px;
            align-items: center;
        }
        .action-badge {
            padding: 1px 6px; border-radius: 3px;
            font-size: 10px; font-weight: 600; text-transform: uppercase; flex-shrink: 0;
        }
        .action-badge.CREATE { background: #2ea04370; color: #3fb950; }
        .action-badge.MODIFY { background: #d2992270; color: #e3b341; }
        .action-badge.DELETE { background: #f8514970; color: #f85149; }
        .action-badge.READ { background: #388bfd70; color: #58a6ff; }
        .file-link {
            color: var(--vscode-textLink-foreground);
            cursor: pointer; text-decoration: underline;
        }
        .plan-actions {
            display: flex; gap: 8px; padding: 8px 12px;
            border-top: 1px solid var(--vscode-panel-border);
        }
        .plan-actions button {
            padding: 5px 18px; border-radius: 5px; border: none;
            cursor: pointer; font-size: 12px; font-weight: 600;
        }
        .plan-actions .approve {
            background: var(--vscode-button-background);
            color: var(--vscode-button-foreground);
        }
        .plan-actions .approve:hover { background: var(--vscode-button-hoverBackground); }
        .plan-actions .reject {
            background: var(--vscode-button-secondaryBackground);
            color: var(--vscode-button-secondaryForeground);
        }

        /* ── Streaming cursor ───────────────────────────── */
        .cursor {
            display: inline-block; width: 7px; height: 14px;
            background: var(--vscode-foreground);
            animation: blink 1s step-end infinite;
            vertical-align: text-bottom; margin-left: 2px;
        }
        @keyframes blink { 50% { opacity: 0; } }

        /* ── Input area ─────────────────────────────────── */
        #input-area {
            border-top: 1px solid var(--vscode-panel-border);
            padding: 10px 12px; flex-shrink: 0;
        }
        #input-wrap { display: flex; gap: 6px; align-items: flex-end; }
        #input {
            flex: 1;
            background: var(--vscode-input-background);
            color: var(--vscode-input-foreground);
            border: 1px solid var(--vscode-input-border);
            border-radius: 8px; padding: 8px 12px;
            font-family: var(--vscode-font-family);
            font-size: var(--font-size);
            resize: none; outline: none;
            min-height: 38px; max-height: 200px; line-height: 1.4;
        }
        #input:focus { border-color: var(--vscode-focusBorder); }
        #input::placeholder { opacity: 0.5; }
        .input-btn {
            flex-shrink: 0; width: 34px; height: 34px;
            border-radius: 8px; border: none;
            cursor: pointer; display: flex; align-items: center; justify-content: center;
            font-size: 15px; transition: all 0.15s;
        }
        #send-btn {
            background: var(--vscode-button-background);
            color: var(--vscode-button-foreground);
        }
        #send-btn:hover { background: var(--vscode-button-hoverBackground); }
        #send-btn:disabled { opacity: 0.35; cursor: not-allowed; }
        #stop-btn {
            background: var(--vscode-inputValidation-errorBackground, #5a1d1d);
            color: var(--vscode-errorForeground, #f48771);
            display: none;
        }
        #stop-btn:hover { opacity: 0.85; }
        .hint { font-size: 10px; opacity: 0.4; margin-top: 5px; }

        /* ── Timestamp ──────────────────────────────────── */
        .msg-time {
            font-size: 9px; opacity: 0.35; margin-top: 3px;
            text-align: right;
        }
        .msg.assistant .msg-time, .msg.system .msg-time { text-align: left; }

        /* ── Code copy button ──────────────────────────── */
        .code-wrap { position: relative; }
        .code-wrap .copy-btn {
            position: absolute; top: 4px; right: 6px;
            background: var(--vscode-button-secondaryBackground);
            color: var(--vscode-button-secondaryForeground);
            border: none; border-radius: 3px; padding: 2px 7px;
            font-size: 10px; cursor: pointer; opacity: 0;
            transition: opacity 0.15s;
        }
        .code-wrap:hover .copy-btn { opacity: 1; }
        .code-wrap .copy-btn:hover { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }

        /* ── Diff stats bar ─────────────────────────────── */
        .diff-stats {
            display: flex; gap: 14px; padding: 6px 12px;
            font-size: 11px; font-weight: 600;
            border: 1px solid var(--vscode-panel-border);
            border-radius: 6px; margin: 6px 0;
            background: var(--vscode-editor-background);
        }
        .diff-stats .add { color: #3fb950; }
        .diff-stats .del { color: #f85149; }
        .diff-stats .files { opacity: 0.7; }

        /* ── Execution log ─────────────────────────────── */
        .exec-log {
            border: 1px solid var(--vscode-panel-border);
            border-radius: 6px; margin: 8px 0; overflow: hidden;
        }
        .exec-log-header {
            background: var(--vscode-editor-background);
            padding: 6px 12px; font-weight: 600; font-size: 12px;
        }
        .exec-step {
            display: flex; gap: 8px; padding: 5px 12px;
            border-top: 1px solid var(--vscode-panel-border); font-size: 11px;
            align-items: center;
        }
        .exec-step .status-icon { flex-shrink: 0; font-size: 12px; }
        .exec-step .status-icon.ok { color: #3fb950; }
        .exec-step .status-icon.fail { color: #f85149; }
        .exec-step .status-icon.skip { color: #8b949e; }

        /* ── OllaBridge health dot ─────────────────────── */
        .ob-dot {
            display: inline-block; width: 6px; height: 6px; border-radius: 50%;
            background: var(--vscode-testing-iconFailed); margin-left: 4px;
            vertical-align: middle;
        }
        .ob-dot.ok { background: var(--vscode-testing-iconPassed); }

        /* ── Welcome ────────────────────────────────────── */
        .welcome { text-align: center; padding: 32px 16px; opacity: 0.6; }
        .welcome h3 { margin-bottom: 8px; font-size: 14px; }
        .welcome p { font-size: 12px; line-height: 1.6; }
        .welcome kbd {
            background: var(--vscode-textCodeBlock-background);
            padding: 2px 6px; border-radius: 3px; font-size: 11px;
            border: 1px solid var(--vscode-panel-border);
        }
    </style>
</head>
<body>
    <div id="header">
        <div class="brand">
            <span>GitPilot</span>
            <div class="status">
                <span class="dot" id="statusDot"></span>
                <span id="statusLabel">Connecting...</span>
            </div>
        </div>
        <div class="actions">
            <button onclick="exportChat()" title="Export chat to clipboard">&#128203;</button>
            <button onclick="newSession()" title="New conversation">+</button>
        </div>
    </div>

    <div id="provider-bar">
        <span>Server AI:</span>
        <button class="provider-btn" id="providerBtn" onclick="selectProvider()" title="Change LLM provider">...</button>
        <button class="provider-btn" id="modelBtn" onclick="selectModel()" title="Change model">...</button>
        <span class="ob-dot" id="obDot" style="display:none" title="OllaBridge gateway"></span>
    </div>

    <div id="messages">
        <div class="welcome" id="welcome">
            <h3>GitPilot Enterprise AI Assistant</h3>
            <p>
                Connected to your GitPilot server for<br>
                multi-agent AI code generation, review, and Git operations.<br>
                Powered by CrewAI agentic workflows.<br><br>
                Try: <kbd>Explain this project</kbd><br>
                <kbd>Create a REST API endpoint</kbd><br>
                <kbd>Fix the bug in the auth module</kbd><br>
                <kbd>/skill to invoke a skill</kbd>
            </p>
        </div>
    </div>

    <div id="input-area">
        <div id="input-wrap">
            <textarea id="input" placeholder="Ask GitPilot anything..." rows="1"></textarea>
            <button class="input-btn" id="send-btn" onclick="sendMessage()" title="Send (Enter)">&#9654;</button>
            <button class="input-btn" id="stop-btn" onclick="stopGeneration()" title="Stop">&#9632;</button>
        </div>
        <div class="hint">Enter to send &middot; Shift+Enter for newline &middot; /skill to invoke</div>
    </div>

    <script nonce="${nonce}">
        const vscode = acquireVsCodeApi();
        const messagesEl = document.getElementById('messages');
        const inputEl = document.getElementById('input');
        const sendBtn = document.getElementById('send-btn');
        const stopBtn = document.getElementById('stop-btn');
        const statusDot = document.getElementById('statusDot');
        const statusLabel = document.getElementById('statusLabel');
        const welcomeEl = document.getElementById('welcome');
        const providerBtn = document.getElementById('providerBtn');
        const modelBtn = document.getElementById('modelBtn');
        const obDot = document.getElementById('obDot');

        let currentBubble = null;
        let currentPlan = null;
        let isGenerating = false;

        // ── Provider labels ────────────────────────────────
        const PLABELS = {
            ollabridge: 'OllaBridge',
            openai: 'OpenAI',
            claude: 'Claude',
            ollama: 'Ollama',
            watsonx: 'Watsonx',
        };

        // ── Input handling ─────────────────────────────────
        inputEl.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (!isGenerating) sendMessage();
            }
        });
        inputEl.addEventListener('input', () => {
            inputEl.style.height = 'auto';
            inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
        });

        function sendMessage() {
            const text = inputEl.value.trim();
            if (!text) return;
            inputEl.value = '';
            inputEl.style.height = '38px';
            vscode.postMessage({ type: 'sendMessage', text });
            inputEl.focus();
        }
        function stopGeneration() { vscode.postMessage({ type: 'cancel' }); }
        function newSession() {
            vscode.postMessage({ type: 'newSession' });
            messagesEl.innerHTML = '';
            if (welcomeEl) { messagesEl.appendChild(welcomeEl); welcomeEl.style.display = ''; }
            currentBubble = null; currentPlan = null;
        }
        function openFile(path) { vscode.postMessage({ type: 'openFile', path }); }
        function selectProvider() { vscode.postMessage({ type: 'selectProvider' }); }
        function selectModel() { vscode.postMessage({ type: 'selectModel' }); }
        function exportChat() { vscode.postMessage({ type: 'exportChat' }); }
        function copyCode(btn) {
            const pre = btn.closest('.code-wrap').querySelector('code');
            if (pre) {
                vscode.postMessage({ type: 'copyCode', code: pre.textContent });
                btn.textContent = 'Copied!';
                setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
            }
        }
        function formatTime(ts) {
            const d = new Date(ts);
            const h = d.getHours().toString().padStart(2,'0');
            const m = d.getMinutes().toString().padStart(2,'0');
            return h + ':' + m;
        }
        function approvePlan() {
            if (currentPlan) {
                vscode.postMessage({ type: 'approvePlan', plan: currentPlan });
                currentPlan = null;
            }
        }

        function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
        function renderMd(text) {
            let h = esc(text);
            h = h.replace(/\`\`\`(\\w*)\n([\\s\\S]*?)\`\`\`/g, '<div class="code-wrap"><button class="copy-btn" onclick="copyCode(this)">Copy</button><pre><code>$2</code></pre></div>');
            h = h.replace(/\`\`\`([\\s\\S]*?)\`\`\`/g, '<div class="code-wrap"><button class="copy-btn" onclick="copyCode(this)">Copy</button><pre><code>$1</code></pre></div>');
            h = h.replace(/\`([^\`]+)\`/g, '<code>$1</code>');
            h = h.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
            h = h.replace(/\\*(.+?)\\*/g, '<em>$1</em>');
            h = h.replace(/(https?:\\/\\/[^\\s<]+)/g, '<a href="$1" style="color:var(--vscode-textLink-foreground)" title="$1">$1</a>');
            return h;
        }
        function scrollBottom() {
            requestAnimationFrame(() => { messagesEl.scrollTop = messagesEl.scrollHeight; });
        }

        function renderPlanBlock(plan) {
            currentPlan = plan;
            let html = '<div class="plan-block"><div class="plan-header"><span>Action Plan</span><span>' + plan.length + ' steps</span></div>';
            for (const step of plan) {
                html += '<div class="plan-step">' +
                    '<span class="action-badge ' + esc(step.action) + '">' + esc(step.action) + '</span>' +
                    '<span class="file-link" onclick="openFile(\\'' + esc(step.file) + '\\')">' + esc(step.file) + '</span>' +
                    '<span style="opacity:0.8">' + esc(step.description) + '</span></div>';
            }
            html += '<div class="plan-actions">' +
                '<button class="approve" onclick="approvePlan()">Approve & Execute</button>' +
                '<button class="reject" onclick="currentPlan=null;this.closest(\\'.plan-block\\').remove()">Dismiss</button>' +
                '</div></div>';
            return html;
        }

        // ── Messages from extension ────────────────────────
        window.addEventListener('message', (event) => {
            const msg = event.data;
            switch (msg.type) {
                case 'addMessage': {
                    if (welcomeEl) welcomeEl.style.display = 'none';
                    const m = msg.message;
                    const div = document.createElement('div');
                    div.className = 'msg ' + m.role;
                    const timeHtml = m.timestamp ? '<div class="msg-time">' + formatTime(m.timestamp) + '</div>' : '';
                    if (m.role === 'user') {
                        div.innerHTML = '<div class="bubble">' + esc(m.content) + '</div>' + timeHtml + '<div style="clear:both"></div>';
                    } else if (m.role === 'system') {
                        div.innerHTML = '<div class="bubble">' + esc(m.content) + '</div>' + timeHtml;
                    } else {
                        div.innerHTML = '<div class="bubble">' + renderMd(m.content) + '</div>' +
                            (m.plan ? renderPlanBlock(m.plan) : '') + timeHtml;
                    }
                    messagesEl.appendChild(div);
                    scrollBottom();
                    break;
                }
                case 'startAssistantMessage': {
                    if (welcomeEl) welcomeEl.style.display = 'none';
                    const div = document.createElement('div');
                    div.className = 'msg assistant';
                    div.id = 'streaming-msg';
                    div.innerHTML = '<div class="bubble" id="streaming-bubble"><span class="cursor"></span></div>';
                    messagesEl.appendChild(div);
                    currentBubble = document.getElementById('streaming-bubble');
                    scrollBottom();
                    break;
                }
                case 'streamText': {
                    if (!currentBubble) break;
                    const cursor = currentBubble.querySelector('.cursor');
                    if (cursor) cursor.remove();
                    if (!currentBubble._rawText) currentBubble._rawText = '';
                    currentBubble._rawText += msg.text;
                    currentBubble.innerHTML = renderMd(currentBubble._rawText) + '<span class="cursor"></span>';
                    scrollBottom();
                    break;
                }
                case 'endAssistantMessage': {
                    if (currentBubble) {
                        const cursor = currentBubble.querySelector('.cursor');
                        if (cursor) cursor.remove();
                    }
                    currentBubble = null;
                    break;
                }
                case 'showPlan': {
                    const planHtml = renderPlanBlock(msg.plan);
                    const div = document.createElement('div');
                    div.innerHTML = planHtml;
                    messagesEl.appendChild(div);
                    scrollBottom();
                    break;
                }
                case 'setGenerating': {
                    isGenerating = msg.generating;
                    sendBtn.style.display = msg.generating ? 'none' : 'flex';
                    stopBtn.style.display = msg.generating ? 'flex' : 'none';
                    inputEl.disabled = false;
                    break;
                }
                case 'connectionState': {
                    statusDot.className = 'dot' + (msg.connected ? ' ok' : '');
                    statusLabel.textContent = msg.connected ? 'Connected' : 'Disconnected';
                    break;
                }
                case 'providerState': {
                    providerBtn.textContent = PLABELS[msg.provider] || msg.provider || '...';
                    modelBtn.textContent = msg.model || '...';
                    break;
                }
                case 'showDiffStats': {
                    const s = msg.stats;
                    const div = document.createElement('div');
                    div.innerHTML = '<div class="diff-stats">' +
                        '<span class="add">+' + (s.additions || 0) + '</span>' +
                        '<span class="del">-' + (s.deletions || 0) + '</span>' +
                        '<span class="files">' + (s.files_changed || 0) + ' file(s)</span></div>';
                    messagesEl.appendChild(div);
                    scrollBottom();
                    break;
                }
                case 'showExecutionLog': {
                    const log = msg.log;
                    let html = '<div class="exec-log"><div class="exec-log-header">Execution Log</div>';
                    for (const entry of log) {
                        const icon = entry.status === 'success' ? 'ok' : entry.status === 'failed' ? 'fail' : 'skip';
                        const sym = entry.status === 'success' ? '&#10003;' : entry.status === 'failed' ? '&#10007;' : '&#9679;';
                        html += '<div class="exec-step">' +
                            '<span class="status-icon ' + icon + '">' + sym + '</span>' +
                            '<span><strong>Step ' + entry.step + ':</strong> ' + esc(entry.title) + '</span>' +
                            (entry.detail ? '<span style="opacity:0.6;font-size:10px">' + esc(entry.detail) + '</span>' : '') +
                            '</div>';
                    }
                    html += '</div>';
                    const div = document.createElement('div');
                    div.innerHTML = html;
                    messagesEl.appendChild(div);
                    scrollBottom();
                    break;
                }
                case 'ollaBridgeHealth': {
                    if (msg.healthy) {
                        obDot.style.display = 'inline-block';
                        obDot.className = 'ob-dot ok';
                        obDot.title = 'OllaBridge gateway: healthy';
                    } else {
                        obDot.style.display = 'inline-block';
                        obDot.className = 'ob-dot';
                        obDot.title = 'OllaBridge gateway: unreachable';
                    }
                    break;
                }
                case 'clearChat': {
                    messagesEl.innerHTML = '';
                    if (welcomeEl) { messagesEl.appendChild(welcomeEl); welcomeEl.style.display = ''; }
                    currentBubble = null; currentPlan = null;
                    break;
                }
            }
        });

        inputEl.focus();
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
