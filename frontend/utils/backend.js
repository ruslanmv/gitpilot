// Resolve the GitPilot backend base URL — one source of truth for the frontend.
//
// Priority:
//   1) VITE_BACKEND_URL (build-time override; set this on Vercel to the HF Space).
//   2) "" (same-origin / Vite proxy) for local dev on localhost.
//   3) The hosted GitPilot backend, so gitpilot.ruslanmv.com works out of the box
//      even when VITE_BACKEND_URL is unset (no more "Connecting to backend…" freeze).
//
// To point Vercel at a different backend, set VITE_BACKEND_URL in the Vercel
// project's Environment Variables and redeploy.

const HOSTED_BACKEND = "https://ruslanmv-gitpilot.hf.space";

export function resolveBackendUrl() {
  const env = import.meta.env.VITE_BACKEND_URL;
  if (env && String(env).trim()) {
    return String(env).trim().replace(/\/+$/, "");
  }
  if (typeof window !== "undefined") {
    const host = window.location.hostname;
    if (host === "localhost" || host === "127.0.0.1" || host === "0.0.0.0") {
      return ""; // relative — Vite dev proxy forwards to the local backend
    }
  }
  return HOSTED_BACKEND;
}

export const HOSTED_BACKEND_URL = HOSTED_BACKEND;
