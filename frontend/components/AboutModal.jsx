// frontend/components/AboutModal.jsx
import React, { useEffect, useCallback, useState } from "react";
import { apiUrl, safeFetchJSON } from "../utils/api.js";

/**
 * AboutModal — "About GitPilot" dialog shown from the user menu.
 *
 * Enterprise design goals:
 * - Prominent brand mark matching docs/logo.svg (orange ring + GP monogram)
 * - Clear identity: name, tagline, version (frontend + backend)
 * - Credits the creator (Ruslan Magana Vsevolodovna) as a link to GitHub
 * - Open-source positioning: MIT license + GitHub repo link
 * - Action row: View on GitHub, Report Issue, Documentation
 * - Accessible: role="dialog", aria-modal, aria-labelledby, Escape to close,
 *   focus trap via initial focus on close button
 * - Brand palette: #D95C3D accent, #1C1C1F card, #27272A border, #EDEDED text
 */

const FRONTEND_VERSION =
  typeof __APP_VERSION__ !== "undefined" ? __APP_VERSION__ : "0.1.5";

export default function AboutModal({ isOpen, onClose }) {
  const [backendVersion, setBackendVersion] = useState(null);

  // Fetch backend version when opened
  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await safeFetchJSON(apiUrl("/api/ping"), { timeout: 4000 });
        if (!cancelled) {
          setBackendVersion(data?.version || null);
        }
      } catch {
        if (!cancelled) setBackendVersion(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isOpen]);

  // Escape to close
  useEffect(() => {
    if (!isOpen) return;
    const handleKey = (e) => {
      if (e.key === "Escape") onClose?.();
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [isOpen, onClose]);

  // Lock body scroll while open
  useEffect(() => {
    if (!isOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [isOpen]);

  const handleBackdropClick = useCallback(
    (e) => {
      if (e.target === e.currentTarget) onClose?.();
    },
    [onClose]
  );

  if (!isOpen) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="about-modal-title"
      onClick={handleBackdropClick}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 2000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
        background: "rgba(0, 0, 0, 0.65)",
        backdropFilter: "blur(4px)",
        WebkitBackdropFilter: "blur(4px)",
        animation: "aboutBackdropIn 180ms ease-out",
      }}
    >
      <div
        style={{
          position: "relative",
          width: "100%",
          maxWidth: 520,
          background: "#1C1C1F",
          border: "1px solid #27272A",
          borderRadius: 16,
          boxShadow:
            "0 32px 64px -16px rgba(0, 0, 0, 0.8), 0 8px 24px rgba(0, 0, 0, 0.4)",
          color: "#EDEDED",
          fontFamily:
            '"Söhne", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
          letterSpacing: "-0.01em",
          overflow: "hidden",
          animation: "aboutCardIn 220ms cubic-bezier(0.16, 1, 0.3, 1)",
        }}
      >
        {/* Close button */}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close About dialog"
          autoFocus
          style={{
            position: "absolute",
            top: 14,
            right: 14,
            width: 32,
            height: 32,
            background: "transparent",
            border: "1px solid transparent",
            borderRadius: 8,
            color: "#A1A1AA",
            cursor: "pointer",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            transition: "all 120ms ease",
            zIndex: 1,
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "#27272A";
            e.currentTarget.style.color = "#EDEDED";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "transparent";
            e.currentTarget.style.color = "#A1A1AA";
          }}
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path
              d="M4 4l8 8M12 4l-8 8"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
        </button>

        {/* Hero: brand mark + name */}
        <div
          style={{
            padding: "40px 32px 24px",
            textAlign: "center",
            background:
              "radial-gradient(circle at 50% 0%, rgba(217, 92, 61, 0.12), transparent 70%)",
          }}
        >
          <BrandMark />

          <h2
            id="about-modal-title"
            style={{
              margin: "20px 0 6px",
              fontSize: 24,
              fontWeight: 700,
              color: "#EDEDED",
              letterSpacing: "-0.02em",
            }}
          >
            GitPilot
          </h2>
          <div
            style={{
              fontSize: 13,
              color: "#A1A1AA",
              marginBottom: 10,
            }}
          >
            Enterprise Workspace Copilot
          </div>

          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              padding: "4px 12px",
              background: "rgba(217, 92, 61, 0.12)",
              border: "1px solid rgba(217, 92, 61, 0.25)",
              borderRadius: 999,
              fontSize: 11,
              fontWeight: 600,
              color: "#ff7a3c",
              letterSpacing: "0.04em",
              textTransform: "uppercase",
            }}
          >
            <span
              aria-hidden="true"
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: "#ff7a3c",
                boxShadow: "0 0 8px rgba(255, 122, 60, 0.8)",
              }}
            />
            Open Source · MIT
          </div>
        </div>

        {/* Body */}
        <div style={{ padding: "8px 32px 0" }}>
          <p
            style={{
              fontSize: 14,
              lineHeight: 1.6,
              color: "#A1A1AA",
              textAlign: "center",
              margin: "0 0 24px",
            }}
          >
            An agentic AI coding companion for your repositories. Ask, plan,
            code, and ship — with multi-LLM support, security scanning, and
            VS Code integration.
          </p>

          {/* Meta table */}
          <div
            style={{
              background: "#131316",
              border: "1px solid #27272A",
              borderRadius: 10,
              padding: "4px 0",
              marginBottom: 24,
            }}
          >
            <MetaRow label="Version" value={`v${FRONTEND_VERSION}`} />
            <MetaRow
              label="Backend"
              value={backendVersion ? `v${backendVersion}` : "—"}
            />
            <MetaRow label="License" value="MIT" />
            <MetaRow
              label="Created by"
              value={
                <a
                  href="https://github.com/ruslanmv"
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    color: "#ff7a3c",
                    textDecoration: "none",
                    fontWeight: 600,
                  }}
                  onMouseEnter={(e) =>
                    (e.currentTarget.style.textDecoration = "underline")
                  }
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.textDecoration = "none")
                  }
                >
                  Ruslan Magana Vsevolodovna
                </a>
              }
              isLast
            />
          </div>
        </div>

        {/* Action row */}
        <div
          style={{
            padding: "0 32px 32px",
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: 8,
          }}
        >
          <ActionButton
            href="https://github.com/ruslanmv/gitpilot"
            icon={<GitHubIcon />}
            label="GitHub"
          />
          <ActionButton
            href="https://github.com/ruslanmv/gitpilot#readme"
            icon={<DocsIcon />}
            label="Docs"
          />
          <ActionButton
            href="https://github.com/ruslanmv/gitpilot/issues"
            icon={<BugIcon />}
            label="Report"
          />
        </div>

        {/* Footer */}
        <div
          style={{
            padding: "16px 32px",
            background: "#131316",
            borderTop: "1px solid #27272A",
            fontSize: 11,
            color: "#71717a",
            textAlign: "center",
            lineHeight: 1.5,
          }}
        >
          &copy; {new Date().getFullYear()} GitPilot · Made with care for
          developers everywhere
        </div>
      </div>

      <style>{`
        @keyframes aboutBackdropIn {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        @keyframes aboutCardIn {
          from { opacity: 0; transform: translateY(12px) scale(0.97); }
          to   { opacity: 1; transform: translateY(0) scale(1); }
        }
      `}</style>
    </div>
  );
}

