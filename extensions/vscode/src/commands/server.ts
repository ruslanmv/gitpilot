/**
 * Server connection, configuration, and LLM provider command handlers.
 *
 * All AI/LLM orchestration is delegated to the GitPilot server.
 * These commands let the user pick providers and models on the server.
 */
import * as vscode from 'vscode';
import { GitPilotApiClient } from '../api/client';
import { ChatViewProvider } from '../views/chatViewProvider';

const PROVIDER_LABELS: Record<string, { label: string; description: string }> = {
    ollabridge: { label: 'OllaBridge', description: 'OllaBridge Cloud — OpenAI-compatible gateway (default)' },
    openai: { label: 'OpenAI', description: 'GPT-4o, GPT-4o-mini, etc.' },
    claude: { label: 'Claude (Anthropic)', description: 'Claude Sonnet, Opus, Haiku' },
    ollama: { label: 'Ollama', description: 'Local models via Ollama' },
    watsonx: { label: 'Watsonx', description: 'IBM Granite and foundation models' },
};

/**
 * Confirm the backend is reachable, reconnecting once if the cached state
 * says otherwise.
 *
 * `client.isConnected` is a cached flag, refreshed on a 30s timer. Treating it
 * as ground truth is how these commands came to report "Not connected" at a
 * server that had been running for minutes. When the probe really does fail,
 * the user is offered the settings page — which has recovery actions —
 * instead of a dead-end warning.
 */
async function ensureConnected(
    client: GitPilotApiClient,
    action: string,
): Promise<boolean> {
    if (client.isConnected && await client.health()) {
        return true;
    }
    const connected = await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Window, title: 'Connecting to GitPilot...' },
        async () => client.connect(),
    );
    if (connected) { return true; }

    const choice = await vscode.window.showWarningMessage(
        `Could not reach the GitPilot server at ${client.serverUrl}, so ${action} is unavailable.`,
        'Open Settings',
    );
    if (choice === 'Open Settings') {
        await vscode.commands.executeCommand('gitpilot.openSettings');
    }
    return false;
}

