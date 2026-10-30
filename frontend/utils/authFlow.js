/**
 * Pure helpers for the /auth page: which view a URL asks for, and the
 * per-tab record of a pending OAuth redirect.
 *
 * Before the browser leaves for GitHub or GitLab, the page stores the
 * provider and the server-issued `state` in sessionStorage (per tab, gone when
 * the tab closes). On return, a callback is only processed when its `state`
 * matches that record and is less than ten minutes old, so a callback link
 * produced by someone else's sign-in (login CSRF) is refused before it reaches
 * the server — which also checks the state, single-use, on its side.
 */

export const PENDING_KEY = "gitpilot_oauth_pending";
export const PENDING_TTL_MS = 10 * 60 * 1000;

function storage() {
  try {
    return typeof sessionStorage !== "undefined" ? sessionStorage : null;
  } catch {
    return null;
  }
}

export function readPending() {
  try {
    return JSON.parse((storage() && storage().getItem(PENDING_KEY)) || "null");
  } catch {
    return null;
  }
}

export function writePending(value) {
  try { storage() && storage().setItem(PENDING_KEY, JSON.stringify(value)); } catch { /* storage blocked */ }
}

export function clearPending() {
  try {
    const s = storage();
    if (!s) return;
    s.removeItem(PENDING_KEY);
    // Keys used by earlier versions.
    s.removeItem("gitpilot_oauth_state");
    s.removeItem("gitpilot_gitlab_oauth_state");
  } catch { /* storage blocked */ }
}

/**
 * What the /auth URL asks for.
 * @returns {{view: string, provider?: string, trusted?: boolean, mode?: string}}
 */
export function initialView(location, pending, now = Date.now()) {
  const params = new URLSearchParams(location.search || "");
  const path = location.pathname || "";
  if (params.get("token") && path.includes("verify-email")) return { view: "verify" };
  if (params.get("token") && path.includes("reset-password")) return { view: "reset" };
  if ((params.get("code") || params.get("error")) && params.get("state")) {
    const state = params.get("state");
    const fresh = !!pending && pending.state === state && now - (pending.startedAt || 0) < PENDING_TTL_MS;
    return { view: "callback", provider: fresh ? pending.provider : null, trusted: fresh };
  }
  const asked = params.get("view") || params.get("connect");
  if (asked === "github" || asked === "gitlab") return { view: "provider", provider: asked };
  return { view: "email", mode: params.get("mode") === "signup" ? "signup" : "signin" };
}

/** A readable message from a FastAPI error body. */
export function errorText(data, fallback) {
  const detail = data && (data.detail || data.error);
  if (Array.isArray(detail)) return detail.map((d) => (d && d.msg) || String(d)).join(" ");
  return typeof detail === "string" && detail ? detail : fallback;
}
