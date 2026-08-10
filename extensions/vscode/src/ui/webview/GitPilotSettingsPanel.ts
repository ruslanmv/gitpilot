import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";

import { GitPilotApiClient } from "../../api/client";
import { SettingsClient, SettingsData } from "../../api/settingsClient";
import { GitPilotServerController } from "../../services/server/GitPilotServerController";
import { McpClient, McpServer } from "../../api/mcpClient";
import { McpForgeInstaller } from "../../services/mcp/McpForgeInstaller";
import {
  ProviderConfigInput,
  ProviderName,
  ProviderOverviewEntry,
  ProviderSetupData,
  ProviderSettingsMessage,
  SanitizedProviderConfig,
  ServerConnectionState,
} from "../../core/types";
import {
  inferOllaBridgeMode,
  OLLABRIDGE_CLOUD_URL,
  OLLABRIDGE_LINK_URL,
  OLLABRIDGE_LOCAL_URL,
  PROVIDER_CATALOG,
  PROVIDER_ORDER,
} from "./providerCatalog";

export interface SettingsPanelDeps {
  extensionUri: vscode.Uri;
  client: GitPilotApiClient;
  settingsClient: SettingsClient;
  serverController: GitPilotServerController;
  mcpClient: McpClient;
  forgeInstaller: McpForgeInstaller;
}

/**
 * GitPilotSettingsPanel — the branded settings tab.
 *
 * General/Agent/Editor sections read and write VS Code configuration. The AI
 * Providers section is different: provider configuration lives on the GitPilot
 * backend, so this panel is a client of that API rather than a settings store.
 *
 * Two rules shape the host/webview split:
 *
 *  - Every network call happens here, never in the webview. The webview has no
 *    server URL, no token, and no fetch.
 *  - Secrets travel one way. Keys read from the backend are reduced to a
 *    boolean and a masked tail before they cross into the webview; keys typed
 *    by the user pass straight through to the backend and are never echoed.
 *
 * Only one instance is kept alive at a time (singleton pattern).
 */
export class GitPilotSettingsPanel {
  public static readonly viewType = "gitpilot.settingsPanel";
  private static _instance: GitPilotSettingsPanel | undefined;

  private readonly _panel: vscode.WebviewPanel;
  private readonly _extensionUri: vscode.Uri;
  private readonly _client: GitPilotApiClient;
  private readonly _settings: SettingsClient;
  private readonly _server: GitPilotServerController;
  private readonly _mcp: McpClient;
  private readonly _forge: McpForgeInstaller;
  private _disposed = false;

  private constructor(panel: vscode.WebviewPanel, deps: SettingsPanelDeps) {
    this._panel = panel;
    this._extensionUri = deps.extensionUri;
    this._client = deps.client;
    this._settings = deps.settingsClient;
    this._server = deps.serverController;
    this._mcp = deps.mcpClient;
    this._forge = deps.forgeInstaller;

    panel.onDidDispose(() => {
      this._disposed = true;
      GitPilotSettingsPanel._instance = undefined;
    });

    panel.webview.onDidReceiveMessage((msg) => void this._onMessage(msg));
    void this._loadHtml();
  }