// ── Brand mark (mirrors docs/logo.svg) ──────────────────────────────
function BrandMark() {
  return (
    <div
      aria-hidden="true"
      style={{
        position: "relative",
        width: 88,
        height: 88,
        margin: "0 auto",
      }}
    >
      {/* Outer subtle ring */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          borderRadius: "50%",
          border: "2px solid rgba(255, 122, 60, 0.22)",
        }}
      />
      {/* Active arc (top-right, uses conic gradient for smooth arc) */}
      <div
        style={{
          position: "absolute",
          inset: -2,
          borderRadius: "50%",
          background:
            "conic-gradient(from -90deg, #ff7a3c 0deg, #D95C3D 90deg, transparent 91deg, transparent 360deg)",
          mask: "radial-gradient(circle, transparent 40px, black 42px, black 44px, transparent 46px)",
          WebkitMask:
            "radial-gradient(circle, transparent 40px, black 42px, black 44px, transparent 46px)",
        }}
      />
      {/* Soft core glow */}
      <div
        style={{
          position: "absolute",
          inset: 14,
          borderRadius: "50%",
          background:
            "radial-gradient(circle, rgba(255, 122, 60, 0.22) 0%, rgba(255, 122, 60, 0.06) 60%, transparent 100%)",
        }}
      />
      {/* GP monogram */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 28,
          fontWeight: 700,
          color: "#EDEDED",
          letterSpacing: "-1px",
        }}
      >
        GP
      </div>
    </div>
  );
}

// ── Meta row ────────────────────────────────────────────────────────
function MetaRow({ label, value, isLast = false }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "10px 16px",
        borderBottom: isLast ? "none" : "1px solid #27272A",
        fontSize: 13,
      }}
    >
      <span style={{ color: "#71717a", fontWeight: 500 }}>{label}</span>
      <span style={{ color: "#EDEDED", fontWeight: 600, textAlign: "right" }}>
        {value}
      </span>
    </div>
  );
}

// ── Action button ───────────────────────────────────────────────────
function ActionButton({ href, icon, label }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 6,
        padding: "12px 8px",
        background: "#131316",
        border: "1px solid #27272A",
        borderRadius: 10,
        color: "#EDEDED",
        fontSize: 12,
        fontWeight: 600,
        textDecoration: "none",
        transition: "all 140ms ease",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "#D95C3D";
        e.currentTarget.style.background = "rgba(217, 92, 61, 0.08)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "#27272A";
        e.currentTarget.style.background = "#131316";
      }}
    >
      <span
        aria-hidden="true"
        style={{
          color: "#ff7a3c",
          display: "inline-flex",
        }}
      >
        {icon}
      </span>
      <span>{label}</span>
    </a>
  );
}

// ── Icons ───────────────────────────────────────────────────────────
function GitHubIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405 1.02 0 2.04.135 3 .405 2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
    </svg>
  );
}

function DocsIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
    </svg>
  );
}

function BugIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="8" y="6" width="8" height="14" rx="4" />
      <path d="M19 7l-3 2M5 7l3 2M19 13h-3M5 13h3M19 19l-3-2M5 19l3-2M12 6V2" />
    </svg>
  );
}
