import React, { useState } from "react";
import { apiUrl } from "../utils/api.js";
import SandboxCanvas from "./SandboxCanvas.jsx";
import ExecutionPlanCard, { fetchExecutionPlan } from "./ExecutionPlanCard.jsx";

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

// Default file extension per language — used when the user clicks
// "Save to repo" and we need to seed a plausible filename.
const LANG_EXT = {
  python: "py", py: "py",
  javascript: "js", js: "js", node: "js",
  bash: "sh", sh: "sh", shell: "sh",
};

// Mirror executor's _looks_like_matplotlib heuristic so plt.show()
// snippets don't hang the headless sandbox.  False positives are
// harmless (Agg is a valid backend for any Python script).
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

/** A single fenced code block with a per-block Run button.
 *
 * Optional ``owner``/``repo`` props enable the "Save to repo" button by
 * giving the save call a real target.  When absent the button is
 * hidden — keeps the contract honest: no button without somewhere to
 * save. */
export default function RunnableCodeBlock({ language, code, owner, repo }) {
  const lang = (language || "").trim().toLowerCase();
  const canRun = RUNNABLE.has(lang);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState(null);
  // Approval-first: clicking ▶ Run first fetches a deterministic
  // ExecutionPlan and surfaces it inline.  The actual sandbox call
  // is gated on the user clicking "Run in Sandbox" inside the plan.
  const [pendingPlan, setPendingPlan] = useState(null);
  const [planError, setPlanError] = useState(null);
  const display = LANG_DISPLAY[lang] || lang || "text";

  const onRunClick = async () => {
    setPlanError(null);
    setResult(null);
    setError(null);
    setBusy(true);
    try {
      const shipped = applyMatplotlibShim(lang, code);
      const plan = await fetchExecutionPlan({
        code: shipped,
        language: lang,
        source: "code_block",
      });
      setPendingPlan(plan);
    } catch (err) {
      setPlanError(err.message || "Could not build execution plan");
    } finally {
      setBusy(false);
    }
  };

  const onApprovePlan = async (plan) => {
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      const res = await fetch(apiUrl("/api/sandbox/run"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(await approveAndBuildRunBody(plan)),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || `HTTP ${res.status}`);
        return;
      }
      setResult(data);
      setPendingPlan(null);
    } catch (err) {
      setError(err.message || "Run failed");
    } finally {
      setBusy(false);
    }
  };

  const onCancelPlan = () => {
    setPendingPlan(null);
    setPlanError(null);
  };

  const copy = () => {
    if (navigator?.clipboard) navigator.clipboard.writeText(code).catch(() => {});
  };

  // POST the snippet to /api/repos/{owner}/{repo}/file with a path
  // chosen by the user.  Pure client-side prompt — no new backend
  // wiring needed because the endpoint already exists.
  const saveToRepo = async (snippet, snippetLang) => {
    if (!owner || !repo) {
      setSaveMsg("No repository context — open this chat inside a repo to save.");
      return;
    }
    const ext = LANG_EXT[(snippetLang || lang).toLowerCase()] || "txt";
    const suggested = `snippets/inline.${ext}`;
    const path = window.prompt("Save snippet to path (inside repo):", suggested);
    if (!path) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      const res = await fetch(apiUrl(`/api/repos/${owner}/${repo}/file`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path,
          content: snippet,
          message: `Save snippet from chat (${path})`,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setSaveMsg(data.detail || `Save failed (HTTP ${res.status})`);
        return;
      }
      setSaveMsg(`Saved to ${path}`);
    } catch (err) {
      setSaveMsg(err.message || "Save failed");
    } finally {
      setSaving(false);
    }
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
              style={styles.iconBtn}
              onClick={() => setCanvasOpen(true)}
              title="Open the snippet in the Canvas split-view editor"
            >
              ⊞ Canvas
            </button>
          )}
          {canRun && owner && repo && (
            <button
              type="button"
              style={{ ...styles.iconBtn, opacity: saving ? 0.6 : 1 }}
              onClick={() => saveToRepo(code, lang)}
              disabled={saving}
              title="Save this snippet as a file in the current repository"
            >
              {saving ? "Saving…" : "Save"}
            </button>
          )}
          {canRun && (
            <button
              type="button"
              style={{ ...styles.runBtn, opacity: busy ? 0.6 : 1 }}
              onClick={onRunClick}
              disabled={busy || !!pendingPlan}
              title="Review an execution plan before running this snippet"
            >
              {busy && !pendingPlan ? "Preparing…" : "▶ Run"}
            </button>
          )}
        </div>
      </div>
      <pre style={styles.code}>{code}</pre>

      {pendingPlan && (
        <ExecutionPlanCard
          plan={pendingPlan}
          variant="compact"
          busy={busy}
          onApprove={onApprovePlan}
          onCancel={onCancelPlan}
        />
      )}
      {planError && (
        <div style={styles.errorBanner}>Plan error: {planError}</div>
      )}

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
          {Array.isArray(result?.artifacts) && result.artifacts.length > 0 && (
            <div style={styles.artifactsBox}>
              <div style={styles.outputLabel}>Artifacts ({result.artifacts.length})</div>
              <ul style={styles.artifactList}>
                {result.artifacts.map((a, i) => (
                  <li key={i} style={styles.artifactItem}>
                    <code>{a.name || a.id}</code>
                    {a.size && <span style={styles.dim}> · {a.size} bytes</span>}
                    {a.mime && <span style={styles.dim}> · {a.mime}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {result && !result.stdout && !result.stderr && (
            <div style={styles.dim}>(no output)</div>
          )}
        </div>
      )}

      {saveMsg && <div style={styles.saveBanner}>{saveMsg}</div>}

      {canvasOpen && (
        <SandboxCanvas
          initialLanguage={lang}
          initialCode={code}
          onClose={() => setCanvasOpen(false)}
          onSaveAsFile={owner && repo ? saveToRepo : undefined}
        />
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
  artifactsBox: {
    marginTop: 8,
    padding: "6px 8px",
    background: "#0a0a0f",
    border: "1px solid #27272A",
    borderRadius: 4,
  },
  artifactList: {
    listStyle: "none",
    padding: 0,
    margin: "4px 0 0",
  },
  artifactItem: {
    padding: "2px 0",
    fontSize: 12,
    color: "#D4D4D8",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  },
  saveBanner: {
    margin: "0",
    padding: "6px 12px",
    fontSize: 11,
    color: "#A1A1AA",
    background: "#0c0c10",
    borderTop: "1px solid #27272A",
  },
  errorBanner: {
    margin: "6px 0",
    padding: "8px 10px",
    fontSize: 12,
    color: "#fca5a5",
    background: "#3d1111",
    border: "1px solid #7f1d1d",
    borderRadius: 6,
  },
};
