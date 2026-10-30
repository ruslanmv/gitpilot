import React, { useEffect, useRef, useState } from "react";
import "./auth.css";
import { adoptSession, authFetch, fetchAuthProviders, fetchSession } from "../utils/api.js";
import { clearPending, errorText, initialView, readPending, writePending } from "../utils/authFlow.js";

// GitPilot sign-in: GitHub or GitLab OAuth, or an email/password account.
//
// One layout for every state: <div.gp-auth> (full-viewport background, hero
// illustration on the left) wraps a single card. Sign in, create account, the
// provider authorization step, email confirmation and password reset all
// render inside that same card.
//
// Security model (see docs/auth.md):
// * OAuth codes are exchanged by the server with a PKCE verifier it keeps; the
//   GitHub/GitLab access tokens are stored encrypted server-side and never
//   reach this page. The browser only receives a GitPilot session (HttpOnly
//   cookie, or a header token on cross-site deployments).
// * The `state` of a provider redirect must match the one this tab stored
//   before leaving (sessionStorage), on top of the server's single-use check:
//   a callback link from someone else's flow is refused here.

const THEME_KEY = "gitpilot_auth_theme";
const PROVIDER_NAMES = { github: "GitHub", gitlab: "GitLab" };

async function call(path, { method = "GET", body } = {}) {
  const res = await authFetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch { /* empty body */ }
  return { ok: res.ok, status: res.status, data };
}

const GithubMark = () => (
  <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M12 2C6.48 2 2 6.58 2 12.25c0 4.53 2.87 8.37 6.84 9.73.5.1.68-.22.68-.49v-1.7c-2.78.62-3.37-1.21-3.37-1.21-.45-1.18-1.11-1.49-1.11-1.49-.91-.64.07-.62.07-.62 1 .07 1.53 1.06 1.53 1.06.89 1.56 2.34 1.11 2.91.85.09-.66.35-1.11.63-1.36-2.22-.26-4.55-1.14-4.55-5.07 0-1.12.39-2.03 1.03-2.75-.1-.26-.45-1.3.1-2.7 0 0 .84-.28 2.75 1.05a9.3 9.3 0 0 1 5 0c1.91-1.33 2.75-1.05 2.75-1.05.55 1.4.2 2.44.1 2.7.64.72 1.03 1.63 1.03 2.75 0 3.94-2.34 4.81-4.57 5.06.36.32.68.94.68 1.9v2.82c0 .27.18.6.69.49A10.02 10.02 0 0 0 22 12.25C22 6.58 17.52 2 12 2z" /></svg>
);
const GitLabMark = () => (
  <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M12 21.6 16.45 8H7.55L12 21.6Z" fill="#E24329"/>
    <path d="m12 21.6-4.45-13H1.6L12 21.6Z" fill="#FC6D26"/>
    <path d="m1.6 8 2.2-6.7c.1-.34.57-.34.68 0L7.55 8H1.6Z" fill="#FCA326"/>
    <path d="m12 21.6 4.45-13h5.95L12 21.6Z" fill="#FC6D26"/>
    <path d="m22.4 8-2.2-6.7c-.1-.34-.57-.34-.68 0L16.45 8h5.95Z" fill="#FCA326"/>
    <path d="M7.55 8 12 21.6 16.45 8H7.55Z" fill="#FC6D26"/>
  </svg>
);
const ProviderMark = ({ provider }) => (provider === "gitlab" ? <GitLabMark /> : <GithubMark />);

