// frontend/components/AdminTabs/WorkspaceModesTab.jsx
import React, { useState } from "react";
import { startSession } from "../../utils/api.js";

/**
 * Workspace Modes tab — allows the user to start a session in one of
 * three modes (folder, local_git, github). Calls POST /api/session/start.
 *
 * Best practices applied:
 * - Loading state while the request is in flight
 * - Per-mode error state (not a global error)
 * - Disabled card during submission to prevent double-click
 * - ARIA role="button" + aria-disabled for accessibility
 * - Toast notification on success
 * - Success callback so App.jsx can set activeSessionId and switch to workspace view
 */

const MODES = [
  {
    id: "folder",
    title: "Folder Mode",
    description: "Work with any local folder. No Git required.",
    requires: "A local folder path",
    enables: "Chat, explain, review",
    promptKey: "folder_path",
    promptLabel: "Folder path (absolute)",
    promptPlaceholder: "/home/you/myproject",
    buildPayload: (value) => ({ mode: "folder", folder_path: value }),
  },
  {
    id: "local_git",
    title: "Local Git Mode",
    description: "Full repo + branch context for AI assistance.",
    requires: "A local Git repository",
    enables: "All local features (branches, diff, commit)",
    promptKey: "repo_root",
    promptLabel: "Repository root (absolute path)",
    promptPlaceholder: "/home/you/my-git-repo",
    buildPayload: (value) => ({ mode: "local_git", repo_root: value }),
  },
  {
    id: "github",
    title: "GitHub Mode",
    description: "PRs, issues, remote workflows via GitHub API.",
    requires: "GitHub token (already signed in)",
    enables: "Full platform features",
    promptKey: "repo_full_name",
    promptLabel: "Repository (owner/repo)",
    promptPlaceholder: "octocat/hello-world",
    buildPayload: (value) => ({ mode: "github", repo_full_name: value }),
  },
];

export default function WorkspaceModesTab({ onSessionStarted, showToast }) {
  const [activeModeId, setActiveModeId] = useState(null);
  const [inputValue, setInputValue] = useState("");
  const [submittingId, setSubmittingId] = useState(null);
  const [errorByMode, setErrorByMode] = useState({});

  const handleCardClick = (mode) => {
    if (submittingId) return;
    setActiveModeId(mode.id);
    setInputValue("");
    setErrorByMode((prev) => ({ ...prev, [mode.id]: null }));
  };

  const handleStart = async (mode) => {
    const trimmed = inputValue.trim();
    if (!trimmed) {
      setErrorByMode((prev) => ({
        ...prev,
        [mode.id]: `${mode.promptLabel} is required`,
      }));
      return;
    }

    setSubmittingId(mode.id);
    setErrorByMode((prev) => ({ ...prev, [mode.id]: null }));

    try {
      const payload = mode.buildPayload(trimmed);
      const result = await startSession(payload);

      showToast?.(
        `${mode.title} started`,
        `Session ${result.session_id?.slice(0, 8) || ""} is now active.`
      );

      onSessionStarted?.(result);
      setActiveModeId(null);
      setInputValue("");
    } catch (err) {
      setErrorByMode((prev) => ({
        ...prev,
        [mode.id]: err?.message || "Failed to start session",
      }));
    } finally {
      setSubmittingId(null);
    }
  };

  const handleCancel = () => {
    if (submittingId) return;
    setActiveModeId(null);
    setInputValue("");
  };

  return (
    <div>
      <h3 style={{ marginBottom: "16px" }}>Workspace Modes</h3>
      <p style={{ fontSize: "12px", opacity: 0.7, marginBottom: "16px" }}>
        Choose how you want GitPilot to interact with your code. You can switch modes at any time.
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "16px" }}>
        {MODES.map((mode) => {
          const isActive = activeModeId === mode.id;
          const isSubmitting = submittingId === mode.id;
          const error = errorByMode[mode.id];

          return (
            <div
              key={mode.id}
              role="button"
              tabIndex={isSubmitting ? -1 : 0}
              aria-disabled={!!submittingId && !isSubmitting}
              onClick={() => !isActive && handleCardClick(mode)}
              onKeyDown={(e) => {
                if ((e.key === "Enter" || e.key === " ") && !isActive) {
                  e.preventDefault();
                  handleCardClick(mode);
                }
              }}
              style={{
                background: isActive ? "#1e3a5f" : "#1a1b26",
                borderRadius: "8px",
                padding: "20px",
                border: isActive ? "1px solid #3B82F6" : "1px solid #2a2b36",
                cursor: submittingId && !isSubmitting ? "not-allowed" : "pointer",
                opacity: submittingId && !isSubmitting ? 0.5 : 1,
                transition: "all 150ms ease",
              }}
            >
              <h4 style={{ marginBottom: "8px", color: isActive ? "#93c5fd" : "#fff" }}>
                {mode.title}
              </h4>
              <p style={{ fontSize: "12px", opacity: 0.7, marginBottom: "12px" }}>
                {mode.description}
              </p>
              <div style={{ fontSize: "12px", marginBottom: "4px" }}>
                <span style={{ opacity: 0.6 }}>Requires: </span>
                {mode.requires}
              </div>
              <div style={{ fontSize: "12px", marginBottom: "12px" }}>
                <span style={{ opacity: 0.6 }}>Enables: </span>
                {mode.enables}
              </div>

              {isActive && (
                <div onClick={(e) => e.stopPropagation()} style={{ marginTop: "12px" }}>
                  <label
                    htmlFor={`mode-input-${mode.id}`}
                    style={{
                      fontSize: "11px",
                      opacity: 0.7,
                      display: "block",
                      marginBottom: "4px",
                    }}
                  >
                    {mode.promptLabel}
                  </label>
                  <input
                    id={`mode-input-${mode.id}`}
                    type="text"
                    value={inputValue}
                    onChange={(e) => setInputValue(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        handleStart(mode);
                      } else if (e.key === "Escape") {
                        handleCancel();
                      }
                    }}
                    placeholder={mode.promptPlaceholder}
                    disabled={isSubmitting}
                    autoFocus
                    style={{
                      width: "100%",
                      padding: "6px 8px",
                      background: "#0d0e15",
                      border: "1px solid #2a2b36",
                      borderRadius: "4px",
                      color: "#fff",
                      fontSize: "12px",
                      fontFamily: "monospace",
                    }}
                  />
                  {error && (
                    <div
                      style={{
                        fontSize: "11px",
                        color: "#f87171",
                        marginTop: "6px",
                      }}
                      role="alert"
                    >
                      {error}
                    </div>
                  )}
                  <div style={{ display: "flex", gap: "6px", marginTop: "10px" }}>
                    <button
                      type="button"
                      onClick={() => handleStart(mode)}
                      disabled={isSubmitting || !inputValue.trim()}
                      style={{
                        padding: "6px 12px",
                        background: isSubmitting ? "#555" : "#3B82F6",
                        color: "#fff",
                        border: "none",
                        borderRadius: "4px",
                        cursor: isSubmitting || !inputValue.trim() ? "not-allowed" : "pointer",
                        fontSize: "12px",
                        fontWeight: 600,
                      }}
                    >
                      {isSubmitting ? "Starting..." : "Start Session"}
                    </button>
                    <button
                      type="button"
                      onClick={handleCancel}
                      disabled={isSubmitting}
                      style={{
                        padding: "6px 12px",
                        background: "transparent",
                        color: "#a0a0b0",
                        border: "1px solid #2a2b36",
                        borderRadius: "4px",
                        cursor: isSubmitting ? "not-allowed" : "pointer",
                        fontSize: "12px",
                      }}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
