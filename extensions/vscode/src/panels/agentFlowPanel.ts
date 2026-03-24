/**
 * Agent Flow Viewer Panel
 *
 * Full webview panel showing the current multi-agent topology
 * as an interactive node graph (similar to the React frontend's
 * ReactFlow-based Agent Flow Viewer).
 */
import * as vscode from 'vscode';
import { GitPilotApiClient, TopologyInfo } from '../api/client';

export class AgentFlowPanel {
    public static readonly viewType = 'gitpilot.agentFlow';
    private static _instance: AgentFlowPanel | undefined;
    private readonly _panel: vscode.WebviewPanel;
    private _disposed = false;

    private constructor(
        panel: vscode.WebviewPanel,
        private readonly _client: GitPilotApiClient,
        extensionUri: vscode.Uri,
    ) {
        this._panel = panel;
        this._panel.webview.html = this._getHtml();

        this._panel.onDidDispose(() => {
            this._disposed = true;
            AgentFlowPanel._instance = undefined;
        });

        this._panel.webview.onDidReceiveMessage(async (msg) => {
            if (msg.type === 'selectTopology') {
                try {
                    await this._client.setTopology(msg.id);
                    vscode.window.showInformationMessage(`Topology set to: ${msg.name}`);
                    this._loadFlow();
                } catch (err: any) {
                    vscode.window.showErrorMessage(err.message);
                }
            } else if (msg.type === 'refresh') {
                this._loadFlow();
            }
        });

        this._loadFlow();
    }

    static show(client: GitPilotApiClient, extensionUri: vscode.Uri): void {
        if (AgentFlowPanel._instance) {
            AgentFlowPanel._instance._panel.reveal();
            return;
        }

        const panel = vscode.window.createWebviewPanel(
            AgentFlowPanel.viewType,
            'GitPilot Agent Flow',
            vscode.ViewColumn.Beside,
            { enableScripts: true, retainContextWhenHidden: true },
        );

        AgentFlowPanel._instance = new AgentFlowPanel(panel, client, extensionUri);
    }

    private async _loadFlow(): Promise<void> {
        if (this._disposed || !this._client.isConnected) { return; }

        try {
            const [current, topologies] = await Promise.all([
                this._client.getCurrentFlow(),
                this._client.listTopologies(),
            ]);
            this._panel.webview.postMessage({
                type: 'flowData',
                current: current.topology,
                activeNode: current.active_node,
                topologies,
            });
        } catch {
            this._panel.webview.postMessage({ type: 'error', message: 'Failed to load flow data' });
        }
    }

