/**
 * GitPilot API Client
 *
 * Enterprise-grade HTTP client with:
 * - Connection health monitoring
 * - Automatic retry with exponential backoff
 * - Token-based authentication
 * - Request/response typing
 * - Event-driven connection state
 */
import * as vscode from 'vscode';

export type ConnectionState = 'connected' | 'disconnected' | 'connecting' | 'error';

export interface ApiError {
    status: number;
    message: string;
    detail?: string;
}

export interface ChatPlanResponse {
    answer: string;
    plan: Array<{
        step: number;
        action: string;
        file: string;
        description: string;
    }>;
    topology?: string;
}

export interface ChatExecuteResponse {
    result: string;
    message?: string;
    files_changed: string[];
    commit_sha?: string;
    pr_url?: string;
    html_url?: string;
    commit_url?: string;
    branch?: string;
    branch_name?: string;
    mode?: string;
    diff_stats?: { additions: number; deletions: number; files_changed: number };
    execution_log?: Array<{ step: number; title: string; status: string; detail?: string }>;
}

export interface SessionInfo {
    id: string;
    repo_owner: string;
    repo_name: string;
    status: string;
    created_at: string;
    updated_at: string;
    message_count: number;
    title?: string;
}

export interface SkillInfo {
    name: string;
    description: string;
    auto_trigger: boolean;
    source: string;
}

export interface SecurityFinding {
    file: string;
    line: number;
    severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
    category: string;
    message: string;
    cwe?: string;
    confidence: number;
}

export interface TopologyInfo {
    id: string;
    name: string;
    description: string;
    category: string;
    nodes: Array<{ id: string; label: string; type: string }>;
    edges: Array<{ source: string; target: string }>;
}

export interface PluginInfo {
    name: string;
    version: string;
    description: string;
    skills: string[];
    hooks: string[];
}

export class GitPilotApiClient {
    private _serverUrl: string;
    private _token: string | undefined;
    private _state: ConnectionState = 'disconnected';
    private _onStateChange = new vscode.EventEmitter<ConnectionState>();
    private _onError = new vscode.EventEmitter<ApiError>();
    private _healthTimer: ReturnType<typeof setInterval> | undefined;

    readonly onStateChange = this._onStateChange.event;
    readonly onError = this._onError.event;

    constructor(serverUrl: string, token?: string) {
        this._serverUrl = serverUrl.replace(/\/+$/, '');
        this._token = token;
    }

    get serverUrl(): string { return this._serverUrl; }
    get state(): ConnectionState { return this._state; }
    get isConnected(): boolean { return this._state === 'connected'; }

    setServerUrl(url: string): void {
        this._serverUrl = url.replace(/\/+$/, '');
        this._state = 'disconnected';
        this._onStateChange.fire(this._state);
    }

    setToken(token: string | undefined): void {
        this._token = token;
    }

    async connect(): Promise<boolean> {
        this._setState('connecting');
        try {
            await this.request<{ status: string }>('/api/health');
            this._setState('connected');
            this.startHealthCheck();
            return true;
        } catch {
            this._setState('disconnected');
            return false;
        }
    }

    disconnect(): void {
        this.stopHealthCheck();
        this._setState('disconnected');
    }

    startHealthCheck(intervalMs = 30000): void {
        this.stopHealthCheck();
        this._healthTimer = setInterval(async () => {
            try {
                await this.request('/api/health');
                if (this._state !== 'connected') {
                    this._setState('connected');
                }
            } catch {
                if (this._state === 'connected') {
                    this._setState('disconnected');
                }
            }
        }, intervalMs);
    }

    stopHealthCheck(): void {
        if (this._healthTimer) {
            clearInterval(this._healthTimer);
            this._healthTimer = undefined;
        }
    }

    // --- Chat & Planning ---

    async chatPlan(owner: string, repo: string, message: string, sessionId?: string): Promise<ChatPlanResponse> {
        return this.request('/api/chat/plan', {
            method: 'POST',
            body: JSON.stringify({ repo_owner: owner, repo_name: repo, message, session_id: sessionId }),
        });
    }

    async chatExecute(owner: string, repo: string, plan: ChatPlanResponse['plan'], sessionId?: string): Promise<ChatExecuteResponse> {
        return this.request('/api/chat/execute', {
            method: 'POST',
            body: JSON.stringify({ repo_owner: owner, repo_name: repo, plan, session_id: sessionId }),
        });
    }

