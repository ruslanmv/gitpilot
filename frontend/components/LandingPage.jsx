import React from "react";
import "./landing.css";

// Premium GitPilot landing page — dark, minimal, Apple‑inspired.
// Public page only: "Get started" / "Sign in" route to the separate /auth page.

const GITHUB_URL = "https://github.com/ruslanmv/gitpilot";
const DOCS_URL = "https://ruslanmv.com/gitpilot/";

// --- tiny inline icons (no dependencies) ----------------------------------
const I = {
  shield: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg>,
  target: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="4" /></svg>,
  code: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" /></svg>,
  github: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.9a3.4 3.4 0 0 0-1-2.6c3-.3 6-1.5 6-7A5.4 5.4 0 0 0 19 4.8 5 5 0 0 0 18.9 1S17.7.7 15 2.5a13 13 0 0 0-7 0C5.3.7 4.1 1 4.1 1A5 5 0 0 0 4 4.8 5.4 5.4 0 0 0 3 8.5c0 5.5 3 6.7 6 7a3.4 3.4 0 0 0-1 2.6V22" /></svg>,
  brain: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z" /><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z" /></svg>,
  bolt: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" /></svg>,
  bot: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="8" width="16" height="11" rx="3" /><path d="M12 8V4" /><circle cx="9" cy="13" r="1" /><circle cx="15" cy="13" r="1" /></svg>,
  home: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 11l9-8 9 8" /><path d="M5 10v10h14V10" /></svg>,
  flow: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="6" cy="6" r="2" /><circle cx="18" cy="18" r="2" /><path d="M8 6h6a2 2 0 0 1 2 2v8" /></svg>,
  gear: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3" /><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.5-2.3 1a7 7 0 0 0-1.7-1L14.5 2h-5l-.4 2.5a7 7 0 0 0-1.7 1l-2.3-1-2 3.5L5 11a7 7 0 0 0 0 2l-2 1.5 2 3.5 2.3-1a7 7 0 0 0 1.7 1l.4 2.5h5l.4-2.5a7 7 0 0 0 1.7-1l2.3 1 2-3.5-2-1.5a7 7 0 0 0 .1-1z" /></svg>,
  copy: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>,
  ext: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 3h6v6" /><path d="M10 14 21 3" /><path d="M21 14v7H3V3h7" /></svg>,
  lock: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="11" width="16" height="9" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>,
};

const FEATURES = [
  { ic: I.github, t: "Connect GitHub", d: "Authorize access with device flow or the GitPilot GitHub App." },
  { ic: I.brain, t: "Understand context", d: "GitPilot reads repository structure, history, and workflow intent." },
  { ic: I.bolt, t: "Run workflows", d: "Generate code, review changes, and automate tasks — safely." },
];

const STEPS = [
  { t: "Connect GitHub", d: "Authorize GitPilot securely using device flow." },
  { t: "Select repository", d: "Choose the repository you want to work with." },
  { t: "Run workflow", d: "Let GitPilot understand context and execute the task." },
];

export default function LandingPage() {
  return (
    <div className="gp-landing">
      <div className="gp-wrap">
        {/* nav */}
        <nav className="gp-nav">
          <a className="gp-brand" href="/">
            <span className="gp-logo">GP</span>
            <span>
              <span className="gp-brand-name">GitPilot</span>
              <br />
              <span className="gp-brand-sub">Agentic GitHub assistant</span>
            </span>
          </a>
          <div className="gp-nav-links">
            <a href="#features">Product</a>
            <a href={DOCS_URL} target="_blank" rel="noreferrer">Docs</a>
            <a href={GITHUB_URL} target="_blank" rel="noreferrer">GitHub ↗</a>
          </div>
          <div className="gp-nav-right">
            <a className="gp-signin" href="/auth?mode=signin">Sign in</a>
            <a className="gp-btn gp-btn-primary" href="/auth?mode=signup">Get started</a>
          </div>
        </nav>

        {/* hero */}
        <header className="gp-hero">
          <div>
            <span className="gp-badge"><span className="dot">✦</span> Built for developers. Powered by agents.</span>
            <h1 className="gp-h1">Agentic <span className="accent">GitHub</span> workflows,<br />under your control.</h1>
            <p className="gp-sub">
              GitPilot connects to your repositories, understands context, and runs
              secure agentic workflows from one workspace.
            </p>
            <div className="gp-cta">
              <a className="gp-btn gp-btn-primary" href="/auth?mode=signup">Get started</a>
              <a className="gp-btn gp-btn-ghost" href={DOCS_URL} target="_blank" rel="noreferrer">View docs</a>
            </div>
            <div className="gp-trust">
              <span>{I.shield} Secure by design</span>
              <span>{I.target} Context‑aware</span>
              <span>{I.code} Developer first</span>
            </div>
          </div>

          {/* product preview */}
          <div className="gp-preview" aria-hidden="true">
            <div className="gp-titlebar"><i /><i /><i /></div>
            <div className="gp-app">
              <aside className="gp-side">
                <div className="gp-side-brand"><span className="gp-logo">GP</span><b>GitPilot</b></div>
                <div className="gp-navitem on">{I.home} Workspace</div>
                <div className="gp-navitem">{I.flow} Agent Workflow</div>
                <div className="gp-navitem">{I.gear} Settings</div>
                <div className="gp-side-label">REPOSITORIES</div>
                <div className="gp-repo"><b>gitpilot <span className="dot">•</span></b></div>
                <div className="gp-repo"><b>signalforge</b></div>
                <div className="gp-repo"><b>matrix‑builder</b></div>
                <div className="gp-addrepo">+ Add repository</div>
              </aside>
              <main className="gp-main">
                <div style={{ textAlign: "center" }}>
                  <div className="gp-bot">{I.bot}</div>
                  <h4>Select a repository</h4>
                  <p>Choose a repository to begin your agentic workflow.</p>
                </div>
                <div className="gp-overlay">
                  <div className="gp-shield">{I.shield}</div>
                  <h5>Authorize this device</h5>
                  <p className="desc">GitPilot needs authorization to access your repositories.</p>
                  <div className="gp-step-k">1. Copy code</div>
                  <div className="gp-code">3111‑C440 {I.copy}</div>
                  <div className="gp-step-k">2. Paste at GitHub</div>
                  <div className="gp-activate">Open activation page {I.ext}</div>
                  <div className="gp-waiting">⟳ Waiting for authorization…</div>
                </div>
              </main>
            </div>
          </div>
        </header>

        {/* features */}
        <section id="features" className="gp-features">
          {FEATURES.map((f) => (
            <div className="gp-fcard" key={f.t}>
              <div className="ic">{f.ic}</div>
              <h3>{f.t}</h3>
              <p>{f.d}</p>
            </div>
          ))}
        </section>

        {/* how it works */}
        <section className="gp-how">
          <h2>How GitPilot works</h2>
          <div className="gp-steps">
            {STEPS.map((s, i) => (
              <div className="gp-step" key={s.t}>
                <div className="n">{i + 1}</div>
                <h4>{s.t}</h4>
                <p>{s.d}</p>
              </div>
            ))}
          </div>
        </section>

        {/* footer */}
        <footer className="gp-foot">
          <div className="lock">{I.lock} Your code stays private. Your workflow stays in control.</div>
          <div className="gp-foot-links">
            <a href={DOCS_URL} target="_blank" rel="noreferrer">Docs</a>
            <a href={GITHUB_URL} target="_blank" rel="noreferrer">GitHub</a>
            <a href="/auth?mode=signin">Sign in</a>
          </div>
        </footer>
      </div>
    </div>
  );
}
