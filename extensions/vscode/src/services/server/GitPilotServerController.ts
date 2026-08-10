/**
 * GitPilotServerController — owns the local `gitpilot serve` process.
 *
 * The settings page must be useful when the backend is down, and "download a
 * terminal, type this command" is not useful. This controller closes that gap:
 * it can tell whether the configured server is reachable, start a local one
 * when the URL is a loopback address, follow the port GitPilot actually bound
 * (it drifts when 8000 is busy), and hand back a live server URL.
 *
 * It deliberately does nothing for remote URLs — starting a process here would
 * never make someone else's server reachable.
 */
import * as vscode from "vscode";
import * as cp from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";

import { GitPilotApiClient } from "../../api/client";

export type ServerStartResult =
  | { ok: true; serverUrl: string }
  | { ok: false; reason: string };

/** Hostnames that mean "this machine", and so may be started locally. */
const LOOPBACK_HOSTS = new Set([
  "localhost",
  "127.0.0.1",
  "0.0.0.0",
  "::1",
  "[::1]",
]);

/** How long to wait for `gitpilot serve` to answer /api/health. */
const START_TIMEOUT_MS = 60_000;
const POLL_INTERVAL_MS = 500;

export class GitPilotServerController implements vscode.Disposable {
  private _process: cp.ChildProcess | undefined;
  private _starting: Promise<ServerStartResult> | undefined;

  constructor(
    private readonly client: GitPilotApiClient,
    private readonly output: vscode.OutputChannel
  ) {}

  /** True when we started the server and it is still running. */
  get isManaged(): boolean {
    return this._process !== undefined && this._process.exitCode === null;
  }

  /** Whether `url` points at this machine, i.e. whether we could start it. */
  isLocalUrl(url: string = this.client.serverUrl): boolean {
    try {
      const host = new URL(url).hostname.toLowerCase();
      return LOOPBACK_HOSTS.has(host);
    } catch {
      return false;
    }
  }

  /** Single fast probe of the configured server. */
  async isHealthy(): Promise<boolean> {
    return this.client.health();
  }

  /** The command used to launch GitPilot, overridable for odd installs. */
  private get command(): string {
    return vscode.workspace
      .getConfiguration("gitpilot")
      .get<string>("serverCommand", "gitpilot")
      .trim() || "gitpilot";
  }

  /** The exact command line we would run, for display and diagnostics. */
  get commandLine(): string {
    return `${this.command} serve --no-open`;
  }

  /**
   * Start a local GitPilot server and wait until it answers.
   *
   * Concurrent callers share one attempt — a settings page that retries while
   * a start is in flight should not spawn a second server.
   */
  async start(token?: vscode.CancellationToken): Promise<ServerStartResult> {
    if (this._starting) {
      return this._starting;
    }
    this._starting = this._start(token).finally(() => {
      this._starting = undefined;
    });
    return this._starting;
  }

