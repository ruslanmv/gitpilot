// frontend/components/AdminTabs/mcp/CustomInstallForm.jsx
// Paste-a-register.json form for installing a custom MCP server.

import React, { useState } from "react";

const SAMPLE = `{
  "name": "mcp-neo4j-server",
  "endpoint": "http://mcp-neo4j-server:8083/mcp",
  "description": "Neo4j MCP server for graph schema discovery",
  "tags": ["graph", "neo4j"],
  "auth": { "type": "bearer", "env": "MCP_NEO4J_SERVER_TOKEN" }
}`;

export default function CustomInstallForm({ onSubmit }) {
  const [text, setText] = useState(SAMPLE);
  const [err, setErr] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    setErr(null);
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      setErr("Invalid JSON: " + (e?.message || ""));
      return;
    }
    if (!parsed.name || !parsed.endpoint) {
      setErr("register.json must include 'name' and 'endpoint'.");
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit(parsed);
      setText(SAMPLE);
    } catch (e) {
      setErr(e?.message || "Install failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      style={{
        background: "#1a1b26",
        border: "1px solid #2a2b36",
        borderRadius: "8px",
        padding: "20px",
      }}
    >
      <h4 style={{ margin: "0 0 6px 0", color: "#e0e0e7" }}>Install custom server</h4>
      <p style={{ margin: "0 0 12px 0", fontSize: "12px", color: "#a0a0b0" }}>
        Paste a Context Forge <code>register.json</code>. The server lands
        disabled — turn it on from the Installed tab once you have set its
        auth token.
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={12}
        spellCheck={false}
        aria-label="register.json"
        style={{
          width: "100%",
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          fontSize: "12px",
          background: "#0f1018",
          color: "#cdd0d8",
          border: "1px solid #2a2b36",
          borderRadius: "6px",
          padding: "12px",
          resize: "vertical",
          boxSizing: "border-box",
        }}
      />
      {err && (
        <div
          role="alert"
          style={{
            marginTop: "8px",
            padding: "8px 12px",
            background: "#3a0f0f",
            border: "1px solid #5a1f1f",
            borderRadius: "6px",
            color: "#fca5a5",
            fontSize: "12px",
          }}
        >
          {err}
        </div>
      )}
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          gap: "8px",
          marginTop: "12px",
        }}
      >
        <button
          onClick={() => setText(SAMPLE)}
          style={{
            padding: "6px 12px",
            background: "transparent",
            color: "#cdd0d8",
            border: "1px solid #3a3b4a",
            borderRadius: "4px",
            cursor: "pointer",
            fontSize: "12px",
          }}
        >
          Reset
        </button>
        <button
          onClick={handleSubmit}
          disabled={submitting}
          style={{
            padding: "6px 14px",
            background: "#3B82F6",
            color: "#fff",
            border: "none",
            borderRadius: "4px",
            cursor: submitting ? "not-allowed" : "pointer",
            fontSize: "12px",
            fontWeight: 600,
          }}
        >
          {submitting ? "Installing…" : "Install server"}
        </button>
      </div>
    </div>
  );
}
