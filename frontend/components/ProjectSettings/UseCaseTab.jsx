import React, { useEffect, useMemo, useRef, useState } from "react";

export default function UseCaseTab({ owner, repo }) {
  const [useCases, setUseCases] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [useCase, setUseCase] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [draftTitle, setDraftTitle] = useState("New Use Case");
  const [message, setMessage] = useState("");
  const messagesEndRef = useRef(null);

  const canUse = useMemo(() => Boolean(owner && repo), [owner, repo]);
  const spec = useCase?.spec || {};

  function scrollToBottom() {
    requestAnimationFrame(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    });
  }

  async function loadUseCases() {
    if (!canUse) return;
    setError("");
    try {
      const res = await fetch(`/api/repos/${owner}/${repo}/use-cases`);
      if (!res.ok) throw new Error(`Failed to list use cases (${res.status})`);
      const data = await res.json();
      const list = data.use_cases || [];
      setUseCases(list);

      // auto select active or first
      const active = list.find((x) => x.is_active);
      const nextId = active?.use_case_id || list[0]?.use_case_id || "";
      if (!selectedId && nextId) setSelectedId(nextId);
    } catch (e) {
      setError(e?.message || "Failed to load use cases");
    }
  }

  async function loadUseCase(id) {
    if (!canUse || !id) return;
    setError("");
    try {
      const res = await fetch(`/api/repos/${owner}/${repo}/use-cases/${id}`);
      if (!res.ok) throw new Error(`Failed to load use case (${res.status})`);
      const data = await res.json();
      setUseCase(data.use_case || null);
      scrollToBottom();
    } catch (e) {
      setError(e?.message || "Failed to load use case");
    }
  }

  useEffect(() => {
    loadUseCases();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [owner, repo]);

  useEffect(() => {
    if (!selectedId) return;
    loadUseCase(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  async function createUseCase() {
    if (!canUse) return;
    setBusy(true);
    setError("");
    try {
      const res = await fetch(`/api/repos/${owner}/${repo}/use-cases`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: draftTitle || "New Use Case" }),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(`Create failed (${res.status}) ${txt}`);
      }
      const data = await res.json();
      const id = data?.use_case?.use_case_id;
      await loadUseCases();
      if (id) setSelectedId(id);
      setDraftTitle("New Use Case");
    } catch (e) {
      setError(e?.message || "Create failed");
    } finally {
      setBusy(false);
    }
  }

  async function sendMessage() {
    if (!canUse || !selectedId) return;
    const msg = (message || "").trim();
    if (!msg) return;

    setBusy(true);
    setError("");

    // optimistic UI: append user message immediately
    setUseCase((prev) => {
      if (!prev) return prev;
      const next = { ...prev };
      next.messages = Array.isArray(next.messages) ? [...next.messages] : [];
      next.messages.push({ role: "user", content: msg, ts: new Date().toISOString() });
      return next;
    });
    setMessage("");
    scrollToBottom();

    try {
      const res = await fetch(
        `/api/repos/${owner}/${repo}/use-cases/${selectedId}/chat`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: msg }),
        }
      );
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(`Chat failed (${res.status}) ${txt}`);
      }
      const data = await res.json();
      setUseCase(data.use_case || null);
      await loadUseCases();
      scrollToBottom();
    } catch (e) {
      setError(e?.message || "Chat failed");
    } finally {
      setBusy(false);
    }
  }

  async function finalizeUseCase() {
    if (!canUse || !selectedId) return;
    setBusy(true);
    setError("");
    try {
      const res = await fetch(
        `/api/repos/${owner}/${repo}/use-cases/${selectedId}/finalize`,
        { method: "POST" }
      );
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(`Finalize failed (${res.status}) ${txt}`);
      }
      const data = await res.json();
      setUseCase(data.use_case || null);
      await loadUseCases();
      alert(
        "Use Case finalized and marked active.\n\nA Markdown export was saved in the repo workspace .gitpilot/context/use_cases/."
      );
    } catch (e) {
      setError(e?.message || "Finalize failed");
    } finally {
      setBusy(false);
    }
  }

  const activeId = useCases.find((x) => x.is_active)?.use_case_id;

  return (
    <div style={styles.wrap}>
      <div style={styles.topRow}>
        <div style={styles.left}>
          <div style={styles.h1}>Use Case</div>
          <div style={styles.h2}>
            Guided chat to clarify requirements and produce a versioned spec.
          </div>
        </div>

        <div style={styles.right}>
          <input
            value={draftTitle}
            onChange={(e) => setDraftTitle(e.target.value)}
            placeholder="New use case title..."
            style={styles.titleInput}
            disabled={!canUse || busy}
          />
          <button
            style={styles.btn}
            onClick={createUseCase}
            disabled={!canUse || busy}
          >
            New
          </button>
          <button
            style={styles.btn}
            onClick={finalizeUseCase}
            disabled={!canUse || busy || !selectedId}
          >
            Finalize
          </button>
          <button
            style={styles.btn}
            onClick={loadUseCases}
            disabled={!canUse || busy}
          >
            Refresh
          </button>
        </div>
      </div>

      {error ? <div style={styles.error}>{error}</div> : null}

      <div style={styles.grid}>
        <div style={styles.sidebar}>
          <div style={styles.sidebarTitle}>Use Cases</div>
          <div style={styles.sidebarList}>
            {useCases.length === 0 ? (
              <div style={styles.sidebarEmpty}>
                No use cases yet. Create one with <b>New</b>.
              </div>
            ) : (
              useCases.map((uc) => (
                <button
                  key={uc.use_case_id}
                  style={{
                    ...styles.ucItem,
                    ...(selectedId === uc.use_case_id ? styles.ucItemActive : {}),
                  }}
                  onClick={() => setSelectedId(uc.use_case_id)}
                >
                  <div style={styles.ucTitleRow}>
                    <div style={styles.ucTitle}>
                      {uc.title || "(untitled)"}
                    </div>
                    {uc.use_case_id === activeId ? (
                      <span style={styles.activePill}>ACTIVE</span>
                    ) : null}
                  </div>
                  <div style={styles.ucMeta}>
                    Updated: {uc.updated_at || uc.created_at || "-"}
                  </div>
                </button>
              ))
            )}
          </div>
        </div>

        <div style={styles.chatCol}>
          <div style={styles.panelTitle}>Guided Chat</div>
          <div style={styles.chatBox}>
            {Array.isArray(useCase?.messages) && useCase.messages.length ? (
              useCase.messages.map((m, idx) => (
                <div
                  key={idx}
                  style={{
                    ...styles.msg,
                    ...(m.role === "user" ? styles.msgUser : styles.msgAsst),
                  }}
                >
                  <div style={styles.msgRole}>
                    {m.role === "user" ? "You" : "Assistant"}
                  </div>
                  <div style={styles.msgContent}>{m.content}</div>
                </div>
              ))
            ) : (
              <div style={styles.chatEmpty}>
                Select a use case and start chatting. You can paste structured
                info like:
                <pre style={styles.pre}>
{`Summary: ...
Problem: ...
Users: ...
Requirements:
- ...
Acceptance Criteria:
- ...`}
                </pre>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          <div style={styles.composer}>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="Type your answer... (Shift+Enter for newline)"
              style={styles.textarea}
              disabled={!canUse || busy || !selectedId}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  sendMessage();
                }
              }}
            />
            <button
              style={styles.sendBtn}
              disabled={!canUse || busy || !selectedId}
              onClick={sendMessage}
            >
              Send
            </button>
          </div>
        </div>

        <div style={styles.specCol}>
          <div style={styles.panelTitle}>Spec Preview</div>
          <div style={styles.specBox}>
            <Section title="Title" value={spec.title || useCase?.title || ""} />
            <Section title="Summary" value={spec.summary || ""} />
            <Section title="Problem" value={spec.problem || ""} />
            <Section title="Users / Personas" value={spec.users || ""} />
            <ListSection title="Requirements" items={spec.requirements || []} />
            <ListSection
              title="Acceptance Criteria"
              items={spec.acceptance_criteria || []}
            />
            <ListSection title="Constraints" items={spec.constraints || []} />
            <ListSection title="Open Questions" items={spec.open_questions || []} />
            <Section title="Notes" value={spec.notes || ""} />
          </div>

          <div style={styles.specFooter}>
            <div style={styles.specHint}>
              Finalize will save a Markdown spec and mark it ACTIVE for context.
            </div>
            <button
              style={styles.primaryBtn}
              disabled={!canUse || busy || !selectedId}
              onClick={finalizeUseCase}
            >
              Finalize Spec
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Section({ title, value }) {
  return (
    <div style={styles.section}>
      <div style={styles.sectionTitle}>{title}</div>
      <div style={styles.sectionBody}>
        {String(value || "").trim() ? (
          <div style={styles.sectionText}>{value}</div>
        ) : (
          <div style={styles.sectionEmpty}>(empty)</div>
        )}
      </div>
    </div>
  );
}

