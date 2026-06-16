import React, { useEffect, useRef, useState } from "react";
import "./landing.css";

// Premium GitPilot landing page — dark, minimal, animated.
// Public page only: "Get started" / "Sign in" route to the separate /auth page.

const GITHUB_URL = "https://github.com/ruslanmv/gitpilot";
const DOCS_URL = "https://ruslanmv.com/gitpilot/";

// --- tiny inline icons (no dependencies) ----------------------------------
const I = {
  shield: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg>,
  target: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="4" /></svg>,
  code: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" /></svg>,
  brain: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z" /><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z" /></svg>,
  map: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="3 6 9 3 15 6 21 3 21 18 15 21 9 18 3 21 3 6" /><line x1="9" y1="3" x2="9" y2="18" /><line x1="15" y1="6" x2="15" y2="21" /></svg>,
  bolt: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" /></svg>,
  check2: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 11l3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" /></svg>,
  bot: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="8" width="16" height="11" rx="3" /><path d="M12 8V4" /><circle cx="9" cy="13" r="1" /><circle cx="15" cy="13" r="1" /></svg>,
  home: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 11l9-8 9 8" /><path d="M5 10v10h14V10" /></svg>,
  flow: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="6" cy="6" r="2" /><circle cx="18" cy="18" r="2" /><path d="M8 6h6a2 2 0 0 1 2 2v8" /></svg>,
  gear: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3" /><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.5-2.3 1a7 7 0 0 0-1.7-1L14.5 2h-5l-.4 2.5a7 7 0 0 0-1.7 1l-2.3-1-2 3.5L5 11a7 7 0 0 0 0 2l-2 1.5 2 3.5 2.3-1a7 7 0 0 0 1.7 1l.4 2.5h5l.4-2.5a7 7 0 0 0 1.7-1l2.3 1 2-3.5-2-1.5a7 7 0 0 0 .1-1z" /></svg>,
  copy: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>,
  ext: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M15 3h6v6" /><path d="M10 14 21 3" /><path d="M21 14v7H3V3h7" /></svg>,
  lock: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="4" y="11" width="16" height="9" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>,
  arrowL: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6" /></svg>,
  arrowR: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6" /></svg>,
  ask: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3" /><line x1="12" y1="17" x2="12" y2="17" /><circle cx="12" cy="12" r="10" /></svg>,
  play: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polygon points="5 3 19 12 5 21 5 3" /></svg>,
  eye: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>,
  git: <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="18" cy="18" r="3" /><circle cx="6" cy="6" r="3" /><path d="M6 9v6a6 6 0 0 0 6 6h3" /></svg>,
};

