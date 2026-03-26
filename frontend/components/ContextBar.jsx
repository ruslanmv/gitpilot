import React, { useCallback, useRef, useState } from "react";
import BranchPicker from "./BranchPicker.jsx";

/**
 * ContextBar — horizontal repo chip bar for multi-repo workspace context.
 *
 * Uses CSS classes for hover-reveal X (Claude-style: subtle by default,
 * visible on chip hover, red on X hover). Each chip owns its own remove
 * button — removing one repo never affects the others.
 */
export default function ContextBar({
  contextRepos,
  activeRepoKey,
  repoStateByKey,
  onActivate,
  onRemove,
  onAdd,
  onBranchChange,
  mode, // workspace mode: "github", "local-git", "folder" (optional)
}) {
  if (!contextRepos || contextRepos.length === 0) return null;

  return (
    <div className="ctxbar">
      {/* Workspace mode indicator */}
      {mode && (
        <span className="ctxbar-mode" title={`Workspace mode: ${mode}`}>
          {mode === "github" ? "GH" : mode === "local-git" ? "Git" : "Dir"}
        </span>
      )}
      <div className="ctxbar-scroll">
        {contextRepos.map((entry) => {
          const isActive = entry.repoKey === activeRepoKey;
          return (
            <RepoChip
              key={entry.repoKey}
              entry={entry}
              isActive={isActive}
              repoState={repoStateByKey?.[entry.repoKey]}
              onActivate={() => onActivate(entry.repoKey)}
              onRemove={() => onRemove(entry.repoKey)}
              onBranchChange={(newBranch) =>
                onBranchChange(entry.repoKey, newBranch)
              }
            />
          );
        })}

        <button
          type="button"
          className="ctxbar-add"
          onClick={onAdd}
          title="Add repository to context"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
        </button>
      </div>

      <div className="ctxbar-meta">
        {contextRepos.length} {contextRepos.length === 1 ? "repo" : "repos"}
      </div>
    </div>
  );
}

function RepoChip({ entry, isActive, repoState, onActivate, onRemove, onBranchChange }) {
  const [branchOpen, setBranchOpen] = useState(false);
  const [hovered, setHovered] = useState(false);
  const branchBtnRef = useRef(null);
  const repo = entry.repo;
  const branch = repoState?.currentBranch || entry.branch || repo?.default_branch || "main";
  const defaultBranch = repoState?.defaultBranch || repo?.default_branch || "main";
  const sessionBranches = repoState?.sessionBranches || [];
  const displayName = repo?.name || entry.repoKey?.split("/")[1] || entry.repoKey;

  const handleChipClick = useCallback(
    (e) => {
      if (e.target.closest("[data-chip-action]")) return;
      onActivate();
    },
    [onActivate]
  );

  return (
    <div
      className={"ctxbar-chip" + (isActive ? " ctxbar-chip-active" : "")}
      onClick={handleChipClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      title={isActive ? `Active (write): ${entry.repoKey}` : `Click to activate ${entry.repoKey}`}
    >
      {/* Active indicator bar */}
      {isActive && <div className="ctxbar-chip-indicator" />}

      {/* Repo name */}
      <span className="ctxbar-chip-name">{displayName}</span>

      {/* Separator dot */}
      <span className="ctxbar-chip-dot" />

      {/* Branch name — single click opens GitHub branch list */}
      <button
        ref={branchBtnRef}
        type="button"
        data-chip-action="branch"
        className={"ctxbar-chip-branch" + (isActive ? " ctxbar-chip-branch-active" : "")}
        onClick={(e) => {
          e.stopPropagation();
          setBranchOpen((v) => !v);
        }}
      >
        {branch}
      </button>

      {/* Write badge for active repo */}
      {isActive && <span className="ctxbar-chip-write">write</span>}

      {/* Remove button: hidden by default, revealed on hover */}
      <button
        type="button"
        data-chip-action="remove"
        className={"ctxbar-chip-remove" + (hovered ? " ctxbar-chip-remove-visible" : "")}
        onClick={(e) => {
          e.stopPropagation();
          onRemove();
        }}
        title={`Remove ${displayName} from context`}
      >
        <svg width="8" height="8" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
          <line x1="18" y1="6" x2="6" y2="18" />
          <line x1="6" y1="6" x2="18" y2="18" />
        </svg>
      </button>

      {/* BranchPicker in external-anchor mode: dropdown opens immediately,
          positioned from the branch button, fetches all branches from GitHub */}
      {branchOpen && (
        <BranchPicker
          repo={repo}
          currentBranch={branch}
          defaultBranch={defaultBranch}
          sessionBranches={sessionBranches}
          externalAnchorRef={branchBtnRef}
          onBranchChange={(newBranch) => {
            onBranchChange(newBranch);
            setBranchOpen(false);
          }}
          onClose={() => setBranchOpen(false)}
        />
      )}
    </div>
  );
}
