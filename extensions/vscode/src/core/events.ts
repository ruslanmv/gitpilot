/**
 * GitPilot Redesign — Event Bus
 * Typed event emitters for cross-service communication.
 */

import * as vscode from "vscode";
import { ConnectionState } from "./types";

export class GitPilotEvents {
  private _onConnectionStateChanged = new vscode.EventEmitter<ConnectionState>();
  private _onProviderChanged = new vscode.EventEmitter<string>();
  private _onSessionChanged = new vscode.EventEmitter<string | undefined>();
  private _onWorkspaceChanged = new vscode.EventEmitter<void>();
  private _onReadinessChanged = new vscode.EventEmitter<void>();

  readonly onConnectionStateChanged = this._onConnectionStateChanged.event;
  readonly onProviderChanged = this._onProviderChanged.event;
  readonly onSessionChanged = this._onSessionChanged.event;
  readonly onWorkspaceChanged = this._onWorkspaceChanged.event;
  readonly onReadinessChanged = this._onReadinessChanged.event;

  fireConnectionStateChanged(state: ConnectionState): void {
    this._onConnectionStateChanged.fire(state);
  }

  fireProviderChanged(provider: string): void {
    this._onProviderChanged.fire(provider);
  }

  fireSessionChanged(sessionId?: string): void {
    this._onSessionChanged.fire(sessionId);
  }

  fireWorkspaceChanged(): void {
    this._onWorkspaceChanged.fire();
  }

  fireReadinessChanged(): void {
    this._onReadinessChanged.fire();
  }

  dispose(): void {
    this._onConnectionStateChanged.dispose();
    this._onProviderChanged.dispose();
    this._onSessionChanged.dispose();
    this._onWorkspaceChanged.dispose();
    this._onReadinessChanged.dispose();
  }
}
