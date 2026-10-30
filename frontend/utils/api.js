/**
 * API utilities for authenticated requests
 */
import { resolveBackendUrl } from "./backend.js";

/**
 * Backend base URL — VITE_BACKEND_URL if set, else the hosted GitPilot backend
 * (so production works without the env var), else relative for local dev.
 */
const BACKEND_URL = resolveBackendUrl();

/**
 * Check if backend URL is configured
 * @returns {boolean} True if backend URL is set
 */
export function isBackendConfigured() {
  return BACKEND_URL !== '' && BACKEND_URL !== undefined;
}

/**
 * Get the configured backend URL
 * @returns {string} Backend URL or empty string
 */
export function getBackendUrl() {
  return BACKEND_URL;
}

/**
 * Construct full API URL
 * @param {string} path - API endpoint path (e.g., '/api/chat/plan')
 * @returns {string} Full URL to API endpoint
 */
export function apiUrl(path) {
  // Idempotent: an already-absolute URL is returned untouched, so wrapping a
  // call site in apiUrl() is always safe even if the value was already absolute.
  if (/^https?:\/\//i.test(path)) return path;
  // Ensure path starts with /
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  return `${BACKEND_URL}${cleanPath}`;
}

/**
 * Enhanced fetch with better error handling for JSON parsing
 * @param {string} url - URL to fetch
 * @param {Object} options - Fetch options
 * @returns {Promise<any>} Parsed JSON response
 */
export async function safeFetchJSON(url, options = {}) {
  try {
    // Add timeout to prevent hanging when backend is starting up.
    // Default raised to 15s to tolerate first-load GitHub API checks.
    const timeout = options.timeout || 15000;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    const fetchOptions = { ...options, signal: options.signal || controller.signal };
    delete fetchOptions.timeout;

    let response;
    try {
      response = await fetch(url, fetchOptions);
    } finally {
      clearTimeout(timer);
    }
    const contentType = response.headers.get('content-type');

    // Check if response is actually JSON
    if (!contentType || !contentType.includes('application/json')) {
      // If not JSON, it might be an HTML error page
      const text = await response.text();

      // Check if it looks like HTML (starts with <!doctype or <html)
      if (text.trim().toLowerCase().startsWith('<!doctype') ||
          text.trim().toLowerCase().startsWith('<html')) {
        throw new Error(
          `Backend not reachable. Received HTML instead of JSON. ` +
          `${!isBackendConfigured() ? 'VITE_BACKEND_URL environment variable is not configured. ' : ''}` +
          `Please check your backend configuration.`
        );
      }

      // Try to return the text as-is if not HTML
      throw new Error(`Unexpected response type: ${contentType || 'unknown'}. Response: ${text.substring(0, 100)}`);
    }

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || data.error || data.message || `Request failed with status ${response.status}`);
    }

    return data;
  } catch (error) {
    // Re-throw with better error message
    if (error.name === 'AbortError') {
      throw new Error(
        `Backend server did not respond in time. ` +
        `Please check that the backend is running at ${BACKEND_URL || 'localhost:8000'}.`
      );
    }
    if (error.message.includes('Failed to fetch') || error.message.includes('NetworkError')) {
      throw new Error(
        `Cannot connect to backend server. ` +
        `${!isBackendConfigured() ? 'VITE_BACKEND_URL environment variable is not configured. ' : ''}` +
        `Please check that the backend is running and accessible.`
      );
    }
    throw error;
  }
}

// ─── Web session (server-side) ───────────────────────────────────────────
// GitHub and GitLab access tokens live on the server, encrypted, attached to a
// GitPilot web session. The browser holds only that session's id, preferably in
// an HttpOnly cookie it cannot read. When the SPA and the API are on different
// sites and the browser blocks the cookie, the id is kept here instead and sent
// in the X-GitPilot-Session header (see adoptSession).
const SESSION_TOKEN_KEY = 'gitpilot_session_token';
// Provider tokens stored by earlier versions; migrated once, then deleted.
const LEGACY_TOKEN_KEYS = { github: 'github_token', gitlab: 'gitlab_token' };
const LEGACY_KEYS = ['github_token', 'github_user', 'gitlab_token', 'gitlab_user', 'gitpilot_git_provider'];

let csrfToken = '';

