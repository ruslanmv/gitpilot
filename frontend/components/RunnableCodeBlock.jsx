import React, { useState } from "react";

// Languages the Run button supports.  Anything not in this set still
// renders as a normal code block (no button) — keeps the visual contract
// honest: if there's a button, the snippet really is executable.
const RUNNABLE = new Set([
  "python", "py",
  "javascript", "js", "node",
  "bash", "sh", "shell",
]);

// Friendly badge text per backend, surfaced so the user always knows
// which sandbox actually ran their code.  Mirrors the labels in
// SettingsModal so the two views agree.
const BACKEND_LABELS = {
  subprocess: "Local",
  matrixlab: "MatrixLab",
  off: "Pass-through",
};

// Map "py" → "python" etc. so the badge always shows the canonical
// language name rather than whatever alias the LLM tagged the fence
// with.
const LANG_DISPLAY = {
  py: "python",
  js: "javascript",
  node: "javascript",
  sh: "bash",
  shell: "bash",
};

/** A single fenced code block with a per-block Run button. */
export default function RunnableCodeBlock({ language, code }) {
  const lang = (language || "").trim().toLowerCase();
  const canRun = RUNNABLE.has(lang);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const display = LANG_DISPLAY[lang] || lang || "text";

  const onRun = async () => {
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      const res = await fetch("/api/sandbox/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language: lang, code }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || `HTTP ${res.status}`);
        return;
      }
      setResult(data);
    } catch (err) {
      setError(err.message || "Run failed");
    } finally {
      setBusy(false);
    }
  };

  const copy = () => {
    if (navigator?.clipboard) navigator.clipboard.writeText(code).catch(() => {});
  };

  return (
    <div style={styles.wrap}>
      <div style={styles.head}>
        <span style={styles.lang}>{display}</span>
        <div style={styles.headRight}>
          <button type="button" style={styles.iconBtn} onClick={copy} title="Copy code">
            Copy
          </button>
          {canRun && (
            <button
              type="button"
              style={{ ...styles.runBtn, opacity: busy ? 0.6 : 1 }}
              onClick={onRun}
              disabled={busy}
              title="Execute this snippet in the configured sandbox"
            >
              {busy ? "Running…" : "▶ Run"}
            </button>
          )}
        </div>
      </div>
      <pre style={styles.code}>{code}</pre>

      {(result || error) && (
        <div style={styles.output}>
          <div style={styles.outputHead}>
            <span style={styles.outputLabel}>Output</span>
            {result && (
              <span style={styles.metaRow}>
                <span style={result.exit_code === 0 ? styles.okPill : styles.failPill}>
                  exit {result.exit_code}
                </span>
                <span style={styles.backendPill}>
                  {BACKEND_LABELS[result.backend] || result.backend}
                </span>
                {typeof result.duration_ms === "number" && (
                  <span style={styles.dim}>{result.duration_ms} ms</span>
                )}
                {result.timed_out && <span style={styles.failPill}>timed out</span>}
                {result.truncated && <span style={styles.warnPill}>truncated</span>}
              </span>
            )}
          </div>
          {error && <pre style={styles.stderr}>{error}</pre>}
          {result?.stdout && <pre style={styles.stdout}>{result.stdout}</pre>}
          {result?.stderr && <pre style={styles.stderr}>{result.stderr}</pre>}
          {result && !result.stdout && !result.stderr && (
            <div style={styles.dim}>(no output)</div>
          )}
        </div>
      )}
    </div>
  );
}

/** Split a markdown-ish string into text and fenced-code segments.
 *
 * Returned shape: ``[{type: 'text', value} | {type: 'code', language, code}]``.
 *
 * Kept deliberately small — full markdown rendering is out of scope; this
 * only needs to recognise ```lang fences so the Run button can attach to
 * code blocks the model emits. */
export function splitFences(input) {
  if (!input) return [];
  const out = [];
  const re = /```([a-zA-Z0-9_+-]*)\s*\n([\s\S]*?)```/g;
  let last = 0;
  let m;
  while ((m = re.exec(input)) !== null) {
    if (m.index > last) {
      out.push({ type: "text", value: input.slice(last, m.index) });
    }
    out.push({ type: "code", language: m[1] || "", code: m[2].replace(/\s+$/, "") });
    last = m.index + m[0].length;
  }
  if (last < input.length) {
    out.push({ type: "text", value: input.slice(last) });
  }
  return out;
}

const styles = {
  wrap: {
    margin: "8px 0",
    background: "#09090B",
    border: "1px solid #27272A",
    borderRadius: 8,
    overflow: "hidden",
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  },
  head: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "6px 12px",
    background: "#18181B",
    borderBottom: "1px solid #27272A",
    fontSize: 11,
  },
  headRight: { display: "flex", gap: 6, alignItems: "center" },
  lang: {
    color: "#A1A1AA",
    fontWeight: 600,
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    fontSize: 10,
  },
  iconBtn: {
    background: "transparent",
    color: "#A1A1AA",
    border: "1px solid #3F3F46",
    borderRadius: 4,
    padding: "2px 8px",
    fontSize: 11,
    cursor: "pointer",
  },
  runBtn: {
    background: "#10B981",
    color: "#052e1c",
    border: "0",
    borderRadius: 4,
    padding: "2px 10px",
    fontSize: 11,
    fontWeight: 600,
    cursor: "pointer",
  },
  code: {
    margin: 0,
    padding: "12px 14px",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    fontSize: 12.5,
    lineHeight: 1.55,
    color: "#E4E4E7",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    overflowX: "auto",
  },
  output: {
    background: "#0c0c10",
    borderTop: "1px solid #27272A",
    padding: "8px 14px 10px",
  },
  outputHead: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    marginBottom: 6,
  },
  outputLabel: {
    fontSize: 10,
    fontWeight: 600,
    color: "#A1A1AA",
    textTransform: "uppercase",
    letterSpacing: "0.05em",
  },
  metaRow: { display: "flex", gap: 6, alignItems: "center" },
  okPill: {
    fontSize: 10,
    fontWeight: 600,
    padding: "1px 6px",
    borderRadius: 9,
    background: "rgba(16, 185, 129, 0.12)",
    color: "#10B981",
    border: "1px solid rgba(16, 185, 129, 0.35)",
  },
  failPill: {
    fontSize: 10,
    fontWeight: 600,
    padding: "1px 6px",
    borderRadius: 9,
    background: "rgba(239, 68, 68, 0.12)",
    color: "#ef4444",
    border: "1px solid rgba(239, 68, 68, 0.35)",
  },
  warnPill: {
    fontSize: 10,
    fontWeight: 600,
    padding: "1px 6px",
    borderRadius: 9,
    background: "rgba(217, 119, 6, 0.12)",
    color: "#f59e0b",
    border: "1px solid rgba(217, 119, 6, 0.35)",
  },
  backendPill: {
    fontSize: 10,
    fontWeight: 600,
    padding: "1px 6px",
    borderRadius: 9,
    background: "rgba(79, 70, 229, 0.12)",
    color: "#a5b4fc",
    border: "1px solid rgba(79, 70, 229, 0.35)",
  },
  dim: { color: "#71717A", fontSize: 11 },
  stdout: {
    margin: "4px 0 0",
    padding: "6px 8px",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    fontSize: 12,
    color: "#D4D4D8",
    background: "#000",
    borderRadius: 4,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  },
  stderr: {
    margin: "4px 0 0",
    padding: "6px 8px",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    fontSize: 12,
    color: "#fca5a5",
    background: "#0a0000",
    borderRadius: 4,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  },
};