// The multi-agent pipeline — the heart of GitPilot, shown as a carousel.
const AGENTS = [
  {
    n: "01", name: "Explorer", role: "Context", ic: I.brain,
    title: "It reads your repo before it writes a line.",
    desc: "Explorer scans your full repository, git history, test suite, and dependencies — so every plan starts from real knowledge, not guesses.",
    mock: (
      <div className="gp-mock gp-mock-scan">
        <div className="gp-mock-bar"><span className="gp-mdot r" /><span className="gp-mdot y" /><span className="gp-mdot g" /><b>explorer</b></div>
        <ul>
          <li><span className="ok">✓</span> Reading <em>src/auth/login.ts</em></li>
          <li><span className="ok">✓</span> Indexed <em>142 files</em> · 18 modules</li>
          <li><span className="ok">✓</span> Parsed <em>git log</em> — 1,204 commits</li>
          <li><span className="run">⟳</span> Mapping <em>854 tests</em> to sources…</li>
        </ul>
      </div>
    ),
  },
  {
    n: "02", name: "Planner", role: "Strategy", ic: I.map,
    title: "A step-by-step plan you approve first.",
    desc: "Planner drafts a safe, reviewable plan with diffs and surfaces risks before any file is touched. Nothing runs until you say go.",
    mock: (
      <div className="gp-mock gp-mock-plan">
        <div className="gp-mock-bar"><span className="gp-mdot r" /><span className="gp-mdot y" /><span className="gp-mdot g" /><b>plan · 3 steps</b></div>
        <ol>
          <li><b>1</b> Add <em>validateEmail()</em> to login.ts</li>
          <li><b>2</b> Wire validation into the submit handler</li>
          <li><b>3</b> Add 2 unit tests for edge cases</li>
        </ol>
        <div className="gp-mock-approve"><span>Approve &amp; Execute</span><span className="muted">Dismiss</span></div>
      </div>
    ),
  },
  {
    n: "03", name: "Coder", role: "Execution", ic: I.bolt,
    title: "It writes the code and runs your tests.",
    desc: "Coder applies the plan, runs your test suite, and self-corrects on failure — iterating until everything passes. Diffs are shown before they're applied.",
    mock: (
      <div className="gp-mock gp-mock-diff">
        <div className="gp-mock-bar"><span className="gp-mdot r" /><span className="gp-mdot y" /><span className="gp-mdot g" /><b>login.ts</b></div>
        <pre>
          <span className="add">+ if (!validateEmail(email)) {"{"}</span>
          <span className="add">+   return error("Enter a valid email");</span>
          <span className="add">+ {"}"}</span>
          <span className="ctx">  await signIn(email, password);</span>
        </pre>
        <div className="gp-mock-test"><span className="ok">✓</span> npm test — <b>3 passed</b></div>
      </div>
    ),
  },
  {
    n: "04", name: "Reviewer", role: "Quality", ic: I.check2,
    title: "It reviews the work and ships it.",
    desc: "Reviewer validates the output, re-runs the suite, and drafts your commit message and pull-request summary — ready for you to merge.",
    mock: (
      <div className="gp-mock gp-mock-pr">
        <div className="gp-mock-bar"><span className="gp-mdot r" /><span className="gp-mdot y" /><span className="gp-mdot g" /><b>pull request</b></div>
        <div className="gp-pr-title">feat(auth): validate email on login</div>
        <ul>
          <li><span className="ok">✓</span> All 854 tests passing</li>
          <li><span className="ok">✓</span> Commit message drafted</li>
          <li><span className="ok">✓</span> PR summary ready to merge</li>
        </ul>
      </div>
    ),
  },
];

const MODES = [
  { ic: I.ask, t: "Ask", tag: "Default", d: "Approve every dangerous action. You see the diff and click Allow or Deny before anything is written." },
  { ic: I.play, t: "Auto", tag: "Fastest", d: "Trust the plan and let every tool run automatically — no prompts. Best for experienced users." },
  { ic: I.eye, t: "Plan", tag: "Read-only", d: "Generate and display the full plan with diffs, but block all file writes and commands." },
];

const STEPS = [
  { t: "Connect a repository", d: "Authorize GitPilot securely with GitHub device flow — or run fully local with Ollama." },
  { t: "Describe the task", d: "Ask in plain language. Explorer and Planner turn it into a reviewable plan." },
  { t: "Approve & ship", d: "Coder writes and tests the change; Reviewer drafts the commit and PR. You stay in control." },
];

