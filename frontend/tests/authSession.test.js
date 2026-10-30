// frontend/tests/authSession.test.js — browser side of server-side sessions.
//
// The browser must never send a GitHub or GitLab token; it sends a GitPilot
// session (cookie, or header when the cookie is blocked) and echoes the CSRF
// token. Tokens left in localStorage by earlier versions are handed to the
// server once and deleted.
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";

class MemoryStorage {
  constructor() { this.map = new Map(); }
  getItem(k) { return this.map.has(k) ? this.map.get(k) : null; }
  setItem(k, v) { this.map.set(k, String(v)); }
  removeItem(k) { this.map.delete(k); }
}

globalThis.localStorage = new MemoryStorage();

const api = await import("../utils/api.js");

let calls = [];
function mockFetch(handler) {
  calls = [];
  globalThis.fetch = async (url, opts = {}) => {
    calls.push({ url, opts });
    const { status = 200, body = {} } = (await handler(url, opts)) || {};
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
      headers: { get: () => "application/json" },
    };
  };
}

beforeEach(() => {
  globalThis.localStorage = new MemoryStorage();
  api.setCsrfToken("");
});

test("auth headers never carry a provider token", () => {
  localStorage.setItem("github_token", "gho_legacy");
  localStorage.setItem("gitlab_token", "gl_legacy");
  localStorage.setItem("gitpilot_git_provider", "gitlab");
  const headers = api.getAuthHeaders();
  assert.equal(headers.Authorization, undefined);
  assert.equal(headers["X-GitPilot-Git-Provider"], undefined);
});

test("session header only in header mode, CSRF token whenever known", () => {
  assert.deepEqual(api.getAuthHeaders(), {});
  api.setCsrfToken("csrf-1");
  api.setSessionToken("gps_header");
  assert.deepEqual(api.getAuthHeaders(), { "X-GitPilot-Session": "gps_header", "X-GitPilot-CSRF": "csrf-1" });
});

test("authFetch sends cookies and the CSRF header", async () => {
  api.setCsrfToken("csrf-2");
  mockFetch(() => ({ body: {} }));
  await api.authFetch("/api/x", { method: "POST" });
  assert.equal(calls[0].opts.credentials, "include");
  assert.equal(calls[0].opts.headers["X-GitPilot-CSRF"], "csrf-2");
});

test("a working cookie means the session id is not kept in storage", async () => {
  mockFetch(() => ({ body: { authenticated: true, transport: "cookie", csrf_token: "c" } }));
  await api.adoptSession({ session_token: "gps_x", csrf_token: "c" });
  assert.equal(api.getSessionToken(), "");
  assert.equal(api.getCsrfToken(), "c");
});

test("a blocked cookie falls back to the header token", async () => {
  mockFetch(() => ({ body: { authenticated: false, csrf_token: null } }));
  await api.adoptSession({ session_token: "gps_y", csrf_token: "c2" });
  assert.equal(api.getSessionToken(), "gps_y");
});

test("legacy tokens are imported once and removed from the browser", async () => {
  localStorage.setItem("github_token", "gho_old");
  localStorage.setItem("github_user", "{}");
  localStorage.setItem("gitlab_token", "gl_old");
  localStorage.setItem("gitpilot_git_provider", "gitlab");
  const imported = [];
  mockFetch((url, opts) => {
    if (url.endsWith("/api/auth/session/import")) {
      imported.push(JSON.parse(opts.body));
      return { body: { session_token: "gps_z", csrf_token: "c3" } };
    }
    return { body: { csrf_token: "c3" } };
  });
  assert.equal(api.hasLegacyTokens(), true);
  assert.equal(await api.migrateLegacyTokens(), true);
  assert.deepEqual(imported, [
    { provider: "github", access_token: "gho_old" },
    { provider: "gitlab", access_token: "gl_old" },
  ]);
  for (const key of ["github_token", "github_user", "gitlab_token", "gitpilot_git_provider"]) {
    assert.equal(localStorage.getItem(key), null, key);
  }
  assert.equal(api.hasLegacyTokens(), false);
});

test("a token the provider rejects is still deleted locally", async () => {
  localStorage.setItem("gitlab_token", "gl_revoked");
  mockFetch(() => ({ status: 401, body: { detail: "nope" } }));
  assert.equal(await api.migrateLegacyTokens(), false);
  assert.equal(localStorage.getItem("gitlab_token"), null);
});

test("logout revokes server-side and forgets the session locally", async () => {
  api.setSessionToken("gps_q");
  api.setCsrfToken("c4");
  mockFetch(() => ({ body: { authenticated: false } }));
  await api.logoutSession();
  assert.ok(calls[0].url.endsWith("/api/auth/logout"));
  assert.equal(calls[0].opts.method, "POST");
  assert.equal(api.getSessionToken(), "");
  assert.equal(api.getCsrfToken(), "");
});

test("tokens are kept when the backend cannot import them yet (frontend deployed first)", async () => {
  localStorage.setItem("github_token", "gho_keep");
  mockFetch(() => ({ status: 404, body: { detail: "Not Found" } }));
  assert.equal(await api.migrateLegacyTokens(), false);
  assert.equal(localStorage.getItem("github_token"), "gho_keep");
  mockFetch(() => { throw new Error("offline"); });
  await api.migrateLegacyTokens();
  assert.equal(localStorage.getItem("github_token"), "gho_keep");
});

test("account sign-in (no CSRF token in the response) with a working cookie stores nothing", async () => {
  mockFetch(() => ({ body: { authenticated: true, transport: "cookie", csrf_token: "from-probe" } }));
  await api.adoptSession({ session_token: "gps_acct", email: "a@b.c" });
  assert.equal(api.getSessionToken(), "");
  assert.equal(api.getCsrfToken(), "from-probe");
  assert.equal(calls[0].opts.headers, undefined); // the probe sends the cookie only
});
