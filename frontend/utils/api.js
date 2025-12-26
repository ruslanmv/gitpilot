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