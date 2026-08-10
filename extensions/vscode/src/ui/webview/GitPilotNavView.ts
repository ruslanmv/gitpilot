/**
 * GitPilotNavView — the sidebar, which is navigation and nothing else.
 *
 * The sidebar used to contain a complete second chat: a message list, a
 * composer, a provider picker and a status card. That duplicated the GitPilot
 * Chat tab, so the same information appeared twice and the same action could
 * be started from two places that behaved slightly differently.
 *
 * Three zones, one job each:
 *
 *   sidebar  →  where do I want to go?      (this file)
 *   editor   →  what am I doing?            (GitPilotHomePanel, then chat)
 *   chat tab →  what is happening now?      (GitPilotPanel)
 *
 * So this view holds one status line, New Task, recent tasks and Settings.
 * Nothing else: a second composer or a second copy of the quick actions only
 * ever raised the question of which one was the real one. Picking a task
 * opens GitPilot showing it — one action, not select-then-open.
 */
import * as vscode from "vscode";
import * as fs from "fs";
import * as path from "path";

import { SessionClient, SessionListItem } from "../../api/sessionClient";
import { GitPilotApiClient } from "../../api/client";
import { StateStore } from "../../core/stateStore";
import { GitPilotServerController } from "../../services/server/GitPilotServerController";

/** What the sidebar renders. Everything else stays in the extension host. */
interface NavState {
  connected: boolean;
  connecting: boolean;
  canStartLocally: boolean;
  detail?: string;
  model?: string;
  repo?: string;
  branch?: string;
  sessions: SessionListItem[];
  activeSessionId?: string;
  /** True while the extension is retrying on its own. */
  retrying: boolean;
}

export interface NavViewDeps {
  extensionUri: vscode.Uri;
  client: GitPilotApiClient;
  sessionClient: SessionClient;
  stateStore: StateStore;
  serverController: GitPilotServerController;
}

export class GitPilotNavView implements vscode.WebviewViewProvider {
  public static readonly viewType = "gitpilot.navView";

  private view: vscode.WebviewView | undefined;
  private sessions: SessionListItem[] = [];
  private connecting = false;
  private detail: string | undefined;

