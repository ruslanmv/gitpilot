/**
 * What GitPilot is actually doing, and what it actually failed at.
 *
 * Every hard-to-diagnose report in this project has had the same shape: the
 * interface said one thing and the truth was somewhere the developer could not
 * see. A backend older than the extension. A provider that was configured but
 * could not answer. A request that took ten seconds and was abandoned at
 * three. None of that was visible from inside VS Code, so debugging meant a
 * terminal, a server log, and guesses.
 *
 * This collects it in one place: the recent request log, the extension and
 * backend versions, and the backend's own account of whether it can answer.
 */
import * as vscode from "vscode";

import { GitPilotApiClient, RequestRecord } from "../../api/client";

/** The backend's `/api/diagnostics` payload. Every field is best-effort. */
export interface BackendDiagnostics {
    version?: string;
    python?: { version?: string; executable?: string };
    package_path?: string;
    chat?: { path?: string; ready?: boolean; detail?: string };
    provider?: {
        name?: string;
        model?: string;
        endpoint?: string;
        configured?: boolean;
    };
    runtimes?: Record<string, boolean>;
}

/** How many calls to keep. Enough to cover a failed task, not a memory leak. */
const HISTORY_LIMIT = 100;

/** Anything slower than this is worth pointing at in the log. */
export const SLOW_REQUEST_MS = 3000;

export class DiagnosticsService implements vscode.Disposable {
    private readonly _history: RequestRecord[] = [];
    private readonly _disposables: vscode.Disposable[] = [];
    private _backendVersion: string | undefined;
    private _warnedAboutVersion = false;

    constructor(
        private readonly client: GitPilotApiClient,
        private readonly output: vscode.OutputChannel,
        private readonly extensionVersion: string
    ) {
        this._disposables.push(
            client.onRequest((record) => this._record(record)),
            client.onStateChange((state) => {
                this.output.appendLine(`[conn] ${state}`);
                if (state === "connected") {
                    void this.checkVersion();
                }
            })
        );
    }

    /** The last requests, newest last. */
    get history(): readonly RequestRecord[] {
        return this._history;
    }

    get backendVersion(): string | undefined {
        return this._backendVersion;
    }

    private _record(record: RequestRecord): void {
        this._history.push(record);
        if (this._history.length > HISTORY_LIMIT) {
            this._history.shift();
        }

        // One line per call, so the Output channel is a usable trace rather
        // than the near-silence it was.
        const ms = `${record.durationMs}ms`;
        if (record.error) {
            this.output.appendLine(
                `[api] ✗ ${record.method} ${record.path} ${ms} — ${record.error}`
            );
        } else if (record.durationMs >= SLOW_REQUEST_MS) {
            this.output.appendLine(
                `[api] 🐢 ${record.method} ${record.path} ${ms} (${record.status})`
            );
        } else {
            this.output.appendLine(
                `[api] ${record.method} ${record.path} ${ms} (${record.status})`
            );
        }
    }

    /**
     * Warn when the backend is not the version this extension was built for.
     *
     * This is the check that would have saved the most time: an extension
     * rebuilt from a fresh checkout, talking to a backend still installed from
     * an older one, looks completely healthy — and then one feature fails for
     * reasons nothing on screen explains. `make extension-dev` never touches
     * the Python package, and `gitpilot` is a console script that imports from
     * site-packages rather than the folder you are standing in, so the two
     * halves drift apart quietly.
     */
    async checkVersion(): Promise<void> {
        let version: string | undefined;
        try {
            const health = await this.client.request<{ version?: string }>(
                "/api/health",
                { timeoutMs: 10000, retries: 0 }
            );
            version = health?.version;
        } catch {
            return; // Connection problems are the connection layer's story.
        }

        this._backendVersion = version;

        if (!version) {
            // A backend too old to report its version is itself the answer.
            this.output.appendLine(
                "[version] backend did not report a version — it predates 0.2.8"
            );
            this._warnMismatch("older than 0.2.8");
            return;
        }

        this.output.appendLine(
            `[version] extension ${this.extensionVersion}, backend ${version}`
        );

        if (version !== this.extensionVersion) {
            this._warnMismatch(version);
        }
    }

    private _warnMismatch(backend: string): void {
        this.output.appendLine(
            `[version] ⚠ mismatch: extension ${this.extensionVersion}, backend ${backend}`
        );

        // Once per session. A modal every reconnect would be worse than the
        // problem it reports.
        if (this._warnedAboutVersion) {
            return;
        }
        this._warnedAboutVersion = true;

        void vscode.window
            .showWarningMessage(
                `GitPilot backend is ${backend} but the extension is ` +
                    `${this.extensionVersion}. Features can misbehave in ways ` +
                    `nothing on screen explains.`,
                "How to fix",
                "Show diagnostics"
            )
            .then((choice) => {
                if (choice === "Show diagnostics") {
                    void vscode.commands.executeCommand("gitpilot.diagnostics");
                } else if (choice === "How to fix") {
                    void vscode.env.openExternal(
                        vscode.Uri.parse(
                            "https://github.com/ruslanmv/gitpilot/blob/main/docs/vscode/troubleshooting.md"
                        )
                    );
                }
            });
    }

