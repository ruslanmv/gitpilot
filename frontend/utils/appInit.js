/**
 * GitPilot App Initialization — Single Source of Truth.
 *
 * Best-practice bootstrap pattern:
 * - Runs EXACTLY ONCE per page load (even with React StrictMode)
 * - Two-phase strategy: fast ping → then parallel status fetch
 * - Long retry budget for slow backends (WSL, HF Spaces cold start)
 * - Shared result between App.jsx and LoginPage.jsx
 * - No duplicate polling, no race conditions
 *
 * Phase 1 — Readiness probe (/api/ping):
 *   /api/ping is a zero-dependency endpoint that responds instantly
 *   once uvicorn is listening. We poll it with short timeouts and many
 *   retries to detect backend readiness WITHOUT wasting time on the
 *   heavy /api/status endpoint (which does GitHub API checks).
 *
 * Phase 2 — Data fetch (/api/status + /api/auth/status):
 *   Only after ping succeeds, we fetch the real status data in parallel.
 *   These can still be slow (GitHub API, LLM provider probes) but the
 *   user already sees the login page.
 *
 * API call budget per page load:
 *   - Best case:  2-3 × /api/ping + 1 × /api/status + 1 × /api/auth/status
 *   - Worst case: up to 30 × /api/ping + 1 + 1  (60s timeout budget)
 */
import { safeFetchJSON, apiUrl } from './api.js';

// Module-level singleton — survives React StrictMode double-mount
let _initPromise = null;
let _initResult = null;

const PING_MAX_ATTEMPTS = 30;     // up to ~60s of readiness polling
const PING_INTERVAL_MS = 2000;    // 2s between pings
const PING_TIMEOUT_MS = 4000;     // each ping gives up after 4s
const STATUS_TIMEOUT_MS = 15000;  // once ready, status fetch has 15s

/**
 * Wait for the backend to become reachable by polling /api/ping.
 * This is a zero-dependency endpoint that responds instantly once
 * uvicorn is listening — much faster than /api/status which does
 * GitHub API checks.
 *
 * @returns {Promise<boolean>} true if backend became reachable, false otherwise
 */
async function waitForBackend() {
  for (let i = 0; i < PING_MAX_ATTEMPTS; i++) {
    try {
      const result = await safeFetchJSON(
        apiUrl('/api/ping'),
        { timeout: PING_TIMEOUT_MS }
      );
      if (result && (result.ok === true || result.service)) {
        console.log(
          `[initApp] ✅ Backend reachable after ${i + 1} ping attempt(s) ` +
          `(${(i * PING_INTERVAL_MS) / 1000}s elapsed)`
        );
        return true;
      }
    } catch (err) {
      // Silent — we expect failures during cold start
      if (i === 0 || i % 5 === 0) {
        console.log(
          `[initApp] Waiting for backend... ` +
          `attempt ${i + 1}/${PING_MAX_ATTEMPTS}`
        );
      }
    }
    // Wait before next ping (except after last attempt)
    if (i < PING_MAX_ATTEMPTS - 1) {
      await new Promise((r) => setTimeout(r, PING_INTERVAL_MS));
    }
  }
  return false;
}

/**
 * Initialize the app.
 * Phase 1: poll /api/ping until backend is reachable
 * Phase 2: fetch /api/status and /api/auth/status in parallel
 *
 * @returns {Promise<{status: object|null, authMode: string, ready: boolean, error: string|null}>}
 */
export function initApp() {
  if (_initPromise) {
    return _initPromise;
  }

  _initPromise = (async () => {
    // ── Phase 1: wait for backend to be reachable ──
    const reachable = await waitForBackend();

    if (!reachable) {
      console.error(
        `[initApp] ❌ Backend did not respond after ${PING_MAX_ATTEMPTS} ping attempts ` +
        `(${(PING_MAX_ATTEMPTS * PING_INTERVAL_MS) / 1000}s). Giving up.`
      );
      _initResult = {
        status: null,
        authMode: 'device',
        ready: false,
        error: 'Backend did not become reachable. Please check that the server is running.',
      };
      return _initResult;
    }

    // ── Phase 2: fetch real data in parallel ──
    try {
      console.log('[initApp] Fetching /api/status + /api/auth/status in parallel...');
      const [status, authStatus] = await Promise.all([
        safeFetchJSON(apiUrl('/api/status'), { timeout: STATUS_TIMEOUT_MS }),
        safeFetchJSON(apiUrl('/api/auth/status'), { timeout: STATUS_TIMEOUT_MS })
          .catch(() => null),
      ]);

      console.log('[initApp] ✅ Init complete');
      _initResult = {
        status,
        authMode: (authStatus && authStatus.mode) || 'device',
        ready: true,
        error: null,
      };
      return _initResult;
    } catch (err) {
      // Backend was reachable via ping but status fetch failed
      // Still return ready:true so UI can proceed with limited state
      console.warn(
        `[initApp] Status fetch failed after ping succeeded: ${err.message || err}. ` +
        `Proceeding with limited state.`
      );
      _initResult = {
        status: null,
        authMode: 'device',
        ready: true,  // backend is up, just slow
        error: null,
      };
      return _initResult;
    }
  })();

  return _initPromise;
}

/**
 * Get the cached init result (null if init hasn't completed yet).
 */
export function getInitResult() {
  return _initResult;
}

/**
 * Reset the init singleton. Call this only when you need to force
 * a re-initialization (e.g., after the user manually clicks "Retry").
 */
export function resetInit() {
  _initPromise = null;
  _initResult = null;
}
