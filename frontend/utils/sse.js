import { API_CREDENTIALS, resolveBackendUrl } from "./backend.js";
/**
 * SSE client for the GitPilot v2 streaming API.
 *
 * Batch V4-H4 made this the web app's transport: the legacy ``/ws/sessions``
 * socket is frozen and deprecated, and one message is one run on
 * ``POST /api/v2/chat/stream``.
 *
 * Usage:
 *   import { streamChat, cancelRun, respondToApproval } from '../utils/sse';
 *
 *   const stop = streamChat(sessionId, message, {
 *     onEvent: (event) => fold(event),          // every event, in order
 *     onTextDelta: (text) => appendToChat(text),
 *     onToolStart: (data) => showToolActivity(data),
 *     onToolResult: (data) => updateToolActivity(data),
 *     onApprovalNeeded: (data) => showApprovalModal(data),
 *     onDone: (data) => finalize(data),
 *     onError: (error) => showError(error),
 *   }, { headers: authHeaders(), permission_mode: 'normal' });
 *
 *   stop();                 // abort the local stream only
 *   cancelRun(sessionId);   // abort it and tell the backend to stop the run
 */

const BACKEND_URL = resolveBackendUrl();

function apiUrl(path) {
  return BACKEND_URL ? `${BACKEND_URL}${path}` : path;
}

function jsonHeaders(options) {
  return { 'Content-Type': 'application/json', ...(options.headers || {}) };
}

// Active abort controllers keyed by sessionId
const activeControllers = new Map();

// 503 is the one answer worth retrying: uvicorn's concurrency guard (and the
// proxies in front of it) send it before the request reaches the app, so no
// run started and a retry cannot start a second one. A 502 or 504 may come
// after the run began, so those are reported, not retried.
const BUSY_STATUS = 503;
const DEFAULT_BUSY_RETRY_DELAYS_MS = [1500, 4000];
const MAX_RETRY_AFTER_MS = 10000;

/** The server's ``Retry-After`` in ms (capped), else ``fallbackMs``. */
function retryDelayMs(res, fallbackMs) {
  const header = res.headers?.get?.('retry-after');
  const seconds = Number(header);
  if (header != null && header !== '' && Number.isFinite(seconds) && seconds >= 0) {
    return Math.min(seconds * 1000, MAX_RETRY_AFTER_MS);
  }
  return fallbackMs;
}

/** Wait ``ms``, or less if ``signal`` aborts first. */
function sleep(ms, signal) {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const timer = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => { clearTimeout(timer); resolve(); }, { once: true });
  });
}

/**
 * One SSE block ("data: {...}" lines, possibly alongside ``id:`` / ``event:``
 * lines) as an event object, or null for framing noise and ``[DONE]``.
 */
export function parseSseBlock(block) {
  const dataLines = [];
  for (const rawLine of String(block).split('\n')) {
    const line = rawLine.replace(/\r$/, '');
    if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''));
  }
  if (dataLines.length === 0) return null;
  const data = dataLines.join('\n').trim();
  if (!data || data === '[DONE]') return null;
  try {
    return JSON.parse(data);
  } catch {
    return null;
  }
}

/** Route one event to the typed handlers (``onEvent`` always sees it first). */
export function dispatchEvent(event, handlers = {}) {
  if (!event || typeof event !== 'object') return;
  handlers.onEvent?.(event);

  switch (event.type) {
    case 'text_delta':
      handlers.onTextDelta?.(event.text);
      break;
    case 'tool_start':
      handlers.onToolStart?.(event);
      break;
    case 'tool_result':
      handlers.onToolResult?.(event);
      break;
    case 'file_write':
      handlers.onFileWrite?.(event);
      break;
    case 'approval_needed':
      handlers.onApprovalNeeded?.(event);
      break;
    case 'approval_resolved':
      handlers.onApprovalResolved?.(event);
      break;
    case 'plan_step':
      handlers.onPlanStep?.(event);
      break;
    case 'terminal_output':
      handlers.onTerminalOutput?.(event);
      break;
    case 'terminal_exit':
      handlers.onTerminalExit?.(event);
      break;
    case 'test_result':
      handlers.onTestResult?.(event);
      break;
    case 'diagnostics':
      handlers.onDiagnostics?.(event);
      break;
    case 'status_change':
      handlers.onStatusChange?.(event.status, event.message);
      break;
    case 'done':
      handlers.onDone?.(event);
      break;
    case 'error':
      handlers.onError?.(event);
      break;
    default:
      break;
  }
}