const FeatureIcon = ({ kind }) => {
  const paths = {
    bolt: <path d="m13 2-8 11h6l-1 9 9-12h-6l1-8z"/>,
    branch: <><circle cx="6" cy="5" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="6" cy="19" r="2"/><path d="M6 7v10m2 0c7 0 2-9 8-9"/></>,
    shield: <><path d="M12 2 20 6v6c0 5-4 8-8 10-4-2-8-5-8-10V6z"/><path d="m9 12 2 2 4-4"/></>,
    team: <><circle cx="8" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M2 20v-2c0-3 3-5 6-5s6 2 6 5v2m1-6c3 0 6 2 6 5v1"/></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[kind]}</svg>;
};

const AuthAside = () => (
  <aside className="gp-auth-aside" aria-label="About GitPilot">
    <div className="gp-aside-upper">
      <a className="gp-brand" href="/" aria-label="GitPilot home">
        <span className="gp-brand-mark" aria-hidden="true">GP</span><span>GitPilot</span>
      </a>
      <div className="gp-aside-content">
        <span className="gp-aside-kicker">YOUR AI DEVELOPER COPILOT</span>
        <h1>From ideas to<br /><em>production, faster.</em></h1>
        <p>GitPilot is the AI agent that understands your repositories, helps you code,
          reviews changes, manages issues, and automates your development workflow.</p>
        <ul className="gp-auth-features" aria-label="Platform capabilities">
          <li className="gp-auth-feature"><span className="gp-feature-icon"><FeatureIcon kind="bolt"/></span>
            <span><b>Understand</b><small>Your codebase instantly</small></span></li>
          <li className="gp-auth-feature"><span className="gp-feature-icon"><FeatureIcon kind="branch"/></span>
            <span><b>Automate</b><small>PRs, MRs, issues and workflows</small></span></li>
          <li className="gp-auth-feature"><span className="gp-feature-icon"><FeatureIcon kind="shield"/></span>
            <span><b>Stay secure</b><small>Review changes before applying</small></span></li>
          <li className="gp-auth-feature"><span className="gp-feature-icon"><FeatureIcon kind="team"/></span>
            <span><b>Work anywhere</b><small>GitHub, GitLab and local Git</small></span></li>
        </ul>
      </div>
    </div>
    <img className="gp-auth-illustration"
      src="/assets/auth/gitpilot-hero-illustration.svg"
      alt="" aria-hidden="true" draggable="false" />
  </aside>
);

const MailMark = () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
  strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
  <rect x="3" y="5" width="18" height="14" rx="2"/><path d="m4 7 8 6 8-6"/></svg>;
const LockMark = () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
  strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
  <rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/><path d="M12 14v3"/></svg>;

const AppearanceControl = ({ light, toggle }) => (
  <div className="gp-auth-tools">
    <button type="button" className="gp-theme-toggle" onClick={toggle}
      aria-pressed={light}
      aria-label={light ? "Use dark theme" : "Use light theme"}
      title={light ? "Use dark theme" : "Use light theme"}>
      <span aria-hidden="true">{light ? "☾" : "☼"}</span>
    </button>
  </div>
);

const Eye = ({ off }) => (off
  ? <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.5 18.5 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19M1 1l22 22"/></svg>
  : <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
);

const Heading = React.forwardRef(function Heading({ children }, ref) {
  return <h2 id="gp-auth-title" ref={ref} tabIndex={-1}>{children}</h2>;
});
const Logo = () => <a className="gp-auth-logo" href="/" aria-label="Back to GitPilot home">GP</a>;
const Footer = () => (
  <div className="gp-copy">GitPilot is open source. <a href="https://github.com/ruslanmv/gitpilot/blob/master/LICENSE"
    target="_blank" rel="noopener noreferrer">Apache 2.0 license</a>.</div>
);

const Note = ({ note }) => (note ? (
  <div className={`gp-note ${note.kind}`} role={note.kind === "err" ? "alert" : "status"}>{note.text}</div>
) : null);

const PasswordField = ({ id, label, value, onChange, autoComplete, show, onToggle, describedBy, placeholder }) => (
  <div className="gp-field gp-pw">
    <label htmlFor={id}>{label}</label>
    <span className="gp-input-icon"><LockMark /></span>
    <input id={id} type={show ? "text" : "password"} required minLength={8}
      value={value} onChange={(e) => onChange(e.target.value)} aria-describedby={describedBy}
      placeholder={placeholder} autoComplete={autoComplete} />
    <button type="button" className="gp-eye" onClick={onToggle}
      aria-label={show ? "Hide password" : "Show password"} aria-pressed={show}>
      <Eye off={show} />
    </button>
  </div>
);

export default function AuthPage({ onAuthenticated, backendReady = false, connectMode = false }) {
  const [start] = useState(() => ({
    params: new URLSearchParams(window.location.search),
    ...initialView(window.location, readPending()),
  }));
  const params = start.params;

  const [view, setView] = useState(start.view); // email | provider | callback | verify | reset
  const [provider, setProvider] = useState(start.provider || "github");
  const [mode, setMode] = useState(start.mode || "signin");
  const [lightTheme, setLightTheme] = useState(() => {
    try { return localStorage.getItem(THEME_KEY) === "light"; } catch { return false; }
  });
  const toggleTheme = () => {
    setLightTheme((current) => {
      try { localStorage.setItem(THEME_KEY, current ? "dark" : "light"); } catch { /* storage blocked */ }
      return !current;
    });
  };

  // What this server offers; null while unknown (buttons stay usable and
  // report a clear error if the server turns out not to support them).
  const [available, setAvailable] = useState(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [name, setName] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null); // {kind: "ok"|"err", text}

  // Provider step (rendered inside the same card)
  const [phase, setPhase] = useState("connecting"); // connecting | device | error
  const [device, setDevice] = useState(null);
  const [providerError, setProviderError] = useState("");
  const pollTimer = useRef(null);
  const stopPolling = useRef(false);
  const started = useRef(false);

  // Email confirmation / password reset results
  const [linkPhase, setLinkPhase] = useState("working"); // working | success | error | form
  const [linkUser, setLinkUser] = useState(null);

  const headingRef = useRef(null);
  useEffect(() => {
    // Move focus to the card heading on every view change (screen readers and
    // keyboard users land on what changed).
    headingRef.current?.focus();
  }, [view, mode, phase, linkPhase]);

  useEffect(() => {
    let cancelled = false;
    fetchAuthProviders()
      .then((data) => { if (!cancelled) setAvailable(data); })
      .catch(() => { /* unknown: keep every option visible */ });
    // Load the CSRF token of an existing session (connect flows from the
    // workspace): state-changing calls below must echo it.
    fetchSession().catch(() => {});
    return () => { cancelled = true; };
  }, [backendReady]);

  const accountsEnabled = !available || available.accounts !== false;
  const providerAvailable = (p) => {
    if (!available) return true;
    if (p === "github") return !!(available.github && (available.github.web || available.github.device));
    return !!(available.gitlab && available.gitlab.web);
  };

  const finish = async (data, { connect } = {}) => {
    stopPolling.current = true;
    if (pollTimer.current) clearTimeout(pollTimer.current);
    clearPending();
    await adoptSession(data);
    if (connect || connectMode) {
      // Linked from inside the workspace: go back to it with the new connection.
      window.location.assign((data && data.return_to) || "/");
      return;
    }
    window.history.replaceState({}, document.title, "/");
    if (typeof onAuthenticated === "function") onAuthenticated(data || {});
  };

  // ── OAuth callback (both providers) ─────────────────────────────────────
  useEffect(() => {
    if (view !== "callback" || started.current) return;
    started.current = true;
    const pending = readPending();
    const code = params.get("code");
    const state = params.get("state");
    const oauthError = params.get("error");
    window.history.replaceState({}, document.title, "/auth");
    if (!start.trusted || !start.provider) {
      clearPending();
      setPhase("error");
      setProviderError("This sign-in response was not started from this browser tab, or it expired. Please start again.");
      return;
    }
    setProvider(start.provider);
    if (oauthError) {
      clearPending();
      setPhase("error");
      setProviderError(oauthError === "access_denied"
        ? `${PROVIDER_NAMES[start.provider]} authorization was cancelled.`
        : (params.get("error_description") || `${PROVIDER_NAMES[start.provider]} returned an error.`));
      return;
    }
    (async () => {
      setPhase("connecting");
      try {
        await fetchSession().catch(() => {});
        const r = await call(`/api/auth/oauth/${start.provider}/callback`, { method: "POST", body: { code, state } });
        if (!r.ok) throw new Error(errorText(r.data, `${PROVIDER_NAMES[start.provider]} sign-in failed.`));
        await finish(r.data, { connect: pending && pending.connect });
      } catch (e) {
        clearPending();
        setPhase("error");
        setProviderError(e.message || "Sign-in failed.");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  // ── Start a provider sign-in ────────────────────────────────────────────
  async function pollDevice(deviceCode, interval) {
    if (stopPolling.current) return;
    try {
      const r = await call("/api/auth/device/poll", { method: "POST", body: { device_code: deviceCode, store: true } });
      if (r.status === 200) { await finish(r.data); return; }
      if (r.status === 202) {
        pollTimer.current = setTimeout(() => pollDevice(deviceCode, interval), interval * 1000);
        return;
      }
      if (r.data.error === "slow_down") {
        pollTimer.current = setTimeout(() => pollDevice(deviceCode, interval + 5), (interval + 5) * 1000);
        return;
      }
      throw new Error(errorText(r.data, `Polling failed (${r.status}).`));
    } catch (e) {
      if (!stopPolling.current) { setPhase("error"); setProviderError(e.message || "Could not reach GitHub."); }
    }
  }

  async function startDeviceFlow() {
    const r = await call("/api/auth/device/code", { method: "POST" });
    if (r.data.error) {
      const msg = String(r.data.error);
      throw new Error(msg.includes("400") || msg.includes("Bad Request")
        ? "Device Flow is disabled for this GitHub App. Enable it under GitHub App Settings → General → 'Enable Device Flow'."
        : msg);
    }
    if (!r.data.device_code) throw new Error("GitHub did not return a device code.");
    setDevice(r.data);
    setPhase("device");
    stopPolling.current = false;
    pollDevice(r.data.device_code, r.data.interval || 5);
  }

  async function startProvider(p) {
    setProvider(p);
    setPhase("connecting");
    setProviderError("");
    setDevice(null);
    try {
      const webAvailable = !available || (available[p] && available[p].web);
      if (webAvailable) {
        const r = await call(`/api/auth/oauth/${p}/start?return_to=${encodeURIComponent("/")}`);
        if (r.ok && r.data.authorization_url) {
          writePending({ provider: p, state: r.data.state, connect: !!connectMode, startedAt: Date.now() });
          window.location.assign(r.data.authorization_url);
          return;
        }
        if (p === "gitlab" || r.status !== 503) {
          throw new Error(errorText(r.data, `${PROVIDER_NAMES[p]} sign-in is not available.`));
        }
      }
      if (p === "github") { await startDeviceFlow(); return; }
      throw new Error("GitLab sign-in is not configured on this server.");
    } catch (e) {
      setPhase("error");
      setProviderError(e.message || `Could not start ${PROVIDER_NAMES[p]} sign-in.`);
    }
  }

  useEffect(() => {
    if (view === "provider") {
      if (started.current) return;
      started.current = true;
      startProvider(provider);
    } else if (view !== "callback") {
      started.current = false;
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
    clearPending();
    setDevice(null);
    setProviderError("");
    setNote(null);
    started.current = false;
    setView("email");
  };

  const chooseProvider = (p) => {
    setNote(null);
    setProvider(p);
    if (view === "provider") {
      // Already on the provider card (e.g. "Try again"): restart in place.
      startProvider(p);
      return;
    }
    started.current = false;
    setView("provider");
  };

  // ── Email confirmation and password reset links ─────────────────────────
  useEffect(() => {
    if (view !== "verify" || started.current) return;
    started.current = true;
    const token = params.get("token");
    window.history.replaceState({}, document.title, "/auth");
    (async () => {
      setLinkPhase("working");
      const r = await call("/api/account/verify-email", { method: "POST", body: { token } });
      if (r.ok) {
        await adoptSession(r.data);
        setLinkUser(r.data);
        setLinkPhase("success");
      } else {
        setNote({ kind: "err", text: errorText(r.data, "This confirmation link is invalid or has expired.") });
        setLinkPhase("error");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  const [resetToken] = useState(() => (start.view === "reset" ? params.get("token") : null));
  useEffect(() => {
    if (view === "reset") {
      window.history.replaceState({}, document.title, "/auth/reset-password");
      setLinkPhase("form");
    }
  }, [view]);

  const submitReset = async (e) => {
    e.preventDefault();
    setNote(null);
    if (password !== password2) { setNote({ kind: "err", text: "The two passwords do not match." }); return; }
    setBusy(true);
    try {
      const r = await call("/api/account/password/reset", { method: "POST", body: { token: resetToken, password } });
      if (r.ok) {
        await adoptSession(r.data);
        setLinkUser(r.data);
        setLinkPhase("success");
      } else {
        setNote({ kind: "err", text: errorText(r.data, "This reset link is invalid, expired or already used.") });
      }
    } catch {
      setNote({ kind: "err", text: "Network error — is the backend reachable?" });
    } finally {
      setBusy(false);
    }
  };

  const resendVerification = async () => {
    if (!email) { setNote({ kind: "err", text: "Enter your email to resend the link." }); return; }
    setBusy(true);
    try {
      await call("/api/account/resend-verification", { method: "POST", body: { email } });
      setNote({ kind: "ok", text: "If your account needs confirmation, a new link is on its way." });
    } finally {
      setBusy(false);
    }
  };

  // ── Email / password ────────────────────────────────────────────────────
  const submit = async (e) => {
    e.preventDefault();
    setNote(null);
    setBusy(true);
    try {
      if (mode === "signin") {
        const r = await call("/api/account/login", { method: "POST", body: { email, password } });
        if (r.ok) await finish({ ...r.data, user: { login: r.data.email, name: r.data.name, email: r.data.email } });
        else if (r.status === 403) setNote({ kind: "err", text: "Verify your email before signing in." });
        else if (r.status === 404) setNote({ kind: "err", text: "Email sign-in is not enabled on this server." });
        else setNote({ kind: "err", text: errorText(r.data, "Invalid email or password.") });
      } else {
        const r = await call("/api/account/signup", { method: "POST", body: { email, password, name: name || null } });
        if (r.status === 202) setNote({ kind: "ok", text: "Check your email — we sent a confirmation link." });
        else setNote({ kind: "err", text: errorText(r.data, "Could not create the account.") });
      }
    } catch {
      setNote({ kind: "err", text: "Network error — is the backend reachable?" });
    } finally {
      setBusy(false);
    }
  };

  const forgot = async () => {
    if (!email) { setNote({ kind: "err", text: "Enter your email first." }); return; }
    setBusy(true);
    try {
      await call("/api/account/password/forgot", { method: "POST", body: { email } });
      setNote({ kind: "ok", text: "If an account exists, we'll send a reset link." });
    } catch {
      setNote({ kind: "err", text: "Network error — is the backend reachable?" });
    } finally {
      setBusy(false);
    }
  };

  const shell = (content, { asForm = false, onSubmit } = {}) => {
    const Card = asForm ? "form" : "div";
    return (
      <div className={lightTheme ? "gp-auth gp-auth-light" : "gp-auth"}>
        <AuthAside />
        <AppearanceControl light={lightTheme} toggle={toggleTheme} />
        <a className="gp-home" href="/">← Back to home</a>
        <main className="gp-auth-main">
          <Card className="gp-auth-card" onSubmit={onSubmit} aria-labelledby="gp-auth-title">
            {content}
          </Card>
        </main>
      </div>
    );
  };


  // ── Email confirmation card ─────────────────────────────────────────────
  if (view === "verify") {
    return shell(<>
      <Logo />
      {linkPhase === "working" && (<>
        <Heading ref={headingRef}>Confirming your email…</Heading>
        <p className="lead">Activating your GitPilot account. This only takes a moment.</p>
        <div className="gp-spinwrap" role="status" aria-label="Confirming"><span className="gp-spin" /></div>
      </>)}
      {linkPhase === "success" && (<>
        <div className="gp-verify-check" aria-hidden="true">✓</div>
        <Heading ref={headingRef}>Email confirmed</Heading>
        <p className="lead">Welcome{linkUser?.name ? `, ${linkUser.name}` : ""}! Your GitPilot account is active.</p>
        <button type="button" className="gp-submit"
          onClick={() => finish({ ...linkUser, user: { login: linkUser?.email, name: linkUser?.name, email: linkUser?.email } })}>
          Continue to GitPilot →
        </button>
      </>)}
      {linkPhase === "error" && (<>
        <Heading ref={headingRef}>Confirmation link problem</Heading>
        <Note note={note} />
        <p className="lead">Links expire after 15 minutes. Enter your email to get a fresh one.</p>
        <div className="gp-field">
          <label htmlFor="gp-resend-email">Email address</label>
          <span className="gp-input-icon"><MailMark /></span>
          <input id="gp-resend-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com" autoComplete="email" />
        </div>
        <button type="button" className="gp-submit" onClick={resendVerification} disabled={busy} aria-busy={busy}>
          {busy ? "Sending…" : "Resend confirmation email"}
        </button>
        <button type="button" className="gp-link gp-block-link" onClick={goEmail}>Back to sign in</button>
      </>)}
      <Footer />
    </>);
  }

  // ── Password reset card ─────────────────────────────────────────────────
  if (view === "reset") {
    if (linkPhase === "success") {
      return shell(<>
        <Logo />
        <div className="gp-verify-check" aria-hidden="true">✓</div>
        <Heading ref={headingRef}>Password updated</Heading>
        <p className="lead">Your password was changed and every other session was signed out.</p>
        <button type="button" className="gp-submit"
          onClick={() => finish({ ...linkUser, user: { login: linkUser?.email, name: linkUser?.name, email: linkUser?.email } })}>
          Continue to GitPilot →
        </button>
        <Footer />
      </>);
    }
    return shell(<>
      <Logo />
      <Heading ref={headingRef}>Choose a new password</Heading>
      <p className="lead">Use at least 8 characters. This link works once and expires after 15 minutes.</p>
      <Note note={note} />
      {!resetToken ? (
        <div className="gp-note err" role="alert">This reset link is incomplete. Request a new one from the sign-in page.</div>
      ) : (<>
        <PasswordField id="gp-reset-password" label="New password" value={password} onChange={setPassword}
          autoComplete="new-password" show={showPw} onToggle={() => setShowPw((s) => !s)}
          describedBy="gp-reset-hint" placeholder="New password" />
        <PasswordField id="gp-reset-password2" label="Confirm new password" value={password2} onChange={setPassword2}
          autoComplete="new-password" show={showPw} onToggle={() => setShowPw((s) => !s)}
          describedBy="gp-reset-hint" placeholder="Repeat the new password" />
        <p id="gp-reset-hint" className="gp-hint">Minimum 8 characters.</p>
        <button className="gp-submit" type="submit" disabled={busy} aria-busy={busy}>
          {busy ? "Saving…" : "Update password →"}
        </button>
      </>)}
      <button type="button" className="gp-link gp-block-link" onClick={goEmail}>Back to sign in</button>
      <Footer />
    </>, { asForm: true, onSubmit: submitReset });
  }

  // ── Provider authorization card (GitHub / GitLab) ──────────────────────
  if (view === "provider" || view === "callback") {
    // A callback this tab did not start has no trustworthy provider.
    const untrusted = view === "callback" && !start.trusted;
    const label = untrusted ? "Sign-in" : PROVIDER_NAMES[provider] || "GitHub";
    return shell(<>
      {connectMode
        ? <button type="button" className="gp-back" onClick={() => { window.location.href = "/"; }}>← Back to workspace</button>
        : <button type="button" className="gp-back" onClick={goEmail}>← Back to sign in</button>}
      <Logo />
      {phase === "connecting" && (<>
        <Heading ref={headingRef}>Connecting to {label}…</Heading>
        <p className="lead">Opening a secure authorization session for your repositories.</p>
        <div className="gp-spinwrap" role="status" aria-label={`Connecting to ${label}`}><span className="gp-spin" /></div>
      </>)}
      {provider === "github" && phase === "device" && device && (<>
        <Heading ref={headingRef}>Authorize GitPilot</Heading>
        <p className="lead">Enter this code on GitHub to grant repository access.</p>
        <button type="button" className="gp-code" title="Copy code"
          aria-label={`Device code ${device.user_code}. Activate to copy.`}
          onClick={() => navigator.clipboard?.writeText(device.user_code)}>
          {device.user_code}
        </button>
        <a className="gp-github" href={device.verification_uri} target="_blank" rel="noreferrer">
          <GithubMark /> Open GitHub to authorize <span className="gp-provider-arrow" aria-hidden="true">↗</span>
        </a>
        <div className="gp-waiting" role="status"><span className="gp-spin gp-spin-sm" /> Waiting for authorization…</div>
      </>)}
      {phase === "error" && (<>
        <Heading ref={headingRef}>{untrusted ? "Sign-in could not be completed" : `${label} connection failed`}</Heading>
        <div className="gp-note err" role="alert">{providerError}</div>
        {!untrusted && (
          <button type="button" className={provider === "gitlab" ? "gp-gitlab" : "gp-github"}
            onClick={() => chooseProvider(provider)} disabled={!providerAvailable(provider)}>
            <ProviderMark provider={provider} /> Try again
          </button>
        )}
      </>)}
      <div className="gp-copy">
        {untrusted ? "Start the sign-in again from this page." : `Your ${label} token is stored encrypted on the GitPilot server and is never kept in this browser.`}
      </div>
    </>);
  }

  // ── Email / password card (sign in + create account share this shell) ──
  const providerButton = (p) => {
    const enabled = providerAvailable(p);
    const host = p === "gitlab" && available && available.gitlab && available.gitlab.host;
    return (
      <button type="button" className={p === "gitlab" ? "gp-gitlab" : "gp-github"} key={p}
        onClick={() => chooseProvider(p)} disabled={!enabled} aria-describedby={enabled ? undefined : `gp-${p}-off`}>
        <ProviderMark provider={p} /> Continue with {PROVIDER_NAMES[p]}
        {host && host !== "gitlab.com" ? <span className="gp-provider-host"> ({host})</span> : null}
        <span className="gp-provider-arrow" aria-hidden="true">↗</span>
      </button>
    );
  };
  const disabledProviders = ["github", "gitlab"].filter((p) => !providerAvailable(p));

  return shell(<>
    {mode === "signup" && (
      <button type="button" className="gp-back" onClick={() => { setMode("signin"); setNote(null); }}>← Back to sign in</button>
    )}
    <Logo />
    {mode === "signin" ? (<>
      <div className="gp-auth-eyebrow">YOUR ENGINEERING WORKSPACE</div>
      <Heading ref={headingRef}>Welcome to <em>GitPilot</em></Heading>
      <p className="lead">Sign in to your account to continue to your<br className="gp-desktop-break" /> repositories and AI workspace.</p>
    </>) : (<>
      <Heading ref={headingRef}>Create your account</Heading>
      <p className="lead">Start using GitPilot with a secure workspace.</p>
    </>)}

    <div className="gp-provider-stack">
      {providerButton("github")}
      {providerButton("gitlab")}
    </div>
    {disabledProviders.map((p) => (
      <p key={p} id={`gp-${p}-off`} className="gp-provider-note">
        {PROVIDER_NAMES[p]} sign-in is not configured on this server.
      </p>
    ))}

    {accountsEnabled ? (<>
      <div className="gp-or">or continue with email</div>
      <Note note={note} />
      {mode === "signup" && (
        <div className="gp-field">
          <label htmlFor="gp-signin-name">Full name</label>
          <input id="gp-signin-name" className="gp-no-icon" value={name} onChange={(e) => setName(e.target.value)}
            placeholder="Full name (optional)" autoComplete="name" />
        </div>
      )}
      <div className="gp-field">
        <label htmlFor="gp-signin-email">Email address</label>
        <span className="gp-input-icon"><MailMark /></span>
        <input id="gp-signin-email" type="email" required value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="you@company.com" autoComplete="email" />
      </div>
      <PasswordField id="gp-signin-password" label="Password" value={password} onChange={setPassword}
        autoComplete={mode === "signin" ? "current-password" : "new-password"} show={showPw}
        onToggle={() => setShowPw((s) => !s)} describedBy={mode === "signup" ? "gp-pw-hint" : undefined}
        placeholder="Enter your password" />
      {mode === "signup" && <p id="gp-pw-hint" className="gp-hint">Minimum 8 characters.</p>}
      {mode === "signin" && (
        <div className="gp-forgot"><button type="button" className="gp-link" onClick={forgot} disabled={busy}>Forgot password?</button></div>
      )}

      <button className="gp-submit" type="submit" disabled={busy} aria-busy={busy}>
        {busy ? "Please wait…" : mode === "signin" ? "Sign in →" : "Create account →"}
      </button>

      <div className="gp-foot">
        {mode === "signin" ? (
          <>Don&apos;t have an account? <button type="button" className="gp-link" onClick={() => { setMode("signup"); setNote(null); }}>Create account</button></>
        ) : (
          <>Already have an account? <button type="button" className="gp-link" onClick={() => { setMode("signin"); setNote(null); }}>Sign in</button></>
        )}
      </div>
    </>) : (
      <p className="gp-provider-note gp-provider-note--center">
        Email sign-in is not enabled on this server. Use GitHub or GitLab to continue.
      </p>
    )}
    <Footer />
  </>, { asForm: accountsEnabled, onSubmit: submit });
}
