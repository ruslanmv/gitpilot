import { resolveBackendUrl } from "./backend.js";
/**
 * WebSocket client for real-time session streaming.
 *
 * Provides auto-reconnection, heartbeat, and event dispatching.
 * Falls back gracefully — callers should always have an HTTP fallback.
 */

const WS_RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 16000];
const HEARTBEAT_INTERVAL = 30000;
const MAX_RECONNECT_ATTEMPTS = 5;
// If a connection dies within this window it counts as unstable
const MIN_STABLE_DURATION_MS = 3000;

export class SessionWebSocket {
  constructor(sessionId, { onMessage, onStatusChange, onError, onConnect, onDisconnect } = {}) {
    this._sessionId = sessionId;
    this._handlers = { onMessage, onStatusChange, onError, onConnect, onDisconnect };
    this._ws = null;
    this._reconnectAttempt = 0;
    this._heartbeatTimer = null;
    this._closed = false;
    this._connectTime = 0;
  }

  connect() {
    if (this._closed) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const backendUrl = resolveBackendUrl();
    let wsUrl;

    if (backendUrl) {
      // Production: replace http(s) with ws(s)
      wsUrl = backendUrl.replace(/^http/, 'ws') + `/ws/sessions/${this._sessionId}`;
    } else {
      // Dev: same host (Vite proxy forwards /ws to backend)
      wsUrl = `${protocol}//${window.location.host}/ws/sessions/${this._sessionId}`;
    }

    try {
      this._ws = new WebSocket(wsUrl);
    } catch {
      // WebSocket constructor can throw if URL is invalid
      this._scheduleReconnect();
      return;
    }

    this._ws.onopen = () => {
      this._connectTime = Date.now();
      this._reconnectAttempt = 0;
      this._startHeartbeat();
      this._handlers.onConnect?.();
    };

    this._ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        this._dispatch(data);
      } catch (e) {
        console.warn('[ws] Failed to parse message:', e);
      }
    };

    this._ws.onclose = (event) => {
      this._stopHeartbeat();
      this._handlers.onDisconnect?.(event);

      if (!this._closed) {
        // If connection died very quickly, count it as unstable
        const lived = Date.now() - (this._connectTime || 0);
        if (lived < MIN_STABLE_DURATION_MS) {
          this._reconnectAttempt++;
        }

        if (this._reconnectAttempt < MAX_RECONNECT_ATTEMPTS) {
          this._scheduleReconnect();
        } else {
          console.warn('[ws] Max reconnect attempts reached, giving up.');
        }
      }
    };

    this._ws.onerror = () => {
      // Suppress noisy console errors during reconnection attempts.
      // The onclose handler already manages reconnection logic.
      // Only notify the caller if we had a stable connection that broke.
      if (this._connectTime && Date.now() - this._connectTime > MIN_STABLE_DURATION_MS) {
        this._handlers.onError?.(new Error('WebSocket connection lost'));
      }
    };
  }

  send(data) {
    if (this._ws?.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify(data));
      return true;
    }
    return false;
  }

  sendMessage(content) {
    return this.send({ type: 'user_message', content });
  }

  cancel() {
    return this.send({ type: 'cancel' });
  }

  close() {
    this._closed = true;
    this._stopHeartbeat();
    if (this._ws) {
      this._ws.close();
      this._ws = null;
    }
  }

  get connected() {
    return this._ws?.readyState === WebSocket.OPEN;
  }

  _dispatch(data) {
    const { type } = data;

    switch (type) {
      case 'agent_message':
      case 'tool_use':
      case 'tool_result':
      case 'diff_update':
      case 'session_restored':
      case 'message_received':
        this._handlers.onMessage?.(data);
        break;
      case 'status_change':
        this._handlers.onStatusChange?.(data.status);
        break;
      case 'error':
        this._handlers.onError?.(new Error(data.message));
        break;
      case 'pong':
        break;
      default:
        this._handlers.onMessage?.(data);
    }
  }

  _startHeartbeat() {
    this._stopHeartbeat();
    this._heartbeatTimer = setInterval(() => {
      this.send({ type: 'ping' });
    }, HEARTBEAT_INTERVAL);
  }

  _stopHeartbeat() {
    if (this._heartbeatTimer) {
      clearInterval(this._heartbeatTimer);
      this._heartbeatTimer = null;
    }
  }

  _scheduleReconnect() {
    const delay = WS_RECONNECT_DELAYS[
      Math.min(this._reconnectAttempt, WS_RECONNECT_DELAYS.length - 1)
    ];
    this._reconnectAttempt++;
    setTimeout(() => this.connect(), delay);
  }
}