    private _getHtml(): string {
        const nonce = getNonce();
        return /*html*/ `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: var(--vscode-font-family);
            color: var(--vscode-foreground);
            background: var(--vscode-editor-background);
            height: 100vh;
            display: flex;
            flex-direction: column;
        }
        #toolbar {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            border-bottom: 1px solid var(--vscode-panel-border);
            flex-shrink: 0;
        }
        #toolbar select {
            background: var(--vscode-dropdown-background);
            color: var(--vscode-dropdown-foreground);
            border: 1px solid var(--vscode-dropdown-border);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 13px;
        }
        #toolbar button {
            background: var(--vscode-button-background);
            color: var(--vscode-button-foreground);
            border: none;
            padding: 4px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
        }
        #toolbar button:hover {
            background: var(--vscode-button-hoverBackground);
        }
        #canvas {
            flex: 1;
            position: relative;
            overflow: auto;
        }
        svg {
            width: 100%;
            height: 100%;
        }
        .node {
            cursor: pointer;
        }
        .node rect {
            fill: var(--vscode-editor-background);
            stroke: var(--vscode-panel-border);
            stroke-width: 2;
            rx: 8;
            ry: 8;
            transition: stroke 0.2s;
        }
        .node:hover rect {
            stroke: var(--vscode-focusBorder);
        }
        .node.active rect {
            stroke: var(--vscode-terminal-ansiGreen);
            stroke-width: 3;
        }
        .node text {
            fill: var(--vscode-foreground);
            font-size: 12px;
            font-weight: 600;
            text-anchor: middle;
            dominant-baseline: central;
        }
        .node .type-label {
            font-size: 10px;
            font-weight: 400;
            opacity: 0.6;
            dominant-baseline: hanging;
        }
        .edge line {
            stroke: var(--vscode-panel-border);
            stroke-width: 2;
            marker-end: url(#arrowhead);
        }
        .info {
            padding: 20px;
            text-align: center;
            opacity: 0.6;
        }
    </style>
</head>
<body>
    <div id="toolbar">
        <label>Topology:</label>
        <select id="topologySelect"></select>
        <button onclick="refresh()">Refresh</button>
    </div>
    <div id="canvas">
        <div class="info" id="placeholder">Loading agent flow...</div>
    </div>

    <script nonce="${nonce}">
        const vscode = acquireVsCodeApi();
        const canvas = document.getElementById('canvas');
        const placeholder = document.getElementById('placeholder');
        const topologySelect = document.getElementById('topologySelect');

        topologySelect.addEventListener('change', () => {
            const opt = topologySelect.options[topologySelect.selectedIndex];
            vscode.postMessage({ type: 'selectTopology', id: opt.value, name: opt.textContent });
        });

        function refresh() {
            vscode.postMessage({ type: 'refresh' });
        }

        function renderGraph(topology, activeNode) {
            if (!topology || !topology.nodes || !topology.nodes.length) {
                placeholder.textContent = 'No nodes in this topology';
                placeholder.style.display = 'block';
                return;
            }
            placeholder.style.display = 'none';

            const nodes = topology.nodes;
            const edges = topology.edges || [];
            const nodeWidth = 160;
            const nodeHeight = 60;
            const hGap = 60;
            const vGap = 40;
            const cols = Math.ceil(Math.sqrt(nodes.length));

            // Position nodes in a grid
            const positions = {};
            nodes.forEach((n, i) => {
                const col = i % cols;
                const row = Math.floor(i / cols);
                positions[n.id] = {
                    x: 40 + col * (nodeWidth + hGap),
                    y: 40 + row * (nodeHeight + vGap),
                };
            });

            const svgWidth = 40 + cols * (nodeWidth + hGap);
            const svgHeight = 40 + Math.ceil(nodes.length / cols) * (nodeHeight + vGap);

            let svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + svgWidth + ' ' + svgHeight + '">';
            svg += '<defs><marker id="arrowhead" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto">';
            svg += '<polygon points="0 0, 10 3.5, 0 7" fill="var(--vscode-panel-border)"/></marker></defs>';

            // Edges
            for (const e of edges) {
                const from = positions[e.source];
                const to = positions[e.target];
                if (from && to) {
                    svg += '<g class="edge"><line x1="' + (from.x + nodeWidth / 2) + '" y1="' + (from.y + nodeHeight) + '" x2="' + (to.x + nodeWidth / 2) + '" y2="' + to.y + '"/></g>';
                }
            }

            // Nodes
            for (const n of nodes) {
                const p = positions[n.id];
                const isActive = n.id === activeNode;
                svg += '<g class="node' + (isActive ? ' active' : '') + '" transform="translate(' + p.x + ',' + p.y + ')">';
                svg += '<rect width="' + nodeWidth + '" height="' + nodeHeight + '"/>';
                svg += '<text x="' + (nodeWidth / 2) + '" y="' + (nodeHeight / 2 - 6) + '">' + escapeHtml(n.label || n.id) + '</text>';
                svg += '<text class="type-label" x="' + (nodeWidth / 2) + '" y="' + (nodeHeight / 2 + 10) + '">' + escapeHtml(n.type || '') + '</text>';
                svg += '</g>';
            }

            svg += '</svg>';
            canvas.innerHTML = svg;
        }

        function escapeHtml(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

        window.addEventListener('message', (event) => {
            const msg = event.data;
            if (msg.type === 'flowData') {
                // Populate topology selector
                topologySelect.innerHTML = '';
                for (const t of (msg.topologies || [])) {
                    const opt = document.createElement('option');
                    opt.value = t.id;
                    opt.textContent = t.name + ' (' + t.category + ')';
                    if (msg.current && msg.current.id === t.id) { opt.selected = true; }
                    topologySelect.appendChild(opt);
                }
                renderGraph(msg.current, msg.activeNode);
            } else if (msg.type === 'error') {
                placeholder.textContent = msg.message;
                placeholder.style.display = 'block';
            }
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