/**
 * Stream a chat message via the V2 SSE endpoint.
 *
 * ``options``: ``headers`` (auth), ``permission_mode`` (default ``normal``),
 * ``topology_id``, ``token`` (a provider token, for API clients that keep
 * their own; the web app sends none — the server uses the GitPilot session's
 * GitHub or GitLab connection, by the session repository's provider),
 * ``busyRetryDelaysMs`` (waits before each retry of a 503; default
 * ``[1500, 4000]``, a ``Retry-After`` header wins, capped at 10 s) and
 * ``fetch`` (an implementation to use instead of the global, for tests).
 *
 * ``onRetry({attempt, status, delayMs})`` fires before each retry. When the
 * retries are spent, ``onError`` gets ``{status: 503, busy: true}``.
 *
 * Returns a function that aborts the stream. Aborting is silent: no handler
 * fires, because the caller asked for it.
 */
export function streamChat(sessionId, message, handlers = {}, options = {}) {
  const controller = new AbortController();
  activeControllers.set(sessionId, controller);
  const fetchImpl = options.fetch || globalThis.fetch;

  const body = {
    session_id: sessionId,
    message,
    permission_mode: options.permission_mode || 'normal',
  };
  if (options.topology_id) body.topology_id = options.topology_id;
  if (options.token) body.token = options.token;

  const busyRetryDelays = options.busyRetryDelaysMs || DEFAULT_BUSY_RETRY_DELAYS_MS;

  const run = async () => {
    try {
      let res;
      for (let attempt = 0; ; attempt++) {
        res = await fetchImpl(apiUrl('/api/v2/chat/stream'), {
          method: 'POST',
          credentials: options.credentials || API_CREDENTIALS,
          headers: jsonHeaders(options),
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        if (res.status !== BUSY_STATUS || attempt >= busyRetryDelays.length) break;
        try { await res.body?.cancel(); } catch { /* already closed */ }
        const delayMs = retryDelayMs(res, busyRetryDelays[attempt]);
        handlers.onRetry?.({ attempt: attempt + 1, status: res.status, delayMs });
        await sleep(delayMs, controller.signal);
        if (controller.signal.aborted) return;
      }

      if (!res.ok || !res.body) {
        // ``busy``: turned away before the run started, retries spent — the
        // caller may send the message another way.
        handlers.onError?.({
          type: 'error',
          error: `Server returned ${res.status}`,
          status: res.status,
          busy: res.status === BUSY_STATUS,
        });
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        // Blocks end at a blank line; a block split across chunks waits in the buffer.
        const parts = buffer.split(/\r?\n\r?\n/);
        buffer = parts.pop() || '';
        for (const part of parts) dispatchEvent(parseSseBlock(part), handlers);
      }
      // A server that closes without a trailing blank line still sent an event.
      if (buffer.trim()) dispatchEvent(parseSseBlock(buffer), handlers);
    } catch (err) {
      if (controller.signal.aborted) {
        // User cancelled — not an error
        return;
      }
      handlers.onError?.({ type: 'error', error: String(err) });
    } finally {
      activeControllers.delete(sessionId);
    }
  };

  run();

  return () => {
    controller.abort();
    activeControllers.delete(sessionId);
  };
}

/**
 * Abort the active SSE stream for a session (local side only).
 */
export function cancelStream(sessionId) {
  const controller = activeControllers.get(sessionId);
  if (controller) {
    controller.abort();
    activeControllers.delete(sessionId);
  }
}

/**
 * Stop a run: abort the local stream and ask the backend to cancel it.
 * Aborting the fetch alone leaves the agent working server-side.
 */
export async function cancelRun(sessionId, options = {}) {
  cancelStream(sessionId);
  const fetchImpl = options.fetch || globalThis.fetch;
  try {
    const res = await fetchImpl(apiUrl('/api/v2/agent/cancel'), {
      method: 'POST',
      credentials: options.credentials || API_CREDENTIALS,
      headers: jsonHeaders(options),
      body: JSON.stringify({ session_id: sessionId }),
    });
    return res.ok;
  } catch (err) {
    console.error('[GitPilot] Cancel request failed:', err);
    return false;
  }
}

/**
 * Send an approval response to the backend.
 */
export async function respondToApproval(sessionId, requestId, approved, scope = 'once', options = {}) {
  const fetchImpl = options.fetch || globalThis.fetch;
  try {
    const res = await fetchImpl(apiUrl('/api/v2/approval/respond'), {
      method: 'POST',
      credentials: options.credentials || API_CREDENTIALS,
      headers: jsonHeaders(options),
      body: JSON.stringify({
        session_id: sessionId,
        request_id: requestId,
        approved,
        scope,
      }),
    });
    return res.ok;
  } catch (err) {
    console.error('[GitPilot] Approval response failed:', err);
    return false;
  }
}

/**
 * Check if the backend supports the V2 streaming API.
 * Call this once on startup to decide whether to use SSE or batch.
 */
export async function checkV2Support(options = {}) {
  const fetchImpl = options.fetch || globalThis.fetch;
  try {
    const res = await fetchImpl(apiUrl('/api/status'));
    if (!res.ok) return false;
    // If the server is running, v2 endpoints are available
    // (they're part of the same api.py)
    return true;
  } catch {
    return false;
  }
}
