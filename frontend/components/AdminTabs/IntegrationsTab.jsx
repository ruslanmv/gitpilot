// frontend/components/AdminTabs/IntegrationsTab.jsx
import React, { useEffect, useState } from "react";
import { disconnectProvider, fetchAuthProviders, fetchSession } from "../../utils/api.js";

/**
 * Integrations tab — GitHub and GitLab connections of the current session.
 *
 * - Status comes from GET /api/auth/session (identity only; the access tokens
 *   stay encrypted on the server) and GET /api/auth/providers (what this
 *   server has configured).
 * - "Connect" runs that provider's OAuth flow from /auth and returns here;
 *   both providers can be connected at the same time.
 * - "Disconnect" revokes the token at the provider and removes it from the
 *   session. Signing out is separate (account menu).
 */

const CARD = {
  background: "#1a1b26",
  borderRadius: "8px",
  padding: "20px",
  border: "1px solid #2a2b36",
  marginBottom: "16px",
};

const PROVIDERS = [
  { id: "github", name: "GitHub", blurb: "Repositories, branches, pull requests, issues and code search." },
  { id: "gitlab", name: "GitLab", blurb: "Projects in groups and subgroups, merge requests, issues and branches." },
];

function Badge({ state }) {
  const on = state === "connected";
  return (
    <span
      style={{
        padding: "2px 10px",
        borderRadius: "10px",
        fontSize: "11px",
        fontWeight: 600,
        background: on ? "#064e3b" : "#374151",
        color: on ? "#a7f3d0" : "#9ca3af",
        border: `1px solid ${on ? "#065f46" : "#4b5563"}`,
      }}
    >
      {state === "checking" ? "CHECKING..." : on ? "CONNECTED" : "NOT CONNECTED"}
    </span>
  );
}

export default function IntegrationsTab({ showToast, onSessionChange }) {
  const [session, setSession] = useState(null);
  const [available, setAvailable] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState(null);

  const refresh = async () => {
    const [info, providers] = await Promise.all([
      fetchSession().catch(() => null),
      fetchAuthProviders().catch(() => null),
    ]);
    setSession(info);
    setAvailable(providers);
    return info;
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await refresh();
      } catch (err) {
        if (!cancelled) setError(err?.message || "Failed to check connections");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const connect = (provider) => {
    setBusy(provider);
    setError(null);
    window.location.href = `/auth?connect=${provider}&view=${provider}`;
  };

  const disconnect = async (provider, name) => {
    if (!window.confirm(`Disconnect ${name}? GitPilot will no longer be able to use your ${name} repositories.`)) return;
    setBusy(provider);
    setError(null);
    try {
      await disconnectProvider(provider);
      const info = await refresh();
      onSessionChange?.(info);
      showToast?.("Disconnected", `${name} access was revoked.`);
    } catch (err) {
      setError(err?.message || `Could not disconnect ${name}.`);
    } finally {
      setBusy("");
    }
  };

  const connections = (session && session.providers) || {};

  return (
    <div>
      <h3 style={{ marginBottom: "16px" }}>Integrations</h3>
      <p style={{ fontSize: "12px", opacity: 0.7, marginBottom: "16px" }}>
        Connect GitHub and GitLab to work on their repositories. Access tokens are kept
        encrypted on the GitPilot server, never in your browser.
      </p>

      {error && (
        <div
          role="alert"
          style={{
            padding: "8px 12px",
            background: "#7f1d1d",
            color: "#fecaca",
            border: "1px solid #991b1b",
            borderRadius: "4px",
            fontSize: "11px",
            marginBottom: "12px",
          }}
        >
          {error}
        </div>
      )}

      {PROVIDERS.map(({ id, name, blurb }) => {
        const conn = connections[id];
        const user = conn && conn.user;
        const configured = !available || (id === "github"
          ? !!(available.github && (available.github.web || available.github.device))
          : !!(available.gitlab && available.gitlab.web));
        const host = id === "gitlab" && available && available.gitlab && available.gitlab.host;
        return (
          <section key={id} style={CARD} aria-labelledby={`integration-${id}`}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "12px" }}>
              <div>
                <h4 id={`integration-${id}`} style={{ marginBottom: "4px" }}>
                  {name}{host && host !== "gitlab.com" ? ` (${host})` : ""}
                </h4>
                <p style={{ fontSize: "12px", opacity: 0.7 }}>{blurb}</p>
              </div>
              <Badge state={loading ? "checking" : conn ? "connected" : "disconnected"} />
            </div>

            {user && (
              <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "12px", background: "#0d0e15", borderRadius: "6px", marginBottom: "12px" }}>
                {user.avatar_url && (
                  <img src={user.avatar_url} alt="" style={{ width: "36px", height: "36px", borderRadius: "50%" }} />
                )}
                <div>
                  <div style={{ fontSize: "13px", fontWeight: 600 }}>{user.name || user.login}</div>
                  <div style={{ fontSize: "11px", opacity: 0.6 }}>@{user.login}</div>
                  {conn.expires_at && (
                    <div style={{ fontSize: "10px", opacity: 0.5 }}>
                      Token renews automatically{conn.refreshable ? "" : " — reconnect when it expires"}.
                    </div>
                  )}
                </div>
              </div>
            )}

            <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
              {conn ? (
                <button
                  type="button"
                  onClick={() => disconnect(id, name)}
                  disabled={busy === id}
                  style={{ padding: "8px 16px", background: "transparent", color: "#f87171", border: "1px solid #991b1b", borderRadius: "4px", cursor: "pointer", fontSize: "12px", fontWeight: 600 }}
                >
                  {busy === id ? "Disconnecting..." : "Disconnect"}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => connect(id)}
                  disabled={busy === id || loading || !configured}
                  title={configured ? undefined : `${name} sign-in is not configured on this server.`}
                  style={{
                    padding: "8px 16px",
                    background: busy === id || !configured ? "#555" : "#3B82F6",
                    color: "#fff",
                    border: "none",
                    borderRadius: "4px",
                    cursor: busy === id || loading || !configured ? "not-allowed" : "pointer",
                    fontSize: "12px",
                    fontWeight: 600,
                  }}
                >
                  {busy === id ? "Connecting..." : `Connect ${name}`}
                </button>
              )}
              {!configured && !conn && (
                <span style={{ fontSize: "11px", opacity: 0.6 }}>
                  An administrator must configure {name} OAuth (see docs/GITLAB_INTEGRATION.md and docs/auth.md).
                </span>
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}