export function getSessionToken() {
  try { return localStorage.getItem(SESSION_TOKEN_KEY) || ''; } catch { return ''; }
}

export function setSessionToken(token) {
  try {
    if (token) localStorage.setItem(SESSION_TOKEN_KEY, token);
    else localStorage.removeItem(SESSION_TOKEN_KEY);
  } catch { /* storage may be blocked */ }
}

export function clearSessionToken() {
  setSessionToken('');
}

/** Remember the session's CSRF token (memory only; re-read from /api/auth/session). */
export function setCsrfToken(token) {
  csrfToken = token || '';
}

export function getCsrfToken() {
  return csrfToken;
}

/**
 * Headers every API call carries: the session id when the cookie cannot be
 * used, and the CSRF token that cookie-authenticated writes must echo. No
 * GitHub or GitLab token is ever sent from the browser.
 * @returns {Object} Headers object
 */
export function getAuthHeaders() {
  const headers = {};
  const sessionToken = getSessionToken();
  if (sessionToken) headers['X-GitPilot-Session'] = sessionToken;
  if (csrfToken) headers['X-GitPilot-CSRF'] = csrfToken;
  return headers;
}

/**
 * Make an authenticated fetch request (session cookie + headers).
 * @param {string} url - API endpoint URL
 * @param {Object} options - Fetch options
 * @returns {Promise<Response>} Fetch response
 */
export async function authFetch(url, options = {}) {
  const headers = {
    ...getAuthHeaders(),
    ...options.headers,
  };

  // Resolve root-relative paths against the configured backend so callers that
  // pass "/api/..." work on the split frontend(Vercel)/backend(HF) deployment.
  return fetch(apiUrl(url), {
    credentials: 'include',
    ...options,
    headers,
  });
}

/**
 * Make an authenticated JSON request
 * @param {string} url - API endpoint URL
 * @param {Object} options - Fetch options
 * @returns {Promise<any>} Parsed JSON response
 */
export async function authFetchJSON(url, options = {}) {
  const response = await authFetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    const err = new Error(error.detail || error.message || 'Request failed');
    err.status = response.status;
    throw err;
  }

  return response.json();
}

/**
 * Current session: who is signed in, which providers are connected, and the
 * CSRF token. Never contains a provider access token.
 * @returns {Promise<{authenticated:boolean, account:?Object, providers:Object, csrf_token:?string}>}
 */
export async function fetchSession() {
  const res = await authFetch('/api/auth/session');
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || 'Could not read the session.');
  setCsrfToken(data.csrf_token || '');
  if (data.session_token) await adoptSession(data);
  return data;
}

/**
 * Take over the session a sign-in response created. If the browser kept the
 * HttpOnly cookie, the session id is not stored anywhere script-readable;
 * otherwise (cross-site deployment with third-party cookies blocked) it is
 * kept for the X-GitPilot-Session header.
 */
export async function adoptSession(data) {
  if (!data) return;
  if (data.csrf_token) setCsrfToken(data.csrf_token);
  if (!data.session_token) return;
  // Probe with the cookie only (no header): if it already authenticates, the
  // browser kept the HttpOnly cookie and the id need not be script-readable.
  // Account sign-in responses carry no CSRF token, so the probe's is used.
  let cookieWorks = false;
  try {
    const probe = await fetch(apiUrl('/api/auth/session'), { credentials: 'include' });
    const body = await probe.json().catch(() => ({}));
    cookieWorks = !!(body && body.authenticated && body.transport === 'cookie'
      && (!data.csrf_token || body.csrf_token === data.csrf_token));
    if (cookieWorks && body.csrf_token) setCsrfToken(body.csrf_token);
  } catch { /* offline: fall back to the header */ }
  setSessionToken(cookieWorks ? '' : data.session_token);
}

/** True when a token from an earlier GitPilot version is still in localStorage. */
export function hasLegacyTokens() {
  try {
    return Object.values(LEGACY_TOKEN_KEYS).some((k) => !!localStorage.getItem(k));
  } catch { return false; }
}

/**
 * One-time migration: hand any provider token an earlier version kept in
 * localStorage to the server (it is validated there), then delete it from the
 * browser. A token the provider rejects is simply dropped.
 *
 * When the backend does not offer the import yet (404 — e.g. a frontend
 * deployed minutes before its backend), or cannot be reached, the tokens are
 * kept so the migration can run once the backend is upgraded.
 */
