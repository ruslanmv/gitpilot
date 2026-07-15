import React, { useEffect, useState } from "react";
import { apiUrl, getAuthHeaders, clearSessionToken } from "../../utils/api.js";

// Account settings — the GitPilot account (your email/password identity).
// This is intentionally separate from the GitHub connection, which only grants
// access to repositories. Here you manage who you are; GitHub manages what you
// can touch.
export default function AccountTab({ onLogout }) {
  const [account, setAccount] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");

  // Profile
  const [name, setName] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileMsg, setProfileMsg] = useState("");

  // Password
  const [curPw, setCurPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [savingPw, setSavingPw] = useState(false);
  const [pwMsg, setPwMsg] = useState(null); // {kind, text}

  // Delete (danger zone)
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [delPw, setDelPw] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [delErr, setDelErr] = useState("");

  const jsonHeaders = () => ({ "Content-Type": "application/json", ...getAuthHeaders() });

  const loadAccount = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const res = await fetch(apiUrl("/api/account/me"), {
        headers: getAuthHeaders(),
      });
      if (res.status === 401) {
        setAccount(null);
        return;
      }
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not load your account.");
      setAccount(data);
      setName(data.name || "");
    } catch (e) {
      setLoadError(e.message || "Could not load your account.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadAccount();
  }, []);

  const isPasswordAccount = account?.provider === "password";

  const saveProfile = async () => {
    setSavingProfile(true);
    setProfileMsg("");
    try {
      const res = await fetch(apiUrl("/api/account/profile"), {
        method: "PATCH",
        headers: jsonHeaders(),
        body: JSON.stringify({ name: name.trim() || null }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not save your profile.");
      setAccount(data);
      setProfileMsg("Saved.");
      setTimeout(() => setProfileMsg(""), 3000);
    } catch (e) {
      setProfileMsg(e.message || "Could not save your profile.");
    } finally {
      setSavingProfile(false);
    }
  };

  const changePassword = async () => {
    setPwMsg(null);
    if (newPw.length < 8) {
      setPwMsg({ kind: "err", text: "New password must be at least 8 characters." });
      return;
    }
    if (newPw !== confirmPw) {
      setPwMsg({ kind: "err", text: "New password and confirmation don't match." });
      return;
    }
    setSavingPw(true);
    try {
      const res = await fetch(apiUrl("/api/account/password/change"), {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ current_password: curPw, new_password: newPw }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Could not change your password.");
      setPwMsg({ kind: "ok", text: "Password changed successfully." });
      setCurPw(""); setNewPw(""); setConfirmPw("");
    } catch (e) {
      setPwMsg({ kind: "err", text: e.message || "Could not change your password." });
    } finally {
      setSavingPw(false);
    }
  };

  const deleteAccount = async () => {
    setDeleting(true);
    setDelErr("");
    try {
      const res = await fetch(apiUrl("/api/account/delete"), {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({ password: delPw || null }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Could not delete your account.");
      // Gone for good — clear local session and return to a signed-out state.
      clearSessionToken();
      if (typeof onLogout === "function") onLogout();
      window.location.href = "/";
    } catch (e) {
      setDelErr(e.message || "Could not delete your account.");
      setDeleting(false);
    }
  };

  if (loading) {
    return (
      <div className="settings-root">
        <h1>Account</h1>
        <p className="settings-muted">Loading your account…</p>
      </div>
    );
  }

  // Signed in with GitHub only (no GitPilot email account) — explain the split.
  if (!account) {
    return (
      <div className="settings-root">
        <h1>Account</h1>
        <p className="settings-muted">
          This is your <strong>GitPilot account</strong> — your email/password identity,
          separate from the GitHub connection that grants repository access.
        </p>
        <div className="settings-card">
          <div className="settings-title">You're signed in with GitHub</div>
          <div className="settings-hint">
            {loadError
              ? loadError
              : "You don't have a GitPilot email account yet. GitHub sign-in gives you repository access; create a GitPilot account to manage a profile, password, and account settings here."}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="settings-root">
      <h1>Account</h1>
      <p className="settings-muted">
        Your <strong>GitPilot account</strong> — your identity and sign-in. This is
        separate from the GitHub connection, which only grants repository access.
      </p>

      {/* Profile */}
      <div className="settings-card">
        <div className="settings-title">Profile</div>

        <label className="settings-label">Email</label>
        <input className="settings-input" value={account.email} disabled readOnly />
        <div className="settings-hint" style={{ marginTop: 6, marginBottom: 12 }}>
          {account.email_verified ? "✓ Verified" : "Not verified"}
          {"  ·  "}Sign-in method: {account.provider}
        </div>

        <label className="settings-label">Display name</label>
        <input
          className="settings-input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Your name"
        />

        <div className="settings-actions" style={{ marginTop: 14 }}>
          <button type="button" className="settings-save-btn" onClick={saveProfile} disabled={savingProfile}>
            {savingProfile ? "Saving…" : "Save profile"}
          </button>
          {profileMsg && <span className="settings-muted" style={{ alignSelf: "center" }}>{profileMsg}</span>}
        </div>
      </div>

      {/* Password */}
      {isPasswordAccount && (
        <div className="settings-card">
          <div className="settings-title">Change password</div>

          {pwMsg && (
            <div className={pwMsg.kind === "ok" ? "settings-success-banner" : "settings-error-banner"}>
              {pwMsg.text}
            </div>
          )}

          <label className="settings-label">Current password</label>
          <input
            className="settings-input"
            type="password"
            value={curPw}
            onChange={(e) => setCurPw(e.target.value)}
            placeholder="Current password"
            autoComplete="current-password"
          />

          <label className="settings-label">New password</label>
          <input
            className="settings-input"
            type="password"
            value={newPw}
            onChange={(e) => setNewPw(e.target.value)}
            placeholder="At least 8 characters"
            autoComplete="new-password"
          />

          <label className="settings-label">Confirm new password</label>
          <input
            className="settings-input"
            type="password"
            value={confirmPw}
            onChange={(e) => setConfirmPw(e.target.value)}
            placeholder="Re-enter new password"
            autoComplete="new-password"
          />

          <div className="settings-actions" style={{ marginTop: 14 }}>
            <button
              type="button"
              className="settings-save-btn"
              onClick={changePassword}
              disabled={savingPw || !curPw || !newPw || !confirmPw}
            >
              {savingPw ? "Updating…" : "Update password"}
            </button>
          </div>
        </div>
      )}

      {/* Danger zone */}
      <div className="settings-danger-card">
        <div className="settings-danger-title">Danger zone</div>
        <div className="settings-hint" style={{ marginBottom: 12 }}>
          Permanently delete your GitPilot account and all of its data. This cannot be undone.
        </div>

        {!confirmDelete ? (
          <button type="button" className="settings-danger-btn" onClick={() => setConfirmDelete(true)}>
            Delete account
          </button>
        ) : (
          <div className="settings-danger-confirm">
            <div className="settings-hint" style={{ marginBottom: 10 }}>
              This is permanent. {isPasswordAccount ? "Enter your password to confirm." : "Confirm to proceed."}
            </div>
            {delErr && <div className="settings-error-banner">{delErr}</div>}
            {isPasswordAccount && (
              <input
                className="settings-input"
                type="password"
                value={delPw}
                onChange={(e) => setDelPw(e.target.value)}
                placeholder="Your password"
                autoComplete="current-password"
                style={{ marginBottom: 12 }}
              />
            )}
            <div className="settings-actions">
              <button
                type="button"
                className="settings-danger-btn"
                onClick={deleteAccount}
                disabled={deleting || (isPasswordAccount && !delPw)}
              >
                {deleting ? "Deleting…" : "Permanently delete my account"}
              </button>
              <button
                type="button"
                className="settings-secondary-btn"
                onClick={() => { setConfirmDelete(false); setDelPw(""); setDelErr(""); }}
                disabled={deleting}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
