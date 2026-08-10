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

/**
 * `RequestInit` plus the knobs the GitPilot client adds on top:
 * a hard per-attempt deadline and a retry budget.
 *
 * A timeout matters more here than it looks: several GitPilot endpoints
 * (`/api/status`, model discovery) probe live providers and can sit for tens
 * of seconds. Without a deadline a settings page just hangs.
 */
export interface GitPilotRequestOptions extends RequestInit {
    /** Abort a single attempt after this many milliseconds. */
    timeoutMs?: number;
    /** Retries after the first attempt. Defaults to 2. */
    retries?: number;
}

/** Deadlines, chosen per call class rather than one blanket number. */
/**
 * What a health probe found.
 *
 * `unreachable` means nothing accepted the connection — start the server.
 * `timeout` means something did, and did not answer in time — wait for it.
 * Reporting both as "Could not reach" sent people to fix the working half.
 */
/** One completed API call, for the log and the diagnostics report. */
export interface RequestRecord {
    method: string;
    path: string;
    durationMs: number;
    /** HTTP status, or 0 when the request never got one. */
    status: number;
    error?: string;
    at: number;
}

export interface ProbeResult {
    ok: boolean;
    outcome: 'reachable' | 'timeout' | 'unreachable';
    elapsedMs: number;
    detail?: string;
}

/**
 * Deadlines for successive connect attempts.
 *
 * Short first, so a genuinely absent server renders "Offline" promptly;
 * generous after that, so a backend still importing its way to readiness is
 * waited for instead of pronounced dead.
 */
export const CONNECT_DEADLINES: readonly number[] = [3000, 10000, 25000];

/** Backoff bounds for the watcher that reconnects on its own. */
export const AUTO_RECONNECT_MIN_MS = 2000;
export const AUTO_RECONNECT_MAX_MS = 60000;

export const REQUEST_TIMEOUTS = {
    /** Liveness probe — must fail fast so "offline" renders promptly. */
    health: 3000,
    /** Persisted settings read; no provider probing server-side. */
    settings: 10000,
    /** Model discovery talks to the provider, so it gets more room. */
    models: 15000,
    /** A connection test performs a real completion round-trip. */
    providerTest: 30000,
    /**
     * A turn of actual inference — chat, plan, execute.
     *
     * These are not slow requests, they are a model thinking, and the
     * default deadline is the wrong order of magnitude for one. A local
     * Ollama answering from a repository context routinely takes 20-40s,
     * so a 20s deadline failed a backend that was working: the server
     * logged `POST /api/chat/send took 21.26s (status=200)` while the
     * extension had already given up at 20000ms. The web app never hit
     * this because it allows five minutes for the same call; this is that
     * number, so both front ends wait equally long for the same backend.
     */
    llm: 300000,
    /** Everything else. */
    default: 20000,
} as const;

/** Lower bound for the configured LLM deadline — below this, nothing finishes. */
export const MIN_LLM_TIMEOUT_SECONDS = 30;

/**
 * How long to let a turn of inference run, in milliseconds.
 *
 * Five minutes suits a small model on a normal machine. It does not suit
 * every machine — a 7B model on CPU can exceed it — so the ceiling is
 * configurable rather than a constant someone has to rebuild the extension
 * to change.
 */
export function llmTimeoutMs(): number {
    const configured = vscode.workspace
        .getConfiguration('gitpilot')
        .get<number>('llmTimeoutSeconds', REQUEST_TIMEOUTS.llm / 1000);
    if (!Number.isFinite(configured) || configured <= 0) {
        return REQUEST_TIMEOUTS.llm;
    }
    return Math.max(configured, MIN_LLM_TIMEOUT_SECONDS) * 1000;
}

/**
 * Request options for a turn of inference.
 *
 * `retries: 0` is the load-bearing half. A timeout carries no HTTP status,
 * so it slips past the non-retryable-status guard and the default budget
 * of two retries fires — three runs of a call the server is still
 * executing. That is how one message became the three
 * `POST /api/chat/send` entries in the report, at 21s, 23s and 35s: each
 * retry queued behind the last on a single-threaded Ollama, so the wait
 * grew with every attempt that was supposed to rescue it. Worse than slow,
 * `/api/chat/send` appends to the session on the way out, so every
 * duplicate attempt that does land writes the exchange to history again.
 */
