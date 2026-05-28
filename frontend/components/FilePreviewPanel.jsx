// frontend/components/FilePreviewPanel.jsx
//
// Read-first file viewer with two density modes.
//
//   mode="preview"    Narrow right-side drawer for a quick look.
//                     ~520px wide, primary CTA "Prepare Run".
//   mode="workspace"  Wide right-side editor for serious review.
//                     ~85vw, same actions, much more reading room.
//
// Header vocabulary follows the enterprise verbs the design review
// nailed down:
//
//   [▶ Prepare Run]   only on runnable files (hidden on README etc.)
//   [Open Workspace]  upgrades preview → workspace (or back)
//   [⋯]               overflow: Ask GitPilot · Open Canvas ·
//                     Copy path · Copy contents
//
// "Prepare Run" never runs anything directly — it dispatches the
// gitpilot:run-file event which lands on the green ExecutionPlanCard
// in chat, where the user approves before the sandbox starts.
//
// Error state: when the file content fetch fails we hide every action
// that depends on having content (Prepare Run / Canvas / Ask).  Only
// safe actions remain (Retry / Copy path).

import React, { useEffect, useMemo, useRef, useState } from "react";

const RUNNABLE_FILE_EXTS = new Set(["py", "js", "mjs", "cjs", "sh", "bash"]);

const LANG_LABEL = {
  py: "Python", js: "JavaScript", mjs: "JavaScript", cjs: "JavaScript",
  sh: "Shell", bash: "Shell",
  md: "Markdown", json: "JSON", yml: "YAML", yaml: "YAML", toml: "TOML",
  html: "HTML", css: "CSS", ts: "TypeScript", tsx: "TypeScript",
  rs: "Rust", go: "Go", java: "Java", c: "C", cpp: "C++", h: "C/C++",
};

function extOf(path) {
  if (!path || !path.includes(".")) return "";
  return path.split(".").pop().toLowerCase();
}

