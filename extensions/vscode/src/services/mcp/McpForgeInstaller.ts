/**
 * McpForgeInstaller — brings up MCP Context Forge from the editor.
 *
 * Attaching an MCP server needs a gateway to attach it to, and today getting
 * one means a terminal, a compose file, and an env file. That is a reasonable
 * ask of an operator and an unreasonable one of somebody who just wants their
 * agents to see a database schema. This service closes that gap.
 *
 * It runs in the extension host rather than the backend for two reasons: the
 * backend may itself be containerised and have no Docker socket, and progress
 * from a multi-minute image pull belongs in the editor's output channel where
 * the user is already looking.
 *
 * Two paths, chosen by what is actually on disk:
 *
 *  - **Repo checkout** — `docker-compose.mcp.yml` in the workspace. Uses the
 *    project's own compose stack, which builds Forge plus the reference
 *    servers from pinned upstreams.
 *  - **Package install** — no checkout, which is what `pip install gitcopilot`
 *    leaves you with. Runs the published Forge image directly.
 *
 * Nothing here is silent: every command and its output is written to the
 * GitPilot output channel, and every failure returns a sentence naming what to
 * do about it.
 */
import * as vscode from "vscode";
import * as cp from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as crypto from "crypto";

/** Forge's default port, and the one GitPilot's gateway config expects. */
export const FORGE_DEFAULT_PORT = 4444;

/** Container name, so a re-run adopts the existing container. */
const CONTAINER_NAME = "gitpilot-mcp-context-forge";

/** Published image used when there is no repo checkout to build from. */
const DEFAULT_FORGE_IMAGE = "ghcr.io/ibm/mcp-context-forge:latest";

const HEALTH_TIMEOUT_MS = 240_000;
const HEALTH_POLL_MS = 2_000;

export type ForgeInstallStage =
  | "checking"
  | "starting"
  | "waiting"
  | "registering"
  | "done"
  | "failed";

export interface ForgeInstallProgress {
  stage: ForgeInstallStage;
  message: string;
}

export type ForgeInstallResult =
  | { ok: true; gatewayUrl: string; mode: "compose" | "container" }
  | { ok: false; reason: string; hint?: string };

export interface PreflightReport {
  dockerAvailable: boolean;
  daemonRunning: boolean;
  composeAvailable: boolean;
  composeFile?: string;
  detail: string;
}

export class McpForgeInstaller {
  private running: Promise<ForgeInstallResult> | undefined;

  constructor(private readonly output: vscode.OutputChannel) {}

  /** True while an install is in flight, so the UI can disable its button. */
  get isRunning(): boolean {
    return this.running !== undefined;
  }

  /** The port Forge will be reachable on. */
  get port(): number {
    return vscode.workspace
      .getConfiguration("gitpilot")
      .get<number>("mcp.forgePort", FORGE_DEFAULT_PORT);
  }

  get gatewayUrl(): string {
    return `http://localhost:${this.port}`;
  }

  private get image(): string {
    return (
      vscode.workspace
        .getConfiguration("gitpilot")
        .get<string>("mcp.forgeImage", DEFAULT_FORGE_IMAGE)
        .trim() || DEFAULT_FORGE_IMAGE
    );
  }

  /**
   * What is available before anything is started.
   *
   * Reported rather than assumed: "Docker is not running" is a far more
   * useful message than a compose error five minutes into a build.
   */
  async preflight(): Promise<PreflightReport> {
    const dockerAvailable = (await this.run("docker", ["--version"])).code === 0;
    if (!dockerAvailable) {
      return {
        dockerAvailable: false,
        daemonRunning: false,
        composeAvailable: false,
        detail:
          "Docker is not installed or not on PATH. MCP Context Forge runs as a container.",
      };
    }

    const daemonRunning = (await this.run("docker", ["info"])).code === 0;
    if (!daemonRunning) {
      return {
        dockerAvailable: true,
        daemonRunning: false,
        composeAvailable: false,
        detail: "Docker is installed but the daemon is not running. Start Docker and retry.",
      };
    }

    const composeAvailable =
      (await this.run("docker", ["compose", "version"])).code === 0;
    const composeFile = this.findComposeFile();

    return {
      dockerAvailable: true,
      daemonRunning: true,
      composeAvailable,
      composeFile,
      detail: composeFile
        ? `Ready. Using the project's MCP stack at ${path.basename(composeFile)}.`
        : `Ready. Will run ${this.image}.`,
    };
  }

