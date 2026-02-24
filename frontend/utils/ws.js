/**
 * WebSocket client for real-time session streaming.
 *
 * Provides auto-reconnection, heartbeat, and event dispatching.
 * Falls back gracefully — callers should always have an HTTP fallback.
 */

const WS_RECONNECT_DELAYS = [1000, 2000, 4000, 8000, 16000];
const HEARTBEAT_INTERVAL = 30000;

export class SessionWebSocket {
  constructor(sessionId, { onMessage, onStatusChange, onError, onConnect, onDisconnect } = {}) {
    this._sessionId = sessionId;
    this._handlers = { onMessage, onStatusChange, onError, onConnect, onDisconnect };
    this._ws = null;
    this._reconnectAttempt = 0;
    this._heartbeatTimer = null;
    this._closed = false;
  }

  connect() {
    if (this._closed) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const backendUrl = import.meta.env.VITE_BACKEND_URL || '';
    let wsUrl;

    if (backendUrl) {
      // Production: replace http(s) with ws(s)
      wsUrl = backendUrl.replace(/^http/, 'ws') + `/ws/sessions/${this._sessionId}`;
    } else {
      // Dev: same host
      wsUrl = `${protocol}//${window.location.host}/ws/sessions/${this._sessionId}`;
    }

    this._ws = new WebSocket(wsUrl);

    this._ws.onopen = () => {
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
        this._scheduleReconnect();
      }
    };

    this._ws.onerror = (error) => {
      this._handlers.onError?.(error);
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
