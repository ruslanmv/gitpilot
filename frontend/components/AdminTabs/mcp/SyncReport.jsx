// frontend/components/AdminTabs/mcp/SyncReport.jsx
// Renders the SyncReport returned by POST /api/mcp/sync as a compact
// dismissible banner. Best practices: structured + colour-coded counts,
// truncated id lists with a "View" toggle, single primary "Dismiss"
// action, ARIA "status" so a screen reader announces the result once.

import React, { useState } from "react";

export default function SyncReport({ report, onDismiss }) {
  const [open, setOpen] = useState(false);
  if (!report) return null;

  const { added = [], kept = [], orphaned = [], forge_unreachable, error } = report;

  if (forge_unreachable) {
    return (
      <Banner
        kind="bad"
        title="Sync failed: forge unreachable"
        body={error || "Could not reach MCP Context Forge."}
        onDismiss={onDismiss}
      />
    );
  }

  const noChanges = added.length === 0 && orphaned.length === 0;

  return (
    <Banner
      kind={noChanges ? "info" : added.length ? "ok" : "warn"}
      title={
        noChanges
          ? "Sync complete — no changes"
          : `Sync complete · +${added.length} added · ${kept.length} refreshed · ${orphaned.length} orphan`
      }
      onDismiss={onDismiss}
      footer={
        <button
          onClick={() => setOpen((v) => !v)}
          style={{
            background: "transparent",
            border: "none",
            color: "#93c5fd",
            cursor: "pointer",
            fontSize: 12,
            padding: 0,
          }}
        >
          {open ? "Hide details ▴" : "View details ▾"}
        </button>
      }
    >
      {open && (
        <div
          style={{ fontSize: 12, color: "#cdd0d8", marginTop: 6, lineHeight: 1.5 }}
        >
          {added.length > 0 && (
            <Detail label="Added" items={added} colour="#86efac" />
          )}
          {kept.length > 0 && (
            <Detail label="Refreshed" items={kept} colour="#93c5fd" />
          )}
          {orphaned.length > 0 && (
            <Detail
              label="Orphaned"
              items={orphaned}
              colour="#fcd34d"
              hint="Still works locally — Forge no longer advertises them. Use 'Forget' to drop, or re-attach them in Forge."
            />
          )}
          {report.correlation_id && (
            <div style={{ marginTop: 6, color: "#7a7d8a" }}>
              correlation_id: <code>{report.correlation_id}</code>
            </div>
          )}
        </div>
      )}
    </Banner>
  );
}

function Banner({ kind, title, body, footer, onDismiss, children }) {
  const palette = {
    ok: { bg: "#0f3a26", border: "#1f5a3e", text: "#86efac" },
    info: { bg: "#1e3a5f", border: "#3B82F6", text: "#93c5fd" },
    warn: { bg: "#3a2e0f", border: "#5a4a1f", text: "#fcd34d" },
    bad: { bg: "#3a0f0f", border: "#5a1f1f", text: "#fca5a5" },
  }[kind] || { bg: "#1a1b26", border: "#2a2b36", text: "#cdd0d8" };

  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        background: palette.bg,
        border: `1px solid ${palette.border}`,
        borderRadius: 6,
        padding: "10px 14px",
        marginBottom: 12,
        color: palette.text,
        fontSize: 13,
        display: "flex",
        flexDirection: "column",
        gap: 4,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 8,
        }}
      >
        <strong>{title}</strong>
        <button
          onClick={onDismiss}
          aria-label="Dismiss"
          style={{
            background: "transparent",
            border: "none",
            color: palette.text,
            cursor: "pointer",
            fontSize: 16,
            lineHeight: 1,
            padding: 0,
          }}
        >
          ×
        </button>
      </div>
      {body && <div>{body}</div>}
      {children}
      {footer && <div>{footer}</div>}
    </div>
  );
}

function Detail({ label, items, colour, hint }) {
  return (
    <div style={{ marginTop: 4 }}>
      <span style={{ color: colour, fontWeight: 600 }}>{label}:</span>{" "}
      <span>
        {items.slice(0, 6).join(", ")}
        {items.length > 6 ? ` … (+${items.length - 6} more)` : ""}
      </span>
      {hint && (
        <div style={{ marginTop: 2, fontSize: 11, color: "#7a7d8a" }}>{hint}</div>
      )}
    </div>
  );
}