export function llmRequestOptions(): { timeoutMs: number; retries: number } {
    return { timeoutMs: llmTimeoutMs(), retries: 0 };
}

/** Endpoints whose slowness is a model thinking, not a server struggling. */
const LLM_PATHS = ['/api/chat/send', '/api/chat/plan', '/api/chat/execute', '/api/chat/message'];

/**
 * What to say when a request runs out of time.
 *
 * "timed out after 20000ms" describes the client's own impatience and
 * nothing the reader can act on — the backend in the report was answering
 * that same call successfully, one second later. When the call was
 * inference, name the cause and the setting that moves it.
 */
export function timeoutMessage(path: string, deadline: number): string {
    const seconds = Math.round(deadline / 1000);
    if (LLM_PATHS.some((p) => path.startsWith(p))) {
        return (
            `The model did not answer within ${seconds}s, so GitPilot stopped waiting. ` +
            `The request was not retried — the backend may still be working on it. ` +
            `A smaller model, or Lite Mode, will answer faster; to simply wait longer, ` +
            `raise "gitpilot.llmTimeoutSeconds" in Settings.`
        );
    }
    return `Request to ${path} timed out after ${deadline}ms`;
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
    private _onRequest = new vscode.EventEmitter<RequestRecord>();
    private _healthTimer: ReturnType<typeof setInterval> | undefined;
    private _reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    private _lastProbe: ProbeResult | undefined;

    readonly onStateChange = this._onStateChange.event;
    readonly onError = this._onError.event;
    /**
     * Every completed request, so the extension can say what it actually did.
     *
     * The Output channel used to carry almost nothing, which meant a slow or
     * failing backend looked identical to a working one from inside VS Code —
     * the evidence only existed in the terminal running the server.
     */
    readonly onRequest = this._onRequest.event;

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
        this._lastProbe = undefined;
        this.stopAutoReconnect();
        this._onStateChange.fire(this._state);
    }

    setToken(token: string | undefined): void {
        this._token = token;
    }

    /**
     * Probe `/api/health` without touching connection state.
     *
     * Returns *why* it failed, not just that it did. "Nothing is listening"
     * and "it answered, slowly, and we gave up first" call for opposite
     * remedies — start the server versus wait for it — and the extension
     * reported both as "Could not reach", which sent people to fix the one
     * thing that was already working.
     */
    async probe(timeoutMs: number = REQUEST_TIMEOUTS.health): Promise<ProbeResult> {
        const started = Date.now();
        try {
            await this.request<{ status: string }>('/api/health', { timeoutMs, retries: 0 });
            return { ok: true, outcome: 'reachable', elapsedMs: Date.now() - started };
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            return {
                ok: false,
                outcome: /timed out/i.test(message) ? 'timeout' : 'unreachable',
                elapsedMs: Date.now() - started,
                detail: message,
            };
        }
    }

    /** Back-compatible boolean probe. */
    async health(timeoutMs: number = REQUEST_TIMEOUTS.health): Promise<boolean> {
        return (await this.probe(timeoutMs)).ok;
    }

    /**
     * Connect, giving a starting server room to finish starting.
     *
     * A single 3s probe was the whole procedure, so a backend that answered
     * in 10s — which the Python side did on WSL, because it probed local
     * providers inline — was pronounced dead on arrival. Clicking Reconnect
     * repeated the same 3s probe and failed the same way, which is why the
     * button appeared to do nothing.
     *
     * The deadline now grows across attempts. A server that is merely slow to
     * warm up gets waited for; one that is genuinely absent still fails in
     * about three seconds on the first attempt, so "offline" renders promptly
     * when it is true.
     */
    async connect(deadlines: readonly number[] = CONNECT_DEADLINES): Promise<boolean> {
        this._setState('connecting');

        for (let attempt = 0; attempt < deadlines.length; attempt++) {
            const result = await this.probe(deadlines[attempt]);

            if (result.ok) {
                this._lastProbe = result;
                this._setState('connected');
                this.startHealthCheck();
                this.stopAutoReconnect();
                return true;
            }

            this._lastProbe = result;

            // Nothing is listening and this was the first look: no amount of
            // waiting turns a refused connection into an answer, so only keep
            // trying when something is there but slow.
            if (result.outcome === 'unreachable' && attempt === 0) {
                break;
            }
        }

        this._setState('disconnected');
        return false;
    }

    /** True while the auto-reconnect watcher is armed. */
    get isRetrying(): boolean {
        return this._reconnectTimer !== undefined;
    }

    /** Why the last probe failed, for a UI that has to explain itself. */
    get lastProbe(): ProbeResult | undefined {
        return this._lastProbe;
    }

    disconnect(): void {
        this.stopHealthCheck();
        this.stopAutoReconnect();
        this._setState('disconnected');
    }

    /**
     * Watch for a server that is not there yet.
     *
     * Starting `gitpilot serve` in a terminal should be enough; having to
     * come back to the sidebar and click Reconnect is a step the extension
     * can take for itself. Backs off so a permanently absent server costs
     * one cheap probe a minute rather than one a second.
     */
    startAutoReconnect(): void {
        if (this._reconnectTimer) {
            return;
        }

        let delay = AUTO_RECONNECT_MIN_MS;
        const tick = async (): Promise<void> => {
            this._reconnectTimer = undefined;
            if (this._state === 'connected') {
                return;
            }

            const result = await this.probe(REQUEST_TIMEOUTS.health);
            if (result.ok) {
                // Full connect so state, health checks and listeners all move
                // together rather than this timer quietly flipping a flag.
                await this.connect();
                return;
            }

            this._lastProbe = result;
            delay = Math.min(delay * 2, AUTO_RECONNECT_MAX_MS);
            this._reconnectTimer = setTimeout(() => void tick(), delay);
        };

        this._reconnectTimer = setTimeout(() => void tick(), delay);
    }

    stopAutoReconnect(): void {
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = undefined;
        }
    }

    startHealthCheck(intervalMs = 30000): void {
        this.stopHealthCheck();
        this._healthTimer = setInterval(async () => {
            // A live server that is briefly busy is not a dead one. The
            // periodic check gets a real deadline and one retry so a slow
            // moment does not blink the whole UI to Offline.
            try {
                await this.request('/api/health', {
                    timeoutMs: REQUEST_TIMEOUTS.settings,
                    retries: 1,
                });
                if (this._state !== 'connected') {
                    this._setState('connected');
                }
            } catch {
                if (this._state === 'connected') {
                    this._setState('disconnected');
                    // Lost it: start watching for it to come back.
                    this.startAutoReconnect();
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
            ...llmRequestOptions(),
            method: 'POST',
            body: JSON.stringify({ repo_owner: owner, repo_name: repo, message, session_id: sessionId }),
        });
    }

    async chatExecute(owner: string, repo: string, plan: ChatPlanResponse['plan'], sessionId?: string): Promise<ChatExecuteResponse> {
        return this.request('/api/chat/execute', {
            ...llmRequestOptions(),
            method: 'POST',
            body: JSON.stringify({ repo_owner: owner, repo_name: repo, plan, session_id: sessionId }),
        });
    }

    async chatMessage(owner: string, repo: string, message: string, sessionId?: string): Promise<{ result: string }> {
        return this.request('/api/chat/message', {
            ...llmRequestOptions(),
            method: 'POST',
            body: JSON.stringify({ repo_owner: owner, repo_name: repo, message, session_id: sessionId }),
        });
    }

    // Routing is a deterministic classifier server-side, not a model call,
    // so it keeps the ordinary deadline.
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

    // Runs the plan and opens a pull request, so it gets the inference
    // budget — and, more importantly, no retries: a re-sent attempt can
    // open a second PR for work the first one already did.
    async createPR(owner: string, repo: string, branch: string, title: string, body?: string): Promise<{ url: string; number: number }> {
        return this.request('/api/chat/execute-with-pr', {
            ...llmRequestOptions(),
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

    // --- Convenience HTTP methods ---

    async get<T = any>(path: string): Promise<T> {
        return this.request<T>(path);
    }

    async post<T = any>(
        path: string,
        body?: any,
        options?: GitPilotRequestOptions
    ): Promise<T> {
        return this.request<T>(path, {
            ...options,
            method: 'POST',
            body: body !== undefined ? JSON.stringify(body) : undefined,
        });
    }

    async put<T = any>(path: string, body?: any): Promise<T> {
        return this.request<T>(path, {
            method: 'PUT',
            body: body !== undefined ? JSON.stringify(body) : undefined,
        });
    }

    async delete<T = void>(path: string): Promise<T> {
        return this.request<T>(path, { method: 'DELETE' });
    }

    // --- Generic request with retry ---

    /** Status codes that should NOT be retried (non-transient errors). */
    private static readonly _noRetryStatuses = new Set([400, 401, 403, 404, 422, 429, 502, 503]);

    async request<T = any>(path: string, options?: GitPilotRequestOptions, retriesArg = 2): Promise<T> {
        const url = `${this._serverUrl}${path}`;
        const headers: Record<string, string> = {
            'Content-Type': 'application/json',
            ...(this._token ? { 'Authorization': `Bearer ${this._token}` } : {}),
        };

        const { timeoutMs, retries: retriesOpt, ...init } = options ?? {};
        const retries = retriesOpt ?? retriesArg;
        const deadline = timeoutMs ?? REQUEST_TIMEOUTS.default;

        const method = (init.method || 'GET').toUpperCase();
        const startedAt = Date.now();
        const report = (status: number, error?: string): void => {
            this._onRequest.fire({
                method, path, status, error,
                durationMs: Date.now() - startedAt,
                at: startedAt,
            });
        };

        let lastError: Error | undefined;
        for (let attempt = 0; attempt <= retries; attempt++) {
            const timer = new AbortController();
            const expire = setTimeout(() => timer.abort(), deadline);
            // A caller-supplied signal still wins — chain it into ours so the
            // request dies on whichever fires first.
            const caller = init.signal;
            const onCallerAbort = () => timer.abort();
            caller?.addEventListener('abort', onCallerAbort, { once: true });

            try {
                const resp = await fetch(url, {
                    ...init,
                    signal: timer.signal,
                    headers: { ...headers, ...(init.headers as Record<string, string> || {}) },
                });
                if (!resp.ok) {
                    const body = await resp.text().catch(() => '');
                    // Try to extract structured detail from JSON error body
                    let detail = body;
                    try {
                        const parsed = JSON.parse(body);
                        if (parsed.detail) { detail = parsed.detail; }
                    } catch { /* body is not JSON */ }
                    const err: ApiError = { status: resp.status, message: resp.statusText, detail };
                    this._onError.fire(err);
                    const error = new Error(`HTTP ${resp.status}: ${detail || resp.statusText}`);
                    (error as any).status = resp.status;
                    // Carry the server's own explanation, not just the code.
                    // The backend writes the actionable sentence — which
                    // provider, which package, what to run — and a status
                    // number cannot be translated back into it.
                    (error as any).detail = detail;
                    // Don't retry non-transient errors (circuit breaker, auth, rate limit)
                    if (GitPilotApiClient._noRetryStatuses.has(resp.status)) {
                        throw error;
                    }
                    throw error;
                }
                const parsed = await resp.json() as T;
                report(resp.status);
                return parsed;
            } catch (err: any) {
                const abortedByCaller = caller?.aborted === true;
                lastError = err?.name === 'AbortError' && !abortedByCaller
                    ? new Error(timeoutMessage(path, deadline))
                    : err;
                // The caller gave up on purpose — surface that, don't retry.
                if (abortedByCaller) {
                    break;
                }
                // Skip retries for non-transient HTTP errors
                if (err.status && GitPilotApiClient._noRetryStatuses.has(err.status)) {
                    break;
                }
                if (attempt < retries) {
                    await new Promise(r => setTimeout(r, Math.pow(2, attempt) * 1000));
                }
            } finally {
                clearTimeout(expire);
                caller?.removeEventListener('abort', onCallerAbort);
            }
        }
        report(
            (lastError as { status?: number } | undefined)?.status ?? 0,
            lastError?.message ?? 'unknown error'
        );
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
