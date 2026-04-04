// frontend/components/UserMenu.jsx
import React, { useEffect, useRef, useState, useCallback } from "react";

/**
 * UserMenu — account dropdown attached to the profile avatar in the
 * bottom-left of the sidebar. Follows the Claude Code / ChatGPT pattern:
 * click avatar → popover with Settings, About, Logout.
 *
 * Best practices applied:
 * - Click outside to close (mousedown listener on document)
 * - Escape key closes
 * - ARIA: role="menu" + aria-haspopup + aria-expanded on trigger
 * - Keyboard navigation (Tab / Shift+Tab cycles items, Enter activates)
 * - Position: absolute popover anchored to trigger, opens upward
 * - Brand palette: #D95C3D accent, #1C1C1F card, #27272A border
 * - Respects sidebarCollapsed: when collapsed, only avatar is shown
 * - Animation: subtle fade+translate for polish
 */

export default function UserMenu({
  userInfo,
  sidebarCollapsed = false,
  onOpenSettings,
  onOpenAbout,
  onLogout,
}) {
  const [open, setOpen] = useState(false);
  const [fixedPos, setFixedPos] = useState(null);
  const containerRef = useRef(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);

  // When the sidebar is collapsed, the parent .sidebar has overflow-x:hidden
  // which clips an absolutely-positioned popover. Escape the clip by using
  // position:fixed with coordinates measured from the trigger's bounding
  // rect. Recompute on open, window resize, and scroll.
  useEffect(() => {
    if (!open || !sidebarCollapsed) {
      setFixedPos(null);
      return;
    }
    const compute = () => {
      const el = triggerRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      setFixedPos({
        left: Math.round(rect.right + 8),
        bottom: Math.round(window.innerHeight - rect.bottom),
      });
    };
    compute();
    window.addEventListener("resize", compute);
    window.addEventListener("scroll", compute, true);
    return () => {
      window.removeEventListener("resize", compute);
      window.removeEventListener("scroll", compute, true);
    };
  }, [open, sidebarCollapsed]);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    const handleDocMouseDown = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleDocMouseDown);
    return () => document.removeEventListener("mousedown", handleDocMouseDown);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handleKey = (e) => {
      if (e.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open]);

  // Focus the first menu item when opened
  useEffect(() => {
    if (open && menuRef.current) {
      const firstItem = menuRef.current.querySelector('[role="menuitem"]');
      firstItem?.focus();
    }
  }, [open]);

  const handleItemClick = useCallback((action) => {
    setOpen(false);
    // Defer to next tick so the dropdown close animation doesn't jitter
    // against the modal open animation.
    window.setTimeout(() => action?.(), 0);
  }, []);

  if (!userInfo) return null;

  const displayName = userInfo.name || userInfo.login;
  const login = userInfo.login || "";

  return (
    <div
      ref={containerRef}
      style={{ position: "relative", width: "100%" }}
    >
      {/* Trigger: avatar + optional name */}
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Account menu for ${displayName}`}
        className="user-menu-trigger"
        style={{
          display: "flex",
          alignItems: "center",
          gap: sidebarCollapsed ? 0 : 10,
          width: "100%",
          padding: sidebarCollapsed ? "6px" : "8px 10px",
          background: open ? "#27272A" : "transparent",
          border: "1px solid",
          borderColor: open ? "#D95C3D" : "transparent",
          borderRadius: 10,
          cursor: "pointer",
          color: "#EDEDED",
          textAlign: "left",
          transition: "background 120ms ease, border-color 120ms ease",
          fontFamily: "inherit",
        }}
        onMouseEnter={(e) => {
          if (!open) e.currentTarget.style.background = "#1C1C1F";
        }}
        onMouseLeave={(e) => {
          if (!open) e.currentTarget.style.background = "transparent";
        }}
      >
        {userInfo.avatar_url ? (
          <img
            src={userInfo.avatar_url}
            alt=""
            style={{
              width: 32,
              height: 32,
              borderRadius: "50%",
              flexShrink: 0,
              border: "1px solid #27272A",
            }}
          />
        ) : (
          <div
            aria-hidden="true"
            style={{
              width: 32,
              height: 32,
              borderRadius: "50%",
              flexShrink: 0,
              background: "rgba(217, 92, 61, 0.15)",
              color: "#D95C3D",
              border: "1px solid rgba(217, 92, 61, 0.3)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 700,
              fontSize: 13,
            }}
          >
            {(displayName || "?").slice(0, 2).toUpperCase()}
          </div>
        )}

        {!sidebarCollapsed && (
          <div style={{ flex: 1, minWidth: 0, lineHeight: 1.25 }}>
            <div
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: "#EDEDED",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {displayName}
            </div>
            {login && (
              <div
                style={{
                  fontSize: 11,
                  color: "#A1A1AA",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                @{login}
              </div>
            )}
          </div>
        )}

        {!sidebarCollapsed && (
          <svg
            aria-hidden="true"
            width="14"
            height="14"
            viewBox="0 0 16 16"
            fill="none"
            style={{
              flexShrink: 0,
              color: "#A1A1AA",
              transform: open ? "rotate(180deg)" : "rotate(0deg)",
              transition: "transform 120ms ease",
            }}
          >
            <path
              d="M4 6l4 4 4-4"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        )}
      </button>

      {/* Dropdown popover */}
      {open && (
        <div
          ref={menuRef}
          role="menu"
          aria-label="Account actions"
          style={
            sidebarCollapsed && fixedPos
              ? {
                  position: "fixed",
                  left: fixedPos.left,
                  bottom: fixedPos.bottom,
                  width: 240,
                  minWidth: 220,
                  background: "#1C1C1F",
                  border: "1px solid #27272A",
                  borderRadius: 12,
                  boxShadow:
                    "0 18px 38px -12px rgba(0, 0, 0, 0.7), 0 4px 12px rgba(0, 0, 0, 0.4)",
                  padding: 6,
                  zIndex: 1000,
                  animation: "userMenuFadeIn 140ms ease-out",
                }
              : {
                  position: "absolute",
                  bottom: "calc(100% + 8px)",
                  left: 0,
                  right: 0,
                  minWidth: 220,
                  background: "#1C1C1F",
                  border: "1px solid #27272A",
                  borderRadius: 12,
                  boxShadow:
                    "0 18px 38px -12px rgba(0, 0, 0, 0.7), 0 4px 12px rgba(0, 0, 0, 0.4)",
                  padding: 6,
                  zIndex: 1000,
                  animation: "userMenuFadeIn 140ms ease-out",
                }
          }
        >
          {/* Header: show full email/username for context */}
          <div
            style={{
              padding: "8px 12px 10px",
              borderBottom: "1px solid #27272A",
              marginBottom: 6,
            }}
          >
            <div
              style={{
                fontSize: 12,
                color: "#A1A1AA",
                fontWeight: 500,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              Signed in as
            </div>
            <div
              style={{
                fontSize: 13,
                color: "#EDEDED",
                fontWeight: 600,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                marginTop: 2,
              }}
              title={displayName}
            >
              {displayName}
            </div>
          </div>

          <MenuItem
            icon={<SettingsIcon />}
            label="Settings"
            onClick={() => handleItemClick(onOpenSettings)}
          />
          <MenuItem
            icon={<InfoIcon />}
            label="About GitPilot"
            onClick={() => handleItemClick(onOpenAbout)}
          />

          <div
            role="separator"
            style={{
              height: 1,
              background: "#27272A",
              margin: "6px 4px",
            }}
          />

          <MenuItem
            icon={<LogoutIcon />}
            label="Log out"
            onClick={() => handleItemClick(onLogout)}
            danger
          />
        </div>
      )}

      {/* Scoped keyframe animation */}
      <style>{`
        @keyframes userMenuFadeIn {
          from { opacity: 0; transform: translateY(4px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}

// ── Menu item primitive ────────────────────────────────────────────
function MenuItem({ icon, label, onClick, danger = false }) {
  const [hover, setHover] = useState(false);
  const color = danger ? "#f87171" : "#EDEDED";
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        width: "100%",
        padding: "9px 12px",
        background: hover ? "#27272A" : "transparent",
        border: "none",
        borderRadius: 8,
        cursor: "pointer",
        color: color,
        fontSize: 13,
        fontWeight: 500,
        textAlign: "left",
        fontFamily: "inherit",
        transition: "background 80ms ease",
      }}
    >
      <span
        aria-hidden="true"
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 16,
          height: 16,
          color: hover && !danger ? "#D95C3D" : color,
          flexShrink: 0,
          transition: "color 80ms ease",
        }}
      >
        {icon}
      </span>
      <span>{label}</span>
    </button>
  );
}

// ── Inline icons (no extra asset loads) ────────────────────────────
function SettingsIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function InfoIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="16" x2="12" y2="12" />
      <line x1="12" y1="8" x2="12.01" y2="8" />
    </svg>
  );
}

function LogoutIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </svg>
  );
}