function ListSection({ title, items }) {
  const list = Array.isArray(items) ? items : [];
  return (
    <div style={styles.section}>
      <div style={styles.sectionTitle}>{title}</div>
      <div style={styles.sectionBody}>
        {list.length ? (
          <ul style={styles.ul}>
            {list.map((x, i) => (
              <li key={i} style={styles.li}>
                {x}
              </li>
            ))}
          </ul>
        ) : (
          <div style={styles.sectionEmpty}>(empty)</div>
        )}
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
  left: { minWidth: 280 },
  right: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" },
  h1: { fontSize: 14, fontWeight: 800, color: "#fff" },
  h2: { fontSize: 12, color: "rgba(255,255,255,0.65)", marginTop: 4 },
  titleInput: {
    width: 260,
    maxWidth: "70vw",
    padding: "8px 10px",
    borderRadius: 10,
    border: "1px solid rgba(255,255,255,0.18)",
    background: "rgba(0,0,0,0.25)",
    color: "#fff",
    fontSize: 13,
    outline: "none",
  },
  btn: {
    background: "rgba(255,255,255,0.10)",
    border: "1px solid rgba(255,255,255,0.18)",
    color: "#fff",
    borderRadius: 10,
    padding: "8px 10px",
    cursor: "pointer",
    fontSize: 13,
  },
  primaryBtn: {
    background: "rgba(255,255,255,0.12)",
    border: "1px solid rgba(255,255,255,0.22)",
    color: "#fff",
    borderRadius: 10,
    padding: "8px 12px",
    cursor: "pointer",
    fontSize: 13,
    fontWeight: 700,
  },
  error: {
    color: "#ffb3b3",
    fontSize: 12,
    padding: "8px 10px",
    border: "1px solid rgba(255,120,120,0.25)",
    borderRadius: 10,
    background: "rgba(255,80,80,0.08)",
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "300px 1.2fr 0.9fr",
    gap: 12,
    alignItems: "stretch",
  },
  sidebar: {
    border: "1px solid rgba(255,255,255,0.12)",
    borderRadius: 12,
    overflow: "hidden",
    background: "rgba(255,255,255,0.02)",
    display: "flex",
    flexDirection: "column",
    minHeight: 520,
  },
  sidebarTitle: {
    padding: 10,
    borderBottom: "1px solid rgba(255,255,255,0.10)",
    fontSize: 12,
    fontWeight: 800,
    color: "rgba(255,255,255,0.85)",
  },
  sidebarList: {
    padding: 8,
    display: "flex",
    flexDirection: "column",
    gap: 8,
    overflow: "auto",
  },
  sidebarEmpty: {
    color: "rgba(255,255,255,0.65)",
    fontSize: 12,
    padding: 8,
  },
  ucItem: {
    textAlign: "left",
    background: "rgba(0,0,0,0.25)",
    border: "1px solid rgba(255,255,255,0.12)",
    color: "#fff",
    borderRadius: 12,
    padding: 10,
    cursor: "pointer",
  },
  ucItemActive: {
    border: "1px solid rgba(255,255,255,0.25)",
    background: "rgba(255,255,255,0.06)",
  },
  ucTitleRow: { display: "flex", alignItems: "center", gap: 8 },
  ucTitle: {
    fontSize: 13,
    fontWeight: 800,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    flex: 1,
  },
  activePill: {
    fontSize: 10,
    fontWeight: 800,
    padding: "2px 8px",
    borderRadius: 999,
    border: "1px solid rgba(120,255,180,0.30)",
    background: "rgba(120,255,180,0.10)",
    color: "rgba(200,255,220,0.95)",
  },
  ucMeta: {
    marginTop: 6,
    fontSize: 11,
    color: "rgba(255,255,255,0.60)",
  },
  chatCol: {
    border: "1px solid rgba(255,255,255,0.12)",
    borderRadius: 12,
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
    background: "rgba(255,255,255,0.02)",
    minHeight: 520,
  },
  specCol: {
    border: "1px solid rgba(255,255,255,0.12)",
    borderRadius: 12,
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
    background: "rgba(255,255,255,0.02)",
    minHeight: 520,
  },
  panelTitle: {
    padding: 10,
    borderBottom: "1px solid rgba(255,255,255,0.10)",
    fontSize: 12,
    fontWeight: 800,
    color: "rgba(255,255,255,0.85)",
  },
  chatBox: {
    flex: 1,
    overflow: "auto",
    padding: 10,
    display: "flex",
    flexDirection: "column",
    gap: 10,
  },
  chatEmpty: {
    color: "rgba(255,255,255,0.65)",
    fontSize: 12,
    padding: 6,
  },
  pre: {
    marginTop: 10,
    padding: 10,
    borderRadius: 10,
    border: "1px solid rgba(255,255,255,0.12)",
    background: "rgba(0,0,0,0.25)",
    color: "rgba(255,255,255,0.8)",
    overflow: "auto",
    fontSize: 11,
  },
  msg: {
    border: "1px solid rgba(255,255,255,0.12)",
    borderRadius: 12,
    padding: 10,
    background: "rgba(0,0,0,0.25)",
  },
  msgUser: {
    border: "1px solid rgba(255,255,255,0.18)",
    background: "rgba(255,255,255,0.04)",
  },
  msgAsst: {},
  msgRole: {
    fontSize: 11,
    fontWeight: 800,
    color: "rgba(255,255,255,0.70)",
    marginBottom: 6,
  },
  msgContent: {
    whiteSpace: "pre-wrap",
    fontSize: 13,
    color: "rgba(255,255,255,0.90)",
    lineHeight: 1.35,
  },
  composer: {
    borderTop: "1px solid rgba(255,255,255,0.10)",
    padding: 10,
    display: "flex",
    gap: 10,
    alignItems: "flex-end",
  },
  textarea: {
    flex: 1,
    minHeight: 52,
    maxHeight: 120,
    resize: "vertical",
    padding: 10,
    borderRadius: 12,
    border: "1px solid rgba(255,255,255,0.18)",
    background: "rgba(0,0,0,0.25)",
    color: "#fff",
    fontSize: 13,
    outline: "none",
  },
  sendBtn: {
    background: "rgba(255,255,255,0.12)",
    border: "1px solid rgba(255,255,255,0.22)",
    color: "#fff",
    borderRadius: 12,
    padding: "10px 12px",
    cursor: "pointer",
    fontSize: 13,
    fontWeight: 800,
  },
  specBox: {
    flex: 1,
    overflow: "auto",
    padding: 10,
    display: "flex",
    flexDirection: "column",
    gap: 10,
  },
  specFooter: {
    borderTop: "1px solid rgba(255,255,255,0.10)",
    padding: 10,
    display: "flex",
    gap: 10,
    alignItems: "center",
    justifyContent: "space-between",
  },
  specHint: { fontSize: 12, color: "rgba(255,255,255,0.60)" },
  section: {
    border: "1px solid rgba(255,255,255,0.10)",
    borderRadius: 12,
    background: "rgba(0,0,0,0.22)",
    overflow: "hidden",
  },
  sectionTitle: {
    padding: "8px 10px",
    borderBottom: "1px solid rgba(255,255,255,0.08)",
    fontSize: 12,
    fontWeight: 800,
    color: "rgba(255,255,255,0.80)",
    background: "rgba(255,255,255,0.02)",
  },
  sectionBody: { padding: "8px 10px" },
  sectionText: {
    whiteSpace: "pre-wrap",
    fontSize: 12,
    color: "rgba(255,255,255,0.90)",
    lineHeight: 1.35,
  },
  sectionEmpty: { fontSize: 12, color: "rgba(255,255,255,0.45)" },
  ul: { margin: 0, paddingLeft: 18 },
  li: { color: "rgba(255,255,255,0.90)", fontSize: 12, lineHeight: 1.35 },
};