  /** Show or reveal the settings tab. */
  public static open(deps: SettingsPanelDeps): void {
    if (GitPilotSettingsPanel._instance) {
      GitPilotSettingsPanel._instance._panel.reveal(vscode.ViewColumn.One);
      return;
    }

    const panel = vscode.window.createWebviewPanel(
      GitPilotSettingsPanel.viewType,
      "GitPilot Settings",
      vscode.ViewColumn.One,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.joinPath(deps.extensionUri, "out")],
      }
    );

    GitPilotSettingsPanel._instance = new GitPilotSettingsPanel(panel, deps);
  }

  // ── Webview plumbing ────────────────────────────────────────────────

  private async _loadHtml(): Promise<void> {
    const nonce = this._getNonce();
    const csp = [
      "default-src 'none'",
      `style-src 'nonce-${nonce}'`,
      `script-src 'nonce-${nonce}'`,
      `img-src ${this._panel.webview.cspSource} https: data:`,
    ].join("; ");

    // Read template from out/ (copied by the build script)
    const templatePath = path.join(
      this._extensionUri.fsPath,
      "out",
      "ui",
      "webview",
      "gitpilotSettingsTemplate.html"
    );

    let html: string;
    try {
      html = fs.readFileSync(templatePath, "utf-8");
    } catch {
      this._panel.webview.html = "<h2>Could not load settings template.</h2>";
      return;
    }

    // Read version from package.json
    let version = "0.0.0";
    try {
      const pkgPath = path.join(this._extensionUri.fsPath, "package.json");
      const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf-8"));
      version = pkg.version || version;
    } catch {
      // ignore
    }

    html = html
      .replace(/__CSP__/g, csp)
      .replace(/__NONCE__/g, nonce)
      .replace(/__VERSION__/g, version)
      .replace(/__PROVIDER_CATALOG__/g, JSON.stringify(PROVIDER_CATALOG))
      .replace(/__PROVIDER_ORDER__/g, JSON.stringify(PROVIDER_ORDER))
      .replace(
        /__OLLABRIDGE_DEFAULTS__/g,
        JSON.stringify({
          cloud: OLLABRIDGE_CLOUD_URL,
          local: OLLABRIDGE_LOCAL_URL,
          link: OLLABRIDGE_LINK_URL,
        })
      );

    this._panel.webview.html = html;
  }

  private post(message: Record<string, unknown>): void {
    if (this._disposed) {
      return;
    }
    void this._panel.webview.postMessage(message);
  }

  private async _onMessage(
    msg: (ProviderSettingsMessage | { type: string; payload?: Record<string, unknown> }) & {
      requestId?: number;
    }
  ): Promise<void> {
    switch (msg.type) {
      // ── VS Code-side settings (General / Agent / Editor) ──
      case "GET_SETTINGS":
        this._sendCurrentSettings();
        return;

      case "SAVE_SETTINGS": {
        const payload = (msg as { payload?: Record<string, unknown> }).payload;
        if (payload) {
          await this._applySettings(payload);
          this.post({ type: "SETTINGS_SAVED" });
        }
        return;
      }

      // ── AI Providers ──
      case "LOAD_PROVIDER_OVERVIEW":
        await this.loadProviderSetup();
        return;

      case "RECONNECT_SERVER":
        this._settings.clearModelCache();
        await this.loadProviderSetup({ forceReconnect: true });
        return;

      case "START_LOCAL_SERVER":
        await this.startLocalServer();
        return;

      case "CHANGE_SERVER_URL":
        await this.changeServerUrl();
        return;

      case "LOAD_PROVIDER_MODELS":
        await this.loadModels(
          (msg as any).provider,
          (msg as any).requestId,
          Boolean((msg as any).force)
        );
        return;

      case "TEST_PROVIDER":
        await this.testProvider(
          (msg as any).provider,
          (msg as any).requestId,
          (msg as any).config || {}
        );
        return;

      case "SAVE_AND_ACTIVATE_PROVIDER":
        await this.saveProvider(
          (msg as any).provider,
          (msg as any).requestId,
          (msg as any).config || {}
        );
        return;

      // ── MCP servers ──
      case "LOAD_MCP_OVERVIEW":
        await this.loadMcpOverview();
        return;

      case "SET_MCP_SERVER_ENABLED":
        await this.setMcpServerEnabled(
          (msg as any).serverId,
          Boolean((msg as any).enabled),
          (msg as any).requestId
        );
        return;

      case "SET_MCP_TOOL_ENABLED":
        await this.setMcpToolEnabled(
          (msg as any).serverId,
          (msg as any).tool,
          Boolean((msg as any).enabled),
          (msg as any).requestId
        );
        return;

      case "TEST_MCP_SERVER":
        await this.testMcpServer((msg as any).serverId, (msg as any).requestId);
        return;

      case "UNINSTALL_MCP_SERVER":
        await this.uninstallMcpServer((msg as any).serverId, (msg as any).requestId);
        return;

      case "INSTALL_MCP_SERVER":
        await this.installMcpServer(
          (msg as any).entryId,
          (msg as any).source,
          (msg as any).requestId
        );
        return;

      case "SEARCH_MCP_REGISTRY":
        await this.searchMcpRegistry((msg as any).query, (msg as any).requestId);
        return;

      case "ADD_CUSTOM_MCP_SERVER":
        await this.addCustomMcpServer((msg as any).requestId);
        return;

      case "INSTALL_MCP_FORGE":
        await this.installMcpForge();
        return;

      case "SYNC_MCP_GATEWAY":
        await this.syncMcpGateway((msg as any).requestId);
        return;

      case "CONFIGURE_MCP_GATEWAY":
        await vscode.commands.executeCommand("gitpilot.configureMcpGateway");
        await this.loadMcpOverview();
        return;

      case "LOAD_TOPOLOGIES":
        await this.loadTopologies();
        return;

      case "SET_TOPOLOGY":
        await this.setTopology(
          (msg as any).topology,
          (msg as any).requestId
        );
        return;

      case "REMOVE_PROVIDER_KEY":
        await this.removeProviderKey(
          (msg as any).provider,
          (msg as any).requestId
        );
        return;

      case "START_OLLABRIDGE_LOGIN":
        await this.startOllaBridgeLogin((msg as any).baseUrl);
        return;

      case "PAIR_OLLABRIDGE":
        await this.pairOllaBridge(
          (msg as any).requestId,
          (msg as any).code,
          (msg as any).baseUrl
        );
        return;

      case "SIGN_OUT_OLLABRIDGE":
        await this.signOutOllaBridge((msg as any).requestId);
        return;

      // ── Escape hatches ──
      case "COPY_DIAGNOSTICS":
        await this.copyDiagnostics();
        return;

      case "OPEN_WEB_ADMIN":
        await vscode.env.openExternal(vscode.Uri.parse(this._client.serverUrl));
        return;

      case "OPEN_EXTERNAL": {
        const url = (msg as any).url;
        if (typeof url === "string" && /^https?:\/\//i.test(url)) {
          await vscode.env.openExternal(vscode.Uri.parse(url));
        }
        return;
      }
    }
  }

  // ── Provider setup ──────────────────────────────────────────────────

  /**
   * Load the provider overview, reconnecting first if the cached connection
   * state says we are down.
   *
   * The cached flag is not evidence: it goes stale the moment the user starts
   * the server in a terminal. So an apparently-disconnected client gets one
   * real health probe before the page reports trouble, and the page renders
   * either way — there is no modal, and no dead end.
   */
  private async loadProviderSetup(
    opts: { forceReconnect?: boolean } = {}
  ): Promise<void> {
    this.postServerState("connecting");

    const connected =
      !opts.forceReconnect && this._client.isConnected
        ? await this._client.health()
        : await this._client.connect();

    if (!connected) {
      this.postServerState("offline", {
        detail: `Could not reach ${this._client.serverUrl}.`,
      });
      return;
    }

    try {
      const settings = await this._settings.getSettings();
      this.postServerState("online");
      this.post({
        type: "PROVIDER_SETUP_DATA",
        data: this.toSetupData(settings),
      });
    } catch (err) {
      this.postServerState("offline", { detail: describeError(err) });
    }
  }

  private postServerState(
    state: ServerConnectionState,
    extra: { detail?: string } = {}
  ): void {
    this.post({
      type: "SERVER_STATE",
      state,
      serverUrl: this._client.serverUrl,
      canStartLocally: this._server.isLocalUrl(),
      command: this._server.commandLine,
      ...extra,
    });
  }

  private async startLocalServer(): Promise<void> {
    this.postServerState("starting");
    const result = await this._server.start();
    if (!result.ok) {
      this.postServerState("offline", { detail: result.reason });
      return;
    }
    // start() may have followed GitPilot onto a different port; persist it so
    // the rest of the extension and the next session agree.
    await vscode.workspace
      .getConfiguration("gitpilot")
      .update(
        "serverUrl",
        result.serverUrl,
        vscode.ConfigurationTarget.Global
      );
    this._settings.clearModelCache();
    await this.loadProviderSetup({ forceReconnect: true });
  }

  private async changeServerUrl(): Promise<void> {
    const url = await vscode.window.showInputBox({
      title: "GitPilot server URL",
      prompt: "Where is the GitPilot backend running?",
      value: this._client.serverUrl,
      validateInput: (val) => {
        try {
          new URL(val);
          return null;
        } catch {
          return "Please enter a valid URL";
        }
      },
    });
    if (!url) {
      return;
    }

    this._client.setServerUrl(url);
    this._settings.clearModelCache();
    await vscode.workspace
      .getConfiguration("gitpilot")
      .update("serverUrl", url, vscode.ConfigurationTarget.Global);
    this._sendCurrentSettings();
    await this.loadProviderSetup({ forceReconnect: true });
  }

  private async loadModels(
    provider: ProviderName,
    requestId: number,
    force: boolean
  ): Promise<void> {
    try {
      const result = await this._settings.listModelsCached(provider, force);
      this.post({
        type: "PROVIDER_MODELS_RESULT",
        requestId,
        provider,
        models: result.models || [],
        error: result.error,
      });
    } catch (err) {
      this.post({
        type: "PROVIDER_MODELS_RESULT",
        requestId,
        provider,
        models: [],
        error: describeError(err),
      });
    }
  }

  private async testProvider(
    provider: ProviderName,
    requestId: number,
    config: ProviderConfigInput
  ): Promise<void> {
    try {
      const result = await this._settings.testProvider({
        provider,
        [provider]: this.toBackendConfig(provider, config),
      } as any);

      const ok = result.health === "ok" || (result.configured && !result.warning);
      this.post({
        type: "PROVIDER_TEST_RESULT",
        requestId,
        provider,
        ok,
        message: ok
          ? `${PROVIDER_CATALOG[provider].label} responded${
              result.model ? ` using ${result.model}` : ""
            }.`
          : result.warning || "The provider did not respond successfully.",
      });
    } catch (err) {
      this.post({
        type: "PROVIDER_TEST_RESULT",
        requestId,
        provider,
        ok: false,
        message: describeProviderError(provider, err),
      });
    }
  }

  /**
   * Persist a provider's configuration, then make it the active one.
   *
   * The order is not incidental: activating first would leave GitPilot
   * pointing at a provider whose key has not landed yet, and any request in
   * that window fails.
   */
  private async saveProvider(
    provider: ProviderName,
    requestId: number,
    config: ProviderConfigInput
  ): Promise<void> {
    const backendConfig = this.toBackendConfig(provider, config);

    if (backendConfig.api_key && !(await this.confirmKeyTransport())) {
      this.post({
        type: "PROVIDER_SAVE_RESULT",
        requestId,
        provider,
        ok: false,
        message: "Save cancelled.",
      });
      return;
    }

    try {
      await this._settings.updateProviderConfig(provider, backendConfig as any);
      await this._settings.setProvider(provider);
      const settings = await this._settings.getSettings();

      this.post({
        type: "PROVIDER_SAVE_RESULT",
        requestId,
        provider,
        ok: true,
        message: `${PROVIDER_CATALOG[provider].label} is now the active provider.`,
        data: this.toSetupData(settings),
      });

      await vscode.commands.executeCommand("gitpilot.refreshStatus");
    } catch (err) {
      this.post({
        type: "PROVIDER_SAVE_RESULT",
        requestId,
        provider,
        ok: false,
        message: describeProviderError(provider, err),
      });
    }
  }

  private async removeProviderKey(
    provider: ProviderName,
    requestId: number
  ): Promise<void> {
    const confirm = await vscode.window.showWarningMessage(
      `Remove the stored ${PROVIDER_CATALOG[provider].label} API key?`,
      { modal: true },
      "Remove key"
    );
    if (confirm !== "Remove key") {
      return;
    }

    try {
      await this._settings.updateProviderConfig(provider, {
        api_key: "",
      } as any);
      const settings = await this._settings.getSettings();
      this.post({
        type: "PROVIDER_SAVE_RESULT",
        requestId,
        provider,
        ok: true,
        message: "API key removed.",
        data: this.toSetupData(settings),
      });
    } catch (err) {
      this.post({
        type: "PROVIDER_SAVE_RESULT",
        requestId,
        provider,
        ok: false,
        message: describeProviderError(provider, err),
      });
    }
  }

  // ── MCP servers ─────────────────────────────────────────────────────

  /**
   * Load everything the MCP overview renders: gateway status, attached
   * servers, and the bundled catalogue.
   *
   * The three are fetched together because the page is meaningless without
   * all of them — "no servers" and "cannot reach the backend" must not look
   * the same.
   */
  private async loadMcpOverview(): Promise<void> {
    const connected = this._client.isConnected
      ? await this._client.health()
      : await this._client.connect();

    if (!connected) {
      this.post({
        type: "MCP_DATA",
        offline: true,
        serverUrl: this._client.serverUrl,
      });
      return;
    }

    try {
      // Status probes the gateway and can be slow; the other two are local
      // reads. Failing status must not hide the server list.
      const [servers, catalog, status] = await Promise.all([
        this._mcp.listServers(),
        this._mcp.listCatalog().catch(() => []),
        this._mcp.status().catch(() => undefined),
      ]);

      this.post({
        type: "MCP_DATA",
        offline: false,
        servers: servers.map(summariseServer),
        catalog,
        status,
        forgeRunning: await this._forge.isHealthy(),
        forgeInstalling: this._forge.isRunning,
      });
    } catch (err) {
      this.post({
        type: "MCP_DATA",
        offline: true,
        serverUrl: this._client.serverUrl,
        detail: describeError(err),
      });
    }
  }

  /**
   * Enable or disable a whole server.
   *
   * This is the switch that changes what the agents can do, so it always
   * reloads afterwards: the page must show what the backend now holds, not
   * what the click optimistically implied.
   */
  private async setMcpServerEnabled(
    serverId: string,
    enabled: boolean,
    requestId: number
  ): Promise<void> {
    try {
      await this._mcp.setServerEnabled(serverId, enabled);
      this.post({
        type: "MCP_ACTION_RESULT",
        requestId,
        ok: true,
        message: enabled
          ? `${serverId} enabled. Its tools are now available to the agents.`
          : `${serverId} disabled.`,
      });
      await this.loadMcpOverview();
    } catch (err) {
      this.post({
        type: "MCP_ACTION_RESULT",
        requestId,
        ok: false,
        message: describeError(err),
      });
    }
  }

  /**
   * Toggle one tool.
   *
   * Enabling a destructive tool is confirmed first. The risk classification
   * is the backend's — it defaults high-risk tools off — and quietly
   * reversing that from a checkbox would defeat the point.
   */
  private async setMcpToolEnabled(
    serverId: string,
    tool: string,
    enabled: boolean,
    requestId: number
  ): Promise<void> {
    if (enabled && isDestructiveName(tool)) {
      const confirm = await vscode.window.showWarningMessage(
        `"${tool}" can destroy data. Allow the agents to call it?`,
        { modal: true },
        "Enable anyway"
      );
      if (confirm !== "Enable anyway") {
        this.post({
          type: "MCP_ACTION_RESULT",
          requestId,
          ok: false,
          message: "Left disabled.",
        });
        await this.loadMcpOverview();
        return;
      }
    }

    try {
      await this._mcp.setToolEnabled(serverId, tool, enabled);
      this.post({
        type: "MCP_ACTION_RESULT",
        requestId,
        ok: true,
        message: `${tool} ${enabled ? "enabled" : "disabled"}.`,
      });
      await this.loadMcpOverview();
    } catch (err) {
      this.post({
        type: "MCP_ACTION_RESULT",
        requestId,
        ok: false,
        message: describeError(err),
      });
    }
  }

  private async testMcpServer(serverId: string, requestId: number): Promise<void> {
    try {
      const result = await this._mcp.testServer(serverId);
      const reachable = result.ok ?? result.reachable ?? false;
      const toolCount = Array.isArray(result.tools) ? result.tools.length : undefined;
      this.post({
        type: "MCP_ACTION_RESULT",
        requestId,
        ok: Boolean(reachable),
        message: reachable
          ? `${serverId} responded${toolCount != null ? ` with ${toolCount} tool(s)` : ""}.`
          : String(result.detail || "The server did not respond."),
      });
    } catch (err) {
      this.post({
        type: "MCP_ACTION_RESULT",
        requestId,
        ok: false,
        message: describeError(err),
      });
    }
  }

  private async uninstallMcpServer(
    serverId: string,
    requestId: number
  ): Promise<void> {
    const confirm = await vscode.window.showWarningMessage(
      `Detach ${serverId}? Its tools will no longer be available to the agents.`,
      { modal: true },
      "Detach"
    );
    if (confirm !== "Detach") {
      return;
    }

    try {
      await this._mcp.uninstall(serverId);
      this.post({
        type: "MCP_ACTION_RESULT",
        requestId,
        ok: true,
        message: `${serverId} detached.`,
      });
      await this.loadMcpOverview();
    } catch (err) {
      this.post({
        type: "MCP_ACTION_RESULT",
        requestId,
        ok: false,
        message: describeError(err),
      });
    }
  }

  /**
   * Attach a server from the bundled catalogue or a registry.
   *
   * It arrives disabled, and the message says so — installing a capability
   * and granting it are deliberately two steps.
   */
  private async installMcpServer(
    entryId: string,
    source: string,
    requestId: number
  ): Promise<void> {
    try {
      if (source === "registry") {
        await this._mcp.installFromRegistry(entryId);
      } else {
        await this._mcp.installFromCatalog(entryId);
      }
      this.post({
        type: "MCP_ACTION_RESULT",
        requestId,
        ok: true,
        message: `${entryId} attached, and left disabled. Enable it to give the agents its tools.`,
      });
      await this.loadMcpOverview();
    } catch (err) {
      this.post({
        type: "MCP_ACTION_RESULT",
        requestId,
        ok: false,
        message: describeError(err),
      });
    }
  }

  private async searchMcpRegistry(
    query: string,
    requestId: number
  ): Promise<void> {
    try {
      const result = await this._mcp.searchRegistry(query);
      this.post({
        type: "MCP_REGISTRY_RESULT",
        requestId,
        items: result.items || [],
        error: result.error,
        registryUrl: result.registry_url,
      });
    } catch (err) {
      this.post({
        type: "MCP_REGISTRY_RESULT",
        requestId,
        items: [],
        error: describeError(err),
      });
    }
  }

  /** Attach a server by hand, prompting for the pieces the backend needs. */
  private async addCustomMcpServer(requestId: number): Promise<void> {
    const id = await vscode.window.showInputBox({
      title: "Add an MCP server (1 of 3)",
      prompt: "Server id — how it will be listed",
      placeHolder: "my-mcp-server",
      validateInput: (v) => (v.trim() ? null : "An id is required"),
    });
    if (!id) {
      return;
    }

    const endpoint = await vscode.window.showInputBox({
      title: "Add an MCP server (2 of 3)",
      prompt: "MCP endpoint URL",
      placeHolder: "http://localhost:8080/mcp",
      validateInput: (v) => {
        try {
          new URL(v);
          return null;
        } catch {
          return "Enter a valid URL";
        }
      },
    });
    if (!endpoint) {
      return;
    }

    // The variable's *name*, never its value: the token belongs on the
    // GitPilot host, not in the editor.
    const authTokenEnv = await vscode.window.showInputBox({
      title: "Add an MCP server (3 of 3)",
      prompt:
        "Environment variable holding this server's token, if it needs one. " +
        "GitPilot reads the value on its own host — do not paste the token here.",
      placeHolder: "MY_MCP_SERVER_TOKEN",
    });

    try {
      await this._mcp.installCustom({
        id: id.trim(),
        endpoint: endpoint.trim(),
        authTokenEnv: (authTokenEnv || "").trim(),
      });
      this.post({
        type: "MCP_ACTION_RESULT",
        requestId,
        ok: true,
        message: `${id.trim()} attached, and left disabled.`,
      });
      await this.loadMcpOverview();
    } catch (err) {
      this.post({
        type: "MCP_ACTION_RESULT",
        requestId,
        ok: false,
        message: describeError(err),
      });
    }
  }

  /**
   * Install and start MCP Context Forge, then point GitPilot at it.
   *
   * Long enough to need a progress notification, so it gets one rather than a
   * settings page that appears to have frozen.
   */
  private async installMcpForge(): Promise<void> {
    if (this._forge.isRunning) {
      return;
    }

    const result = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: "Installing MCP Context Forge",
        cancellable: true,
      },
      async (progress, token) => {
        return this._forge.install((update) => {
          progress.report({ message: update.message });
          this.post({
            type: "MCP_FORGE_PROGRESS",
            stage: update.stage,
            message: update.message,
          });
        }, token);
      }
    );

    if (!result.ok) {
      this.post({
        type: "MCP_FORGE_PROGRESS",
        stage: "failed",
        message: result.reason,
      });
      const action = result.hint ? "Open docs" : undefined;
      const choice = await vscode.window.showErrorMessage(
        result.reason,
        ...(action ? [action] : [])
      );
      if (choice === action && result.hint) {
        await vscode.env.openExternal(vscode.Uri.parse(result.hint));
      }
      await this.loadMcpOverview();
      return;
    }

    // Point GitPilot's gateway config at the Forge we just started, so the
    // agents actually reach it rather than the previous address.
    try {
      await this._client.request("/api/mcp/gateway", {
        method: "POST",
        body: JSON.stringify({ url: result.gatewayUrl, auth_mode: "auto" }),
      });
    } catch (err) {
      this._output(`Could not save the gateway URL: ${describeError(err)}`);
    }

    this.post({
      type: "MCP_FORGE_PROGRESS",
      stage: "done",
      message: `MCP Context Forge is running at ${result.gatewayUrl}.`,
    });
    vscode.window.showInformationMessage(
      `MCP Context Forge is running at ${result.gatewayUrl}.`
    );
    await this.loadMcpOverview();
  }

  private async syncMcpGateway(requestId: number): Promise<void> {
    try {
      const result = await this._mcp.syncGateway();
      const added = Array.isArray(result.added) ? result.added.length : 0;
      this.post({
        type: "MCP_ACTION_RESULT",
        requestId,
        ok: !result.error,
        message: result.error
          ? String(result.error)
          : `Gateway synced. ${added} server(s) added.`,
      });
      await this.loadMcpOverview();
    } catch (err) {
      this.post({
        type: "MCP_ACTION_RESULT",
        requestId,
        ok: false,
        message: describeError(err),
      });
    }
  }

  private _output(line: string): void {
    // The panel has no channel of its own; surface installer notes as a
    // non-modal message rather than swallowing them.
    vscode.window.setStatusBarMessage(`GitPilot: ${line}`, 5000);
  }

  // ── Agent topologies ────────────────────────────────────────────────

  /**
   * Load the topology presets and which one is currently in force.
   *
   * Like the provider pages, this needs the backend — the presets and the
   * saved preference both live there. A page that cannot reach it says so
   * rather than showing a stale list.
   */
  private async loadTopologies(): Promise<void> {
    const connected = this._client.isConnected
      ? await this._client.health()
      : await this._client.connect();

    if (!connected) {
      this.post({
        type: "TOPOLOGY_DATA",
        offline: true,
        serverUrl: this._client.serverUrl,
      });
      return;
    }

    try {
      const [topologies, saved] = await Promise.all([
        this._settings.listTopologies(),
        this._settings.getTopologyPreference(),
      ]);
      this.post({
        type: "TOPOLOGY_DATA",
        offline: false,
        topologies,
        // No saved preference means the server routes per request; the page
        // shows that as "Automatic" rather than inventing a selection.
        selected: saved,
      });
    } catch (err) {
      this.post({
        type: "TOPOLOGY_DATA",
        offline: true,
        serverUrl: this._client.serverUrl,
        detail: describeError(err),
      });
    }
  }

  /**
   * Make a topology the default for future requests.
   *
   * The preference lives on the server, which is what actually routes work —
   * writing it only into VS Code configuration, as this page used to, left
   * the setting with no effect at all. The local copy is kept in step so the
   * rest of the extension agrees.
   */
  private async setTopology(
    topology: string,
    requestId: number
  ): Promise<void> {
    try {
      await this._settings.setTopologyPreference(topology);
      await vscode.workspace
        .getConfiguration("gitpilot")
        .update("defaultTopology", topology, vscode.ConfigurationTarget.Global);

      this.post({
        type: "TOPOLOGY_SAVED",
        requestId,
        ok: true,
        topology,
      });
      await vscode.commands.executeCommand("gitpilot.refreshStatus");
    } catch (err) {
      this.post({
        type: "TOPOLOGY_SAVED",
        requestId,
        ok: false,
        message: describeError(err),
      });
    }
  }

  // ── OllaBridge Cloud ────────────────────────────────────────────────

  /**
   * Open the OllaBridge sign-in page in the user's browser.
   *
   * Device pairing rather than a password box: VS Code is the wrong place to
   * type an account password, and a pairing code is revocable.
   */
  private async startOllaBridgeLogin(baseUrl?: string): Promise<void> {
    const target = deriveLinkUrl(baseUrl);
    await vscode.env.openExternal(vscode.Uri.parse(target));
    this.post({ type: "OLLABRIDGE_LOGIN_OPENED", url: target });
  }

  private async pairOllaBridge(
    requestId: number,
    code: string,
    baseUrl?: string
  ): Promise<void> {
    const gateway = (baseUrl || "").trim() || OLLABRIDGE_CLOUD_URL;
    const trimmed = (code || "").trim();
    if (!trimmed) {
      this.post({
        type: "OLLABRIDGE_PAIR_RESULT",
        requestId,
        ok: false,
        message: "Enter the pairing code shown in your browser.",
      });
      return;
    }

    try {
      const result = await this._client.ollaBridgePair(gateway, trimmed);
      if (!result.success) {
        this.post({
          type: "OLLABRIDGE_PAIR_RESULT",
          requestId,
          ok: false,
          message: result.error || "Pairing failed.",
        });
        return;
      }

      await this._settings.updateProviderConfig("ollabridge", {
        base_url: gateway,
        api_key: result.token || "",
      });
      await this._settings.setProvider("ollabridge");
      const settings = await this._settings.getSettings();

      this.post({
        type: "OLLABRIDGE_PAIR_RESULT",
        requestId,
        ok: true,
        message: "Device paired. OllaBridge Cloud is now the active provider.",
        data: this.toSetupData(settings),
      });
      await vscode.commands.executeCommand("gitpilot.refreshStatus");
    } catch (err) {
      this.post({
        type: "OLLABRIDGE_PAIR_RESULT",
        requestId,
        ok: false,
        message: describeError(err),
      });
    }
  }

  private async signOutOllaBridge(requestId: number): Promise<void> {
    const confirm = await vscode.window.showWarningMessage(
      "Sign out of OllaBridge Cloud? The stored device token will be removed.",
      { modal: true },
      "Sign out"
    );
    if (confirm !== "Sign out") {
      return;
    }

    try {
      await this._settings.updateProviderConfig("ollabridge", { api_key: "" });
      const settings = await this._settings.getSettings();
      this.post({
        type: "OLLABRIDGE_PAIR_RESULT",
        requestId,
        ok: true,
        message: "Signed out of OllaBridge Cloud.",
        data: this.toSetupData(settings),
      });
    } catch (err) {
      this.post({
        type: "OLLABRIDGE_PAIR_RESULT",
        requestId,
        ok: false,
        message: describeError(err),
      });
    }
  }

  // ── Translation between backend settings and the webview ────────────

  /**
   * Reduce backend settings to what the webview is allowed to know.
   *
   * `SettingsData` carries plaintext API keys. Nothing below copies one: each
   * key becomes `hasApiKey` plus a four-character tail.
   */
  private toSetupData(settings: SettingsData): ProviderSetupData {
    const active = (settings.provider as ProviderName) || "ollabridge";
    const configs: Partial<Record<ProviderName, SanitizedProviderConfig>> = {};
    const providers: ProviderOverviewEntry[] = [];

    for (const name of PROVIDER_ORDER) {
      const raw = (settings as any)[name] as Record<string, string> | undefined;
      const meta = PROVIDER_CATALOG[name];
      const apiKey = raw?.api_key || "";
      const model = name === "watsonx" ? raw?.model_id : raw?.model;

      const sanitized: SanitizedProviderConfig = {
        model: model || "",
        base_url: raw?.base_url || "",
        hasApiKey: Boolean(apiKey),
        apiKeyHint: apiKey ? maskKey(apiKey) : undefined,
      };
      if (name === "watsonx") {
        sanitized.project_id = raw?.project_id || "";
      }
      if (name === "custom") {
        // Headers carry routing and attribution values, not credentials, so
        // they round-trip to the page as stored.
        sanitized.headers = { ...((raw?.headers as unknown as Record<string, string>) || {}) };
      }
      configs[name] = sanitized;

      providers.push({
        name,
        label: meta.label,
        description: meta.description,
        model: model || undefined,
        active: name === active,
        configured: this.isConfigured(name, raw, apiKey, model),
      });
    }

    return {
      activeProvider: active,
      providers,
      configs,
      ollabridgeMode: inferOllaBridgeMode(
        configs.ollabridge?.base_url,
        Boolean(configs.ollabridge?.hasApiKey)
      ),
      serverUrl: this._client.serverUrl,
    };
  }

  /**
   * Whether a provider has everything it needs to be selected.
   *
   * This mirrors the backend's `is_provider_configured`, which is the one
   * place that knows a provider's real requirements — notably that Ollama,
   * OllaBridge and Open WebUI need no API key. Inventing a "has a key?" rule
   * here is what made OllaBridge look unconfigured.
   */
  private isConfigured(
    name: ProviderName,
    raw: Record<string, string> | undefined,
    apiKey: string,
    model: string | undefined
  ): boolean {
    switch (name) {
      case "ollama":
      case "ollabridge":
        return true;
      case "openwebui":
        // An instance may be open to the local network without auth.
        return Boolean(raw?.base_url);
      case "custom":
        // Nothing to fall back on: a custom endpoint needs both halves.
        return Boolean(raw?.base_url && model);
      case "watsonx":
        return Boolean(apiKey && raw?.project_id);
      default:
        return Boolean(apiKey);
    }
  }

  /**
   * Shape webview input for the backend.
   *
   * A blank API key is dropped rather than sent: an empty string would clear
   * the stored key, and a page that saves a model change must not do that.
   * Clearing is REMOVE_PROVIDER_KEY, which asks first.
   */
  private toBackendConfig(
    provider: ProviderName,
    config: ProviderConfigInput
  ): Record<string, unknown> {
    const out: Record<string, unknown> = {};

    const model = (config.model || "").trim();
    if (model) {
      out[provider === "watsonx" ? "model_id" : "model"] = model;
    }
    if (config.base_url !== undefined) {
      out.base_url = config.base_url.trim();
    }
    if (provider === "watsonx" && config.project_id !== undefined) {
      out.project_id = config.project_id.trim();
    }
    if (provider === "custom" && config.headers !== undefined) {
      // Headers replace wholesale rather than merge — the editor shows the
      // complete set, so a row the user deleted has to actually go.
      const headers: Record<string, string> = {};
      for (const [name, value] of Object.entries(config.headers)) {
        const key = name.trim();
        if (key) {
          headers[key] = String(value ?? "").trim();
        }
      }
      out.headers = headers;
    }

    const apiKey = (config.api_key || "").trim();
    if (apiKey) {
      out.api_key = apiKey;
    }

    return out;
  }

  /**
   * Ask before a credential leaves the machine in the clear.
   *
   * Sending a key to a local server over http is fine — it never touches the
   * network. Sending one to a remote host over http is not.
   */
  private async confirmKeyTransport(): Promise<boolean> {
    const url = this._client.serverUrl;
    if (!isPlainHttpRemote(url)) {
      return true;
    }
    const choice = await vscode.window.showWarningMessage(
      `${url} is a remote server reached over plain HTTP. Your API key would ` +
        `be sent unencrypted.`,
      { modal: true },
      "Send anyway"
    );
    return choice === "Send anyway";
  }

  private async copyDiagnostics(): Promise<void> {
    const lines = [
      `GitPilot server URL: ${this._client.serverUrl}`,
      `Connection state:    ${this._client.state}`,
      `Local address:       ${this._server.isLocalUrl() ? "yes" : "no"}`,
      `Managed process:     ${this._server.isManaged ? "yes" : "no"}`,
      `Start command:       ${this._server.commandLine}`,
      `VS Code:             ${vscode.version}`,
      `Platform:            ${process.platform}`,
    ];
    await vscode.env.clipboard.writeText(lines.join("\n"));
    vscode.window.showInformationMessage("GitPilot diagnostics copied.");
  }

  // ── VS Code configuration (General / Agent / Editor) ────────────────

  private _sendCurrentSettings(): void {
    const cfg = vscode.workspace.getConfiguration("gitpilot");

    this.post({
      type: "SETTINGS_DATA",
      payload: {
        serverUrl: cfg.get<string>("serverUrl", "http://127.0.0.1:8000"),
        autoConnect: cfg.get<boolean>("autoConnect", true),
        githubToken: cfg.get<string>("githubToken", ""),
        permissionMode: cfg.get<string>("permissionMode", "normal"),
        defaultTopology: cfg.get<string>("defaultTopology", "default"),
        liteMode: cfg.get<boolean>("liteMode", false),
        showInlineHints: cfg.get<boolean>("showInlineHints", true),
        scanOnSave: cfg.get<boolean>("scanOnSave", false),
        showSecurityDiagnostics: cfg.get<boolean>(
          "showSecurityDiagnostics",
          true
        ),
        chatFontSize: cfg.get<number>("chatFontSize", 13),
        maxChatHistory: cfg.get<number>("maxChatHistory", 100),
      },
    });
  }

  private async _applySettings(
    payload: Record<string, unknown>
  ): Promise<void> {
    const cfg = vscode.workspace.getConfiguration("gitpilot");
    const target = vscode.ConfigurationTarget.Global;

    const set = async (key: string, value: unknown) => {
      if (value !== undefined) {
        await cfg.update(key, value, target);
      }
    };

    await set("serverUrl", payload.serverUrl);
    await set("autoConnect", payload.autoConnect);
    await set("githubToken", payload.githubToken);
    await set("permissionMode", payload.permissionMode);
    await set("defaultTopology", payload.defaultTopology);
    await set("liteMode", payload.liteMode);
    await set("showInlineHints", payload.showInlineHints);
    await set("scanOnSave", payload.scanOnSave);
    await set("showSecurityDiagnostics", payload.showSecurityDiagnostics);
    await set("chatFontSize", payload.chatFontSize);
    await set("maxChatHistory", payload.maxChatHistory);

    // The server URL is not just a stored string — the live client has to
    // follow it, or the provider pages keep talking to the old address.
    const url = typeof payload.serverUrl === "string" ? payload.serverUrl : "";
    if (url && url.replace(/\/+$/, "") !== this._client.serverUrl) {
      this._client.setServerUrl(url);
      this._settings.clearModelCache();
    }
  }

  private _getNonce(): string {
    const chars =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    let result = "";
    for (let i = 0; i < 32; i++) {
      result += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return result;
  }
}