  constructor(private readonly deps: NavViewDeps) {
    // The sidebar mirrors state it does not own, so it re-renders whenever
    // the store changes rather than polling.
    deps.stateStore.onDidChangeState(() => this.render());
    deps.client.onStateChange(() => {
      this.connecting = deps.client.state === "connecting";
      this.render();
    });
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.deps.extensionUri, "out")],
    };
    view.webview.html = this.html(view.webview);
    view.webview.onDidReceiveMessage((msg) => void this.onMessage(msg));

    // Refreshing only while visible keeps a collapsed sidebar off the wire.
    view.onDidChangeVisibility(() => {
      if (view.visible) {
        void this.refresh();
      }
    });
    void this.refresh();
  }

  /** Re-read sessions from the backend and repaint. */
  async refresh(): Promise<void> {
    if (this.deps.client.isConnected) {
      try {
        this.sessions = await this.deps.sessionClient.listSessions();
        this.detail = undefined;
      } catch (err) {
        // A session list that cannot be fetched is not worth an error
        // dialog; the sidebar simply shows what it last knew.
        this.detail = err instanceof Error ? err.message : String(err);
      }
    } else {
      this.sessions = [];
    }
    this.render();
  }

  private render(): void {
    if (!this.view) {
      return;
    }
    const s = this.deps.stateStore.state;
    const connected = this.deps.client.isConnected;

    const state: NavState = {
      connected,
      connecting: this.connecting,
      canStartLocally: this.deps.serverController.isLocalUrl(),
      detail: connected ? this.detail : this.offlineReason(),
      model: s.provider?.model,
      repo: s.projectContextSummary?.repoName || s.workspace?.folderName,
      branch: s.workspace?.git?.branch,
      sessions: this.sessions,
      activeSessionId: s.session?.sessionId,
      retrying: this.deps.client.isRetrying,
    };

    void this.view.webview.postMessage({ type: "NAV_STATE", state });
  }

  /**
   * Why the server is not answering, in the words that match the remedy.
   *
   * "Could not reach" was shown for both halves of a distinction that
   * matters: a refused connection means start the server, while a timeout
   * means the server is there and busy, and pressing Start server would only
   * fail on a port already in use.
   */
  private offlineReason(): string {
    const probe = this.deps.client.lastProbe;
    const url = this.deps.client.serverUrl;

    if (probe?.outcome === "timeout") {
      return `${url} is not answering yet — still trying`;
    }
    return `Could not reach ${url}`;
  }

  private async onMessage(msg: { type: string; [key: string]: unknown }): Promise<void> {
    switch (msg.type) {
      case "NAV_READY":
        await this.refresh();
        return;

      case "OPEN_SETTINGS":
        await vscode.commands.executeCommand("gitpilot.openSettings");
        return;

      case "QUICK_ACTION":
        // The chat is where a quick action's answer lands, so go there first.
        await vscode.commands.executeCommand("gitpilot.openChatTab");
        await vscode.commands.executeCommand(`gitpilot.${msg.action as string}`);
        return;

      case "SELECT_MODEL":
        await vscode.commands.executeCommand("gitpilot.selectModel");
        await this.refresh();
        return;

      /**
       * New Task is a new task.
       *
       * It used to only open the tab, on the theory that a session should be
       * created once there was something to create it for. In practice that
       * meant clicking New Task landed you in the previous conversation, with
       * its transcript, its plan and its changed files still on screen — so
       * the one control named for starting fresh was the one that never did.
       */
      case "NEW_TASK":
        await vscode.commands.executeCommand("gitpilot.newSession");
        await this.refresh();
        return;


      /**
       * Restore the session *and* reveal the chat. Requiring a second click
       * on "Open Chat" was the friction this replaces.
       */
      case "OPEN_SESSION":
        await vscode.commands.executeCommand(
          "gitpilot.loadSession",
          msg.sessionId as string
        );
        await vscode.commands.executeCommand("gitpilot.openChat");
        await this.refresh();
        return;

      case "SESSION_MENU":
        await this.sessionMenu(msg.sessionId as string);
        return;

      case "VIEW_ALL_SESSIONS":
        await vscode.commands.executeCommand("gitpilot.sessionsView.focus");
        return;

      /**
       * Actually start it.
       *
       * This button opened Settings, which is a page about configuration and
       * has no way to start anything either — so the one control offered to a
       * user whose server is down did not start the server.
       */
      case "START_SERVER":
        await this.startServer();
        return;

      case "RECONNECT":
        await vscode.commands.executeCommand("gitpilot.reconnect");
        await this.refresh();
        return;

      case "STOP_WAITING":
        this.deps.client.stopAutoReconnect();
        this.render();
        return;

      case "OPEN_DOCS":
        await vscode.env.openExternal(
          vscode.Uri.parse("https://github.com/ruslanmv/gitpilot")
        );
        return;
    }
  }

  /** Start the local backend, then connect to it. */
  private async startServer(): Promise<void> {
    const result = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: "Starting the GitPilot server\u2026",
        cancellable: true,
      },
      (_progress, token) => this.deps.serverController.start(token)
    );

    if (!result.ok) {
      vscode.window.showErrorMessage(
        `Could not start the GitPilot server. ${result.reason ?? ""}`.trim()
      );
      this.render();
      return;
    }

    // A server that has just booted is the slow case this whole procedure
    // exists for, so connect with the full escalating deadline.
    await this.deps.client.connect();
    await this.refresh();
  }

  /**
   * The row's overflow menu.
   *
   * Kept out of the row itself: permanently visible per-row controls are what
   * turns a readable list into a cluttered one.
   */
  private async sessionMenu(sessionId: string): Promise<void> {
    const session = this.sessions.find((s) => s.id === sessionId);
    const choice = await vscode.window.showQuickPick(
      [
        { label: "$(comment-discussion) Open", action: "open" },
        { label: "$(trash) Delete", action: "delete" },
      ],
      { title: session?.name || "Session" }
    );
    if (!choice) {
      return;
    }

    if (choice.action === "open") {
      await vscode.commands.executeCommand("gitpilot.loadSession", sessionId);
      await vscode.commands.executeCommand("gitpilot.openChat");
    } else if (choice.action === "delete") {
      const confirm = await vscode.window.showWarningMessage(
        `Delete "${session?.name || sessionId}"? This cannot be undone.`,
        { modal: true },
        "Delete"
      );
      if (confirm === "Delete") {
        try {
          await this.deps.sessionClient.deleteSession(sessionId);
        } catch (err) {
          vscode.window.showErrorMessage(
            `Could not delete the session: ${
              err instanceof Error ? err.message : String(err)
            }`
          );
        }
      }
    }
    await this.refresh();
  }

  private html(webview: vscode.Webview): string {
    const nonce = getNonce();
    const csp = [
      "default-src 'none'",
      `style-src 'nonce-${nonce}'`,
      `script-src 'nonce-${nonce}'`,
      `img-src ${webview.cspSource} https: data:`,
    ].join("; ");

    const templatePath = path.join(
      this.deps.extensionUri.fsPath,
      "out",
      "ui",
      "webview",
      "gitpilotNavTemplate.html"
    );

    let html: string;
    try {
      html = fs.readFileSync(templatePath, "utf-8");
    } catch {
      return "<h3>Could not load the GitPilot sidebar.</h3>";
    }
    return html.replace(/__CSP__/g, csp).replace(/__NONCE__/g, nonce);
  }
}

function getNonce(): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let out = "";
  for (let i = 0; i < 32; i++) {
    out += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return out;
}
