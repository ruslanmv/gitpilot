/**
 * Status bar management for GitPilot connection state.
 */
import * as vscode from 'vscode';
import { ConnectionState } from '../api/client';

const STATE_LABELS: Record<ConnectionState, { icon: string; text: string; tooltip: string; color?: string }> = {
    connected: { icon: '$(rocket)', text: 'GitPilot', tooltip: 'GitPilot — Connected', color: undefined },
    disconnected: { icon: '$(circle-slash)', text: 'GitPilot', tooltip: 'GitPilot — Disconnected (click to reconnect)', color: 'statusBarItem.warningBackground' },
    connecting: { icon: '$(sync~spin)', text: 'GitPilot', tooltip: 'GitPilot — Connecting...', color: undefined },
    error: { icon: '$(error)', text: 'GitPilot', tooltip: 'GitPilot — Connection Error', color: 'statusBarItem.errorBackground' },
};

export class StatusBarManager {
    private _item: vscode.StatusBarItem;

    constructor() {
        this._item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
        this._item.command = 'gitpilot.openChat';
        this.update('disconnected');
        this._item.show();
    }

    update(state: ConnectionState): void {
        const label = STATE_LABELS[state];
        this._item.text = `${label.icon} ${label.text}`;
        this._item.tooltip = label.tooltip;
        this._item.backgroundColor = label.color
            ? new vscode.ThemeColor(label.color)
            : undefined;
    }

    dispose(): void {
        this._item.dispose();
    }
}
