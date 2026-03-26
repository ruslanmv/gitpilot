/**
 * GitPilot Redesign — Webview Bridge
 * Typed message passing between extension host and React/HTML webview.
 */

import * as vscode from "vscode";
import {
  ExtensionToWebviewMessage,
  WebviewToExtensionMessage,
} from "../../core/types";

export class WebviewBridge {
  constructor(private _webview: vscode.Webview) {}

  postMessage(msg: ExtensionToWebviewMessage): void {
    this._webview.postMessage(msg);
  }

  onMessage(
    handler: (msg: WebviewToExtensionMessage) => void
  ): vscode.Disposable {
    return this._webview.onDidReceiveMessage(handler);
  }
}