export function registerServerCommands(
    context: vscode.ExtensionContext,
    client: GitPilotApiClient,
    chatProvider: ChatViewProvider,
): void {
    context.subscriptions.push(
        vscode.commands.registerCommand('gitpilot.setServer', async () => {
            const url = await vscode.window.showInputBox({
                prompt: 'Enter GitPilot server URL',
                value: client.serverUrl,
                validateInput: (val) => {
                    try {
                        new URL(val);
                        return null;
                    } catch {
                        return 'Please enter a valid URL';
                    }
                },
            });
            if (!url) { return; }

            client.setServerUrl(url);
            await vscode.workspace.getConfiguration('gitpilot').update('serverUrl', url, true);
            vscode.window.showInformationMessage(`Server URL set to: ${url}`);

            // Try to connect
            await vscode.window.withProgress(
                { location: vscode.ProgressLocation.Notification, title: 'Connecting to GitPilot...' },
                async () => {
                    const connected = await client.connect();
                    chatProvider.updateConnectionState(connected);
                    if (connected) {
                        vscode.window.showInformationMessage('Connected to GitPilot server');
                    } else {
                        vscode.window.showWarningMessage('Could not connect. Is the server running?');
                    }
                },
            );
        }),

        vscode.commands.registerCommand('gitpilot.reconnect', async () => {
            await vscode.window.withProgress(
                { location: vscode.ProgressLocation.Notification, title: 'Reconnecting to GitPilot...' },
                async () => {
                    const connected = await client.connect();
                    chatProvider.updateConnectionState(connected);
                    if (connected) {
                        vscode.window.showInformationMessage('Reconnected to GitPilot');
                    } else {
                        vscode.window.showWarningMessage('Could not reconnect. Is the server running?');
                    }
                },
            );
        }),

        vscode.commands.registerCommand('gitpilot.showServerInfo', async () => {
            if (!await ensureConnected(client, 'server info')) { return; }

            try {
                const settings = await client.getSettings();
                const perms = await client.getPermissions();
                const providerKey = settings.provider || 'unknown';
                const providerLabel = PROVIDER_LABELS[providerKey]?.label || providerKey;

                // Get model info for the active provider
                let modelInfo = '';
                const providerCfg = settings[providerKey];
                if (providerCfg) {
                    const model = providerCfg.model || providerCfg.model_id || '';
                    if (model) { modelInfo = `\nModel: ${model}`; }
                }

                const info = [
                    `Server: ${client.serverUrl}`,
                    `Provider: ${providerLabel}`,
                    modelInfo ? `Model: ${providerCfg?.model || providerCfg?.model_id || ''}` : '',
                    `Permission Mode: ${perms.mode}`,
                ].filter(Boolean).join('\n');
                vscode.window.showInformationMessage(info, { modal: true });
            } catch (err: any) {
                vscode.window.showErrorMessage(`Failed to fetch server info: ${err.message}`);
            }
        }),

        // ── Provider Selection ─────────────────────────────────────
        vscode.commands.registerCommand('gitpilot.selectProvider', async () => {
            if (!await ensureConnected(client, 'provider selection')) { return; }

            try {
                const settings = await client.getSettings();
                const available: string[] = settings.providers || Object.keys(PROVIDER_LABELS);
                const currentProvider = settings.provider || '';

                const items = available.map(p => {
                    const info = PROVIDER_LABELS[p] || { label: p, description: '' };
                    return {
                        label: info.label + (p === currentProvider ? '  (active)' : ''),
                        description: info.description,
                        provider: p,
                    };
                });

                const selected = await vscode.window.showQuickPick(items, {
                    placeHolder: `Current provider: ${PROVIDER_LABELS[currentProvider]?.label || currentProvider}`,
                    title: 'Select LLM Provider (server-side)',
                });
                if (!selected) { return; }

                await vscode.window.withProgress(
                    { location: vscode.ProgressLocation.Notification, title: `Switching to ${selected.label}...` },
                    async () => {
                        await client.setProvider(selected.provider);
                    },
                );

                vscode.window.showInformationMessage(`LLM provider set to: ${PROVIDER_LABELS[selected.provider]?.label || selected.provider}`);
                chatProvider.refreshProviderState();
            } catch (err: any) {
                vscode.window.showErrorMessage(`Failed to switch provider: ${err.message}`);
            }
        }),

        // ── Model Selection ────────────────────────────────────────
        vscode.commands.registerCommand('gitpilot.selectModel', async () => {
            if (!await ensureConnected(client, 'model selection')) { return; }

            try {
                const settings = await client.getSettings();
                const provider = settings.provider;
                const providerCfg = settings[provider] || {};
                const currentModel = providerCfg.model || providerCfg.model_id || '';

                if (provider === 'ollabridge') {
                    // Fetch live model list from OllaBridge
                    const baseUrl = providerCfg.base_url || '';
                    const apiKey = providerCfg.api_key || '';

                    let models: string[] = [];
                    await vscode.window.withProgress(
                        { location: vscode.ProgressLocation.Notification, title: 'Fetching OllaBridge models...' },
                        async () => {
                            models = await client.getOllaBridgeModels(baseUrl || undefined, apiKey || undefined);
                        },
                    );

                    if (models.length === 0) {
                        const manual = await vscode.window.showInputBox({
                            prompt: 'No models found. Enter model name manually',
                            value: currentModel,
                        });
                        if (manual) {
                            await client.updateLlmSettings({ ollabridge: { ...providerCfg, model: manual } });
                            vscode.window.showInformationMessage(`OllaBridge model set to: ${manual}`);
                            chatProvider.refreshProviderState();
                        }
                        return;
                    }

                    const items = models.map(m => ({
                        label: m + (m === currentModel ? '  (active)' : ''),
                        model: m,
                    }));

                    const selected = await vscode.window.showQuickPick(items, {
                        placeHolder: `Current model: ${currentModel}`,
                        title: 'Select OllaBridge Model',
                    });
                    if (!selected) { return; }

                    await client.updateLlmSettings({ ollabridge: { ...providerCfg, model: selected.model } });
                    vscode.window.showInformationMessage(`OllaBridge model set to: ${selected.model}`);
                    chatProvider.refreshProviderState();

                } else if (provider === 'openai') {
                    const models = ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo', 'o1-mini', 'o1-preview'];
                    const items = models.map(m => ({
                        label: m + (m === currentModel ? '  (active)' : ''),
                        model: m,
                    }));
                    const selected = await vscode.window.showQuickPick(items, {
                        placeHolder: `Current model: ${currentModel}`,
                        title: 'Select OpenAI Model',
                    });
                    if (!selected) { return; }
                    await client.updateLlmSettings({ openai: { ...providerCfg, model: selected.model } });
                    vscode.window.showInformationMessage(`OpenAI model set to: ${selected.model}`);
                    chatProvider.refreshProviderState();

                } else if (provider === 'claude') {
                    const models = ['claude-sonnet-4-5', 'claude-opus-4-6', 'claude-haiku-4-5-20251001', 'claude-sonnet-4-6'];
                    const items = models.map(m => ({
                        label: m + (m === currentModel ? '  (active)' : ''),
                        model: m,
                    }));
                    const selected = await vscode.window.showQuickPick(items, {
                        placeHolder: `Current model: ${currentModel}`,
                        title: 'Select Claude Model',
                    });
                    if (!selected) { return; }
                    await client.updateLlmSettings({ claude: { ...providerCfg, model: selected.model } });
                    vscode.window.showInformationMessage(`Claude model set to: ${selected.model}`);
                    chatProvider.refreshProviderState();

                } else if (provider === 'ollama') {
                    const model = await vscode.window.showInputBox({
                        prompt: 'Enter Ollama model name',
                        value: currentModel || 'llama3',
                        placeHolder: 'e.g. llama3, codellama, mistral',
                    });
                    if (!model) { return; }
                    await client.updateLlmSettings({ ollama: { ...providerCfg, model } });
                    vscode.window.showInformationMessage(`Ollama model set to: ${model}`);
                    chatProvider.refreshProviderState();

                } else if (provider === 'watsonx') {
                    const models = ['ibm/granite-3-8b-instruct', 'meta-llama/llama-3-3-70b-instruct', 'ibm/granite-3-2b-instruct'];
                    const items = models.map(m => ({
                        label: m + (m === currentModel ? '  (active)' : ''),
                        model: m,
                    }));
                    const selected = await vscode.window.showQuickPick(items, {
                        placeHolder: `Current model: ${currentModel}`,
                        title: 'Select Watsonx Model',
                    });
                    if (!selected) { return; }
                    await client.updateLlmSettings({ watsonx: { ...providerCfg, model_id: selected.model } });
                    vscode.window.showInformationMessage(`Watsonx model set to: ${selected.model}`);
                    chatProvider.refreshProviderState();

                } else {
                    vscode.window.showWarningMessage(`Model selection not available for provider: ${provider}`);
                }
            } catch (err: any) {
                vscode.window.showErrorMessage(`Failed to change model: ${err.message}`);
            }
        }),

        vscode.commands.registerCommand('gitpilot.setPermissionMode', async () => {
            const modes: Array<{ label: string; description: string; mode: 'normal' | 'plan' | 'auto' }> = [
                { label: 'Normal', description: 'Ask before risky operations', mode: 'normal' },
                { label: 'Plan Only', description: 'Read-only — all writes blocked', mode: 'plan' },
                { label: 'Auto', description: 'Allow everything without confirmation', mode: 'auto' },
            ];
            const selected = await vscode.window.showQuickPick(modes, {
                placeHolder: 'Select permission mode',
            });
            if (!selected) { return; }

            if (selected.mode === 'auto') {
                const confirm = await vscode.window.showWarningMessage(
                    'Auto mode allows all operations without confirmation. Continue?',
                    { modal: true },
                    'Enable Auto Mode',
                );
                if (confirm !== 'Enable Auto Mode') { return; }
            }

            try {
                await client.setPermissionMode(selected.mode);
                vscode.window.showInformationMessage(`Permission mode set to: ${selected.label}`);
            } catch (err: any) {
                vscode.window.showErrorMessage(`Failed to set mode: ${err.message}`);
            }
        }),

        // ── OllaBridge Pairing ──────────────────────────────────────
        vscode.commands.registerCommand('gitpilot.ollaBridgePair', async () => {
            const baseUrl = await vscode.window.showInputBox({
                prompt: 'Enter OllaBridge Cloud gateway URL',
                value: 'https://ollabridge.example.com',
                validateInput: (val) => {
                    try { new URL(val); return null; } catch { return 'Enter a valid URL'; }
                },
            });
            if (!baseUrl) { return; }

            // Check health first
            try {
                await vscode.window.withProgress(
                    { location: vscode.ProgressLocation.Notification, title: 'Checking OllaBridge gateway...' },
                    async () => {
                        const health = await client.getOllaBridgeHealth(baseUrl);
                        if (health.status !== 'ok') {
                            throw new Error('Gateway returned status: ' + health.status);
                        }
                    },
                );
            } catch (err: any) {
                vscode.window.showErrorMessage(`OllaBridge gateway unreachable: ${err.message}`);
                return;
            }

            const pairingCode = await vscode.window.showInputBox({
                prompt: 'Enter the pairing code from OllaBridge Cloud dashboard',
                placeHolder: 'e.g. AB12-CD34',
                validateInput: (val) => val.trim().length < 4 ? 'Code too short' : null,
            });
            if (!pairingCode) { return; }

            try {
                const result = await vscode.window.withProgress(
                    { location: vscode.ProgressLocation.Notification, title: 'Pairing with OllaBridge...' },
                    async () => client.ollaBridgePair(baseUrl, pairingCode.trim()),
                );

                if (result.success) {
                    // Persist paired base_url into provider settings
                    const settings = await client.getSettings();
                    const ollaCfg = settings.ollabridge || {};
                    await client.updateLlmSettings({
                        ollabridge: { ...ollaCfg, base_url: baseUrl, api_key: result.token || ollaCfg.api_key },
                    });
                    await client.setProvider('ollabridge');
                    vscode.window.showInformationMessage(`Paired with OllaBridge at ${baseUrl}`);
                    chatProvider.refreshProviderState();
                    chatProvider.checkOllaBridgeHealth();
                } else {
                    vscode.window.showErrorMessage(`Pairing failed: ${result.error || 'Unknown error'}`);
                }
            } catch (err: any) {
                vscode.window.showErrorMessage(`OllaBridge pairing error: ${err.message}`);
            }
        }),

        // ── LLM API Key Configuration ─────────────────────────────
        vscode.commands.registerCommand('gitpilot.setLlmApiKey', async () => {
            if (!await ensureConnected(client, 'API key configuration')) { return; }

            try {
                const settings = await client.getSettings();
                const provider = settings.provider;
                if (!provider) {
                    vscode.window.showWarningMessage('No active LLM provider. Select a provider first.');
                    return;
                }

                const providerLabel = PROVIDER_LABELS[provider]?.label || provider;
                const providerCfg = settings[provider] || {};
                const currentKey = providerCfg.api_key ? '••••' + providerCfg.api_key.slice(-4) : '(not set)';

                const apiKey = await vscode.window.showInputBox({
                    prompt: `Enter API key for ${providerLabel} (current: ${currentKey})`,
                    placeHolder: 'sk-...',
                    password: true,
                });
                if (apiKey === undefined) { return; } // cancelled

                await client.updateLlmSettings({ [provider]: { ...providerCfg, api_key: apiKey } });
                vscode.window.showInformationMessage(
                    apiKey ? `API key updated for ${providerLabel}` : `API key cleared for ${providerLabel}`,
                );
            } catch (err: any) {
                vscode.window.showErrorMessage(`Failed to set API key: ${err.message}`);
            }
        }),

        // ── LLM Base URL Configuration ────────────────────────────
        vscode.commands.registerCommand('gitpilot.setLlmBaseUrl', async () => {
            if (!await ensureConnected(client, 'base URL configuration')) { return; }

            try {
                const settings = await client.getSettings();
                const provider = settings.provider;
                if (!provider) {
                    vscode.window.showWarningMessage('No active LLM provider. Select a provider first.');
                    return;
                }

                const providerLabel = PROVIDER_LABELS[provider]?.label || provider;
                const providerCfg = settings[provider] || {};
                const currentUrl = providerCfg.base_url || '(default)';

                const baseUrl = await vscode.window.showInputBox({
                    prompt: `Enter base URL for ${providerLabel} (current: ${currentUrl})`,
                    value: providerCfg.base_url || '',
                    placeHolder: 'https://api.example.com/v1',
                    validateInput: (val) => {
                        if (!val) { return null; } // empty = reset to default
                        try { new URL(val); return null; } catch { return 'Enter a valid URL'; }
                    },
                });
                if (baseUrl === undefined) { return; } // cancelled

                await client.updateLlmSettings({ [provider]: { ...providerCfg, base_url: baseUrl || undefined } });
                vscode.window.showInformationMessage(
                    baseUrl ? `Base URL for ${providerLabel} set to: ${baseUrl}` : `Base URL for ${providerLabel} reset to default`,
                );
                chatProvider.refreshProviderState();
            } catch (err: any) {
                vscode.window.showErrorMessage(`Failed to set base URL: ${err.message}`);
            }
        }),

        vscode.commands.registerCommand('gitpilot.selectTopology', async () => {
            if (!await ensureConnected(client, 'topology selection')) { return; }
            try {
                const topologies = await client.listTopologies();
                const items = topologies.map(t => ({
                    label: t.name,
                    description: `[${t.category}]`,
                    detail: t.description,
                    id: t.id,
                }));
                const selected = await vscode.window.showQuickPick(items, {
                    placeHolder: 'Select an agent topology',
                    matchOnDescription: true,
                    matchOnDetail: true,
                });
                if (selected) {
                    await client.setTopology(selected.id);
                    vscode.window.showInformationMessage(`Topology set to: ${selected.label}`);
                }
            } catch (err: any) {
                vscode.window.showErrorMessage(`Failed to load topologies: ${err.message}`);
            }
        }),

        vscode.commands.registerCommand('gitpilot.toggleLiteMode', async () => {
            if (!await ensureConnected(client, 'Lite Mode')) { return; }
            try {
                // Read current state from server
                const data = await client.request<{ lite_mode: boolean }>('/api/settings/lite-mode');
                const currentValue = data.lite_mode ?? false;
                const newValue = !currentValue;

                // Toggle on server
                await client.request('/api/settings/lite-mode', {
                    method: 'POST',
                    body: JSON.stringify({ lite_mode: newValue }),
                });

                // Sync VS Code setting
                await vscode.workspace.getConfiguration('gitpilot').update(
                    'liteMode', newValue, vscode.ConfigurationTarget.Global,
                );

                const label = newValue ? 'ON' : 'OFF';
                vscode.window.showInformationMessage(
                    `Lite Mode: ${label}. ${newValue
                        ? 'Using simplified prompts for small LLMs.'
                        : 'Using standard multi-agent pipelines.'
                    }`
                );
            } catch (err: any) {
                vscode.window.showErrorMessage(`Failed to toggle Lite Mode: ${err.message}`);
            }
        }),
    );
}
