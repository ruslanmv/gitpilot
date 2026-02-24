import React, { useState } from "react";
import { createPortal } from "react-dom";

/**
 * EnvironmentEditor — Claude-Code-on-Web parity environment config modal.
 *
 * Allows setting name, network access level, and environment variables.
 */
export default function EnvironmentEditor({ environment, onSave, onDelete, onClose }) {
  const [name, setName] = useState(environment?.name || "");
  const [networkAccess, setNetworkAccess] = useState(environment?.network_access || "limited");
  const [envVarsText, setEnvVarsText] = useState(
    environment?.env_vars
      ? Object.entries(environment.env_vars)
          .map(([k, v]) => `${k}=${v}`)
          .join("\n")
      : ""
  );

  const handleSave = () => {
    const envVars = {};
    envVarsText
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line && line.includes("="))
      .forEach((line) => {
        const idx = line.indexOf("=");
        const key = line.slice(0, idx).trim();
        const val = line.slice(idx + 1).trim();
        if (key) envVars[key] = val;
      });

    onSave({
      id: environment?.id || null,
      name: name.trim() || "Default",
      network_access: networkAccess,
      env_vars: envVars,
    });
  };

  return createPortal(
    <div style={styles.overlay} onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={styles.modal} onMouseDown={(e) => e.stopPropagation()}>
        <div style={styles.header}>
          <span style={styles.headerTitle}>
            {environment?.id ? "Edit Environment" : "New Environment"}
          </span>
          <button type="button" style={styles.closeBtn} onClick={onClose}>
            &times;
          </button>
        </div>

        <div style={styles.body}>
          {/* Name */}
          <label style={styles.label}>Environment Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Development, Staging, Production"
            style={styles.input}
          />

          {/* Network Access */}
          <label style={styles.label}>Network Access</label>
          <div style={styles.radioGroup}>
            {[
              { value: "limited", label: "Limited", desc: "Allowlisted domains only (package managers, APIs)" },
              { value: "full", label: "Full", desc: "Unrestricted internet access" },
              { value: "none", label: "None", desc: "Air-gapped — no external network" },
            ].map((opt) => (
              <label
                key={opt.value}
                style={{
                  ...styles.radioItem,
                  borderColor:
                    networkAccess === opt.value ? "#3B82F6" : "#27272A",
                  backgroundColor:
                    networkAccess === opt.value
                      ? "rgba(59, 130, 246, 0.05)"
                      : "transparent",
                }}
              >
                <input
                  type="radio"
                  name="network"
                  value={opt.value}
                  checked={networkAccess === opt.value}
                  onChange={(e) => setNetworkAccess(e.target.value)}
                  style={{ display: "none" }}
                />
                <div>
                  <div style={{
                    fontSize: 13,
                    fontWeight: 500,
                    color: networkAccess === opt.value ? "#E4E4E7" : "#A1A1AA",
                  }}>
                    {opt.label}
                  </div>
                  <div style={{ fontSize: 11, color: "#71717A", marginTop: 2 }}>
                    {opt.desc}
                  </div>
                </div>
              </label>
            ))}
          </div>

          {/* Environment Variables */}
          <label style={styles.label}>Environment Variables</label>
          <textarea
            value={envVarsText}
            onChange={(e) => setEnvVarsText(e.target.value)}
            placeholder={"NODE_ENV=development\nDEBUG=true\nAPI_KEY=your-key-here"}
            rows={6}
            style={styles.textarea}
          />
          <div style={{ fontSize: 10, color: "#52525B", marginTop: 4 }}>
            One KEY=VALUE per line. Secrets are stored locally.
          </div>
        </div>

        <div style={styles.footer}>
          {onDelete && (
            <button type="button" style={styles.deleteBtn} onClick={onDelete}>
              Delete
            </button>
          )}
          <div style={{ flex: 1 }} />
          <button type="button" style={styles.cancelBtn} onClick={onClose}>
            Cancel
          </button>
          <button type="button" style={styles.saveBtn} onClick={handleSave}>
            Save
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}

const styles = {
  overlay: {
    position: "fixed",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    backgroundColor: "rgba(0, 0, 0, 0.6)",
    zIndex: 10000,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  modal: {
    width: 480,
    maxHeight: "80vh",
    backgroundColor: "#131316",
    border: "1px solid #27272A",
    borderRadius: 12,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "14px 16px",
    borderBottom: "1px solid #27272A",
    backgroundColor: "#18181B",
  },
  headerTitle: {
    fontSize: 14,
    fontWeight: 600,
    color: "#E4E4E7",
  },
  closeBtn: {
    width: 26,
    height: 26,
    borderRadius: 6,
    border: "1px solid #3F3F46",
    background: "transparent",
    color: "#A1A1AA",
    fontSize: 16,
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  body: {
    padding: "16px",
    overflowY: "auto",
    flex: 1,
  },
  label: {
    display: "block",
    fontSize: 12,
    fontWeight: 600,
    color: "#A1A1AA",
    marginBottom: 6,
    marginTop: 14,
  },
  input: {
    width: "100%",
    padding: "8px 10px",
    borderRadius: 6,
    border: "1px solid #3F3F46",
    background: "#18181B",
    color: "#E4E4E7",
    fontSize: 13,
    outline: "none",
    boxSizing: "border-box",
  },
  radioGroup: {
    display: "flex",
    flexDirection: "column",
    gap: 6,
  },
  radioItem: {
    display: "flex",
    alignItems: "flex-start",
    gap: 10,
    padding: "8px 10px",
    borderRadius: 6,
    border: "1px solid #27272A",
    cursor: "pointer",
    transition: "border-color 0.15s, background-color 0.15s",
  },
  textarea: {
    width: "100%",
    padding: "8px 10px",
    borderRadius: 6,
    border: "1px solid #3F3F46",
    background: "#18181B",
    color: "#E4E4E7",
    fontSize: 12,
    fontFamily: "monospace",
    outline: "none",
    resize: "vertical",
    boxSizing: "border-box",
  },
  footer: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "12px 16px",
    borderTop: "1px solid #27272A",
  },
  cancelBtn: {
    padding: "6px 14px",
    borderRadius: 6,
    border: "1px solid #3F3F46",
    background: "transparent",
    color: "#A1A1AA",
    fontSize: 12,
    cursor: "pointer",
  },
  saveBtn: {
    padding: "6px 14px",
    borderRadius: 6,
    border: "none",
    background: "#3B82F6",
    color: "#fff",
    fontSize: 12,
    fontWeight: 600,
    cursor: "pointer",
  },
  deleteBtn: {
    padding: "6px 14px",
    borderRadius: 6,
    border: "1px solid rgba(239, 68, 68, 0.3)",
    background: "transparent",
    color: "#EF4444",
    fontSize: 12,
    cursor: "pointer",
  },
};
