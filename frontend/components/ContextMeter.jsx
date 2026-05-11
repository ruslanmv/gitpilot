// frontend/components/ContextMeter.jsx
//
// Small bottom-right control that shows the active LLM's context-window
// utilisation.  Collapsed: a single ⓘ icon (no number — keeps the UI
// quiet during normal use).  Expanded: a compact popover with the
// breakdown, topology line, and a manual refresh button.
//
// Refresh model: lazy — fetched only when the popover opens, plus the
// explicit ↻ button.  Zero idle traffic.
//
// Token-count estimate flag: when the backend reports is_estimate=true
// (Ollama / OllaBridge — no real tokenizer available) every number is
// prefixed with ≈ so the imprecision is visible.
//
// Colours: GitPilot orange #D95C3D for ≥60% (warning), red #B91C1C for
// ≥85% (saturated).  No new dependencies; inline styles + a scoped
// <style> block for animations / focus rings.

import React, { useEffect, useRef, useState } from "react";

const GITPILOT_ORANGE = "#D95C3D";
const SATURATED_RED = "#B91C1C";
const DIM = "#9aa0b4";
const SLATE = "#6b7280";

const fmt = (n) => {
  if (n == null) return "—";
  return new Intl.NumberFormat("en-US").format(n);
};

const pct = (used, total) => {
  if (!total) return 0;
  return Math.max(0, Math.min(100, (100 * used) / total));
};

const colourFor = (percent) => {
  if (percent >= 85) return SATURATED_RED;
  if (percent >= 60) return GITPILOT_ORANGE;
  return SLATE;
};

function Bar({ percent, colour }) {
  // 16-segment monochrome bar, matching the ASCII design.
  const filled = Math.round((percent / 100) * 16);
  const segs = [];
  for (let i = 0; i < 16; i++) {
    segs.push(
      <span
        key={i}
        aria-hidden="true"
        style={{
          display: "inline-block",
          width: 6,
          height: 8,
          marginRight: 1,
          background: i < filled ? colour : "rgba(255,255,255,0.08)",
          borderRadius: 1,
        }}
      />,
    );
  }
  return (
    <span style={{ display: "inline-flex", alignItems: "center", lineHeight: 1 }}>
      {segs}
    </span>
  );
}

function Row({ label, tokens, total, estimate, accent }) {
  const p = pct(tokens, total);
  const prefix = estimate ? "≈ " : "";
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "1fr auto auto",
        gap: 12,
        padding: "4px 0",
        fontSize: 12,
        color: accent ? "#e5e7eb" : DIM,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      <span>{label}</span>
      <span style={{ color: accent ? "#e5e7eb" : "#cbd1e3" }}>
        {prefix}
        {fmt(tokens)}
      </span>
      <span style={{ width: 48, textAlign: "right" }}>{p.toFixed(1)}%</span>
    </div>
  );
}

