// frontend/components/AdminTabs/SkillsTab.jsx
import React, { useEffect, useState, useCallback } from "react";
import { apiUrl, safeFetchJSON } from "../../utils/api.js";

/**
 * Skills tab — lists all loaded skills from /api/skills and allows
 * reloading them from disk via /api/skills/reload.
 *
 * Best practices applied:
 * - Fetch on mount
 * - Explicit reload button (skills are loaded from .md files on disk)
 * - Loading / empty / error states
 * - Auto-trigger indicator badge
 * - Required tools list per skill
 * - Source file path for debugging
 */

export default function SkillsTab({ showToast }) {
  const [skills, setSkills] = useState([]);
  const [loading, setLoading] = useState(true);
  const [reloading, setReloading] = useState(false);
  const [error, setError] = useState(null);

  const fetchSkills = useCallback(async () => {
    setError(null);
    try {
      const data = await safeFetchJSON(apiUrl("/api/skills"), { timeout: 10000 });
      setSkills(Array.isArray(data.skills) ? data.skills : []);
    } catch (err) {
      setError(err?.message || "Failed to load skills");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  const handleReload = async () => {
    setReloading(true);
    setError(null);
    try {
      const data = await safeFetchJSON(apiUrl("/api/skills/reload"), {
        method: "POST",
        timeout: 10000,
      });
      showToast?.(
        "Skills reloaded",
        `${data.count ?? 0} skill${data.count !== 1 ? "s" : ""} loaded from disk.`
      );
      await fetchSkills();
    } catch (err) {
      setError(err?.message || "Failed to reload skills");
    } finally {
      setReloading(false);
    }
  };

  return (
    <div>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "flex-start",
          marginBottom: "16px",
        }}
      >
        <div>
          <h3 style={{ marginBottom: "4px" }}>Skills</h3>
          <p style={{ fontSize: "12px", opacity: 0.7 }}>
            Reusable prompt templates loaded from{" "}
            <code style={{ fontSize: "11px" }}>.gitpilot/skills/*.md</code> files.
          </p>
        </div>
        <button
          type="button"
          onClick={handleReload}
          disabled={reloading || loading}
          style={{
            padding: "6px 12px",
            background: reloading ? "#555" : "#3B82F6",
            color: "#fff",
            border: "none",
            borderRadius: "4px",
            cursor: reloading || loading ? "not-allowed" : "pointer",
            fontSize: "12px",
            fontWeight: 600,
          }}
        >
          {reloading ? "Reloading..." : "Reload Skills"}
        </button>
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
          Loading skills...
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
          }}
        >
          <strong>Error: </strong>
          {error}
        </div>
      )}

      {/* Empty state */}
      {!loading && !error && skills.length === 0 && (
        <div
          style={{
            background: "#1a1b26",
            borderRadius: "8px",
            padding: "40px 20px",
            textAlign: "center",
            border: "1px dashed #2a2b36",
          }}
        >
          <div style={{ fontSize: "32px", marginBottom: "8px" }}>📚</div>
          <div style={{ fontSize: "14px", fontWeight: 600, marginBottom: "4px" }}>
            No skills loaded
          </div>
          <div style={{ fontSize: "12px", opacity: 0.6 }}>
            Create a <code>.gitpilot/skills/my-skill.md</code> file with YAML
            frontmatter to add custom skills.
          </div>
        </div>
      )}

      {/* Skills grid */}
      {!loading && skills.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
            gap: "12px",
          }}
        >
          {skills.map((skill) => (
            <div
              key={skill.name}
              style={{
                background: "#1a1b26",
                borderRadius: "8px",
                padding: "16px",
                border: "1px solid #2a2b36",
                display: "flex",
                flexDirection: "column",
                gap: "8px",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  gap: "8px",
                }}
              >
                <h4
                  style={{
                    fontSize: "14px",
                    fontWeight: 600,
                    margin: 0,
                    color: "#fff",
                  }}
                >
                  {skill.name}
                </h4>
                {skill.auto_trigger && (
                  <span
                    title="Auto-triggered by matching context"
                    style={{
                      padding: "2px 8px",
                      background: "#1e3a5f",
                      color: "#93c5fd",
                      border: "1px solid #3B82F6",
                      borderRadius: "10px",
                      fontSize: "9px",
                      fontWeight: 700,
                      textTransform: "uppercase",
                      whiteSpace: "nowrap",
                    }}
                  >
                    Auto
                  </span>
                )}
              </div>

              <p
                style={{
                  fontSize: "12px",
                  opacity: 0.7,
                  lineHeight: 1.5,
                  margin: 0,
                  minHeight: "36px",
                }}
              >
                {skill.description || "No description"}
              </p>

              {Array.isArray(skill.required_tools) && skill.required_tools.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                  {skill.required_tools.map((t) => (
                    <span
                      key={t}
                      style={{
                        padding: "2px 6px",
                        background: "#0d0e15",
                        border: "1px solid #2a2b36",
                        borderRadius: "4px",
                        fontSize: "10px",
                        fontFamily: "monospace",
                        opacity: 0.8,
                      }}
                    >
                      {t}
                    </span>
                  ))}
                </div>
              )}

              {skill.source && (
                <div
                  style={{
                    fontSize: "10px",
                    opacity: 0.4,
                    fontFamily: "monospace",
                    borderTop: "1px solid #2a2b36",
                    paddingTop: "8px",
                    wordBreak: "break-all",
                  }}
                >
                  {skill.source}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
