import React, { useEffect, useMemo, useState } from "react";
import { apiUrl } from "../../utils/api.js";

export default function ConventionsTab({ owner, repo }) {
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const canUse = useMemo(() => Boolean(owner && repo), [owner, repo]);

  async function load() {
    if (!canUse) return;
    setError("");
    setBusy(true);
    try {
      const res = await fetch(apiUrl(`/api/repos/${owner}/${repo}/context`));
      if (!res.ok) throw new Error(`Failed to load conventions (${res.status})`);
      const data = await res.json();
      // backend may return { context: "..."} or { conventions: "..."} depending on implementation
      setContent(data.context || data.conventions || data.memory || data.text || "");
    } catch (e) {
      setError(e?.message || "Failed to load conventions");
    } finally {
      setBusy(false);
    }
  }

  async function initialize() {
    if (!canUse) return;
    setError("");
    setBusy(true);
    try {
      const res = await fetch(apiUrl(`/api/repos/${owner}/${repo}/context/init`), {
        method: "POST",
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(`Init failed (${res.status}) ${txt}`);
      }
      await load();
    } catch (e) {
      setError(e?.message || "Init failed");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [owner, repo]);

  return (
    <div style={styles.wrap}>
      <div style={styles.topRow}>
        <div>
          <div style={styles.h1}>Project Conventions</div>
          <div style={styles.h2}>
            This is the project memory/conventions file used by GitPilot.
          </div>
        </div>
        <div style={styles.actions}>
          <button style={styles.btn} disabled={!canUse || busy} onClick={load}>
            Refresh
          </button>
          <button
            style={styles.btn}
            disabled={!canUse || busy}
            onClick={initialize}
          >
            Initialize
          </button>
        </div>
      </div>

      {error ? <div style={styles.error}>{error}</div> : null}

      <div style={styles.box}>
        {content ? (
          <pre style={styles.pre}>{content}</pre>
        ) : (
          <div style={styles.empty}>
            No conventions found yet. Click <b>Initialize</b> to create default
            project memory if supported.
          </div>
        )}
      </div>

      <div style={styles.note}>
        Editing conventions is intentionally not included here to keep this
        feature additive/non-destructive. You can extend this later with an
        explicit &quot;Edit&quot; mode.
      </div>
    </div>
  );
}

const styles = {
  wrap: { display: "flex", flexDirection: "column", gap: 12 },
  topRow: {
    display: "flex",
    justifyContent: "space-between",
    gap: 12,
    alignItems: "flex-start",
    flexWrap: "wrap",
  },
  actions: { display: "flex", gap: 8, flexWrap: "wrap" },
  h1: { fontSize: 14, fontWeight: 800, color: "#fff" },
  h2: { fontSize: 12, color: "rgba(255,255,255,0.65)", marginTop: 4 },
  btn: {
    background: "rgba(255,255,255,0.10)",
    border: "1px solid rgba(255,255,255,0.18)",
    color: "#fff",
    borderRadius: 10,
    padding: "8px 10px",
    cursor: "pointer",
    fontSize: 13,
  },
  error: {
    color: "#ffb3b3",
    fontSize: 12,
    padding: "8px 10px",
    border: "1px solid rgba(255,120,120,0.25)",
    borderRadius: 10,
    background: "rgba(255,80,80,0.08)",
  },
  box: {
    border: "1px solid rgba(255,255,255,0.12)",
    borderRadius: 12,
    overflow: "hidden",
    background: "rgba(0,0,0,0.22)",
  },
  pre: {
    margin: 0,
    padding: 12,
    color: "rgba(255,255,255,0.85)",
    fontSize: 12,
    lineHeight: 1.35,
    whiteSpace: "pre-wrap",
    overflow: "auto",
    maxHeight: 520,
  },
  empty: {
    padding: 12,
    color: "rgba(255,255,255,0.65)",
    fontSize: 13,
  },
  note: {
    color: "rgba(255,255,255,0.55)",
    fontSize: 12,
  },
};
