import React, { useState, useRef, useEffect } from "react";

export default function UserMenu({ user, onLogout }) {
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef(null);

  // Close menu when clicking outside
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    };

    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isOpen]);

  const handleLogoutClick = async () => {
    setIsOpen(false);
    try {
      const res = await fetch("/api/auth/logout", {
        method: "POST",
      });

      if (res.ok) {
        onLogout();
      }
    } catch (e) {
      console.error("Logout failed:", e);
    }
  };

  return (
    <div className="user-menu" ref={menuRef}>
      <button
        type="button"
        className="user-menu-trigger"
        onClick={() => setIsOpen(!isOpen)}
        aria-label="User menu"
      >
        {user.avatar_url ? (
          <img
            src={user.avatar_url}
            alt={user.username}
            className="user-avatar"
          />
        ) : (
          <div className="user-avatar-placeholder">
            {user.username?.charAt(0).toUpperCase() || "U"}
          </div>
        )}
        <span className="user-menu-arrow">{isOpen ? "▲" : "▼"}</span>
      </button>

      {isOpen && (
        <div className="user-menu-dropdown">
          <div className="user-menu-header">
            <div className="user-menu-name">{user.username}</div>
            {user.email && <div className="user-menu-email">{user.email}</div>}
          </div>

          <div className="user-menu-divider" />

          <button
            type="button"
            className="user-menu-item"
            onClick={handleLogoutClick}
          >
            <span className="user-menu-item-icon">🚪</span>
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