// ── Helpers ───────────────────────────────────────────────────────────

/**
 * Reduce a server to what the overview needs.
 *
 * The tool list is kept — it is the point of the page — but each tool is
 * trimmed to the fields the UI renders, so a server advertising a hundred
 * tools does not ship a hundred full schemas into the webview.
 */
function summariseServer(server: McpServer): Record<string, unknown> {
  const tools = (server.tools || []).map((tool) => ({
    name: tool.name,
    risk: tool.risk,
    enabled: tool.enabled,
    destructive: tool.destructive,
    mutation: tool.mutation,
    used_by: tool.used_by || [],
  }));
  return {
    id: server.id,
    installed: server.installed,
    enabled: server.enabled,
    endpoint: server.endpoint,
    description: server.description,
    tags: server.tags || [],
    tool_count: server.tool_count ?? tools.length,
    enabled_tool_count: tools.filter((t) => t.enabled).length,
    tools,
    orphan: server.orphan,
    source: server.source,
    // The variable's name is not a secret; its value never leaves the host.
    auth_token_env: server.auth_token_env,
  };
}

/** Tool names that destroy data, which are never enabled without asking. */
function isDestructiveName(tool: string): boolean {
  return /\b(drop|delete|truncate|destroy|remove)\b/i.test(tool.replace(/[._-]/g, " "));
}

