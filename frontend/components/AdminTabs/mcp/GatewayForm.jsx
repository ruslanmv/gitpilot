// frontend/components/AdminTabs/mcp/GatewayForm.jsx
//
// "Which Context Forge, and how do we sign in to it."
//
// The design rules, in the order they matter:
//
//   1. Test before you commit. The primary action is "Test connection", not
//      "Save" — a credential is proved against the real gateway here, rather
//      than discovered to be wrong in the middle of a coding run. Save is
//      enabled either way, because sometimes you are configuring a gateway
//      that is not up yet.
//   2. A saved secret is never displayed. The server returns hasPassword /
//      hasApiToken booleans and nothing else, so these fields render empty with
//      a "saved" marker. Leaving them blank keeps what is stored; removing a
//      credential is an explicit button, never a side effect of an empty box.
//   3. Say where a value came from. A URL set by the deployment's environment
//      is labelled as such, so nobody wonders why their edit "did not stick".
//
// Layout is a single column that reflows on narrow screens, so the same
// component serves desktop and mobile without a second implementation.

import React, { useEffect, useState } from "react";
import { apiUrl, safeFetchJSON } from "../../../utils/api.js";

const AUTH_MODES = [
  { id: "auto", label: "Automatic", hint: "Detect what the gateway accepts. Recommended." },
  { id: "none", label: "No credential", hint: "For a gateway running with authentication disabled." },
  { id: "token", label: "API token", hint: "A Forge API token. Revocable; best for shared gateways." },
  { id: "login", label: "Email + password", hint: "Sign in as a Forge admin and use the token it returns." },
];

const box = {
  background: "#1a1b26",
  border: "1px solid #2a2b36",
  borderRadius: "8px",
  padding: "20px",
};

const label = { display: "block", fontSize: "12px", color: "#a0a0b0", marginBottom: "6px" };

const input = {
  width: "100%",
  boxSizing: "border-box",
  padding: "10px 12px",
  background: "#12131c",
  border: "1px solid #2a2b36",
  borderRadius: "6px",
  color: "#e0e0e7",
  fontSize: "13px",
  outline: "none",
};

const button = (variant) => ({
  padding: "10px 16px",
  borderRadius: "6px",
  border: variant === "primary" ? "none" : "1px solid #2a2b36",
  background: variant === "primary" ? "#3B82F6" : "transparent",
  color: variant === "primary" ? "#fff" : "#a0a0b0",
  cursor: "pointer",
  fontSize: "13px",
  fontWeight: variant === "primary" ? 600 : 400,
  minHeight: "40px",
});

