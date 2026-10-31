// Resolve the GitPilot backend base URL — one source of truth for the frontend.
//
// Priority:
//   1) VITE_BACKEND_URL (build-time override; set this on Vercel to the HF Space).
//   2) "" (same-origin / Vite proxy) for local dev on localhost.
//   3) "" (same-origin) on a Hugging Face Space (*.hf.space): the Space's own
//      FastAPI serves this build, so a duplicated Space talks to its own backend
//      and its session cookie stays first-party.
//   4) The hosted GitPilot backend, so gitpilot.ruslanmv.com works out of the box
//      even when VITE_BACKEND_URL is unset (no more "Connecting to backend…" freeze).
//
// To point Vercel at a different backend, set VITE_BACKEND_URL in the Vercel
// project's Environment Variables and redeploy.

const HOSTED_BACKEND = "https://ruslanmv-gitpilot.hf.space";

/** Backend base URL for a page served from ``hostname`` (exported for tests). */
export function backendUrlForHost(hostname, env) {
  if (env && String(env).trim()) {
    return String(env).trim().replace(/\/+$/, "");
  }
  const host = String(hostname || "").toLowerCase();
  if (host === "localhost" || host === "127.0.0.1" || host === "0.0.0.0") {
    return ""; // relative — Vite dev proxy forwards to the local backend
  }
  if (host.endsWith(".hf.space")) {
    return ""; // served by the Space's own backend
  }
  return HOSTED_BACKEND;
}

export function resolveBackendUrl() {
  // ``import.meta.env`` is Vite's; under plain Node (the unit tests) it is absent.
  const env = (import.meta.env || {}).VITE_BACKEND_URL;
  const host = typeof window !== "undefined" ? window.location.hostname : "";
  return backendUrlForHost(host, env);
}

export const HOSTED_BACKEND_URL = HOSTED_BACKEND;

// Credentials mode for every API request. "same-origin": the session cookie is
// sent when the page and the API share an origin (the Space's own UI, local dev
// through the Vite proxy), and never across sites. Production
// (gitpilot.ruslanmv.com → *.hf.space) authenticates with the
// X-GitPilot-Session header instead: the Hugging Face proxy answers CORS
// preflights itself without Access-Control-Allow-Credentials, so any
// credentialed request that needs a preflight (JSON bodies, the session header)
// is blocked by the browser ("Failed to fetch"). Cross-site cookies are blocked
// anyway (SameSite=Lax, third-party cookie blocking). Do not change to "include".
export const API_CREDENTIALS = "same-origin";