    /** Ask the backend to account for itself. */
    async fetchBackend(): Promise<BackendDiagnostics | undefined> {
        try {
            return await this.client.request<BackendDiagnostics>(
                "/api/diagnostics",
                { timeoutMs: 10000, retries: 0 }
            );
        } catch {
            return undefined;
        }
    }

    /**
     * The whole picture as text, ready to read or paste into an issue.
     *
     * Deliberately plain: the point is that it can be copied into a bug report
     * without the reporter having to know which parts matter.
     */
    async report(): Promise<string> {
        const backend = await this.fetchBackend();
        const probe = this.client.lastProbe;
        const lines: string[] = [];

        lines.push("GitPilot diagnostics");
        lines.push("=".repeat(60));
        lines.push("");

        lines.push("Versions");
        lines.push(`  extension        ${this.extensionVersion}`);
        lines.push(`  backend          ${backend?.version ?? this._backendVersion ?? "unknown"}`);
        if (backend?.version && backend.version !== this.extensionVersion) {
            lines.push("  ⚠ MISMATCH — the two halves are from different builds.");
            lines.push("    `make extension-dev` rebuilds only the extension.");
            lines.push("    Reinstall the backend:  pip install -e . --no-deps");
        }
        lines.push("");

        lines.push("Connection");
        lines.push(`  server url       ${this.client.serverUrl}`);
        lines.push(`  state            ${this.client.state}`);
        if (probe) {
            lines.push(`  last probe       ${probe.outcome} after ${probe.elapsedMs}ms`);
        }
        lines.push(`  retrying         ${this.client.isRetrying ? "yes" : "no"}`);
        lines.push("");

        if (backend) {
            lines.push("Chat");
            lines.push(`  path             ${backend.chat?.path ?? "unknown"}`);
            lines.push(`  can answer       ${backend.chat?.ready ? "yes" : "no"}`);
            if (backend.chat?.detail) {
                lines.push(`  detail           ${backend.chat.detail}`);
            }
            lines.push("");

            lines.push("Provider");
            lines.push(`  name             ${backend.provider?.name ?? "unknown"}`);
            lines.push(`  model            ${backend.provider?.model || "(none selected)"}`);
            lines.push(`  endpoint         ${backend.provider?.endpoint || "(n/a)"}`);
            lines.push(`  configured       ${backend.provider?.configured ? "yes" : "no"}`);
            lines.push("");

            lines.push("Backend environment");
            lines.push(`  python           ${backend.python?.version ?? "?"}`);
            lines.push(`  interpreter      ${backend.python?.executable ?? "?"}`);
            // The one that explains "I pulled but nothing changed": a console
            // script imports from site-packages, not the folder you are in.
            lines.push(`  package path     ${backend.package_path ?? "?"}`);
            const runtimes = backend.runtimes ?? {};
            const names = Object.keys(runtimes);
            if (names.length) {
                lines.push(
                    `  runtimes         ${names
                        .map((n) => `${n}=${runtimes[n] ? "yes" : "no"}`)
                        .join("  ")}`
                );
            }
            lines.push("");
        } else {
            lines.push("Backend");
            lines.push("  /api/diagnostics did not answer.");
            lines.push("  Either the server is down, or it predates 0.2.8.");
            lines.push("");
        }

        lines.push(`Recent requests (${this._history.length})`);
        if (this._history.length === 0) {
            lines.push("  none yet");
        } else {
            for (const r of this._history.slice(-25)) {
                const mark = r.error ? "✗" : r.durationMs >= SLOW_REQUEST_MS ? "🐢" : " ";
                lines.push(
                    `  ${mark} ${String(r.durationMs).padStart(6)}ms  ` +
                        `${r.method.padEnd(6)} ${r.path}` +
                        (r.error ? `  — ${r.error}` : ` (${r.status})`)
                );
            }
        }
        lines.push("");

        const failures = this._history.filter((r) => r.error);
        const slow = this._history.filter((r) => !r.error && r.durationMs >= SLOW_REQUEST_MS);
        lines.push("Summary");
        lines.push(`  requests         ${this._history.length}`);
        lines.push(`  failed           ${failures.length}`);
        lines.push(`  slow (>${SLOW_REQUEST_MS}ms)      ${slow.length}`);

        return lines.join("\n");
    }

    dispose(): void {
        for (const d of this._disposables) {
            d.dispose();
        }
    }
}
