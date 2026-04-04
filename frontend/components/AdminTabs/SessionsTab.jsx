// frontend/components/AdminTabs/SessionsTab.jsx
import React, { useEffect, useMemo, useState, useCallback } from "react";
import { apiUrl, safeFetchJSON } from "../../utils/api.js";

/**
 * Sessions tab — admin-level table view of all saved sessions with
 * search, sort, and delete actions.
 *
 * Best practices applied:
 * - Fetch all sessions on mount
 * - Client-side search (useMemo for filtered list)
 * - Confirmation dialog before delete
 * - Row hover effect
 * - Empty / loading / error states
 * - Relative timestamps ("2 hours ago")
 * - Click row to open in workspace view
 */

function formatRelativeTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    const diff = Date.now() - d.getTime();
    if (diff < 60_000) return "just now";
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    if (diff < 2_592_000_000) return `${Math.floor(diff / 86_400_000)}d ago`;
    return d.toLocaleDateString();
  } catch {
    return "—";
  }
}

export default function SessionsTab({ onSelectSession, showToast }) {
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const [deletingId, setDeletingId] = useState(null);

  const fetchSessions = useCallback(async () => {
    setError(null);
    try {
      const data = await safeFetchJSON(apiUrl("/api/sessions"), { timeout: 10000 });
      setSessions(Array.isArray(data.sessions) ? data.sessions : []);
    } catch (err) {
      setError(err?.message || "Failed to load sessions");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const handleDelete = async (session) => {
    if (
      !window.confirm(
        `Delete session "${session.name || session.id?.slice(0, 8)}"? This cannot be undone.`
      )
    ) {
      return;
    }

    setDeletingId(session.id);
    try {
      const res = await fetch(apiUrl(`/api/sessions/${session.id}`), {
        method: "DELETE",
      });
      if (!res.ok) {
        throw new Error(`Delete failed (${res.status})`);
      }
      showToast?.("Session deleted", session.name || session.id);
      // Optimistic removal
      setSessions((prev) => prev.filter((s) => s.id !== session.id));
    } catch (err) {
      setError(err?.message || "Failed to delete session");
    } finally {
      setDeletingId(null);
    }
  };

  const filtered = useMemo(() => {
    if (!query.trim()) return sessions;
    const q = query.toLowerCase();
    return sessions.filter((s) => {
      return (
        (s.name || "").toLowerCase().includes(q) ||
        (s.repo || "").toLowerCase().includes(q) ||
        (s.branch || "").toLowerCase().includes(q) ||
        (s.id || "").toLowerCase().includes(q)
      );
    });
  }, [sessions, query]);

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "16px",
          gap: "12px",
          flexWrap: "wrap",
        }}
      >
        <div>
          <h3 style={{ marginBottom: "4px" }}>Sessions</h3>
          <p style={{ fontSize: "12px", opacity: 0.7 }}>
            All saved chat sessions ({sessions.length} total
            {query ? `, ${filtered.length} matching` : ""}).
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search sessions..."
            style={{
              padding: "6px 10px",
              background: "#0d0e15",
              border: "1px solid #2a2b36",
              borderRadius: "4px",
              color: "#fff",
              fontSize: "12px",
              width: "220px",
            }}
          />
          <button
            type="button"
            onClick={fetchSessions}
            disabled={loading}
            style={{
              padding: "6px 12px",
              background: "transparent",
              color: "#a0a0b0",
              border: "1px solid #2a2b36",
              borderRadius: "4px",
              cursor: loading ? "not-allowed" : "pointer",
              fontSize: "12px",
            }}
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Loading state */}
      {loading && (
        <div
          style={{
            background: "#1a1b26",
            borderRadius: "8px",
            padding: "40px 20px",
            textAlign: "center",
            border: "1px solid #2a2b36",
            fontSize: "12px",
            opacity: 0.6,
          }}
        >
          Loading sessions...
        </div>
      )}

      {/* Error state */}
      {error && !loading && (
        <div
          role="alert"
          style={{
            background: "#7f1d1d",
            color: "#fecaca",
            border: "1px solid #991b1b",
            borderRadius: "8px",
            padding: "12px",
            fontSize: "12px",
            marginBottom: "12px",
          }}
        >
          <strong>Error: </strong>
          {error}
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && sessions.length === 0 && (
        <div
          style={{
            background: "#1a1b26",
            borderRadius: "8px",
            padding: "40px 20px",
            textAlign: "center",
            border: "1px dashed #2a2b36",
          }}
        >
          <div style={{ fontSize: "32px", marginBottom: "8px" }}>💬</div>
          <div style={{ fontSize: "14px", fontWeight: 600, marginBottom: "4px" }}>
            No sessions yet
          </div>
          <div style={{ fontSize: "12px", opacity: 0.6 }}>
            Start chatting with GitPilot to create your first session.
          </div>
        </div>
      )}

      {/* Table */}
      {!loading && filtered.length > 0 && (
        <div
          style={{
            background: "#1a1b26",
            borderRadius: "8px",
            border: "1px solid #2a2b36",
            overflow: "hidden",
          }}
        >
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: "12px",
            }}
          >
            <thead>
              <tr style={{ background: "#0d0e15" }}>
                <th style={thStyle}>Name</th>
                <th style={thStyle}>Repository</th>
                <th style={thStyle}>Branch</th>
                <th style={thStyle}>Messages</th>
                <th style={thStyle}>Status</th>
                <th style={thStyle}>Updated</th>
                <th style={{ ...thStyle, textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((s) => (
                <tr
                  key={s.id}
                  style={{
                    borderTop: "1px solid #2a2b36",
                    cursor: onSelectSession ? "pointer" : "default",
                  }}
                  onMouseEnter={(e) =>
                    (e.currentTarget.style.background = "#22232e")
                  }
                  onMouseLeave={(e) =>
                    (e.currentTarget.style.background = "transparent")
                  }
                  onClick={() => onSelectSession?.(s)}
                >
                  <td style={tdStyle}>
                    <div style={{ fontWeight: 600 }}>
                      {s.name || <span style={{ opacity: 0.4 }}>(unnamed)</span>}
                    </div>
                    <div
                      style={{
                        fontSize: "10px",
                        opacity: 0.4,
                        fontFamily: "monospace",
                      }}
                    >
                      {s.id?.slice(0, 12)}
                    </div>
                  </td>
                  <td style={{ ...tdStyle, fontFamily: "monospace" }}>
                    {s.repo || <span style={{ opacity: 0.4 }}>—</span>}
                  </td>
                  <td style={{ ...tdStyle, fontFamily: "monospace" }}>
                    {s.branch || <span style={{ opacity: 0.4 }}>—</span>}
                  </td>
                  <td style={tdStyle}>{s.message_count ?? 0}</td>
                  <td style={tdStyle}>
                    <span
                      style={{
                        padding: "2px 8px",
                        background:
                          s.status === "active"
                            ? "#064e3b"
                            : s.status === "completed"
                            ? "#1e3a5f"
                            : "#374151",
                        color:
                          s.status === "active"
                            ? "#a7f3d0"
                            : s.status === "completed"
                            ? "#93c5fd"
                            : "#9ca3af",
                        borderRadius: "10px",
                        fontSize: "10px",
                        fontWeight: 600,
                        textTransform: "uppercase",
                      }}
                    >
                      {s.status || "unknown"}
                    </span>
                  </td>
                  <td style={{ ...tdStyle, opacity: 0.7 }}>
                    {formatRelativeTime(s.updated_at)}
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right" }}>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(s);
                      }}
                      disabled={deletingId === s.id}
                      style={{
                        padding: "4px 10px",
                        background: "transparent",
                        color: "#f87171",
                        border: "1px solid #991b1b",
                        borderRadius: "4px",
                        cursor: deletingId === s.id ? "not-allowed" : "pointer",
                        fontSize: "11px",
                      }}
                    >
                      {deletingId === s.id ? "..." : "Delete"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* No matches for search */}
      {!loading && sessions.length > 0 && filtered.length === 0 && (
        <div
          style={{
            background: "#1a1b26",
            borderRadius: "8px",
            padding: "20px",
            textAlign: "center",
            border: "1px dashed #2a2b36",
            fontSize: "12px",
            opacity: 0.7,
          }}
        >
          No sessions match "{query}"
        </div>
      )}
    </div>
  );
}

const thStyle = {
  padding: "10px 12px",
  textAlign: "left",
  fontSize: "11px",
  fontWeight: 600,
  textTransform: "uppercase",
  letterSpacing: "0.5px",
  opacity: 0.7,
};

const tdStyle = {
  padding: "10px 12px",
  verticalAlign: "middle",
};