function bytesPretty(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

// ---------------------------------------------------------------------------
// Overflow menu — secondary actions live here so the header stays calm.
// ---------------------------------------------------------------------------

function OverflowMenu({ path, content, runnable, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    const onDown = () => onClose?.();
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [onClose]);

  const copy = (text) => {
    if (navigator?.clipboard && text != null) navigator.clipboard.writeText(text).catch(() => {});
  };
  const items = [
    { label: "Ask GitPilot",
      onClick: () => window.dispatchEvent(new CustomEvent("gitpilot:ask-about-file", { detail: { path } })) },
    { label: "Open in Canvas",  runnable: true,
      onClick: () => window.dispatchEvent(new CustomEvent("gitpilot:open-in-canvas", { detail: { path } })) },
    { divider: true },
    { label: "Copy path",     onClick: () => copy(path) },
    { label: "Copy contents", onClick: () => copy(content), disabled: !content },
  ];

  return (
    <div role="menu"
         onMouseDown={(e) => e.stopPropagation()}
         style={{
           position: "absolute", right: 6, top: 36, zIndex: 50,
           minWidth: 200,
           background: "#18181B", border: "1px solid #3F3F46",
           borderRadius: 6, padding: "4px 0",
           boxShadow: "0 8px 20px rgba(0,0,0,0.45)",
         }}>
      {items.map((it, i) => {
        if (it.divider) return <div key={i} style={{ height: 1, background: "#27272A", margin: "4px 0" }} />;
        if (it.runnable && !runnable) return null;
        return (
          <button
            key={i}
            role="menuitem"
            type="button"
            disabled={!!it.disabled}
            onClick={(e) => { e.stopPropagation(); it.onClick(); onClose?.(); }}
            style={{
              width: "100%", textAlign: "left",
              background: "transparent",
              color: it.disabled ? "#52525B" : "#E4E4E7",
              border: "none",
              padding: "6px 12px", fontSize: 12,
              cursor: it.disabled ? "not-allowed" : "pointer",
            }}
            onMouseEnter={(e) => { if (!it.disabled) e.currentTarget.style.background = "#27272A"; }}
            onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
          >
            {it.label}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main panel
// ---------------------------------------------------------------------------

export default function FilePreviewPanel({
  path,
  content,
  loading,
  error,
  errorCode,            // numeric HTTP status when known (e.g. 404)
  notFoundKind,         // "deleted" | "syncing" | "unavailable"
  mode = "preview",     // "preview" | "workspace"
  branch,
  onModeChange,
  onClose,
  onRetry,
  onRefreshTree,
}) {
  const ext = extOf(path);
  const lang = LANG_LABEL[ext] || ext.toUpperCase() || "Text";
  const runnable = RUNNABLE_FILE_EXTS.has(ext);
  const size = content ? new Blob([content]).size : null;
  const filename = path ? path.split("/").pop() : "";
  const [menuOpen, setMenuOpen] = useState(false);
  const menuBtnRef = useRef(null);

  // Esc closes — keyboard contract every modal/drawer in GitPilot uses.
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const prepareRun = () => {
    window.dispatchEvent(new CustomEvent("gitpilot:run-file", { detail: { path } }));
    onClose?.();
  };
  const toggleWorkspace = () => {
    if (onModeChange) onModeChange(mode === "workspace" ? "preview" : "workspace");
  };

  const lines = useMemo(() => (content || "").split("\n"), [content]);

  // Width contract: preview = peek, workspace = serious reading.
  const widthCss = mode === "workspace"
    ? "min(1280px, 86vw)"
    : "min(520px, 44vw)";

  // Decide which actions to show.  Two rules:
  //   1. While loading, secondary actions exist but disabled.
  //   2. On error, hide every action that requires content; we show
  //      Retry + Copy path only.
  const showActions = !error;

  return (
    <aside style={{ ...s.shell, width: widthCss }} role="region" aria-label="File preview">
      <header style={s.header}>
        <div style={s.titleRow}>
          <div style={{ minWidth: 0 }}>
            <div style={s.filename}>{filename || "Untitled"}</div>
            <div style={s.subpath}>
              <span style={{ opacity: 0.8 }}>Path:</span> {path}
              {branch && (
                <>
                  <span style={s.dot}>·</span>
                  <span style={{ opacity: 0.8 }}>Branch:</span> {branch}
                </>
              )}
            </div>
          </div>
          <div style={s.metaPills}>
            <span style={s.pill}>{lang}</span>
            {size != null && <span style={s.pillDim}>{bytesPretty(size)}</span>}
            <button type="button" style={s.btnClose} onClick={onClose} aria-label="Close preview">
              ✕
            </button>
          </div>
        </div>

        <div style={s.actions}>
          {showActions && runnable && (
            <button
              type="button" style={s.btnPrimary} onClick={prepareRun}
              disabled={loading || !content}
              title="Build an Execution Plan you can review before running"
            >
              ▶ Prepare Run
            </button>
          )}
          {showActions && (
            <button type="button" style={s.btnSecondary} onClick={toggleWorkspace}
                    title={mode === "workspace" ? "Collapse to preview" : "Open a wider editor view"}>
              {mode === "workspace" ? "Collapse" : "Open Workspace"}
            </button>
          )}
          {/* Error-only actions */}
          {error && (
            <button type="button" style={s.btnPrimary} onClick={onRetry}>
              Retry
            </button>
          )}
          {error && (
            <button
              type="button" style={s.btnSecondary}
              onClick={() => {
                if (navigator?.clipboard && path) navigator.clipboard.writeText(path).catch(() => {});
              }}
            >
              Copy path
            </button>
          )}

          {/* Overflow menu — only on the happy path; on error we
              already trimmed the safe-only action set above. */}
          {showActions && (
            <div style={{ marginLeft: "auto", position: "relative" }}>
              <button
                type="button" ref={menuBtnRef}
                style={s.btnSecondary}
                aria-haspopup="menu" aria-expanded={menuOpen}
                onMouseDown={(e) => e.stopPropagation()}
                onClick={() => setMenuOpen((v) => !v)}
                title="More actions"
              >
                ⋯
              </button>
              {menuOpen && (
                <OverflowMenu
                  path={path}
                  content={content}
                  runnable={runnable}
                  onClose={() => setMenuOpen(false)}
                />
              )}
            </div>
          )}
        </div>
      </header>

      <div style={s.body}>
        {loading && <div style={s.empty}>Loading {path}…</div>}

        {error && !loading && (
          <NotAvailableCard
            path={path}
            branch={branch}
            error={error}
            errorCode={errorCode}
            kind={notFoundKind}
            onRetry={onRetry}
            onRefreshTree={onRefreshTree}
          />
        )}

        {!loading && !error && (
          <pre style={s.code}>
            {lines.map((line, i) => (
              <div key={i} style={s.lineRow}>
                <span style={s.lineNo}>{i + 1}</span>
                <span style={s.lineCode}>{line || " "}</span>
              </div>
            ))}
          </pre>
        )}
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------------------
// Calm empty state — replaces the harsh red "Couldn't load this file"
// box. Same component, classifies the situation into one of:
//   deleted     → execution removed the file from this branch
//   syncing     → execution created the file but content isn't ready yet
//   unavailable → file simply isn't on this branch
// ---------------------------------------------------------------------------

function NotAvailableCard({ path, branch, error, errorCode, kind, onRetry, onRefreshTree }) {
  const isNotFound =
    errorCode === 404 ||
    /not\s*found/i.test(String(error || "")) ||
    /HTTP\s*404/i.test(String(error || ""));

  const effectiveKind = isNotFound ? (kind || "unavailable") : "error";

  const copy = {
    deleted: {
      icon: "🗑",
      title: "File deleted",
      body: `${path ? path.split("/").pop() : "This file"} was removed by the latest execution. View the execution summary for details.`,
      tone: "muted",
    },
    syncing: {
      icon: "↻",
      title: "File still syncing",
      body: "GitPilot just created this file. Its content is being published to the active branch — try again in a moment.",
      tone: "info",
    },
    unavailable: {
      icon: "○",
      title: "File unavailable",
      body: "This file isn't available on the current branch. It may have been moved, renamed, or never committed here.",
      tone: "muted",
    },
    error: {
      icon: "!",
      title: "Couldn't load this file",
      body: "Something went wrong fetching the file content. Try again, or refresh the file tree.",
      tone: "warn",
    },
  }[effectiveKind];

  return (
    <div
      className={`file-notavail file-notavail--${copy.tone}`}
      role="status"
      aria-live="polite"
    >
      <div className="file-notavail__icon" aria-hidden="true">{copy.icon}</div>
      <div className="file-notavail__title">{copy.title}</div>
      <div className="file-notavail__body">{copy.body}</div>

      <dl className="file-notavail__fields">
        <dt>Path</dt>
        <dd><code>{path}</code></dd>
        {branch && (
          <>
            <dt>Branch</dt>
            <dd><code>{branch}</code></dd>
          </>
        )}
      </dl>

      <div className="file-notavail__actions">
        <button type="button" className="file-notavail__btn file-notavail__btn--primary" onClick={onRetry}>
          Retry
        </button>
        {onRefreshTree && (
          <button type="button" className="file-notavail__btn" onClick={onRefreshTree}>
            Refresh files
          </button>
        )}
        <button
          type="button"
          className="file-notavail__btn"
          onClick={() => {
            if (navigator?.clipboard && path) {
              navigator.clipboard.writeText(path).catch(() => {});
            }
          }}
        >
          Copy path
        </button>
      </div>

      {effectiveKind === "error" && error && (
        <details className="file-notavail__details">
          <summary>Technical details</summary>
          <pre>{String(error)}</pre>
        </details>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const s = {
  shell: {
    position: "fixed",
    top: 0, right: 0, bottom: 0,
    // ``width`` is set per-mode in the component.
    background: "#0d0e17",
    borderLeft: "1px solid #2a2b36",
    display: "flex", flexDirection: "column",
    zIndex: 90,
    color: "#e4e4e7",
    fontFamily: "system-ui, sans-serif",
    boxShadow: "-12px 0 32px rgba(0,0,0,0.45)",
    transition: "width 0.15s ease",
  },
  header: {
    padding: "12px 16px 10px",
    borderBottom: "1px solid #2a2b36",
    background: "#14152a",
  },
  titleRow: { display: "flex", justifyContent: "space-between", gap: 12 },
  filename: {
    fontSize: 16, fontWeight: 600,
    fontFamily: "ui-monospace, monospace",
    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
  },
  subpath: {
    fontSize: 11, color: "#9092b5", marginTop: 2,
    fontFamily: "ui-monospace, monospace",
    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
  },
  dot: { margin: "0 6px", color: "#52525B" },
  metaPills: { display: "flex", alignItems: "flex-start", gap: 6, flexShrink: 0 },
  pill: {
    fontSize: 11, padding: "2px 8px", borderRadius: 4,
    background: "#1e3a5f", color: "#93c5fd",
    border: "1px solid #3B82F6",
  },
  pillDim: {
    fontSize: 11, padding: "2px 8px", borderRadius: 4,
    background: "#0d0e17", color: "#9092b5",
    border: "1px solid #2c2d46",
  },
  actions: { display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap", alignItems: "center" },
  btnPrimary: {
    padding: "6px 14px", fontSize: 12, fontWeight: 600,
    background: "#10B981", color: "#052e1c",
    border: "0", borderRadius: 6, cursor: "pointer",
  },
  btnSecondary: {
    padding: "6px 12px", fontSize: 12,
    background: "transparent", color: "#c3c5dd",
    border: "1px solid #2c2d46", borderRadius: 6, cursor: "pointer",
  },
  btnClose: {
    marginLeft: 6,
    padding: "2px 8px", fontSize: 14,
    background: "transparent", color: "#9092b5",
    border: "1px solid #2c2d46", borderRadius: 6, cursor: "pointer",
  },
  body: { flex: 1, overflow: "auto", padding: "12px 0" },
  empty: { padding: 30, textAlign: "center", color: "#9092b5", fontSize: 13 },
  code: {
    margin: 0, padding: 0,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    fontSize: 12.5, lineHeight: 1.55,
    color: "#D4D4D8",
    background: "transparent",
    whiteSpace: "pre",
  },
  lineRow: { display: "grid", gridTemplateColumns: "48px 1fr", gap: 12, padding: "0 18px" },
  lineNo: {
    color: "#52525B", textAlign: "right",
    userSelect: "none",
    fontVariantNumeric: "tabular-nums",
  },
  lineCode: { whiteSpace: "pre-wrap", wordBreak: "break-word" },
};
