import { resolveBackendUrl } from "./backend.js";
/**
 * SSE (Server-Sent Events) client for GitPilot V2 streaming API.
 *
 * Usage:
 *   import { streamChat, cancelStream } from '../utils/sse';
 *
 *   const unsubscribe = streamChat(sessionId, message, {
 *     onTextDelta: (text) => appendToChat(text),
 *     onToolStart: (data) => showToolActivity(data),
 *     onToolResult: (data) => updateToolActivity(data),
 *     onApprovalNeeded: (data) => showApprovalModal(data),
 *     onTerminalOutput: (data) => appendTerminal(data),
 *     onTestResult: (data) => showTestBadges(data),
 *     onDiagnostics: (data) => showDiagnostics(data),
 *     onDone: (data) => finalize(data),
 *     onError: (error) => showError(error),
 *   });
 *
 *   // To cancel:
 *   cancelStream(sessionId);
 */

const BACKEND_URL = resolveBackendUrl();

function apiUrl(path) {
  return BACKEND_URL ? `${BACKEND_URL}${path}` : path;
}

// Active abort controllers keyed by sessionId
const activeControllers = new Map();

/**
 * Stream a chat message via the V2 SSE endpoint.
 * Returns a cleanup function to abort the stream.
 */
export function streamChat(sessionId, message, handlers = {}) {
  const controller = new AbortController();
  activeControllers.set(sessionId, controller);

  const run = async () => {
    try {
      const res = await fetch(apiUrl('/api/v2/chat/stream'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          message,
          permission_mode: 'normal',
        }),
        signal: controller.signal,
      });

      if (!res.ok || !res.body) {
        handlers.onError?.({ error: `Server returned ${res.status}` });
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';

        for (const part of parts) {
          if (!part.startsWith('data: ')) continue;
          let event;
          try {
            event = JSON.parse(part.slice(6));
          } catch {
            continue;
          }

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
            case 'approval_needed':
              handlers.onApprovalNeeded?.(event);
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
          }
        }
      }
    } catch (err) {
      if (controller.signal.aborted) {
        // User cancelled — not an error
        return;
      }
      handlers.onError?.({ error: String(err) });
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
 * Cancel the active SSE stream for a session.
 */
export function cancelStream(sessionId) {
  const controller = activeControllers.get(sessionId);
  if (controller) {
    controller.abort();
    activeControllers.delete(sessionId);
  }
}

/**
 * Send an approval response to the backend.
 */
export async function respondToApproval(sessionId, requestId, approved, scope = 'once') {
  try {
    await fetch(apiUrl('/api/v2/approval/respond'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        request_id: requestId,
        approved,
        scope,
      }),
    });
  } catch (err) {
    console.error('[GitPilot] Approval response failed:', err);
  }
}

/**
 * Check if the backend supports the V2 streaming API.
 * Call this once on startup to decide whether to use SSE or batch.
 */
export async function checkV2Support() {
  try {
    const res = await fetch(apiUrl('/api/status'));
    if (!res.ok) return false;
    // If the server is running, v2 endpoints are available
    // (they're part of the same api.py)
    return true;
  } catch {
    return false;
  }
}
