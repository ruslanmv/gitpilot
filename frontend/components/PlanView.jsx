import React from "react";

export default function PlanView({ plan }) {
  if (!plan) return null;

  // Calculate totals for each action type
  const totals = { CREATE: 0, MODIFY: 0, DELETE: 0 };
  plan.steps.forEach((step) => {
    step.files.forEach((file) => {
      totals[file.action] = (totals[file.action] || 0) + 1;
    });
  });

  const theme = {
    bg: "#18181B",
    border: "#27272A",
    textPrimary: "#EDEDED",
    textSecondary: "#A1A1AA",
    successBg: "rgba(16, 185, 129, 0.1)",
    successText: "#10B981",
    warningBg: "rgba(245, 158, 11, 0.1)",
    warningText: "#F59E0B",
    dangerBg: "rgba(239, 68, 68, 0.1)",
    dangerText: "#EF4444",
  };

  const styles = {
    container: {
      display: "flex",
      flexDirection: "column",
      gap: "20px",
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    },
    header: {
      display: "flex",
      flexDirection: "column",
      gap: "8px",
      paddingBottom: "16px",
      borderBottom: `1px solid ${theme.border}`,
    },
    goal: {
      fontSize: "14px",
      fontWeight: "600",
      color: theme.textPrimary,
    },
    summary: {
      fontSize: "13px",
      color: theme.textSecondary,
      lineHeight: "1.5",
    },
    totals: {
      display: "flex",
      gap: "12px",
      flexWrap: "wrap",
    },
    totalBadge: {
      fontSize: "11px",
      fontWeight: "500",
      padding: "4px 8px",
      borderRadius: "4px",
      border: "1px solid transparent",
    },
    totalCreate: {
      backgroundColor: theme.successBg,
      color: theme.successText,
      borderColor: "rgba(16, 185, 129, 0.2)",
    },
    totalModify: {
      backgroundColor: theme.warningBg,
      color: theme.warningText,
      borderColor: "rgba(245, 158, 11, 0.2)",
    },
    totalDelete: {
      backgroundColor: theme.dangerBg,
      color: theme.dangerText,
      borderColor: "rgba(239, 68, 68, 0.2)",
    },
    stepsList: {
      listStyle: "none",
      padding: 0,
      margin: 0,
      display: "flex",
      flexDirection: "column",
      gap: "24px",
    },
    step: {
      display: "flex",
      flexDirection: "column",
      gap: "8px",
      position: "relative",
    },
    stepHeader: {
      display: "flex",
      alignItems: "baseline",
      gap: "8px",
      fontSize: "13px",
      fontWeight: "600",
      color: theme.textPrimary,
    },
    stepNumber: {
      color: theme.textSecondary,
      fontSize: "11px",
      textTransform: "uppercase",
      letterSpacing: "0.05em",
    },
    stepDescription: {
      fontSize: "13px",
      color: theme.textSecondary,
      lineHeight: "1.5",
      margin: 0,
    },
    fileList: {
      marginTop: "8px",
      display: "flex",
      flexDirection: "column",
      gap: "4px",
      backgroundColor: "#131316",
      padding: "8px 12px",
      borderRadius: "6px",
      border: `1px solid ${theme.border}`,
    },
    fileItem: {
      display: "flex",
      alignItems: "center",
      gap: "10px",
      fontSize: "12px",
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    },
    actionBadge: {
      padding: "2px 6px",
      borderRadius: "4px",
      fontSize: "10px",
      fontWeight: "bold",
      textTransform: "uppercase",
      minWidth: "55px",
      textAlign: "center",
      letterSpacing: "0.02em",
    },
    path: {
      color: "#D4D4D8",
      whiteSpace: "nowrap",
      overflow: "hidden",
      textOverflow: "ellipsis",
    },
    risks: {
      marginTop: "8px",
      fontSize: "12px",
      color: theme.warningText,
      backgroundColor: "rgba(245, 158, 11, 0.05)",
      padding: "8px 12px",
      borderRadius: "6px",
      border: "1px solid rgba(245, 158, 11, 0.1)",
      display: "flex",
      gap: "6px",
      alignItems: "flex-start",
    },
  };

  const getActionStyle = (action) => {
    switch (action) {
      case "CREATE": return styles.totalCreate;
      case "MODIFY": return styles.totalModify;
      case "DELETE": return styles.totalDelete;
      default: return {};
    }
  };

  return (
    <div style={styles.container}>
      {/* Header & Summary */}
      <div style={styles.header}>
        <div style={styles.goal}>Goal: {plan.goal}</div>
        <div style={styles.summary}>{plan.summary}</div>
      </div>

      {/* Totals Summary */}
      <div style={styles.totals}>
        {totals.CREATE > 0 && (
          <span style={{ ...styles.totalBadge, ...styles.totalCreate }}>
            {totals.CREATE} to create
          </span>
        )}
        {totals.MODIFY > 0 && (
          <span style={{ ...styles.totalBadge, ...styles.totalModify }}>
            {totals.MODIFY} to modify
          </span>
        )}
        {totals.DELETE > 0 && (
          <span style={{ ...styles.totalBadge, ...styles.totalDelete }}>
            {totals.DELETE} to delete
          </span>
        )}
      </div>

      {/* Steps List */}
      <ol style={styles.stepsList}>
        {plan.steps.map((s) => (
          <li key={s.step_number} style={styles.step}>
            <div style={styles.stepHeader}>
              <span style={styles.stepNumber}>Step {s.step_number}</span>
              <span>{s.title}</span>
            </div>
            <p style={styles.stepDescription}>{s.description}</p>

            {/* Files List */}
            {s.files && s.files.length > 0 && (
              <div style={styles.fileList}>
                {s.files.map((file, idx) => (
                  <div key={idx} style={styles.fileItem}>
                    <span style={{ ...styles.actionBadge, ...getActionStyle(file.action) }}>
                      {file.action}
                    </span>
                    <span style={styles.path}>{file.path}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Risks */}
            {s.risks && (
              <div style={styles.risks}>
                <span>⚠️</span>
                <span>{s.risks}</span>
              </div>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}