  /**
   * Start Forge and wait until it answers.
   *
   * Concurrent callers share one attempt — a settings page that retries while
   * an install is in flight must not start a second container.
   */
  async install(
    onProgress: (progress: ForgeInstallProgress) => void,
    token?: vscode.CancellationToken
  ): Promise<ForgeInstallResult> {
    if (this.running) {
      return this.running;
    }
    this.running = this.doInstall(onProgress, token).finally(() => {
      this.running = undefined;
    });
    return this.running;
  }

  private async doInstall(
    onProgress: (progress: ForgeInstallProgress) => void,
    token?: vscode.CancellationToken
  ): Promise<ForgeInstallResult> {
    onProgress({ stage: "checking", message: "Checking Docker…" });

    const report = await this.preflight();
    if (!report.dockerAvailable || !report.daemonRunning) {
      return {
        ok: false,
        reason: report.detail,
        hint: "https://docs.docker.com/get-docker/",
      };
    }

    // Something may already be listening — another window, an earlier run.
    if (await this.isHealthy()) {
      this.log(`Forge already answering on ${this.gatewayUrl}`);
      onProgress({ stage: "done", message: "MCP Context Forge is already running." });
      return { ok: true, gatewayUrl: this.gatewayUrl, mode: "container" };
    }

    const useCompose = Boolean(report.composeFile && report.composeAvailable);
    onProgress({
      stage: "starting",
      message: useCompose
        ? "Starting the project's MCP stack (this can take several minutes on first run)…"
        : `Starting ${this.image}…`,
    });

    const started = useCompose
      ? await this.startWithCompose(report.composeFile!)
      : await this.startContainer();

    if (!started.ok) {
      return started;
    }

    onProgress({ stage: "waiting", message: "Waiting for Context Forge to become ready…" });
    const ready = await this.waitForHealth(token);
    if (!ready.ok) {
      return ready;
    }

    return {
      ok: true,
      gatewayUrl: this.gatewayUrl,
      mode: useCompose ? "compose" : "container",
    };
  }

  // ── Start strategies ────────────────────────────────────────────────

  private async startWithCompose(composeFile: string): Promise<ForgeInstallResult> {
    const cwd = path.dirname(composeFile);
    const envFile = path.join(cwd, ".mcp.env");

    // The compose file refuses to start without MCP_AUTH_TOKEN — it is the
    // JWT signing secret, and a shared default would be a real weakness. Seed
    // a per-install random one rather than making the user invent it.
    if (!fs.existsSync(envFile)) {
      try {
        fs.writeFileSync(
          envFile,
          [
            "# Written by the GitPilot VS Code extension.",
            "# MCP_AUTH_TOKEN signs Forge's tokens. It is never sent anywhere.",
            `MCP_AUTH_TOKEN=${crypto.randomBytes(32).toString("hex")}`,
            "MCP_FORGE_ADMIN_EMAIL=admin@example.com",
            "",
          ].join("\n"),
          { mode: 0o600 }
        );
        this.log(`Seeded ${envFile} with a fresh signing secret`);
      } catch (err) {
        return {
          ok: false,
          reason: `Could not write ${envFile}: ${describe(err)}`,
        };
      }
    }

    const result = await this.run(
      "docker",
      [
        "compose",
        "-f",
        path.basename(composeFile),
        "--profile",
        "mcp",
        "up",
        "-d",
        "mcp-context-forge",
      ],
      { cwd, timeoutMs: HEALTH_TIMEOUT_MS }
    );

    if (result.code !== 0) {
      return {
        ok: false,
        reason:
          "docker compose could not start MCP Context Forge. " +
          "See the GitPilot output channel for the full log.",
      };
    }
    return { ok: true, gatewayUrl: this.gatewayUrl, mode: "compose" };
  }

