import React, { useEffect, useState } from "react";
import EnvironmentEditor from "./EnvironmentEditor.jsx";

/**
 * EnvironmentSelector — Claude-Code-on-Web parity environment dropdown.
 *
 * Shows current environment name + gear icon. Gear opens the editor modal.
 * Fetches environments from /api/environments.
 */
export default function EnvironmentSelector({ activeEnvId, onEnvChange }) {
  const [envs, setEnvs] = useState([]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingEnv, setEditingEnv] = useState(null);

  const fetchEnvs = async () => {
    try {
      const res = await fetch("/api/environments", { cache: "no-cache" });
      if (!res.ok) return;
      const data = await res.json();
      setEnvs(data.environments || []);
    } catch (err) {
      console.warn("Failed to fetch environments:", err);
    }
  };

  useEffect(() => {
    fetchEnvs();
  }, []);

  const activeEnv =
    envs.find((e) => e.id === activeEnvId) || envs[0] || { name: "Default", id: "default" };

  const handleSave = async (config) => {
    try {
      const method = config.id ? "PUT" : "POST";
      const url = config.id ? `/api/environments/${config.id}` : "/api/environments";
      await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      await fetchEnvs();
      setEditorOpen(false);
      setEditingEnv(null);
    } catch (err) {
      console.warn("Failed to save environment:", err);
    }
  };

  const handleDelete = async (envId) => {
    try {
      await fetch(`/api/environments/${envId}`, { method: "DELETE" });
      await fetchEnvs();
      if (activeEnvId === envId) {
        onEnvChange?.(null);
      }
    } catch (err) {
      console.warn("Failed to delete environment:", err);
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.label}>ENVIRONMENT</div>
      <div style={styles.row}>
        <div style={styles.envCard}>
          {/* Env selector */}
          <select
            value={activeEnv.id || "default"}
            onChange={(e) => onEnvChange?.(e.target.value)}
            style={styles.select}
          >
            {envs.map((env) => (
              <option key={env.id} value={env.id}>
                {env.name}
              </option>
            ))}
          </select>

          {/* Network badge */}
          <span style={{
            ...styles.networkBadge,
            color: activeEnv.network_access === "full"
              ? "#10B981"
              : activeEnv.network_access === "none"
              ? "#EF4444"
              : "#F59E0B",
          }}>
            {activeEnv.network_access || "limited"}
          </span>
        </div>

        {/* Gear icon */}
        <button
          type="button"
          style={styles.gearBtn}
          onClick={() => {
            setEditingEnv(activeEnv);
            setEditorOpen(true);
          }}
          title="Configure environment"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>

        {/* Add new */}
        <button
          type="button"
          style={styles.gearBtn}
          onClick={() => {
            setEditingEnv(null);
            setEditorOpen(true);
          }}
          title="Add environment"
        >
          +
        </button>
      </div>

      {/* Editor modal */}
      {editorOpen && (
        <EnvironmentEditor
          environment={editingEnv}
          onSave={handleSave}
          onDelete={editingEnv?.id ? () => handleDelete(editingEnv.id) : null}
          onClose={() => {
            setEditorOpen(false);
            setEditingEnv(null);
          }}
        />
      )}
    </div>
  );
}

const styles = {
  container: {
    padding: "10px 14px",
  },
  label: {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: "0.08em",
    color: "#71717A",
    textTransform: "uppercase",
    marginBottom: 6,
  },
  row: {
    display: "flex",
    alignItems: "center",
    gap: 6,
  },
  envCard: {
    flex: 1,
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "4px 8px",
    borderRadius: 6,
    border: "1px solid #27272A",
    backgroundColor: "#18181B",
    minWidth: 0,
  },
  select: {
    flex: 1,
    background: "transparent",
    border: "none",
    color: "#E4E4E7",
    fontSize: 12,
    fontWeight: 500,
    outline: "none",
    cursor: "pointer",
    minWidth: 0,
  },
  networkBadge: {
    fontSize: 9,
    fontWeight: 600,
    textTransform: "uppercase",
    letterSpacing: "0.04em",
    flexShrink: 0,
  },
  gearBtn: {
    width: 28,
    height: 28,
    borderRadius: 6,
    border: "1px solid #27272A",
    background: "transparent",
    color: "#71717A",
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 14,
    flexShrink: 0,
  },
};
