// frontend/components/SandboxCanvas.jsx
//
// File-aware Canvas: a modal that adapts to the file type it was
// opened with.  Three modes:
//
//   code      → split editor + sandbox output.  Prepare-Run flow.
//                 Used for .py / .js / .sh / inline snippets.
//   markdown  → split source + rendered preview.  No Run button.
//                 Used for .md / .markdown.
//   text      → source-only viewer (read-only by default).
//                 Used for everything else: .json, .yml, .csv,
//                 .html, .txt, ...
//
// Mode is computed from the ``filename`` prop's extension.  When
// ``filename`` is null (an anonymous inline snippet from a chat code
// block) we fall back to code mode using the ``initialLanguage`` tag.
//
// The sandbox approval contract is unchanged: Prepare Run dispatches
// /api/sandbox/plan, the user approves a green ExecutionPlanCard,
// only then /api/sandbox/run actually executes.

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fetchExecutionPlan } from "./ExecutionPlanCard.jsx";

const BACKEND_LABELS = {
  subprocess: "Local",
  matrixlab: "MatrixLab",
  off: "Pass-through",
};

const LANG_DISPLAY = {
  py: "python", js: "javascript", node: "javascript",
  sh: "bash", shell: "bash",
};

// Extensions that route through the sandbox.  Everything else gets
// a viewer-only canvas — README.md must NOT show a Python execution
// plan, JSON must NOT pretend it can run.
const RUNNABLE_EXTS = new Set(["py", "js", "mjs", "cjs", "sh", "bash"]);
const MARKDOWN_EXTS = new Set(["md", "markdown", "mdx"]);

// Map extension → canonical sandbox language tag used by the planner.
const EXT_TO_LANG = {
  py: "python", js: "javascript", mjs: "javascript", cjs: "javascript",
  sh: "bash", bash: "bash",
};

// Display label per file type — keeps the header honest about what
// the Canvas is showing.
const TYPE_LABEL = {
  python: "Python", javascript: "JavaScript", bash: "Shell",
  markdown: "Markdown", json: "JSON", yaml: "YAML", csv: "CSV",
  html: "HTML", text: "Text",
};

function extOf(name) {
  if (!name || !name.includes(".")) return "";
  return name.split(".").pop().toLowerCase();
}

// Decide which Canvas branch to render based on the opening context.
// Pure function so tests can pin it.
function resolveCanvasMode({ filename, initialLanguage }) {
  const ext = extOf(filename || "");
  if (filename && MARKDOWN_EXTS.has(ext)) {
    return { kind: "markdown", typeLabel: "Markdown", language: "markdown" };
  }
  if (filename && RUNNABLE_EXTS.has(ext)) {
    const lang = EXT_TO_LANG[ext];
    return { kind: "code", typeLabel: TYPE_LABEL[lang], language: lang };
  }
  if (filename) {
    // Known-but-non-runnable: json / yaml / csv / html / txt / etc.
    const known = { json: "json", yml: "yaml", yaml: "yaml",
                    csv: "csv", html: "html", txt: "text" }[ext];
    if (known) {
      return { kind: "text", typeLabel: TYPE_LABEL[known], language: known };
    }
    return { kind: "text", typeLabel: ext.toUpperCase() || "Text", language: "text" };
  }
  // No filename → inline snippet from a chat code block.  Honor the
  // language tag the chat fence carried.
  const tag = (initialLanguage || "python").toLowerCase();
  if (tag === "py" || tag === "python") return { kind: "code", typeLabel: "Python snippet", language: "python" };
  if (tag === "js" || tag === "javascript" || tag === "node")
    return { kind: "code", typeLabel: "JavaScript snippet", language: "javascript" };
  if (tag === "sh" || tag === "bash" || tag === "shell")
    return { kind: "code", typeLabel: "Shell snippet", language: "bash" };
  return { kind: "text", typeLabel: "Text", language: "text" };
}