  private async _start(
    token?: vscode.CancellationToken
  ): Promise<ServerStartResult> {
    if (!this.isLocalUrl()) {
      return {
        ok: false,
        reason:
          `${this.client.serverUrl} is not a local address, so GitPilot ` +
          `cannot start it from here. Start it on that host, or change the ` +
          `server URL to a local one.`,
      };
    }

    // Something may already be listening (another VS Code window, a terminal).
    if (await this.isHealthy()) {
      return { ok: true, serverUrl: this.client.serverUrl };
    }

    const portFile = path.join(
      fs.mkdtempSync(path.join(os.tmpdir(), "gitpilot-")),
      "port"
    );

    const args = ["serve", "--no-open", "--port-file", portFile];
    const requested = this.preferredPort();
    if (requested !== undefined) {
      args.push("--port", String(requested));
    }

    this.output.appendLine(
      `[GitPilot] Starting server: ${this.command} ${args.join(" ")}`
    );

    let child: cp.ChildProcess;
    try {
      child = cp.spawn(this.command, args, {
        cwd: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
        // On Windows `gitpilot` is a .exe/.cmd shim that needs the shell to
        // resolve; elsewhere spawning directly avoids quoting surprises.
        shell: process.platform === "win32",
        env: { ...process.env },
      });
    } catch (err) {
      return { ok: false, reason: describeSpawnError(err, this.command) };
    }

    this._process = child;

    child.stdout?.on("data", (chunk: Buffer) =>
      this.output.append(`[gitpilot] ${chunk.toString()}`)
    );
    child.stderr?.on("data", (chunk: Buffer) =>
      this.output.append(`[gitpilot] ${chunk.toString()}`)
    );

    let spawnError: string | undefined;
    child.on("error", (err) => {
      spawnError = describeSpawnError(err, this.command);
      this.output.appendLine(`[GitPilot] ${spawnError}`);
    });
    child.on("exit", (code, signal) => {
      this.output.appendLine(
        `[GitPilot] Server process exited (code=${code}, signal=${signal})`
      );
      if (this._process === child) {
        this._process = undefined;
      }
    });

    const deadline = Date.now() + START_TIMEOUT_MS;
    let adoptedPort: number | undefined;

    while (Date.now() < deadline) {
      if (token?.isCancellationRequested) {
        this.stop();
        return { ok: false, reason: "Cancelled." };
      }
      if (spawnError) {
        return { ok: false, reason: spawnError };
      }
      if (child.exitCode !== null) {
        return {
          ok: false,
          reason:
            `GitPilot exited immediately (code ${child.exitCode}). ` +
            `See the GitPilot output channel for the reason.`,
        };
      }

      // GitPilot writes the port before binding, and moves off a busy port —
      // so the file, not our request, is the source of truth.
      if (adoptedPort === undefined) {
        const port = readPortFile(portFile);
        if (port !== undefined) {
          adoptedPort = port;
          const url = this.withPort(port);
          if (url !== this.client.serverUrl) {
            this.output.appendLine(
              `[GitPilot] Server bound port ${port}; following it to ${url}`
            );
            this.client.setServerUrl(url);
          }
        }
      }

      if (adoptedPort !== undefined && (await this.isHealthy())) {
        cleanupPortFile(portFile);
        return { ok: true, serverUrl: this.client.serverUrl };
      }

      await delay(POLL_INTERVAL_MS);
    }

    cleanupPortFile(portFile);
    return {
      ok: false,
      reason:
        `GitPilot did not become ready within ${Math.round(
          START_TIMEOUT_MS / 1000
        )}s. See the GitPilot output channel for details.`,
    };
  }

  /** Terminate the server we started. A server we did not start is left alone. */
  stop(): void {
    const child = this._process;
    this._process = undefined;
    if (!child || child.exitCode !== null) {
      return;
    }
    this.output.appendLine("[GitPilot] Stopping managed server process");
    try {
      if (process.platform === "win32" && child.pid !== undefined) {
        cp.spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"]);
      } else {
        child.kill("SIGTERM");
      }
    } catch {
      /* the process is already gone */
    }
  }

  /** Port from the configured URL, when it names one. */
  private preferredPort(): number | undefined {
    try {
      const port = new URL(this.client.serverUrl).port;
      return port ? Number(port) : undefined;
    } catch {
      return undefined;
    }
  }

  /** The configured URL with its port replaced by the one actually bound. */
  private withPort(port: number): string {
    try {
      const url = new URL(this.client.serverUrl);
      url.port = String(port);
      return url.toString().replace(/\/+$/, "");
    } catch {
      return `http://127.0.0.1:${port}`;
    }
  }

  dispose(): void {
    this.stop();
  }
}

function readPortFile(file: string): number | undefined {
  try {
    const port = Number(fs.readFileSync(file, "utf-8").trim());
    return Number.isInteger(port) && port > 0 ? port : undefined;
  } catch {
    return undefined;
  }
}

function cleanupPortFile(file: string): void {
  try {
    fs.rmSync(path.dirname(file), { recursive: true, force: true });
  } catch {
    /* a leftover temp file is not worth reporting */
  }
}

function describeSpawnError(err: unknown, command: string): string {
  const code = (err as NodeJS.ErrnoException)?.code;
  if (code === "ENOENT") {
    return (
      `Could not find "${command}" on your PATH. Install it with ` +
      `"pip install gitpilot", or set gitpilot.serverCommand to its full path.`
    );
  }
  return `Failed to start "${command}": ${
    err instanceof Error ? err.message : String(err)
  }`;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