export async function migrateLegacyTokens() {
  let migrated = false;
  let settled = true;
  for (const [provider, key] of Object.entries(LEGACY_TOKEN_KEYS)) {
    let token = '';
    try { token = localStorage.getItem(key) || ''; } catch { /* blocked */ }
    if (!token) continue;
    try {
      const res = await authFetch('/api/auth/session/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, access_token: token }),
      });
      if (res.ok) {
        await adoptSession(await res.json());
        migrated = true;
      } else if (res.status === 404 || res.status >= 500) {
        settled = false; // backend not upgraded or unavailable: retry on a later load
      }
    } catch {
      settled = false;
    }
  }
  if (settled) {
    try { LEGACY_KEYS.forEach((k) => localStorage.removeItem(k)); } catch { /* blocked */ }
  }
  return migrated;
}

/** Sign out: revoke the server session (and its provider grants) and forget it locally. */
export async function logoutSession() {
  try {
    await authFetch('/api/auth/logout', { method: 'POST' });
  } catch { /* the local state is cleared regardless */ }
  clearSessionToken();
  setCsrfToken('');
  try { LEGACY_KEYS.forEach((k) => localStorage.removeItem(k)); } catch { /* blocked */ }
}

/** Disconnect one provider from the session (and revoke its token at the provider). */
export async function disconnectProvider(provider) {
  return authFetchJSON(`/api/auth/providers/${encodeURIComponent(provider)}/disconnect`, { method: 'POST' });
}

/** Which sign-in methods this server offers ({github:{web,device}, gitlab:{web,host}, accounts}). */
export async function fetchAuthProviders() {
  const res = await fetch(apiUrl('/api/auth/providers'), { credentials: 'include' });
  if (!res.ok) throw new Error('Could not load sign-in options.');
  return res.json();
}

/**
 * A short-lived, single-use ticket for opening a WebSocket (browsers cannot
 * send headers on a WebSocket handshake, and the cookie may be cross-site).
 */
export async function fetchWsTicket() {
  try {
    const data = await authFetchJSON('/api/auth/ws-ticket', { method: 'POST' });
    return data.ticket || '';
  } catch {
    return '';
  }
}

// ─── Redesigned API Endpoints ────────────────────────────

/**
 * Get normalized server status
 */
export async function fetchStatus() {
  return safeFetchJSON(apiUrl("/api/status"));
}

/**
 * Get server status with retry (for startup when backend may still be booting).
 * Retries up to `maxRetries` times with `delayMs` between attempts.
 * @param {number} maxRetries - Maximum retry attempts (default: 8)
 * @param {number} delayMs - Delay between retries in ms (default: 2000)
 * @returns {Promise<any>} Parsed status response or null
 */
export async function fetchStatusWithRetry(maxRetries = 8, delayMs = 2000) {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await safeFetchJSON(apiUrl("/api/status"), { timeout: 5000 });
    } catch {
      if (i < maxRetries - 1) {
        await new Promise((r) => setTimeout(r, delayMs));
      }
    }
  }
  return null;
}

/**
 * Get detailed provider status
 */
export async function fetchProviderStatus() {
  return safeFetchJSON(apiUrl("/api/providers/status"));
}

/**
 * Test a provider configuration
 */
export async function testProvider(providerConfig) {
  return safeFetchJSON(apiUrl("/api/providers/test"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(providerConfig),
  });
}

/**
 * Start a session by mode
 */
export async function startSession(sessionConfig) {
  return safeFetchJSON(apiUrl("/api/session/start"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sessionConfig),
  });
}

/**
 * Send a chat message (redesigned endpoint)
 */
export async function sendChatMessage(messageConfig) {
  return safeFetchJSON(apiUrl("/api/chat/send"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(messageConfig),
  });
}

/**
 * Get workspace summary
 */
export async function fetchWorkspaceSummary(folderPath) {
  const query = folderPath ? `?folder_path=${encodeURIComponent(folderPath)}` : "";
  return safeFetchJSON(apiUrl(`/api/workspace/summary${query}`));
}

/**
 * Run security scan on workspace
 */
export async function scanWorkspace(path) {
  const query = path ? `?path=${encodeURIComponent(path)}` : "";
  return safeFetchJSON(apiUrl(`/api/security/scan-workspace${query}`));
}