// Minimal, dependency-free Markdown → HTML renderer.  Covers the
// patterns that matter for README files: headings, bold, italic,
// inline code, fenced code blocks, lists, links.  Escapes HTML to
// avoid XSS when README contains <script> or similar.
function escapeHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
function renderMarkdown(src) {
  if (!src) return "";
  let s = escapeHtml(src);
  // Fenced code blocks first so their content isn't re-processed.
  s = s.replace(/```([\w-]*)\n([\s\S]*?)```/g, (_m, _lang, code) =>
    `<pre class="md-code"><code>${code}</code></pre>`,
  );
  // Headings — process before line-based replacements.
  s = s.replace(/^######\s+(.+)$/gm, "<h6>$1</h6>");
  s = s.replace(/^#####\s+(.+)$/gm, "<h5>$1</h5>");
  s = s.replace(/^####\s+(.+)$/gm,  "<h4>$1</h4>");
  s = s.replace(/^###\s+(.+)$/gm,   "<h3>$1</h3>");
  s = s.replace(/^##\s+(.+)$/gm,    "<h2>$1</h2>");
  s = s.replace(/^#\s+(.+)$/gm,     "<h1>$1</h1>");
  // Bold / italic / inline code.
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  // Links: [text](url) — only allow http(s) + mailto + relative paths.
  s = s.replace(/\[([^\]]+)\]\(((?:https?:\/\/|mailto:|\/|\.)[^)\s]+)\)/g,
                '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  // Unordered list items.
  s = s.replace(/^[ \t]*[-*]\s+(.+)$/gm, "<li>$1</li>");
  s = s.replace(/(<li>[\s\S]*?<\/li>(?:\n<li>[\s\S]*?<\/li>)*)/g,
                (m) => `<ul>${m.replace(/\n/g, "")}</ul>`);
  // Paragraphs — blank-line separated runs that aren't already block-tagged.
  s = s.split(/\n{2,}/).map((para) => {
    if (/^<(h\d|ul|pre|p|blockquote)/.test(para.trim())) return para;
    return `<p>${para.replace(/\n/g, " ")}</p>`;
  }).join("\n");
  return s;
}

// Mirrors the executor's _looks_like_matplotlib heuristic so plt.show()
// snippets don't hang the headless sandbox.  False positives are
// harmless (Agg is a valid backend for any Python script); false
// negatives cause a hang on a plot window that never opens.
function looksLikeMatplotlib(code) {
  if (!code) return false;
  const lower = code.toLowerCase();
  return /import\s+matplotlib|from\s+matplotlib|plt\.show|pyplot/.test(lower);
}

function applyMatplotlibShim(language, code) {
  if (language !== "python" && language !== "py") return code;
  if (!looksLikeMatplotlib(code)) return code;
  return (
    "import os as _gp_os\n" +
    '_gp_os.environ.setdefault("MPLBACKEND", "Agg")\n' +
    code
  );
}

export default function SandboxCanvas(props) {
  // Branch by mode at the top so non-runnable file types never see
  // the sandbox plumbing.  Each branch is a focused subcomponent —
  // easier to read and easier to test than a single 400-line function
  // with three conditional render paths.
  const mode = resolveCanvasMode({
    filename: props.filename,
    initialLanguage: props.initialLanguage,
  });
  if (mode.kind === "markdown") return <MarkdownCanvas {...props} mode={mode} />;
  if (mode.kind === "text") return <TextCanvas {...props} mode={mode} />;
  return <CodeCanvas {...props} mode={mode} />;
}

// ---------------------------------------------------------------------------
// CodeCanvas — the original sandbox-enabled Canvas, now scoped to
// runnable code.  Uses ``filename`` (when present and not "inline.*")
// as the ``display_filename`` on the ExecutionPlan so the command
// reads ``python demo.py`` instead of ``python inline.py``.
// ---------------------------------------------------------------------------

function CodeCanvas({
  initialCode,
  filename,
  onClose,
  onSaveAsFile,
  mode,
}) {
  const language = mode.language;
  const [code, setCode] = useState(initialCode || "");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [startTime, setStartTime] = useState(null);
  const [elapsed, setElapsed] = useState(0);
  const tickRef = useRef(null);
  // Approval-first: clicking Run fetches a deterministic plan first;
  // the slim confirmation strip lets the user back out before any code
  // actually executes in the sandbox.
  const [pendingPlan, setPendingPlan] = useState(null);
  const [planError, setPlanError] = useState(null);
  const [showRunDetails, setShowRunDetails] = useState(false);
  const [showMenu, setShowMenu] = useState(false);
  const [finishedAt, setFinishedAt] = useState(null);
  const [phase, setPhase] = useState("idle"); // idle | preparing | running | done | failed
  const menuRef = useRef(null);

  useEffect(() => {
    if (!busy || !startTime) return;
    tickRef.current = setInterval(() => {
      setElapsed(Date.now() - startTime);
    }, 100);
    return () => {
      if (tickRef.current) clearInterval(tickRef.current);
      tickRef.current = null;
    };
  }, [busy, startTime]);

  useEffect(() => {
    if (!showMenu) return;
    const onDown = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setShowMenu(false);
      }
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [showMenu]);

  const requestPlan = useCallback(async () => {
    setPlanError(null);
    setError(null);
    setBusy(true);
    setPhase("preparing");
    try {
      const shipped = applyMatplotlibShim(language, code);
      const isInlineName = (filename || "").toLowerCase().startsWith("inline.");
      const displayFilename = filename && !isInlineName ? filename : undefined;
      const plan = await fetchExecutionPlan({
        code: shipped,
        language,
        source: "canvas",
        display_filename: displayFilename,
      });
      setPendingPlan(plan);
    } catch (err) {
      setPlanError(err.message || "Could not build execution plan");
      setPhase("failed");
    } finally {
      setBusy(false);
    }
  }, [language, code, filename]);

  const approveRun = useCallback(async (plan) => {
    setBusy(true);
    setResult(null);
    setError(null);
    setStartTime(Date.now());
    setElapsed(0);
    setPhase("running");
    try {
      const res = await fetch("/api/sandbox/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          language: plan.language,
          code: plan.inline_code,
          timeout_sec: plan.timeout_sec,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || `HTTP ${res.status}`);
        setPhase("failed");
        return;
      }
      setResult(data);
      setPendingPlan(null);
      setFinishedAt(Date.now());
      setPhase(data.exit_code === 0 ? "done" : "failed");
    } catch (err) {
      setError(err.message || "Run failed");
      setPhase("failed");
    } finally {
      setBusy(false);
    }
  }, []);

  const run = requestPlan;
  const cancelPending = () => { setPendingPlan(null); setPhase("idle"); };

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape" && !busy) onClose?.();
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") run();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [run, busy, onClose]);

  const exitOk = result?.exit_code === 0;
  const displayLang = LANG_DISPLAY[language] || language;
  const headerFilename = filename
    || `inline.${displayLang === "javascript" ? "js" : displayLang === "bash" ? "sh" : "py"}`;

  const isPython = language === "python";
  const showMplNotice = isPython && looksLikeMatplotlib(code);
  // Crude but useful: flag scripts that read env vars so users can spot
  // sandbox-scoped variable scoping issues before they run.
  const readsEnvVars = /os\.environ|process\.env|getenv/.test(code || "");

  const runCommand = useMemo(() => {
    const base = headerFilename.split("/").pop();
    if (language === "python") return `python ${base}`;
    if (language === "javascript") return `node ${base}`;
    if (language === "bash") return `bash ${base}`;
    return base;
  }, [headerFilename, language]);

  const lineCount = (code || "").split("\n").length;
  const charCount = (code || "").length;

  const statusText =
    phase === "preparing" ? "Preparing sandbox…" :
    phase === "running"   ? `Running ${runCommand}…` :
    phase === "done"      ? `Completed in ${((finishedAt - startTime) / 1000).toFixed(1)}s` :
    phase === "failed"    ? "Execution failed" :
                            "Ready to run";

  const statusTone =
    phase === "preparing" || phase === "running" ? "info" :
    phase === "done"   ? "ok" :
    phase === "failed" ? "bad" :
                          "ok";

  return (
    <div style={s.backdrop} onClick={onClose}>
      <div className="canvas-modal" style={s.modal} onClick={(e) => e.stopPropagation()}>
        <header className="canvas-header">
          <div className="canvas-header__left">
            <div className="canvas-header__glyph" aria-hidden="true">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                   strokeLinejoin="round">
                <polyline points="4 17 10 11 4 5" />
                <line x1="12" y1="19" x2="20" y2="19" />
              </svg>
            </div>
            <div className="canvas-header__title">{headerFilename.split("/").pop()}</div>
            <span className="canvas-pill canvas-pill--lang">
              <span className="canvas-pill__dot" />
              {mode.typeLabel}
            </span>
            <span className="canvas-pill canvas-pill--sandbox">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                   strokeLinejoin="round" aria-hidden="true">
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              </svg>
              Local sandbox
            </span>
          </div>
          <div className="canvas-header__right">
            <button
              type="button"
              className="canvas-run-btn"
              onClick={pendingPlan ? () => approveRun(pendingPlan) : requestPlan}
              disabled={busy && phase !== "running"}
            >
              {phase === "running" ? (
                <>
                  <span className="canvas-run-btn__spinner" />
                  Running {(elapsed / 1000).toFixed(1)}s
                </>
              ) : phase === "preparing" ? (
                "Preparing…"
              ) : pendingPlan ? (
                <>▶ Confirm & run</>
              ) : (
                <>▶ Run</>
              )}
            </button>
            <div className="canvas-menu" ref={menuRef}>
              <button
                type="button"
                className="canvas-icon-btn"
                onClick={() => setShowMenu((v) => !v)}
                aria-label="More actions"
                aria-haspopup="menu"
                aria-expanded={showMenu}
              >
                ⋮
              </button>
              {showMenu && (
                <div className="canvas-menu__panel" role="menu">
                  {onSaveAsFile && (
                    <button
                      type="button"
                      className="canvas-menu__item"
                      onClick={() => { setShowMenu(false); onSaveAsFile(code, language); }}
                    >
                      Save to repo…
                    </button>
                  )}
                  <button
                    type="button"
                    className="canvas-menu__item"
                    onClick={() => {
                      if (navigator?.clipboard) {
                        navigator.clipboard.writeText(code).catch(() => {});
                      }
                      setShowMenu(false);
                    }}
                  >
                    Copy source
                  </button>
                  <button
                    type="button"
                    className="canvas-menu__item"
                    onClick={() => { setShowMenu(false); onClose?.(); }}
                  >
                    Close canvas
                  </button>
                </div>
              )}
            </div>
            <button type="button" className="canvas-icon-btn canvas-icon-btn--close"
                    onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </header>

        <div className="canvas-status">
          <div className={`canvas-status__dot canvas-status__dot--${statusTone}`} aria-hidden="true">
            {phase === "done" ? "✓" :
             phase === "failed" ? "✕" :
             phase === "preparing" || phase === "running" ? "" :
             "▸"}
          </div>
          <div className="canvas-status__text">
            <div className="canvas-status__title">{statusText}</div>
            <div className="canvas-status__sub">
              <code>{runCommand}</code>
              <span className="canvas-status__sep">·</span>
              Timeout 120s
              <span className="canvas-status__sep">·</span>
              Network disabled
            </div>
          </div>
          <button
            type="button"
            className="canvas-status__details"
            onClick={() => setShowRunDetails((v) => !v)}
            aria-expanded={showRunDetails}
          >
            Details {showRunDetails ? "▴" : "▾"}
          </button>
        </div>

        {showRunDetails && (
          <div className="canvas-run-config">
            <div className="canvas-run-config__row">
              <span className="canvas-run-config__label">Command</span>
              <code className="canvas-run-config__val">{runCommand}</code>
            </div>
            <div className="canvas-run-config__row">
              <span className="canvas-run-config__label">Environment</span>
              <span className="canvas-run-config__val">Local sandbox (isolated)</span>
            </div>
            <div className="canvas-run-config__row">
              <span className="canvas-run-config__label">Timeout</span>
              <span className="canvas-run-config__val">120s</span>
            </div>
            <div className="canvas-run-config__row">
              <span className="canvas-run-config__label">Network</span>
              <span className="canvas-run-config__val">Disabled</span>
            </div>
            <div className="canvas-run-config__row">
              <span className="canvas-run-config__label">Snippet</span>
              <span className="canvas-run-config__val">{lineCount} lines · {charCount} chars</span>
            </div>
          </div>
        )}

        {(showMplNotice || readsEnvVars) && (
          <div className="canvas-notices">
            <div className="canvas-notices__label">Notices</div>
            <div className="canvas-notices__row">
              {readsEnvVars && (
                <div className="canvas-notice canvas-notice--info">
                  <div className="canvas-notice__icon" aria-hidden="true">ⓘ</div>
                  <div className="canvas-notice__body">
                    <div className="canvas-notice__title">Reads environment variables</div>
                    <div className="canvas-notice__desc">
                      Script reads env vars — only sandbox-scoped variables are present.
                    </div>
                  </div>
                </div>
              )}
              {showMplNotice && (
                <div className="canvas-notice canvas-notice--accent">
                  <div className="canvas-notice__icon" aria-hidden="true">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                         stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                         strokeLinejoin="round">
                      <polyline points="3 17 9 11 13 15 21 7" />
                    </svg>
                  </div>
                  <div className="canvas-notice__body">
                    <div className="canvas-notice__title">Matplotlib detected</div>
                    <div className="canvas-notice__desc">
                      Plots will render inline in the sandbox output.
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {pendingPlan && (
          <div className="canvas-confirm">
            <div className="canvas-confirm__text">
              GitPilot will run <code>{runCommand}</code> in the local sandbox.
              Click <strong>Confirm & run</strong> to proceed.
            </div>
            <button type="button" className="canvas-confirm__cancel" onClick={cancelPending}>
              Cancel
            </button>
          </div>
        )}
        {planError && (
          <div className="canvas-plan-error">Plan error: {planError}</div>
        )}

        <div className="canvas-body">
          <div className="canvas-pane canvas-pane--source">
            <div className="canvas-pane__head">
              <div className="canvas-pane__title">Source</div>
              <div className="canvas-pane__head-right">
                <span className="canvas-pane__meta">{mode.typeLabel}</span>
                <button
                  type="button"
                  className="canvas-pane__icon"
                  title="Copy source"
                  onClick={() => {
                    if (navigator?.clipboard) {
                      navigator.clipboard.writeText(code).catch(() => {});
                    }
                  }}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                       stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                       strokeLinejoin="round">
                    <rect x="9" y="9" width="13" height="13" rx="2" />
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                  </svg>
                </button>
              </div>
            </div>
            <div className="canvas-editor-wrap">
              <pre className="canvas-editor-gutter" aria-hidden="true">
                {Array.from({ length: Math.max(lineCount, 1) }, (_, i) => (
                  <div key={i} className="canvas-editor-gutter__line">{i + 1}</div>
                ))}
              </pre>
              <textarea
                className="canvas-editor"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                spellCheck={false}
              />
            </div>
            <div className="canvas-statusbar">
              <span>{lineCount} lines</span>
              <span className="canvas-statusbar__sep">·</span>
              <span>{mode.typeLabel}</span>
              <span className="canvas-statusbar__spacer" />
              <span className="canvas-statusbar__kbd">⌘/Ctrl+Enter to run · Esc to close</span>
            </div>
          </div>

          <div className="canvas-pane canvas-pane--output">
            <div className="canvas-pane__head">
              <div className="canvas-pane__title">Sandbox output</div>
              {result && (
                <button
                  type="button"
                  className="canvas-pane__icon-btn"
                  title="Clear output"
                  onClick={() => { setResult(null); setError(null); setPhase("idle"); }}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                       stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                       strokeLinejoin="round" aria-hidden="true">
                    <polyline points="3 6 5 6 21 6" />
                    <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                  </svg>
                  Clear
                </button>
              )}
            </div>

            <div className="canvas-output-body">
              {!result && !error && phase === "idle" && (
                <div className="canvas-output-empty">
                  <div className="canvas-output-empty__title">Ready to run</div>
                  <div className="canvas-output-empty__sub">
                    Click <strong>▶ Run</strong> to execute this snippet in the local sandbox.
                  </div>
                </div>
              )}

              {(phase === "preparing" || phase === "running") && (
                <div className="canvas-output-running">
                  <span className="canvas-output-running__spinner" aria-hidden="true" />
                  <span>
                    {phase === "preparing" ? "Preparing sandbox…" : `Running… ${(elapsed / 1000).toFixed(1)}s`}
                  </span>
                </div>
              )}

              {error && phase === "failed" && (
                <div className="canvas-result-head canvas-result-head--bad">
                  <span className="canvas-result-icon">✕</span>
                  <div>
                    <div className="canvas-result-title">Execution failed</div>
                    <pre className="canvas-result-err">{error}</pre>
                  </div>
                </div>
              )}

              {result && (
                <>
                  <div className={`canvas-result-head canvas-result-head--${exitOk ? "ok" : "bad"}`}>
                    <span className="canvas-result-icon">{exitOk ? "✓" : "✕"}</span>
                    <div className="canvas-result-headtext">
                      <div className="canvas-result-title">
                        {exitOk ? `Completed in ${((finishedAt - startTime) / 1000).toFixed(1)}s` : `Failed (exit ${result.exit_code})`}
                      </div>
                      <div className="canvas-result-sub">
                        {BACKEND_LABELS[result.backend] || result.backend}
                        {typeof result.duration_ms === "number" && ` · ${result.duration_ms} ms`}
                        {result.timed_out && " · timed out"}
                        {result.truncated && " · truncated"}
                      </div>
                    </div>
                    <span className="canvas-result-time">
                      {new Date(finishedAt || Date.now()).toLocaleTimeString([], {
                        hour: "numeric", minute: "2-digit",
                      })}
                    </span>
                  </div>

                  {result.stdout && (
                    <div className="canvas-output-section">
                      <div className="canvas-output-section__label">
                        <span>STDOUT</span>
                      </div>
                      <pre className="canvas-output-pre">{result.stdout}</pre>
                    </div>
                  )}

                  {Array.isArray(result.artifacts) && result.artifacts.length > 0 && (
                    <div className="canvas-output-section">
                      <div className="canvas-output-section__label">
                        Artifacts <span className="canvas-output-section__count">{result.artifacts.length}</span>
                      </div>
                      <ul className="canvas-artifacts">
                        {result.artifacts.map((a, i) => (
                          <ArtifactItem key={i} artifact={a} />
                        ))}
                      </ul>
                    </div>
                  )}

                  {result.stderr && (
                    <div className="canvas-output-section">
                      <div className="canvas-output-section__label canvas-output-section__label--err">
                        STDERR
                      </div>
                      <pre className="canvas-output-pre canvas-output-pre--err">{result.stderr}</pre>
                    </div>
                  )}

                  {!result.stdout && !result.stderr && (!result.artifacts || result.artifacts.length === 0) && (
                    <div className="canvas-output-empty">
                      <div className="canvas-output-empty__sub">
                        {exitOk
                          ? "The script completed without producing any output."
                          : "The script failed without producing any output."}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ArtifactItem({ artifact }) {
  const name = artifact.name || artifact.id || "artifact";
  const isImage =
    (artifact.mime && artifact.mime.startsWith("image/")) ||
    /\.(png|jpg|jpeg|gif|svg|webp)$/i.test(name);
  const src =
    artifact.data_url ||
    artifact.url ||
    (artifact.content_b64 && artifact.mime
      ? `data:${artifact.mime};base64,${artifact.content_b64}`
      : null);

  return (
    <li className="canvas-artifact">
      {isImage && src && (
        <img src={src} alt={name} className="canvas-artifact__img" />
      )}
      <div className="canvas-artifact__row">
        <div className="canvas-artifact__icon" aria-hidden="true">
          {isImage ? (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                 strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <circle cx="8.5" cy="8.5" r="1.5" />
              <polyline points="21 15 16 10 5 21" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                 strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
          )}
        </div>
        <code className="canvas-artifact__name">{name}</code>
        {isImage && src && (
          <span className="canvas-artifact__badge">Plot rendered inline</span>
        )}
        {artifact.size != null && (
          <span className="canvas-artifact__meta">
            {typeof artifact.size === "number" ? `${artifact.size} bytes` : artifact.size}
          </span>
        )}
        {src && (
          <a
            href={src}
            download={name}
            className="canvas-artifact__download"
            title="Download artifact"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                 strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" />
              <line x1="12" y1="15" x2="12" y2="3" />
            </svg>
          </a>
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// MarkdownCanvas — split source / rendered preview.  No Run button,
// no execution plan.  Mode switch lets users pick Source / Preview /
// Split.  Renderer is dependency-free (escapes HTML, then formats
// the patterns README files actually use).
// ---------------------------------------------------------------------------

function MarkdownCanvas({ initialCode, filename, onClose, mode }) {
  const [view, setView] = useState("split"); // "source" | "preview" | "split"
  const html = useMemo(() => renderMarkdown(initialCode || ""), [initialCode]);
  // Inject the rendered-markdown stylesheet once.  Cheaper than a
  // styled-component dance and keeps the renderer dependency-free.
  useEffect(() => {
    const id = "gitpilot-md-style";
    if (document.getElementById(id)) return;
    const style = document.createElement("style");
    style.id = id;
    style.textContent = `
      .gp-md h1 { font-size: 22px; margin: 18px 0 8px; padding-bottom: 6px; border-bottom: 1px solid #2c2d46; color: #f4f4f5; }
      .gp-md h2 { font-size: 18px; margin: 16px 0 6px; padding-bottom: 4px; border-bottom: 1px solid #1f2937; color: #f4f4f5; }
      .gp-md h3 { font-size: 15px; margin: 14px 0 4px; color: #f4f4f5; }
      .gp-md h4, .gp-md h5, .gp-md h6 { font-size: 13px; margin: 12px 0 4px; color: #e4e4e7; }
      .gp-md p  { margin: 8px 0; color: #d4d4d8; }
      .gp-md ul { margin: 6px 0 6px 18px; padding: 0; color: #d4d4d8; }
      .gp-md li { margin: 3px 0; }
      .gp-md a  { color: #93c5fd; }
      .gp-md code { background: #000; color: #86efac; padding: 1px 5px; border-radius: 3px; font-family: ui-monospace, monospace; font-size: 12.5px; }
      .gp-md pre.md-code { background: #000; color: #d4d4d8; padding: 10px 12px; border-radius: 6px; overflow: auto; }
      .gp-md pre.md-code code { background: transparent; color: inherit; padding: 0; }
      .gp-md strong { color: #f4f4f5; }
    `;
    document.head.appendChild(style);
  }, []);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const copy = () => {
    if (navigator?.clipboard) navigator.clipboard.writeText(initialCode || "").catch(() => {});
  };

  return (
    <div style={s.backdrop} onClick={onClose}>
      <div style={s.modal} onClick={(e) => e.stopPropagation()}>
        <header style={s.header}>
          <div style={s.headerLeft}>
            <span style={s.canvasBadge}>Canvas</span>
            <span style={s.filename}>{filename || "untitled.md"}</span>
            <span style={s.langPill}>{mode.typeLabel}</span>
          </div>
          <div style={s.headerRight}>
            {/* Mode switch — Source / Preview / Split.  Default split
                because users often want to compare raw markdown to
                rendered output side-by-side. */}
            <div style={s.segGroup} role="tablist">
              {["source", "preview", "split"].map((v) => (
                <button
                  key={v}
                  type="button"
                  role="tab"
                  aria-selected={view === v}
                  onClick={() => setView(v)}
                  style={view === v ? s.segActive : s.seg}
                >
                  {v[0].toUpperCase() + v.slice(1)}
                </button>
              ))}
            </div>
            <button type="button" style={s.btn} onClick={copy}>
              Copy
            </button>
            <button type="button" style={s.btnClose} onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </header>

        <div style={view === "split" ? s.body : s.bodySingle}>
          {(view === "source" || view === "split") && (
            <div style={s.editorPane}>
              <div style={s.paneLabel}>Markdown source</div>
              <pre style={s.mdSource}>{initialCode || ""}</pre>
            </div>
          )}
          {(view === "preview" || view === "split") && (
            <div style={s.outputPane}>
              <div style={s.paneLabel}>Rendered preview</div>
              <div
                className="gp-md"
                style={s.mdRendered}
                // Renderer escapes HTML before formatting, so the
                // injected string is safe.  No <script>, no event
                // handlers slip through.
                dangerouslySetInnerHTML={{ __html: html }}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// TextCanvas — viewer-only Canvas for non-runnable, non-markdown
// file types (JSON, YAML, CSV, HTML, plain text, ...).  No Run, no
// plan, no markdown rendering.  Useful when a user clicks Open in
// Canvas on, say, a config file just to read it.
// ---------------------------------------------------------------------------

function TextCanvas({ initialCode, filename, onClose, mode }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const copy = () => {
    if (navigator?.clipboard) navigator.clipboard.writeText(initialCode || "").catch(() => {});
  };

  return (
    <div style={s.backdrop} onClick={onClose}>
      <div style={s.modal} onClick={(e) => e.stopPropagation()}>
        <header style={s.header}>
          <div style={s.headerLeft}>
            <span style={s.canvasBadge}>Canvas</span>
            <span style={s.filename}>{filename || "untitled"}</span>
            <span style={s.langPill}>{mode.typeLabel}</span>
          </div>
          <div style={s.headerRight}>
            <button type="button" style={s.btn} onClick={copy}>
              Copy
            </button>
            <button type="button" style={s.btnClose} onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </header>

        <div style={s.bodySingle}>
          <div style={s.editorPane}>
            <div style={s.paneLabel}>Source</div>
            <pre style={s.mdSource}>{initialCode || ""}</pre>
          </div>
        </div>
      </div>
    </div>
  );
}

const s = {
  backdrop: {
    position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)",
    display: "flex", alignItems: "center", justifyContent: "center",
    zIndex: 100,
  },
  modal: {
    width: "min(1200px, 96vw)", height: "min(800px, 92vh)",
    background: "#1a1b26", border: "1px solid #2a2b36", borderRadius: 10,
    display: "flex", flexDirection: "column",
    color: "#e6e8ff", overflow: "hidden",
  },
  header: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    padding: "10px 14px", borderBottom: "1px solid #2a2b36",
    background: "#14152a",
  },
  headerLeft:  { display: "flex", alignItems: "center", gap: 10 },
  headerRight: { display: "flex", alignItems: "center", gap: 8 },
  canvasBadge: {
    fontSize: 10, fontWeight: 700, letterSpacing: "0.06em",
    padding: "2px 8px", borderRadius: 4,
    background: "#1e3a5f", color: "#93c5fd", textTransform: "uppercase",
  },
  filename: { fontFamily: "ui-monospace, monospace", fontSize: 13 },
  langPill: {
    fontSize: 11, color: "#9092b5",
    padding: "1px 6px", borderRadius: 4, background: "#0e0f24",
    border: "1px solid #2c2d46",
  },
  btn: {
    padding: "6px 12px", fontSize: 12, fontWeight: 600,
    background: "#3B82F6", color: "#fff",
    border: "none", borderRadius: 6, cursor: "pointer",
  },
  btnClose: {
    padding: "4px 10px", fontSize: 14,
    background: "transparent", color: "#9092b5",
    border: "1px solid #2c2d46", borderRadius: 6, cursor: "pointer",
  },
  body: { flex: 1, display: "grid", gridTemplateColumns: "1fr 1fr", overflow: "hidden" },
  editorPane: {
    display: "flex", flexDirection: "column",
    borderRight: "1px solid #2a2b36", padding: 12, gap: 6,
  },
  outputPane: {
    display: "flex", flexDirection: "column",
    padding: 12, gap: 6, overflow: "auto",
  },
  paneLabel: { fontSize: 11, color: "#9092b5", textTransform: "uppercase", letterSpacing: "0.06em" },
  editor: {
    flex: 1, resize: "none",
    background: "#0d0e17", color: "#e6e8ff",
    border: "1px solid #2c2d46", borderRadius: 6, padding: 10,
    fontFamily: "ui-monospace, SFMono-Regular, monospace", fontSize: 13, lineHeight: 1.5,
  },
  editorFoot: { display: "flex", justifyContent: "space-between", fontSize: 11, color: "#9092b5" },
  shimHint:  { color: "#86efac" },
  kbdHint:   { color: "#6b7280" },
  empty: { color: "#9092b5", fontSize: 13, padding: "20px 8px", textAlign: "center" },
  resultHead: { display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 },
  outputSection: { marginTop: 6 },
  outputLabel: { cursor: "pointer", fontSize: 11, color: "#9092b5" },
  stdout: {
    margin: "4px 0 0", padding: "8px 10px", borderRadius: 4,
    background: "#000", color: "#d4d4d8", fontSize: 12,
    fontFamily: "ui-monospace, monospace", whiteSpace: "pre-wrap",
  },
  stderr: {
    margin: "4px 0 0", padding: "8px 10px", borderRadius: 4,
    background: "#000", color: "#fca5a5", fontSize: 12,
    fontFamily: "ui-monospace, monospace", whiteSpace: "pre-wrap",
  },
  errorBox: {
    padding: "8px 10px", borderRadius: 6,
    background: "#3d1111", border: "1px solid #7f1d1d", color: "#fca5a5", fontSize: 12,
  },
  errorPre: { margin: "4px 0 0", whiteSpace: "pre-wrap" },
  okPill:      { padding: "2px 8px", borderRadius: 10, fontSize: 11, fontWeight: 600, background: "#0d3320", color: "#86efac", border: "1px solid #166534" },
  failPill:    { padding: "2px 8px", borderRadius: 10, fontSize: 11, fontWeight: 600, background: "#3d1111", color: "#fca5a5", border: "1px solid #7f1d1d" },
  warnPill:    { padding: "2px 8px", borderRadius: 10, fontSize: 11, fontWeight: 600, background: "#3d2d11", color: "#fde68a", border: "1px solid #854d0e" },
  backendPill: { padding: "2px 8px", borderRadius: 10, fontSize: 11, color: "#c3c5dd", border: "1px solid #2c2d46" },
  dim: { fontSize: 11, color: "#9092b5" },
  artifactList: { listStyle: "none", padding: 0, margin: "6px 0 0" },
  artifactItem: { padding: "3px 0", fontSize: 12 },
  planStrip: {
    padding: "8px 12px",
    background: "#0d0e17",
    borderBottom: "1px solid #2a2b36",
  },
  planErrorStrip: {
    padding: "8px 12px",
    fontSize: 12,
    color: "#fca5a5",
    background: "#3d1111",
    borderBottom: "1px solid #7f1d1d",
  },
  // Single-pane layout used by MarkdownCanvas (when not in split) and
  // TextCanvas.  Stretches the single child column across the full
  // modal body width.
  bodySingle: { flex: 1, display: "grid", gridTemplateColumns: "1fr", overflow: "hidden" },
  // Source / Preview / Split segmented control.
  segGroup: { display: "inline-flex", background: "#0d0e17", border: "1px solid #2c2d46", borderRadius: 6, overflow: "hidden" },
  seg: {
    padding: "5px 10px", fontSize: 11,
    background: "transparent", color: "#9092b5",
    border: "none", cursor: "pointer",
  },
  segActive: {
    padding: "5px 10px", fontSize: 11, fontWeight: 600,
    background: "#1e3a5f", color: "#fff",
    border: "none", cursor: "pointer",
  },
  // Markdown source pane — read-only preformatted view, distinct from
  // the editable <textarea> the CodeCanvas uses.
  mdSource: {
    flex: 1, margin: 0, padding: 12,
    background: "#0d0e17", color: "#e6e8ff",
    border: "1px solid #2c2d46", borderRadius: 6,
    fontFamily: "ui-monospace, SFMono-Regular, monospace", fontSize: 13, lineHeight: 1.55,
    whiteSpace: "pre-wrap", wordBreak: "break-word",
    overflow: "auto",
  },
  // Rendered preview pane.  Subset of CSS that matches the look of
  // GitHub's rendered README without pulling in a markdown library.
  mdRendered: {
    flex: 1, padding: "8px 16px 24px",
    background: "#0d0e17", color: "#e6e8ff",
    border: "1px solid #2c2d46", borderRadius: 6,
    overflow: "auto",
    fontSize: 14, lineHeight: 1.6,
  },
};
