import React, { useEffect, useRef, useState } from "react";
import "./auth.css";
import { resolveBackendUrl } from "../utils/backend.js";

// Premium /auth — GitPilot account (email/password) + "Continue with GitHub".
// GitPilot account = who you are; GitHub = repository access (the device flow).
//
// One layout for every state: <div.gp-auth> (full-viewport background) wraps a
// single <gp-auth-card>. Sign in, create account, and the GitHub authorization
// step all render INSIDE that same card — no nested wrappers, no second
// rectangle. The GitHub step shows "Connecting to GitHub…" with a spinner and
// then either redirects (web OAuth) or shows the device code in place.

const api = (path) => `${resolveBackendUrl()}${path}`;

async function post(path, body) {
  const r = await fetch(api(path), {
    method: "POST",
    headers: { "content-type": "application/json" },
    credentials: "include", // receive/send the HttpOnly session cookie
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try { data = await r.json(); } catch { /* empty body */ }
  return { ok: r.ok, status: r.status, data };
}

async function getJSON(path, opts = {}) {
  const r = await fetch(api(path), { credentials: "include", ...opts });
  let data = {};
  try { data = await r.json(); } catch { /* empty body */ }
  return { ok: r.ok, status: r.status, data };
}

const GithubMark = () => (
  <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.58 2 12.25c0 4.53 2.87 8.37 6.84 9.73.5.1.68-.22.68-.49v-1.7c-2.78.62-3.37-1.21-3.37-1.21-.45-1.18-1.11-1.49-1.11-1.49-.91-.64.07-.62.07-.62 1 .07 1.53 1.06 1.53 1.06.89 1.56 2.34 1.11 2.91.85.09-.66.35-1.11.63-1.36-2.22-.26-4.55-1.14-4.55-5.07 0-1.12.39-2.03 1.03-2.75-.1-.26-.45-1.3.1-2.7 0 0 .84-.28 2.75 1.05a9.3 9.3 0 0 1 5 0c1.91-1.33 2.75-1.05 2.75-1.05.55 1.4.2 2.44.1 2.7.64.72 1.03 1.63 1.03 2.75 0 3.94-2.34 4.81-4.57 5.06.36.32.68.94.68 1.9v2.82c0 .27.18.6.69.49A10.02 10.02 0 0 0 22 12.25C22 6.58 17.52 2 12 2z" /></svg>
);
const Eye = ({ off }) => (off
  ? <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.5 18.5 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19M1 1l22 22"/></svg>
  : <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
);

export default function AuthPage({ onAuthenticated, backendReady = false }) {
  const params = new URLSearchParams(window.location.search);
  const [mode, setMode] = useState(params.get("mode") === "signup" ? "signup" : "signin");
  const [view, setView] = useState("email"); // "email" | "github"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null); // {kind, text}

  // GitHub authorization state (rendered inside the same card)
  const [ghPhase, setGhPhase] = useState("connecting"); // "connecting" | "device" | "error"
  const [ghDevice, setGhDevice] = useState(null);        // {user_code, verification_uri, ...}
  const [ghError, setGhError] = useState("");
  const pollTimer = useRef(null);
  const stopPolling = useRef(false);
  const ghStarted = useRef(false);

  // Email-verification links land here with ?token=...
  useEffect(() => {
    const token = params.get("token");
    if (token && window.location.pathname.includes("verify-email")) {
      (async () => {
        const r = await post("/api/account/verify-email", { token });
        if (r.ok && typeof onAuthenticated === "function") onAuthenticated({ user: r.data });
        else setNote({ kind: "err", text: r.data.detail || "Invalid or expired link." });
      })();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── GitHub authorization (device + web OAuth), all inside the same card ──
  function finishGitHub(data) {
    stopPolling.current = true;
    if (pollTimer.current) clearTimeout(pollTimer.current);
    if (!data || !data.access_token || !data.user) {
      setGhPhase("error");
      setGhError("GitHub returned an incomplete session. Please try again.");
      return;
    }
    try {
      localStorage.setItem("github_token", data.access_token);
      localStorage.setItem("github_user", JSON.stringify(data.user));
    } catch { /* storage may be blocked */ }
    if (typeof onAuthenticated === "function") {
      onAuthenticated({ access_token: data.access_token, user: data.user });
    }
  }

  async function pollDevice(deviceCode, interval) {
    if (stopPolling.current) return;
    try {
      const r = await fetch(api("/api/auth/device/poll"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_code: deviceCode }),
      });
      if (r.status === 200) { finishGitHub(await r.json()); return; }
      if (r.status === 202) {
        pollTimer.current = setTimeout(() => pollDevice(deviceCode, interval), interval * 1000);
        return;
      }
      const err = await r.json().catch(() => ({ error: "Unknown polling error" }));
      if (err.error === "slow_down") {
        pollTimer.current = setTimeout(() => pollDevice(deviceCode, interval + 5), (interval + 5) * 1000);
        return;
      }
      throw new Error(err.error || `Polling failed: ${r.status}`);
    } catch (e) {
      if (!stopPolling.current) { setGhPhase("error"); setGhError(e.message || "Could not reach GitHub."); }
    }
  }

  async function startGitHub() {
    setGhPhase("connecting");
    setGhError("");
    setGhDevice(null);
    stopPolling.current = false;

    // If we returned from a web OAuth redirect, exchange the code first.
    const code = params.get("code");
    const state = params.get("state") || "";
    if (code) {
      window.history.replaceState({}, document.title, window.location.pathname);
      try {
        const r = await post("/api/auth/callback", { code, state });
        if (r.ok) { finishGitHub(r.data); return; }
        throw new Error(r.data.detail || "GitHub sign-in failed.");
      } catch (e) {
        setGhPhase("error"); setGhError(e.message || "GitHub sign-in failed."); return;
      }
    }

    // Try the standard web OAuth redirect; fall back to device flow.
    try {
      const r = await getJSON("/api/auth/url");
      if (r.ok && r.data.authorization_url) {
        if (r.data.state) sessionStorage.setItem("gitpilot_oauth_state", r.data.state);
        window.location.href = r.data.authorization_url; // redirect to GitHub
        return;
      }
    } catch { /* fall through to device flow */ }

    // Device flow (no client secret configured): show the code in place.
    try {
      const r = await post("/api/auth/device/code", {});
      if (r.data.error) {
        if (String(r.data.error).includes("400") || String(r.data.error).includes("Bad Request")) {
          throw new Error("Device Flow is disabled for this GitHub App. Enable it under GitHub App Settings → General → 'Enable Device Flow'.");
        }
        throw new Error(r.data.error);
      }
      if (!r.data.device_code) throw new Error("GitHub did not return a device code.");
      setGhDevice(r.data);
      setGhPhase("device");
      pollDevice(r.data.device_code, r.data.interval || 5);
    } catch (e) {
      setGhPhase("error");
      setGhError(e.message || "Could not start GitHub sign-in.");
    }
  }

  // Kick off the GitHub flow when entering the github view.
  useEffect(() => {
    if (view === "github") {
      if (ghStarted.current) return;
      ghStarted.current = true;
      startGitHub();
    } else {
      ghStarted.current = false;
      stopPolling.current = true;
      if (pollTimer.current) clearTimeout(pollTimer.current);
    }
    return () => {
      stopPolling.current = true;
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  const goEmail = () => {
    stopPolling.current = true;
    if (pollTimer.current) clearTimeout(pollTimer.current);
    setGhDevice(null);
    setGhError("");
    setView("email");
  };

  const submit = async (e) => {
    e.preventDefault();
    setNote(null);
    setBusy(true);
    try {
      if (mode === "signin") {
        const r = await post("/api/account/login", { email, password });
        if (r.ok && typeof onAuthenticated === "function") onAuthenticated({ user: r.data });
        else if (r.status === 403) setNote({ kind: "err", text: "Verify your email before signing in." });
        else setNote({ kind: "err", text: r.data.detail || "Invalid email or password." });
      } else {
        const r = await post("/api/account/signup", { email, password, name: name || null });
        if (r.status === 202) setNote({ kind: "ok", text: "Check your email — we sent a confirmation link." });
        else setNote({ kind: "err", text: r.data.detail || "Could not create the account." });
      }
    } catch {
      setNote({ kind: "err", text: "Network error — is the backend reachable?" });
    } finally {
      setBusy(false);
    }
  };

  const forgot = async () => {
    if (!email) { setNote({ kind: "err", text: "Enter your email first." }); return; }
    await post("/api/account/password/forgot", { email });
    setNote({ kind: "ok", text: "If an account exists, we'll send a reset link." });
  };

  // ── GitHub authorization card (same shell, no nested rectangle) ──
  if (view === "github") {
    return (
      <div className="gp-auth">
        <a className="gp-home" href="/">← Back to home</a>
        <div className="gp-auth-card">
          <button className="gp-back" onClick={goEmail}>← Back to sign in</button>
          <a className="gp-auth-logo" href="/" aria-label="Back to GitPilot home">GP</a>

          {ghPhase === "connecting" && (
            <>
              <h2>Connecting to GitHub…</h2>
              <p className="lead">Opening a secure authorization session for your repositories.</p>
              <div className="gp-spinwrap"><span className="gp-spin" /></div>
            </>
          )}

          {ghPhase === "device" && ghDevice && (
            <>
              <h2>Authorize GitPilot</h2>
              <p className="lead">Enter this code on GitHub to grant repository access.</p>
              <button
                type="button"
                className="gp-code"
                title="Click to copy"
                onClick={() => navigator.clipboard?.writeText(ghDevice.user_code)}
              >
                {ghDevice.user_code}
              </button>
              <a className="gp-github" href={ghDevice.verification_uri} target="_blank" rel="noreferrer">
                <GithubMark /> Open GitHub to authorize ↗
              </a>
              <div className="gp-waiting"><span className="gp-spin gp-spin-sm" /> Waiting for authorization…</div>
            </>
          )}

          {ghPhase === "error" && (
            <>
              <h2>GitHub sign-in failed</h2>
              <div className="gp-note err">{ghError}</div>
              <button type="button" className="gp-github" onClick={startGitHub}>
                <GithubMark /> Try again
              </button>
            </>
          )}

          <div className="gp-copy">© {new Date().getFullYear()} GitPilot Inc.</div>
        </div>
      </div>
    );
  }

  // ── Email / password card (sign in + create account share this shell) ──
  return (
    <div className="gp-auth">
      <a className="gp-home" href="/">← Back to home</a>
      <form className="gp-auth-card" onSubmit={submit}>
        {mode === "signup" && (
          <button type="button" className="gp-back" onClick={() => { setMode("signin"); setNote(null); }}>← Back to sign in</button>
        )}
        <a className="gp-auth-logo" href="/" aria-label="Back to GitPilot home">GP</a>
        {mode === "signin" ? (
          <>
            <h2>GitPilot Enterprise</h2>
            <p className="lead">Agentic AI workflow for your repositories.<br />Secure. Context-aware. Automated.</p>
          </>
        ) : (
          <>
            <h2>Create your account</h2>
            <p className="lead">Start using GitPilot with a secure workspace.</p>
          </>
        )}

        <button type="button" className="gp-github" onClick={() => setView("github")}>
          <GithubMark /> Continue with GitHub
        </button>
        <div className="gp-or">or</div>

        {note && <div className={`gp-note ${note.kind}`}>{note.text}</div>}

        {mode === "signup" && (
          <div className="gp-field">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (optional)" />
          </div>
        )}
        <div className="gp-field">
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email" />
        </div>
        <div className="gp-field gp-pw">
          <input type={showPw ? "text" : "password"} required minLength={8} value={password}
            onChange={(e) => setPassword(e.target.value)} placeholder="Password" />
          <button type="button" className="gp-eye" onClick={() => setShowPw((s) => !s)} aria-label="Toggle password">
            <Eye off={showPw} />
          </button>
        </div>
        {mode === "signin" && (
          <div className="gp-forgot"><button type="button" className="gp-link" onClick={forgot}>Forgot password?</button></div>
        )}

        <button className="gp-submit" type="submit" disabled={busy}>
          {busy ? "Please wait…" : mode === "signin" ? "Sign in" : "Create account"}
        </button>

        <div className="gp-foot">
          {mode === "signin" ? (
            <>Don&apos;t have an account? <button type="button" className="gp-link" onClick={() => { setMode("signup"); setNote(null); }}>Create account</button></>
          ) : (
            <>Already have an account? <button type="button" className="gp-link" onClick={() => { setMode("signin"); setNote(null); }}>Sign in</button></>
          )}
        </div>
        <div className="gp-copy">© {new Date().getFullYear()} GitPilot Inc.</div>
      </form>
    </div>
  );
}