    async chatMessage(owner: string, repo: string, message: string, sessionId?: string): Promise<{ result: string }> {
        return this.request('/api/chat/message', {
            method: 'POST',
            body: JSON.stringify({ repo_owner: owner, repo_name: repo, message, session_id: sessionId }),
        });
    }

    async chatRoute(message: string): Promise<{ topology: string; confidence: number }> {
        return this.request('/api/chat/route', {
            method: 'POST',
            body: JSON.stringify({ message }),
        });
    }

    // --- Sessions ---

    async listSessions(): Promise<SessionInfo[]> {
        const resp = await this.request<{ sessions: SessionInfo[] }>('/api/sessions');
        return resp.sessions || [];
    }

    async createSession(owner: string, repo: string): Promise<SessionInfo> {
        return this.request('/api/sessions', {
            method: 'POST',
            body: JSON.stringify({ repo_owner: owner, repo_name: repo }),
        });
    }

    async getSession(id: string): Promise<SessionInfo & { messages: Array<{ role: string; content: string }> }> {
        return this.request(`/api/sessions/${encodeURIComponent(id)}`);
    }

    async deleteSession(id: string): Promise<void> {
        await this.request(`/api/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' });
    }

    // --- Repository ---

    async listRepos(): Promise<Array<{ owner: string; name: string; description?: string }>> {
        const resp = await this.request<{ repos: any[] }>('/api/repos');
        return resp.repos || [];
    }

    async getRepoTree(owner: string, repo: string): Promise<any> {
        return this.request(`/api/repos/${owner}/${repo}/tree`);
    }

    async getFileContent(owner: string, repo: string, path: string): Promise<{ content: string }> {
        return this.request(`/api/repos/${owner}/${repo}/file?path=${encodeURIComponent(path)}`);
    }

    // --- Security ---

    async scanFile(filePath: string, content: string): Promise<{ findings: SecurityFinding[] }> {
        return this.request('/api/security/scan-file', {
            method: 'POST',
            body: JSON.stringify({ file_path: filePath, content }),
        });
    }

    async scanDirectory(directory: string): Promise<{ findings: SecurityFinding[] }> {
        return this.request('/api/security/scan-directory', {
            method: 'POST',
            body: JSON.stringify({ directory }),
        });
    }

    async scanDiff(): Promise<{ findings: SecurityFinding[] }> {
        return this.request('/api/security/scan-diff', { method: 'POST' });
    }

    // --- Skills ---

    async listSkills(): Promise<SkillInfo[]> {
        const resp = await this.request<{ skills: SkillInfo[] }>('/api/skills');
        return resp.skills || [];
    }

    async invokeSkill(name: string, context?: Record<string, string>): Promise<{ result: string }> {
        return this.request('/api/skills/invoke', {
            method: 'POST',
            body: JSON.stringify({ name, context }),
        });
    }

    // --- Plugins ---

    async listPlugins(): Promise<PluginInfo[]> {
        const resp = await this.request<{ plugins: PluginInfo[] }>('/api/plugins');
        return resp.plugins || [];
    }

    async installPlugin(source: string): Promise<{ name: string }> {
        return this.request('/api/plugins/install', {
            method: 'POST',
            body: JSON.stringify({ source }),
        });
    }

    async uninstallPlugin(name: string): Promise<void> {
        await this.request(`/api/plugins/${encodeURIComponent(name)}`, { method: 'DELETE' });
    }

    // --- Topologies / Agent Flow ---

    async listTopologies(): Promise<TopologyInfo[]> {
        const resp = await this.request<{ topologies: TopologyInfo[] }>('/api/flow/topologies');
        return resp.topologies || [];
    }

    async getCurrentFlow(): Promise<{ topology: TopologyInfo; active_node?: string }> {
        return this.request('/api/flow/current');
    }

    async setTopology(id: string): Promise<void> {
        await this.request(`/api/flow/topology/${encodeURIComponent(id)}`, { method: 'POST' });
    }

    // --- Settings & Provider Management ---

    async getSettings(): Promise<Record<string, any>> {
        return this.request('/api/settings');
    }

    async getModels(): Promise<Array<{ id: string; name: string; provider: string }>> {
        const resp = await this.request<{ models: any[] }>('/api/settings/models');
        return resp.models || [];
    }

    async setProvider(provider: string): Promise<Record<string, any>> {
        return this.request('/api/settings/provider', {
            method: 'POST',
            body: JSON.stringify({ provider }),
        });
    }

    async updateLlmSettings(updates: Record<string, any>): Promise<Record<string, any>> {
        return this.request('/api/settings/llm', {
            method: 'PUT',
            body: JSON.stringify(updates),
        });
    }

    async getOllaBridgeModels(baseUrl?: string, apiKey?: string): Promise<string[]> {
        const params = new URLSearchParams();
        if (baseUrl) { params.set('base_url', baseUrl); }
        if (apiKey) { params.set('api_key', apiKey); }
        const qs = params.toString();
        const resp = await this.request<{ models: string[] }>(`/api/ollabridge/models${qs ? '?' + qs : ''}`);
        return resp.models || [];
    }

    async getOllaBridgeHealth(baseUrl?: string): Promise<{ status: string }> {
        const params = baseUrl ? `?base_url=${encodeURIComponent(baseUrl)}` : '';
        return this.request(`/api/ollabridge/health${params}`);
    }

    async ollaBridgePair(baseUrl: string, code: string): Promise<{ success: boolean; token?: string; error?: string }> {
        return this.request('/api/ollabridge/pair', {
            method: 'POST',
            body: JSON.stringify({ base_url: baseUrl, code }),
        });
    }

    // --- Pull Requests ---

    async createPR(owner: string, repo: string, branch: string, title: string, body?: string): Promise<{ url: string; number: number }> {
        return this.request('/api/chat/execute-with-pr', {
            method: 'POST',
            body: JSON.stringify({ repo_owner: owner, repo_name: repo, branch, title, body }),
        });
    }

    // --- Predictions ---

    async getSuggestions(owner: string, repo: string, event: string): Promise<Array<{ action: string; reason: string }>> {
        const resp = await this.request<{ suggestions: any[] }>('/api/predictions/suggest', {
            method: 'POST',
            body: JSON.stringify({ repo_owner: owner, repo_name: repo, event }),
        });
        return resp.suggestions || [];
    }

    // --- Hooks ---

    async listHooks(): Promise<Array<{ event: string; name: string; command: string; blocking: boolean }>> {
        const resp = await this.request<{ hooks: any[] }>('/api/hooks');
        return resp.hooks || [];
    }

    // --- Permissions ---

    async getPermissions(): Promise<{ mode: string; blocked_paths: string[]; allowed_commands: string[] }> {
        return this.request('/api/permissions');
    }

    async setPermissionMode(mode: 'normal' | 'plan' | 'auto'): Promise<void> {
        await this.request('/api/permissions/mode', {
            method: 'PUT',
            body: JSON.stringify({ mode }),
        });
    }

    // --- Generic request with retry ---

    async request<T = any>(path: string, options?: RequestInit, retries = 2): Promise<T> {
        const url = `${this._serverUrl}${path}`;
        const headers: Record<string, string> = {
            'Content-Type': 'application/json',
            ...(this._token ? { 'Authorization': `Bearer ${this._token}` } : {}),
        };

        let lastError: Error | undefined;
        for (let attempt = 0; attempt <= retries; attempt++) {
            try {
                const resp = await fetch(url, { ...options, headers: { ...headers, ...(options?.headers as Record<string, string> || {}) } });
                if (!resp.ok) {
                    const body = await resp.text().catch(() => '');
                    const err: ApiError = { status: resp.status, message: resp.statusText, detail: body };
                    this._onError.fire(err);
                    throw new Error(`HTTP ${resp.status}: ${resp.statusText} — ${body}`);
                }
                return await resp.json() as T;
            } catch (err: any) {
                lastError = err;
                if (attempt < retries) {
                    await new Promise(r => setTimeout(r, Math.pow(2, attempt) * 1000));
                }
            }
        }
        throw lastError!;
    }

    private _setState(state: ConnectionState): void {
        if (this._state !== state) {
            this._state = state;
            this._onStateChange.fire(state);
        }
    }

    dispose(): void {
        this.stopHealthCheck();
        this._onStateChange.dispose();
        this._onError.dispose();
    }
}