export default function GatewayForm({ showToast, onSaved }) {
  const [config, setConfig] = useState(null);
  const [draft, setDraft] = useState({ url: "", authMode: "auto", email: "", password: "", apiToken: "", label: "" });
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    safeFetchJSON(apiUrl("/api/mcp/gateway"), { timeout: 5000 })
      .then((data) => {
        if (!live || !data) return;
        setConfig(data);
        setDraft((d) => ({
          ...d,
          url: data.savedUrl || data.url || "",
          authMode: data.authMode || "auto",
          email: data.email || "",
          label: data.label || "",
        }));
      })
      .catch((err) => live && setError(err?.message || "Could not load the gateway settings"));
    return () => {
      live = false;
    };
  }, []);

  // Only send what the user actually filled in: a blank secret means "keep the
  // one you have", so it must not be transmitted as an empty string.
  const payload = () => {
    const body = {
      url: draft.url,
      authMode: draft.authMode,
      email: draft.email,
      label: draft.label,
    };
    if (draft.password) body.password = draft.password;
    if (draft.apiToken) body.apiToken = draft.apiToken;
    return body;
  };

  const send = async (path, method, body) => {
    const res = await fetch(apiUrl(path), {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data?.detail || `HTTP ${res.status}`);
    return data;
  };

  const onTest = async () => {
    setBusy("test");
    setError("");
    setResult(null);
    try {
      setResult(await send("/api/mcp/gateway/test", "POST", payload()));
    } catch (err) {
      setError(err?.message || "The test could not run");
    } finally {
      setBusy("");
    }
  };

  const onSave = async () => {
    setBusy("save");
    setError("");
    try {
      const saved = await send("/api/mcp/gateway", "PUT", payload());
      setConfig(saved);
      // Clear the secret boxes: what is stored is now authoritative, and we
      // never want a live credential sitting in component state.
      setDraft((d) => ({ ...d, password: "", apiToken: "" }));
      showToast?.("Gateway saved", saved.url);
      onSaved?.(saved);
    } catch (err) {
      setError(err?.message || "Could not save the gateway settings");
    } finally {
      setBusy("");
    }
  };

  const onClear = async (which) => {
    const body = which === "password" ? { clearPassword: true } : { clearApiToken: true };
    setBusy("clear");
    try {
      const saved = await send("/api/mcp/gateway", "PUT", body);
      setConfig(saved);
      showToast?.("Credential removed", which === "password" ? "Password" : "API token");
    } catch (err) {
      setError(err?.message || "Could not remove the credential");
    } finally {
      setBusy("");
    }
  };

  const mode = AUTH_MODES.find((m) => m.id === draft.authMode) || AUTH_MODES[0];
  const showToken = draft.authMode === "auto" || draft.authMode === "token";
  const showLogin = draft.authMode === "auto" || draft.authMode === "login";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      <div style={box}>
        <h4 style={{ margin: "0 0 4px 0", color: "#e0e0e7", fontSize: "14px" }}>
          Context Forge connection
        </h4>
        <p style={{ margin: "0 0 20px 0", color: "#a0a0b0", fontSize: "12px", lineHeight: 1.5 }}>
          Point GitPilot at any MCP Context Forge — your own, a shared one, or the gateway
          another app on this machine already started. Its servers become tools your coding
          agents can call.
        </p>

        <div style={{ marginBottom: "16px" }}>
          <label style={label} htmlFor="mcp-gateway-url">Gateway URL</label>
          <input
            id="mcp-gateway-url"
            style={input}
            value={draft.url}
            onChange={(e) => setDraft({ ...draft, url: e.target.value })}
            placeholder={config?.url || "http://localhost:4444"}
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
          />
          {config?.urlSource === "environment" && !draft.url && (
            <small style={{ color: "#a0a0b0", fontSize: "11px" }}>
              Currently <code>{config.url}</code>, set by this deployment's environment.
              Type a URL here to override it.
            </small>
          )}
        </div>

        <div style={{ marginBottom: "16px" }}>
          <label style={label} htmlFor="mcp-gateway-label">Name (optional)</label>
          <input
            id="mcp-gateway-label"
            style={input}
            value={draft.label}
            onChange={(e) => setDraft({ ...draft, label: e.target.value })}
            placeholder="Team gateway"
          />
        </div>

        <div style={{ marginBottom: "16px" }}>
          <label style={label} htmlFor="mcp-gateway-auth">Sign-in method</label>
          <select
            id="mcp-gateway-auth"
            style={input}
            value={draft.authMode}
            onChange={(e) => setDraft({ ...draft, authMode: e.target.value })}
          >
            {AUTH_MODES.map((m) => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
          <small style={{ color: "#a0a0b0", fontSize: "11px" }}>{mode.hint}</small>
        </div>

        {showLogin && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "12px", marginBottom: "16px" }}>
            <div>
              <label style={label} htmlFor="mcp-gateway-email">Admin email</label>
              <input
                id="mcp-gateway-email"
                style={input}
                value={draft.email}
                onChange={(e) => setDraft({ ...draft, email: e.target.value })}
                placeholder="admin@example.com"
                inputMode="email"
                autoComplete="username"
                spellCheck={false}
              />
            </div>
            <div>
              <label style={label} htmlFor="mcp-gateway-password">Admin password</label>
              <input
                id="mcp-gateway-password"
                type="password"
                style={input}
                value={draft.password}
                onChange={(e) => setDraft({ ...draft, password: e.target.value })}
                placeholder={config?.hasPassword ? "•••••••• saved" : "Not set"}
                autoComplete="current-password"
              />
              <CredentialNote
                saved={config?.hasPassword}
                fromEnv={config?.passwordFromEnv}
                onClear={() => onClear("password")}
                busy={busy === "clear"}
              />
            </div>
          </div>
        )}

        {showToken && (
          <div style={{ marginBottom: "16px" }}>
            <label style={label} htmlFor="mcp-gateway-token">API token</label>
            <input
              id="mcp-gateway-token"
              type="password"
              style={input}
              value={draft.apiToken}
              onChange={(e) => setDraft({ ...draft, apiToken: e.target.value })}
              placeholder={config?.hasApiToken ? "•••••••• saved" : "Not set"}
              autoComplete="off"
              spellCheck={false}
            />
            <CredentialNote
              saved={config?.hasApiToken}
              fromEnv={config?.apiTokenFromEnv}
              onClear={() => onClear("apiToken")}
              busy={busy === "clear"}
            />
          </div>
        )}

        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
          <button style={button("primary")} onClick={onTest} disabled={!!busy}>
            {busy === "test" ? "Testing…" : "Test connection"}
          </button>
          <button style={button()} onClick={onSave} disabled={!!busy}>
            {busy === "save" ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      {error && (
        <div role="alert" style={{ ...box, background: "#5c1a1a", borderColor: "#8a2a2a", color: "#fecaca", fontSize: "13px" }}>
          {error}
        </div>
      )}

      {result && <TestResult result={result} />}
    </div>
  );
}

