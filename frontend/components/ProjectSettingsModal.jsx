import React, { useEffect, useMemo, useState } from "react";
import ContextTab from "./ProjectSettings/ContextTab.jsx";
import UseCaseTab from "./ProjectSettings/UseCaseTab.jsx";
import ConventionsTab from "./ProjectSettings/ConventionsTab.jsx";
import EnvironmentSelector from "./EnvironmentSelector.jsx";

export default function ProjectSettingsModal({
  owner,
  repo,
  isOpen,
  onClose,
  activeEnvId,
  onEnvChange,
}) {
  const [activeTab, setActiveTab] = useState("context");

  useEffect(() => {
    if (!isOpen) return;
    // reset to Context each time opened (safe default)
    setActiveTab("context");
  }, [isOpen]);

  const title = useMemo(() => {
    const repoLabel = owner && repo ? `${owner}/${repo}` : "Project";
    return `Project Settings — ${repoLabel}`;
  }, [owner, repo]);

  if (!isOpen) return null;

  return (
    <div
      style={styles.backdrop}
      onMouseDown={(e) => {
        // click outside closes
        if (e.target === e.currentTarget) onClose?.();
      }}
    >
      <div style={styles.modal} onMouseDown={(e) => e.stopPropagation()}>
        <div style={styles.header}>
          <div style={styles.headerLeft}>
            <div style={styles.title}>{title}</div>
            <div style={styles.subtitle}>
              Manage context, use cases, and project conventions (additive only).
            </div>
          </div>
          <button style={styles.closeBtn} onClick={onClose}>
            ✕
          </button>
        </div>

        <div style={styles.tabsRow}>
          <TabButton
            label="Context"
            isActive={activeTab === "context"}
            onClick={() => setActiveTab("context")}
          />
          <TabButton
            label="Use Case"
            isActive={activeTab === "usecase"}
            onClick={() => setActiveTab("usecase")}
          />
          <TabButton
            label="Conventions"
            isActive={activeTab === "conventions"}
            onClick={() => setActiveTab("conventions")}
          />
          <TabButton
            label="Environment"
            isActive={activeTab === "environment"}
            onClick={() => setActiveTab("environment")}
          />
        </div>

        <div style={styles.body}>
          {activeTab === "context" && <ContextTab owner={owner} repo={repo} />}
          {activeTab === "usecase" && <UseCaseTab owner={owner} repo={repo} />}
          {activeTab === "conventions" && (
            <ConventionsTab owner={owner} repo={repo} />
          )}
          {activeTab === "environment" && (
            <div style={{ maxWidth: 480 }}>
              <div style={{ marginBottom: 12, fontSize: 13, color: "rgba(255,255,255,0.65)" }}>
                Select and configure the execution environment for agent operations.
              </div>
              <EnvironmentSelector
                activeEnvId={activeEnvId}
                onEnvChange={onEnvChange}
              />
            </div>
          )}
        </div>

        <div style={styles.footer}>
          <div style={styles.footerHint}>
            Tip: Upload meeting notes/transcripts in Context, then finalize a Use
            Case spec.
          </div>
          <button style={styles.primaryBtn} onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

function TabButton({ label, isActive, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        ...styles.tabBtn,
        ...(isActive ? styles.tabBtnActive : {}),
      }}
    >
      {label}
    </button>
  );
}

const styles = {
  backdrop: {
    position: "fixed",
    inset: 0,
    background: "rgba(0,0,0,0.45)",
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
    zIndex: 9999,
    padding: 16,
  },
  modal: {
    width: "min(1100px, 96vw)",
    height: "min(760px, 90vh)",
    background: "#111",
    border: "1px solid rgba(255,255,255,0.12)",
    borderRadius: 12,
    overflow: "hidden",
    display: "flex",
    flexDirection: "column",
    boxShadow: "0 12px 40px rgba(0,0,0,0.35)",
  },
  header: {
    padding: "14px 14px 10px",
    display: "flex",
    gap: 12,
    alignItems: "flex-start",
    justifyContent: "space-between",
    borderBottom: "1px solid rgba(255,255,255,0.10)",
    background: "linear-gradient(180deg, rgba(255,255,255,0.04), transparent)",
  },
  headerLeft: {
    display: "flex",
    flexDirection: "column",
    gap: 4,
    minWidth: 0,
  },
  title: {
    fontSize: 16,
    fontWeight: 700,
    color: "#fff",
    lineHeight: 1.2,
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
    maxWidth: "88vw",
  },
  subtitle: {
    fontSize: 12,
    color: "rgba(255,255,255,0.65)",
  },
  closeBtn: {
    background: "transparent",
    border: "1px solid rgba(255,255,255,0.18)",
    color: "rgba(255,255,255,0.85)",
    borderRadius: 10,
    padding: "6px 10px",
    cursor: "pointer",
  },
  tabsRow: {
    display: "flex",
    gap: 8,
    padding: 10,
    borderBottom: "1px solid rgba(255,255,255,0.10)",
    background: "rgba(255,255,255,0.02)",
  },
  tabBtn: {
    background: "transparent",
    border: "1px solid rgba(255,255,255,0.14)",
    color: "rgba(255,255,255,0.75)",
    borderRadius: 999,
    padding: "8px 12px",
    cursor: "pointer",
    fontSize: 13,
  },
  tabBtnActive: {
    border: "1px solid rgba(255,255,255,0.28)",
    color: "#fff",
    background: "rgba(255,255,255,0.06)",
  },
  body: {
    flex: 1,
    overflow: "auto",
    padding: 12,
  },
  footer: {
    padding: 12,
    borderTop: "1px solid rgba(255,255,255,0.10)",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
    background: "rgba(255,255,255,0.02)",
  },
  footerHint: {
    color: "rgba(255,255,255,0.6)",
    fontSize: 12,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  primaryBtn: {
    background: "rgba(255,255,255,0.10)",
    border: "1px solid rgba(255,255,255,0.20)",
    color: "#fff",
    borderRadius: 10,
    padding: "8px 12px",
    cursor: "pointer",
  },
};
