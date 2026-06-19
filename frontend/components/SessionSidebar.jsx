import React, { useEffect, useRef, useState } from "react";
import { apiUrl } from "../utils/api.js";
import SessionItem from "./SessionItem.jsx";

/**
 * SessionSidebar — Claude-Code-on-Web parity.
 *
 * Shows a scrollable list of coding sessions with status indicators,
 * timestamps, and a "New Session" button. Additive — does not modify
 * any existing component.
 */
export default function SessionSidebar({
  repo,
  activeSessionId,
  onSelectSession,
  onNewSession,
  onDeleteSession,
  refreshNonce = 0,
}) {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(false);
  const pollRef = useRef(null);

  const repoFullName = repo?.full_name || (repo ? `${repo.owner}/${repo.name}` : null);

  // Fetch sessions
  useEffect(() => {
    if (!repoFullName) {
      setSessions([]);
      return;
    }

    let cancelled = false;

    const fetchSessions = async () => {
      setLoading(true);
      try {
        const token = localStorage.getItem("github_token");
        const headers = token ? { Authorization: `Bearer ${token}` } : {};
        const res = await fetch(apiUrl(`/api/sessions`), { headers, cache: "no-cache" });
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled) return;

        // Filter to current repo
        const filtered = (data.sessions || []).filter(
          (s) => s.repo === repoFullName
        );
        setSessions(filtered);
      } catch (err) {
        console.warn("Failed to fetch sessions:", err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    fetchSessions();

    // Poll every 15s for status updates
    pollRef.current = setInterval(fetchSessions, 15000);

    return () => {
      cancelled = true;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [repoFullName, refreshNonce]);

  const handleDelete = async (sessionId) => {
    try {
      const token = localStorage.getItem("github_token");
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      await fetch(apiUrl(`/api/sessions/${sessionId}`), { method: "DELETE", headers });
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      // Notify parent so it can clear the chat if this was the active session
      onDeleteSession?.(sessionId);
    } catch (err) {
      console.warn("Failed to delete session:", err);
    }
  };

  return (
    <div style={styles.container}>
      <style>{animStyles}</style>

      {/* Header */}
      <div style={styles.header}>
        <span style={styles.label}>SESSIONS</span>
        <button
          type="button"
          style={styles.newBtn}
          onClick={onNewSession}
          title="New session"
        >
          +
        </button>
      </div>

      {/* Session list */}
      <div style={styles.list}>
        {loading && sessions.length === 0 && (
          <div style={styles.empty}>Loading...</div>
        )}

        {!loading && sessions.length === 0 && (
          <div style={styles.empty}>
            No sessions yet.
            <br />
            <span style={{ fontSize: 11, opacity: 0.6 }}>
              Your first message will create one automatically.
            </span>
          </div>
        )}

        {sessions.map((s) => (
          <SessionItem
            key={s.id}
            session={s}
            isActive={s.id === activeSessionId}
            onSelect={() => onSelectSession?.(s)}
            onDelete={() => handleDelete(s.id)}
          />
        ))}
      </div>
    </div>
  );
}

const animStyles = `
  @keyframes session-fade-in {
    from { opacity: 0; transform: translateY(4px); }
    to { opacity: 1; transform: translateY(0); }
  }
`;

const styles = {
  container: {
    display: "flex",
    flexDirection: "column",
    borderTop: "1px solid #27272A",
    flex: 1,
    minHeight: 0,
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "10px 14px 6px",
  },
  label: {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: "0.08em",
    color: "#71717A",
    textTransform: "uppercase",
  },
  newBtn: {
    width: 22,
    height: 22,
    borderRadius: 4,
    border: "1px dashed #3F3F46",
    background: "transparent",
    color: "#A1A1AA",
    fontSize: 14,
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    lineHeight: 1,
  },
  list: {
    flex: 1,
    overflowY: "auto",
    padding: "0 6px 8px",
  },
  empty: {
    textAlign: "center",
    color: "#52525B",
    fontSize: 12,
    padding: "20px 8px",
    lineHeight: 1.5,
  },
};