export default function ContextMeter({ sessionId = null }) {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const popoverRef = useRef(null);
  const triggerRef = useRef(null);

  const fetchUsage = async () => {
    setLoading(true);
    setError(null);
    try {
      const qs = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
      const r = await fetch(`/api/context/usage${qs}`);
      if (!r.ok) {
        // 404 means the feature flag is off — render nothing in that case.
        if (r.status === 404) {
          setError("disabled");
          setData(null);
        } else {
          setError(`http ${r.status}`);
        }
      } else {
        setData(await r.json());
      }
    } catch (e) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  };

  // Refetch every time the popover opens so the user sees the *current*
  // numbers after each plan/execute cycle — not a frozen snapshot from
  // first open.  The endpoint is cheap (single-digit-ms after the first
  // provider probe), so re-fetch-on-open is the honest default.
  useEffect(() => {
    if (open) {
      fetchUsage();
    }
  }, [open, sessionId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Invalidate the displayed snapshot when the active session changes
  // so we don't briefly show another session's numbers.
  useEffect(() => {
    setData(null);
  }, [sessionId]);

  // Click-outside + Esc to close.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (
        popoverRef.current &&
        !popoverRef.current.contains(e.target) &&
        triggerRef.current &&
        !triggerRef.current.contains(e.target)
      ) {
        setOpen(false);
      }
    };
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Feature flag off — render nothing.
  if (error === "disabled") return null;

  const percent = data ? data.percent_used : 0;
  const bar = colourFor(percent);
  const estimate = data?.is_estimate;
  const prefix = estimate ? "≈ " : "";

  return (
    <span
      className="gitpilot-ctx-meter"
      style={{ position: "relative", display: "inline-flex" }}
    >
      <style>{`
        .gitpilot-ctx-meter .ctx-trigger {
          background: transparent;
          border: 1px solid rgba(255,255,255,0.12);
          color: ${DIM};
          width: 22px;
          height: 22px;
          border-radius: 11px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          font-size: 12px;
          line-height: 1;
          cursor: pointer;
          padding: 0;
          transition: color 120ms ease, border-color 120ms ease;
        }
        .gitpilot-ctx-meter .ctx-trigger:hover,
        .gitpilot-ctx-meter .ctx-trigger:focus-visible {
          color: #e5e7eb;
          border-color: rgba(255,255,255,0.28);
          outline: none;
        }
        .gitpilot-ctx-meter .ctx-trigger[data-warn="1"] { color: ${GITPILOT_ORANGE}; border-color: ${GITPILOT_ORANGE}55; }
        .gitpilot-ctx-meter .ctx-trigger[data-sat="1"]  { color: ${SATURATED_RED}; border-color: ${SATURATED_RED}55; }
        .gitpilot-ctx-meter .ctx-popover {
          position: absolute;
          right: 0;
          bottom: calc(100% + 8px);
          width: 360px;
          background: #1a1c25;
          border: 1px solid rgba(255,255,255,0.10);
          border-radius: 8px;
          box-shadow: 0 8px 24px rgba(0,0,0,0.45);
          padding: 14px 16px;
          z-index: 50;
          font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
        }
        .gitpilot-ctx-meter .ctx-popover h4 {
          margin: 0 0 10px 0;
          font-size: 12px;
          font-weight: 600;
          letter-spacing: 0.04em;
          text-transform: uppercase;
          color: ${DIM};
        }
        .gitpilot-ctx-meter .ctx-meta {
          display: grid;
          grid-template-columns: 84px 1fr;
          gap: 2px 12px;
          font-size: 12px;
          color: #cbd1e3;
          margin-bottom: 12px;
          font-variant-numeric: tabular-nums;
        }
        .gitpilot-ctx-meter .ctx-meta .k { color: ${DIM}; }
        .gitpilot-ctx-meter .ctx-divider {
          height: 1px;
          background: rgba(255,255,255,0.08);
          margin: 6px 0;
        }
        .gitpilot-ctx-meter .ctx-footer {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-top: 10px;
          font-size: 11px;
          color: ${DIM};
        }
        .gitpilot-ctx-meter .ctx-refresh {
          background: transparent;
          border: 1px solid rgba(255,255,255,0.14);
          color: #cbd1e3;
          font-size: 11px;
          padding: 2px 8px;
          border-radius: 4px;
          cursor: pointer;
        }
        .gitpilot-ctx-meter .ctx-refresh:hover { color: #fff; border-color: rgba(255,255,255,0.3); }
        .gitpilot-ctx-meter .ctx-refresh:disabled { opacity: 0.5; cursor: default; }
        .gitpilot-ctx-meter .ctx-warn {
          margin-top: 10px;
          padding: 8px 10px;
          border: 1px solid ${GITPILOT_ORANGE}55;
          background: ${GITPILOT_ORANGE}14;
          color: ${GITPILOT_ORANGE};
          border-radius: 4px;
          font-size: 11px;
          line-height: 1.5;
        }
        .gitpilot-ctx-meter .ctx-warn[data-sat="1"] {
          border-color: ${SATURATED_RED}66;
          background: ${SATURATED_RED}14;
          color: ${SATURATED_RED};
        }
        .gitpilot-ctx-meter .ctx-warn ul { margin: 4px 0 0 18px; padding: 0; }
      `}</style>

      <button
        ref={triggerRef}
        type="button"
        className="ctx-trigger"
        aria-label="Context window usage"
        aria-haspopup="dialog"
        aria-expanded={open}
        data-warn={data && percent >= 60 && percent < 85 ? "1" : "0"}
        data-sat={data && percent >= 85 ? "1" : "0"}
        onClick={() => setOpen((v) => !v)}
        title="Context window usage"
      >
        {"ⓘ"}
      </button>

      {open && (
        <div
          ref={popoverRef}
          className="ctx-popover"
          role="dialog"
          aria-label="Context window usage details"
        >
          <h4>Context window</h4>

          {loading && !data && (
            <div style={{ color: DIM, fontSize: 12 }}>Loading…</div>
          )}
          {error && error !== "disabled" && (
            <div style={{ color: "#ffb3b7", fontSize: 12 }}>
              Couldn't load: {error}
            </div>
          )}

          {data && (
            <>
              <div className="ctx-meta">
                <span className="k">Provider</span>
                <span>{data.provider}</span>
                <span className="k">Model</span>
                <span>{data.model || "—"}</span>
                <span className="k">Topology</span>
                <span>{data.topology}</span>
              </div>

              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  fontSize: 12,
                  color: "#cbd1e3",
                  fontVariantNumeric: "tabular-nums",
                  marginBottom: 8,
                }}
              >
                <Bar percent={percent} colour={bar} />
                <span>
                  {prefix}
                  {fmt(data.used)} / {fmt(data.context_window)}{" "}
                  <span style={{ color: bar }}>({percent.toFixed(1)}%)</span>
                </span>
              </div>

              <Row
                label="Conversation messages"
                tokens={data.breakdown?.messages || 0}
                total={data.context_window}
                estimate={estimate}
              />
              <Row
                label="Planner system prompt"
                tokens={data.breakdown?.system_prompt || 0}
                total={data.context_window}
                estimate={estimate}
              />
              <Row
                label="Repo context summary"
                tokens={data.breakdown?.repo_context || 0}
                total={data.context_window}
                estimate={estimate}
              />
              <Row
                label={`Tool schemas (${data.tool_count || 0})`}
                tokens={data.breakdown?.tool_schemas || 0}
                total={data.context_window}
                estimate={estimate}
              />
              <Row
                label="Reserved for response"
                tokens={data.reserved_response}
                total={data.context_window}
                estimate={false}
              />

              <div className="ctx-divider" />

              <Row
                label="Free space"
                tokens={data.free}
                total={data.context_window}
                estimate={estimate}
                accent
              />

              {percent >= 85 && (
                <div className="ctx-warn" data-sat={percent >= 95 ? "1" : "0"}>
                  Context near saturation. Consider:
                  <ul>
                    <li>Resetting the conversation</li>
                    <li>Switching to a larger-context model</li>
                    <li>Reducing repository scope</li>
                  </ul>
                </div>
              )}

              <div className="ctx-footer">
                <span>{estimate ? "Token counts are estimated" : "Token counts via tiktoken"}</span>
                <button
                  type="button"
                  className="ctx-refresh"
                  onClick={fetchUsage}
                  disabled={loading}
                  aria-label="Refresh context usage"
                >
                  {loading ? "…" : "↻ refresh"}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </span>
  );
}
