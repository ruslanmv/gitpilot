// frontend/components/ThinkingIndicator.jsx
//
// Compact, enterprise-grade thinking state.  Sits inline in the chat
// timeline as a small assistant-style bubble:
//
//     ● Reading repository...    · · ·
//
// Design goals (from the bug report):
//   * Calm, precise, technical — no large card, no big glow, no
//     all-caps "THINKING" label.
//   * Sits inline next to other chat messages; ~36 px tall, auto width.
//   * Tiny pulsing brand-orange dot as the only accent (no rings,
//     no progress sweep, no nested animated panels).
//   * Muted text, sentence case, task-specific labels that rotate
//     ("Reading repository", "Building plan", "Checking context",
//     "Preparing response").
//   * Three tiny fading dots on the right as a generic "still working"
//     signal.
//
// Implementation constraints (this codebase, not the proposal's):
//   * No Tailwind — uses plain inline-style objects.
//   * No framer-motion — uses CSS @keyframes in one scoped <style> tag.
//   * No icon library — there are no glyphs in the final design.
//   * Brand-correct colour — GitPilot orange ``#D95C3D`` (matches the
//     Action Plan header and README badges), not Claude's ``#D97757``.
//
// API: accepts ``labels: string[]`` (defaults to the standard set)
// and an optional ``label`` to force a single non-rotating message.

import React, { useEffect, useState } from "react";

const BRAND_ORANGE = "#D95C3D";

const DEFAULT_LABELS = [
  "Reading repository",
  "Building plan",
  "Checking context",
  "Preparing response",
];

const ROTATION_MS = 1800;

// Scoped keyframes.  One <style> tag per mount; tiny enough that it
// would not be worth lifting to a global stylesheet.
const KEYFRAMES = `
@keyframes gp-thinking-mount {
  from { opacity: 0; transform: translateY(2px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes gp-thinking-label {
  from { opacity: 0; transform: translateY(1px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes gp-thinking-dot-pulse {
  0%, 100% { opacity: 0.50; transform: scale(1); }
  50%      { opacity: 1.00; transform: scale(1.18); }
}
@keyframes gp-thinking-trail {
  0%, 100% { opacity: 0.25; }
  50%      { opacity: 0.90; }
}
`;

export default function ThinkingIndicator({ label, labels = DEFAULT_LABELS }) {
  const [step, setStep] = useState(0);

  // Honour an explicit ``label`` prop (callers that already know what
  // the agent is doing — e.g. "Planning changes…" during the plan
  // round-trip) — otherwise rotate through the generic set.
  const useRotation = !label && Array.isArray(labels) && labels.length > 1;

  useEffect(() => {
    if (!useRotation) return undefined;
    const id = setInterval(
      () => setStep((prev) => (prev + 1) % labels.length),
      ROTATION_MS,
    );
    return () => clearInterval(id);
  }, [labels, useRotation]);

  const currentLabel = label || labels[step] || labels[0] || "Thinking";

  // Width budget: long-form labels like "Preparing response" wrap to
  // ~140 px at 13 px font.  Pin a min-width so the bubble does not
  // jitter as labels rotate.
  const styles = {
    bubble: {
      display: "inline-flex",
      alignItems: "center",
      gap: "8px",
      height: "32px",
      padding: "0 12px",
      borderRadius: "10px",
      border: "1px solid rgba(255, 255, 255, 0.08)",
      background: "rgba(255, 255, 255, 0.035)",
      color: "rgba(255, 255, 255, 0.72)",
      fontSize: "13px",
      fontWeight: 500,
      letterSpacing: "normal",
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
      animation: "gp-thinking-mount 220ms ease-out both",
    },
    brandDot: {
      flex: "0 0 auto",
      width: "6px",
      height: "6px",
      borderRadius: "999px",
      background: BRAND_ORANGE,
      animation: "gp-thinking-dot-pulse 1.35s ease-in-out infinite",
    },
    label: {
      minWidth: "120px",                 // stops bubble width jitter on rotation
      animation: "gp-thinking-label 180ms ease-out both",
    },
    trailRow: {
      display: "inline-flex",
      alignItems: "center",
      gap: "3px",
      paddingLeft: "2px",
    },
    trailDot: {
      width: "4px",
      height: "4px",
      borderRadius: "999px",
      background: "currentColor",
      animation: "gp-thinking-trail 1.2s ease-in-out infinite",
    },
  };

  return (
    <div
      className="gitpilot-thinking-indicator"
      role="status"
      aria-live="polite"
      aria-label={`${currentLabel} in progress`}
      style={styles.bubble}
    >
      <style>{KEYFRAMES}</style>
      <span style={styles.brandDot} aria-hidden="true" />
      {/* keyed on the label so the fade-in plays each rotation */}
      <span key={currentLabel} style={styles.label}>
        {currentLabel}
      </span>
      <span style={styles.trailRow} aria-hidden="true">
        <span style={{ ...styles.trailDot, animationDelay: "0s" }} />
        <span style={{ ...styles.trailDot, animationDelay: "0.18s" }} />
        <span style={{ ...styles.trailDot, animationDelay: "0.36s" }} />
      </span>
    </div>
  );
}