// Reveal-on-scroll wrapper (IntersectionObserver, no dependency).
function Reveal({ children, className = "", style, delay = 0 }) {
  const ref = useRef(null);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    if (typeof IntersectionObserver === "undefined") { setShown(true); return undefined; }
    const io = new IntersectionObserver(
      ([e]) => { if (e.isIntersecting) { setShown(true); io.disconnect(); } },
      { threshold: 0.12 }
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return (
    <div ref={ref} style={{ ...style, transitionDelay: `${delay}ms` }} className={`gp-reveal ${shown ? "in" : ""} ${className}`}>
      {children}
    </div>
  );
}

function AgentCarousel() {
  const [active, setActive] = useState(0);
  const [paused, setPaused] = useState(false);
  const n = AGENTS.length;

  useEffect(() => {
    if (paused) return undefined;
    const t = setInterval(() => setActive((a) => (a + 1) % n), 5200);
    return () => clearInterval(t);
  }, [paused, n]);

  const go = (i) => setActive(((i % n) + n) % n);
  const cur = AGENTS[active];

  return (
    <div
      className="gp-car"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <div className="gp-car-head">
        <div className="gp-car-badge">{cur.ic}<span>Agent {cur.n} · {cur.role}</span></div>
        <div className="gp-car-ctrls">
          <button type="button" aria-label="Previous agent" onClick={() => go(active - 1)}>{I.arrowL}</button>
          <span className="gp-car-count">{cur.n} / {String(n).padStart(2, "0")}</span>
          <button type="button" aria-label="Next agent" onClick={() => go(active + 1)}>{I.arrowR}</button>
        </div>
      </div>

      <div className="gp-car-view">
        <div className="gp-car-track" style={{ transform: `translateX(-${active * 100}%)` }}>
          {AGENTS.map((a) => (
            <div className="gp-slide" key={a.name} aria-hidden={a.name !== cur.name}>
              <div className="gp-slide-text">
                <div className="gp-slide-name">{a.name}</div>
                <h3>{a.title}</h3>
                <p>{a.desc}</p>
              </div>
              <div className="gp-slide-visual">{a.mock}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="gp-car-dots" role="tablist">
        {AGENTS.map((a, i) => (
          <button
            type="button"
            key={a.name}
            className={i === active ? "on" : ""}
            aria-label={`Show ${a.name}`}
            aria-selected={i === active}
            onClick={() => go(i)}
          />
        ))}
      </div>
    </div>
  );
}

// Real GitPilot app screens, shown as a carousel inside the hero preview box.
const SCREENS = [
  {
    key: "workspace", label: "Workspace",
    body: (
      <>
        <div className="gp-ps-head"><b>Connect a repository</b><span className="gp-ps-pill ok">Write access ✓</span></div>
        <div className="gp-ps-rows">
          <div className="gp-ps-row on"><span className="gp-rdot" /> ai-doctor/medical-data <em>main · 26 files</em></div>
          <div className="gp-ps-row"><span className="gp-rdot off" /> gitpilot</div>
          <div className="gp-ps-row"><span className="gp-rdot off" /> signalforge</div>
          <div className="gp-ps-row"><span className="gp-rdot off" /> matrix-builder</div>
        </div>
        <div className="gp-ps-cta">{I.git} Connect with GitHub</div>
      </>
    ),
  },
  {
    key: "agents", label: "Agent workflow",
    body: (
      <div className="gp-flowmini">
        <div className="gp-fnode top">User request</div>
        <span className="gp-fline" />
        <div className="gp-fnode router">Task Router</div>
        <span className="gp-fline" />
        <div className="gp-frow">
          <span>Explorer</span><span>Planner</span><span>Coder</span><span>Reviewer</span>
        </div>
      </div>
    ),
  },
  {
    key: "plan", label: "Plan & approve",
    body: (
      <>
        <div className="gp-ps-head"><b>Action plan</b><span className="gp-ps-meta2">Replace README.md</span></div>
        <div className="gp-plan-chips"><span className="c create">1 create</span><span className="c modify">1 modify</span><span className="c delete">1 delete</span></div>
        <div className="gp-plan-steps">
          <div><span className="k">STEP 1</span> Delete existing README.md</div>
          <div><span className="k">STEP 2</span> Rename README.md.new → README.md</div>
        </div>
        <div className="gp-ps-approve"><span>Approve &amp; execute</span><em>Dismiss</em></div>
      </>
    ),
  },
  {
    key: "exec", label: "Execution",
    body: (
      <>
        <div className="gp-ps-head"><b>Execution log</b></div>
        <ul className="gp-exec">
          <li><span className="ok">✓</span> Deleted <em>README.md</em></li>
          <li><span className="ok">✓</span> Created <em>README.md</em></li>
          <li><span className="ok">✓</span> Ran <em>npm test</em> — 3 passed</li>
          <li><span className="run">⟳</span> Re-checking suite…</li>
        </ul>
        <div className="gp-ps-note ok">Successfully executed 2 steps</div>
      </>
    ),
  },
  {
    key: "review", label: "Review & PR",
    body: (
      <>
        <div className="gp-ps-head"><b>Pull request</b><span className="gp-ps-pill ok">Ready</span></div>
        <div className="gp-pr-name">feat(auth): validate email on login</div>
        <ul className="gp-exec">
          <li><span className="ok">✓</span> All 854 tests passing</li>
          <li><span className="ok">✓</span> Commit message drafted</li>
          <li><span className="ok">✓</span> PR summary ready to merge</li>
        </ul>
      </>
    ),
  },
];

// Animated carousel that lives inside the existing hero preview window.
function PreviewCarousel() {
  const [i, setI] = useState(0);
  const [paused, setPaused] = useState(false);
  const n = SCREENS.length;

  useEffect(() => {
    if (paused) return undefined;
    const t = setInterval(() => setI((p) => (p + 1) % n), 4200);
    return () => clearInterval(t);
  }, [paused, n]);

  const go = (k) => setI(((k % n) + n) % n);

  return (
    <div
      className="gp-preview gp-float"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <div className="gp-titlebar">
        <i /><i /><i />
        <span className="gp-tb-label">{SCREENS[i].label}</span>
      </div>

      <div className="gp-pcar">
        <div className="gp-pcar-track" style={{ transform: `translateX(-${i * 100}%)` }}>
          {SCREENS.map((s) => (
            <div className="gp-pscreen" key={s.key} aria-hidden={s.key !== SCREENS[i].key}>
              <div className="gp-ps-body">{s.body}</div>
            </div>
          ))}
        </div>
        <button type="button" className="gp-pcar-arrow l" aria-label="Previous screen" onClick={() => go(i - 1)}>{I.arrowL}</button>
        <button type="button" className="gp-pcar-arrow r" aria-label="Next screen" onClick={() => go(i + 1)}>{I.arrowR}</button>
      </div>

      <div className="gp-pcar-foot">
        <div className="gp-pcar-dots">
          {SCREENS.map((s, k) => (
            <button type="button" key={s.key} className={k === i ? "on" : ""} aria-label={s.label} onClick={() => go(k)} />
          ))}
        </div>
        <div className="gp-pcar-prog"><span style={{ width: `${((i + 1) / n) * 100}%` }} /></div>
      </div>
    </div>
  );
}

export default function LandingPage() {
  return (
    <div className="gp-landing">
      <div className="gp-wrap">
        {/* above-the-fold: navbar + hero + capabilities occupy one viewport */}
        <div className="gp-fold">
        {/* nav */}
        <nav className="gp-nav">
          <a className="gp-brand" href="/">
            <span className="gp-logo">GP</span>
            <span>
              <span className="gp-brand-name">GitPilot</span>
              <br />
              <span className="gp-brand-sub">Multi-agent coding assistant</span>
            </span>
          </a>
          <div className="gp-nav-links">
            <a href="#agents">Agents</a>
            <a href="#modes">Control</a>
            <a href={DOCS_URL} target="_blank" rel="noreferrer">Docs</a>
            <a href={GITHUB_URL} target="_blank" rel="noreferrer">GitHub ↗</a>
          </div>
          <div className="gp-nav-right">
            <a className="gp-signin" href="/auth?mode=signin">Sign in</a>
            <a className="gp-btn gp-btn-primary" href="/auth?mode=signup">Get started</a>
          </div>
        </nav>

        {/* hero — vertically centered in the remaining fold space */}
        <div className="gp-fold-main">
        <header className="gp-hero">
          <div className="gp-hero-copy">
            <span className="gp-badge gp-up"><span className="dot">✦</span> The first open-source multi-agent coding assistant</span>
            <h1 className="gp-h1 gp-up d1">Your AI engineering<br />team, <span className="accent">under your control.</span></h1>
            <p className="gp-sub gp-up d2">
              Specialized agents — in configurable topologies — read your repo,
              plan changes you approve, write code, and run your tests.
              On any LLM. Open source.
            </p>
            <div className="gp-cta gp-up d3">
              <a className="gp-btn gp-btn-primary gp-btn-sm" href="/auth?mode=signup">Get started — free</a>
              <a className="gp-btn gp-btn-ghost gp-btn-sm" href={GITHUB_URL} target="_blank" rel="noreferrer">{I.git} Star on GitHub</a>
            </div>
            <div className="gp-trust gp-up d4">
              <span>{I.shield} Apache 2.0 open source</span>
              <span>{I.check2} 854 tests passing</span>
              <span>{I.lock} Private by default</span>
            </div>
          </div>

          {/* product preview — animated carousel of real GitPilot app screens */}
          <PreviewCarousel />
        </header>
        </div>

        {/* capabilities — enterprise value prop, no brand names */}
        <Reveal className="gp-llm-sec">
          <div className="gp-llm-title">Any LLM, Zero Lock-In</div>
          <div className="gp-llm-caps">
            Proprietary Cloud APIs&nbsp;&nbsp;•&nbsp;&nbsp;Local &amp; Private Models&nbsp;&nbsp;•&nbsp;&nbsp;Open-Source Weights&nbsp;&nbsp;•&nbsp;&nbsp;Custom Deployments
          </div>
        </Reveal>
        </div>{/* /gp-fold */}

        {/* agent carousel */}
        <section id="agents" className="gp-section gp-after-fold">
          <Reveal className="gp-sec-head">
            <div className="gp-eyebrow">Multi-agent topologies</div>
            <h2>One request. A whole engineering team.</h2>
            <p>Most AI tools are a single model behind a chat box. GitPilot orchestrates configurable agent topologies — routing dispatch, ReAct loops with subagents, and task pipelines — so specialized roles explore, plan, build, and review your code.</p>
          </Reveal>
          <Reveal delay={80}><AgentCarousel /></Reveal>
        </section>

        {/* execution modes */}
        <section id="modes" className="gp-section">
          <Reveal className="gp-sec-head">
            <div className="gp-eyebrow">You hold the controls</div>
            <h2>Three modes. You decide how far it goes.</h2>
            <p>Switch per session. Diffs are shown before they're applied; tests run before anything is committed. No surprises.</p>
          </Reveal>
          <div className="gp-modes">
            {MODES.map((m, i) => (
              <Reveal key={m.t} delay={i * 90}>
                <div className="gp-mode">
                  <div className="gp-mode-top">
                    <div className="ic">{m.ic}</div>
                    <span className="gp-mode-tag">{m.tag}</span>
                  </div>
                  <h3>{m.t}</h3>
                  <p>{m.d}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </section>

        {/* how it works */}
        <section className="gp-section gp-how">
          <Reveal className="gp-sec-head"><h2>From idea to pull request</h2></Reveal>
          <div className="gp-steps">
            {STEPS.map((s, i) => (
              <Reveal key={s.t} delay={i * 90}>
                <div className="gp-step">
                  <div className="n">{i + 1}</div>
                  <h4>{s.t}</h4>
                  <p>{s.d}</p>
                </div>
              </Reveal>
            ))}
          </div>
        </section>

        {/* CTA band */}
        <Reveal className="gp-cta-band">
          <h2>Ship better code, without giving up control.</h2>
          <p>Start free and local with Ollama, or bring your own OpenAI, Claude, or watsonx key.</p>
          <div className="gp-cta">
            <a className="gp-btn gp-btn-primary" href="/auth?mode=signup">Get started — free</a>
            <a className="gp-btn gp-btn-ghost" href={DOCS_URL} target="_blank" rel="noreferrer">View docs</a>
          </div>
        </Reveal>

        {/* footer */}
        <footer className="gp-foot">
          <div className="lock">{I.lock} Your code stays private. Your workflow stays in control.</div>
          <div className="gp-foot-links">
            <a href={DOCS_URL} target="_blank" rel="noreferrer">Docs</a>
            <a href={GITHUB_URL} target="_blank" rel="noreferrer">GitHub</a>
            <a href="/auth?mode=signin">Sign in</a>
          </div>
          <div className="gp-foot-copy">© {new Date().getFullYear()} GitPilot · Apache 2.0 · Made by ruslanmv</div>
        </footer>
      </div>
    </div>
  );
}
