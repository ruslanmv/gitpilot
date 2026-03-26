/**
 * API utilities for authenticated requests
 */

/**
 * Get backend URL from environment or use relative path (for local dev)
 * - Production (Vercel): Uses VITE_BACKEND_URL env var (e.g., https://gitpilot-backend.onrender.com)
 * - Development (local): Uses relative paths (proxied by Vite to localhost:8000)
 */
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || '';

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
    const response = await fetch(url, options);
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

/**
 * Get authorization headers with GitHub token
 * @returns {Object} Headers object with Authorization if token exists
 */
export function getAuthHeaders() {
  const token = localStorage.getItem('github_token');

  if (!token) {
    return {};
  }

  return {
    'Authorization': `Bearer ${token}`,
  };
}

/**
 * Make an authenticated fetch request
 * @param {string} url - API endpoint URL
 * @param {Object} options - Fetch options
 * @returns {Promise<Response>} Fetch response
 */
export async function authFetch(url, options = {}) {
  const headers = {
    ...getAuthHeaders(),
    ...options.headers,
  };

  return fetch(url, {
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
  const headers = {
    'Content-Type': 'application/json',
    ...getAuthHeaders(),
    ...options.headers,
  };

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Request failed' }));
    throw new Error(error.detail || error.message || 'Request failed');
  }

  return response.json();
}

// ─── Redesigned API Endpoints ────────────────────────────

/**
 * Get normalized server status
 */
export async function fetchStatus() {
  return safeFetchJSON(apiUrl("/api/status"));
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