function CredentialNote({ saved, fromEnv, onClear, busy }) {
  if (!saved) {
    return <small style={{ color: "#a0a0b0", fontSize: "11px" }}>Nothing saved yet.</small>;
  }
  if (fromEnv) {
    return (
      <small style={{ color: "#a0a0b0", fontSize: "11px" }}>
        Provided by this deployment's environment. Type here to override it.
      </small>
    );
  }
  return (
    <small style={{ color: "#a0a0b0", fontSize: "11px" }}>
      Saved. Leave blank to keep it, or{" "}
      <button
        onClick={onClear}
        disabled={busy}
        style={{ background: "none", border: "none", color: "#93c5fd", cursor: "pointer", padding: 0, font: "inherit" }}
      >
        remove it
      </button>
      .
    </small>
  );
}

function TestResult({ result }) {
  const good = result.ok;
  return (
    <div
      role="status"
      style={{
        ...box,
        borderColor: good ? "#166534" : "#8a2a2a",
        background: good ? "#0f2417" : "#2a1414",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
        <span aria-hidden style={{ fontSize: "15px" }}>{good ? "✅" : "⚠️"}</span>
        <strong style={{ color: good ? "#86efac" : "#fecaca", fontSize: "13px" }}>
          {good ? "Connection works" : "Not connected"}
        </strong>
      </div>
      <p style={{ margin: 0, color: "#d0d0d8", fontSize: "13px", lineHeight: 1.5 }}>{result.detail}</p>
      <dl
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: "8px 16px",
          margin: "12px 0 0 0",
          fontSize: "12px",
          color: "#a0a0b0",
        }}
      >
        <Fact term="Gateway" value={result.url} />
        <Fact term="Reachable" value={result.reachable ? "yes" : "no"} />
        <Fact
          term="Authentication"
          value={result.authRequired === false ? "disabled on gateway" : result.authenticated ? `signed in (${result.authModeUsed})` : "required"}
        />
        {result.serverCount != null && <Fact term="Servers visible" value={String(result.serverCount)} />}
      </dl>
      {(result.warnings || []).map((w) => (
        <p key={w} style={{ margin: "8px 0 0 0", color: "#fcd34d", fontSize: "12px" }}>{w}</p>
      ))}
    </div>
  );
}

function Fact({ term, value }) {
  return (
    <div>
      <dt style={{ color: "#6b7280" }}>{term}</dt>
      <dd style={{ margin: 0, color: "#d0d0d8", wordBreak: "break-all" }}>{value}</dd>
    </div>
  );
}