  private async startContainer(): Promise<ForgeInstallResult> {
    // Adopt a stopped container from an earlier run rather than colliding
    // with its name.
    const existing = await this.run("docker", [
      "ps",
      "-aq",
      "-f",
      `name=^${CONTAINER_NAME}$`,
    ]);
    if (existing.code === 0 && existing.stdout.trim()) {
      this.log(`Reusing existing container ${CONTAINER_NAME}`);
      const restarted = await this.run("docker", ["start", CONTAINER_NAME]);
      if (restarted.code === 0) {
        return { ok: true, gatewayUrl: this.gatewayUrl, mode: "container" };
      }
      // Fall through and recreate: a container that will not start is worse
      // than no container.
      await this.run("docker", ["rm", "-f", CONTAINER_NAME]);
    }

    const secret = crypto.randomBytes(32).toString("hex");
    const result = await this.run(
      "docker",
      [
        "run",
        "-d",
        "--name",
        CONTAINER_NAME,
        "--restart",
        "unless-stopped",
        "-p",
        `${this.port}:4444`,
        "-e",
        "HOST=0.0.0.0",
        "-e",
        "PORT=4444",
        // Local install over plain HTTP: a Secure cookie would be dropped by
        // the browser and look like "login succeeds then bounces".
        "-e",
        "AUTH_REQUIRED=false",
        "-e",
        `JWT_SECRET_KEY=${secret}`,
        "-e",
        "MCPGATEWAY_UI_ENABLED=true",
        "-e",
        "MCPGATEWAY_ADMIN_API_ENABLED=true",
        "-e",
        "SECURE_COOKIES=false",
        "--add-host",
        "host.docker.internal:host-gateway",
        this.image,
      ],
      { timeoutMs: HEALTH_TIMEOUT_MS }
    );

    if (result.code !== 0) {
      const notFound = /manifest unknown|not found|pull access denied/i.test(
        result.stderr
      );
      return {
        ok: false,
        reason: notFound
          ? `Could not pull ${this.image}. Set gitpilot.mcp.forgeImage to an image you can reach.`
          : "docker run could not start MCP Context Forge. See the GitPilot output channel.",
      };
    }
    return { ok: true, gatewayUrl: this.gatewayUrl, mode: "container" };
  }

  // ── Health ──────────────────────────────────────────────────────────

  /** Single fast probe of Forge's health endpoint. */
  async isHealthy(timeoutMs = 2500): Promise<boolean> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const resp = await fetch(`${this.gatewayUrl}/health`, {
        signal: controller.signal,
      });
      return resp.ok;
    } catch {
      return false;
    } finally {
      clearTimeout(timer);
    }
  }

  private async waitForHealth(
    token?: vscode.CancellationToken
  ): Promise<ForgeInstallResult> {
    const deadline = Date.now() + HEALTH_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (token?.isCancellationRequested) {
        return { ok: false, reason: "Cancelled." };
      }
      if (await this.isHealthy()) {
        this.log(`Context Forge is ready at ${this.gatewayUrl}`);
        return { ok: true, gatewayUrl: this.gatewayUrl, mode: "container" };
      }
      await delay(HEALTH_POLL_MS);
    }
    return {
      ok: false,
      reason:
        `MCP Context Forge did not become ready within ` +
        `${Math.round(HEALTH_TIMEOUT_MS / 1000)}s. ` +
        `Check "docker logs ${CONTAINER_NAME}".`,
    };
  }

  /** Stop the container this extension started. Compose stacks are left alone. */
  async stop(): Promise<void> {
    await this.run("docker", ["stop", CONTAINER_NAME]);
  }

  // ── Process plumbing ────────────────────────────────────────────────

  /** The compose file in the open workspace, if this is a repo checkout. */
  private findComposeFile(): string | undefined {
    for (const folder of vscode.workspace.workspaceFolders || []) {
      const candidate = path.join(folder.uri.fsPath, "docker-compose.mcp.yml");
      if (fs.existsSync(candidate)) {
        return candidate;
      }
    }
    return undefined;
  }

  private log(line: string): void {
    this.output.appendLine(`[GitPilot MCP] ${line}`);
  }

  private run(
    command: string,
    args: string[],
    opts: { cwd?: string; timeoutMs?: number } = {}
  ): Promise<{ code: number; stdout: string; stderr: string }> {
    return new Promise((resolve) => {
      this.log(`$ ${command} ${args.join(" ")}`);

      let child: cp.ChildProcess;
      try {
        child = cp.spawn(command, args, {
          cwd: opts.cwd,
          shell: process.platform === "win32",
          env: { ...process.env },
        });
      } catch (err) {
        this.log(`failed to spawn: ${describe(err)}`);
        resolve({ code: -1, stdout: "", stderr: describe(err) });
        return;
      }

      let stdout = "";
      let stderr = "";
      let settled = false;

      const finish = (code: number) => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timer);
        resolve({ code, stdout, stderr });
      };

      const timer = setTimeout(() => {
        this.log(`timed out after ${opts.timeoutMs}ms; killing`);
        child.kill();
        finish(-1);
      }, opts.timeoutMs ?? 60_000);

      child.stdout?.on("data", (chunk: Buffer) => {
        const text = chunk.toString();
        stdout += text;
        this.output.append(text);
      });
      child.stderr?.on("data", (chunk: Buffer) => {
        const text = chunk.toString();
        stderr += text;
        this.output.append(text);
      });

      child.on("error", (err) => {
        this.log(`error: ${err.message}`);
        stderr += err.message;
        finish(-1);
      });
      child.on("close", (code) => finish(code ?? -1));
    });
  }
}

function describe(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