/** "sk-ant-...A7X2" → "••••A7X2". Never returns more than the last four. */
function maskKey(key: string): string {
  return `••••${key.slice(-4)}`;
}

/** True when `url` is a remote host reached without TLS. */
function isPlainHttpRemote(url: string): boolean {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "http:") {
      return false;
    }
    const host = parsed.hostname.toLowerCase();
    return !(host === "localhost" || host === "127.0.0.1" || host === "::1");
  } catch {
    return false;
  }
}

/** The browser sign-in page belonging to a given OllaBridge gateway. */
function deriveLinkUrl(baseUrl?: string): string {
  const base = (baseUrl || "").trim();
  if (!base || base.replace(/\/+$/, "") === OLLABRIDGE_CLOUD_URL) {
    return OLLABRIDGE_LINK_URL;
  }
  try {
    return `${new URL(base).toString().replace(/\/+$/, "")}/link`;
  } catch {
    return OLLABRIDGE_LINK_URL;
  }
}

/**
 * A message safe to show and safe to log.
 *
 * Error text from a failed request can quote the request body, which for a
 * save is the API key. Anything key-shaped is redacted before it is shown.
 */
function describeError(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  return redactSecrets(raw);
}

/** Turn a raw failure into something a user can act on. */
function describeProviderError(provider: ProviderName, err: unknown): string {
  const message = describeError(err);
  const status = (err as { status?: number })?.status;
  const label = PROVIDER_CATALOG[provider].label;

  if (status === 401 || status === 403) {
    return `${label} rejected the credentials. Check the API key and try again.`;
  }
  if (status === 404) {
    return `${label} endpoint not found. Check the base URL.`;
  }
  if (/timed out/i.test(message)) {
    return `${label} did not respond in time. Check the base URL and that the service is running.`;
  }
  if (/fetch failed|ECONNREFUSED|ENOTFOUND/i.test(message)) {
    return `Could not reach ${label}. Check the base URL and that the service is running.`;
  }
  return message;
}

/** Blank out anything that looks like a credential. */
function redactSecrets(text: string): string {
  return text
    .replace(/\b(sk|xai|gsk)-[A-Za-z0-9_-]{8,}/g, "[redacted]")
    .replace(/"api_key"\s*:\s*"[^"]*"/g, '"api_key":"[redacted]"')
    .replace(/\b[Bb]earer\s+[A-Za-z0-9._-]{8,}/g, "Bearer [redacted]");
}
