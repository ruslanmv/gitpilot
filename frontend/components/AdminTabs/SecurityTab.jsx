// frontend/components/AdminTabs/SecurityTab.jsx
import React, { useState } from "react";
import { scanWorkspace } from "../../utils/api.js";

/**
 * Security tab — runs a workspace scan via /api/security/scan-workspace
 * and renders findings grouped by severity.
 *
 * Best practices applied:
 * - Custom path input (defaults to ".")
 * - Loading spinner while scanning
 * - Error state with retry
 * - Empty state ("No findings") with green checkmark
 * - Findings grouped by severity (critical → info)
 * - Each finding shows file, line, CWE, recommendation
 * - Color-coded severity badges
 */

const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"];

const SEVERITY_COLORS = {
  critical: { bg: "#7f1d1d", text: "#fecaca", border: "#991b1b" },
  high:     { bg: "#9a3412", text: "#fed7aa", border: "#c2410c" },
  medium:   { bg: "#78350f", text: "#fde68a", border: "#a16207" },
  low:      { bg: "#164e63", text: "#a5f3fc", border: "#0e7490" },
  info:     { bg: "#1e3a5f", text: "#93c5fd", border: "#3B82F6" },
};

function SeverityBadge({ severity }) {
  const c = SEVERITY_COLORS[severity] || SEVERITY_COLORS.info;
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        background: c.bg,
        color: c.text,
        border: `1px solid ${c.border}`,
        borderRadius: "10px",
        fontSize: "10px",
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.5px",
      }}
    >
      {severity}
    </span>
  );
}

export default function SecurityTab({ showToast }) {
  const [path, setPath] = useState(".");
  const [scanning, setScanning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const handleScan = async () => {
    setScanning(true);
    setError(null);
    setResult(null);
    try {
      const data = await scanWorkspace(path.trim() || ".");
      setResult(data);
      const findingsCount = data.findings?.length || 0;
      showToast?.(
        "Scan complete",
        findingsCount === 0
          ? "No security findings."
          : `Found ${findingsCount} issue${findingsCount !== 1 ? "s" : ""}.`
      );
    } catch (err) {
      setError(err?.message || "Scan failed");
    } finally {
      setScanning(false);
    }
  };

  // Group findings by severity
  const grouped = React.useMemo(() => {
    const out = {};
    if (result?.findings) {
      for (const f of result.findings) {
        const sev = f.severity || "info";
        if (!out[sev]) out[sev] = [];
        out[sev].push(f);
      }
    }
    return out;
  }, [result]);

  const totalFindings = result?.findings?.length || 0;

  return (
    <div>
      <h3 style={{ marginBottom: "16px" }}>Security Scanning</h3>
      <p style={{ fontSize: "12px", opacity: 0.7, marginBottom: "16px" }}>
        Scan your workspace for vulnerabilities, secrets, and insecure patterns (OWASP Top 10).
      </p>

      {/* Scan controls */}
      <div
        style={{
          background: "#1a1b26",
          borderRadius: "8px",
          padding: "16px",
          border: "1px solid #2a2b36",
          marginBottom: "16px",
          display: "flex",
          gap: "8px",
          alignItems: "flex-end",
        }}
      >
        <div style={{ flex: 1 }}>
          <label
            htmlFor="security-scan-path"
            style={{
              fontSize: "11px",
              opacity: 0.7,
              display: "block",
              marginBottom: "4px",
            }}
          >
            Path to scan (relative or absolute)
          </label>
          <input
            id="security-scan-path"
            type="text"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            disabled={scanning}
            placeholder="."
            style={{
              width: "100%",
              padding: "8px 10px",
              background: "#0d0e15",
              border: "1px solid #2a2b36",
              borderRadius: "4px",
              color: "#fff",
              fontSize: "12px",
              fontFamily: "monospace",
            }}
          />
        </div>
        <button
          type="button"
          onClick={handleScan}
          disabled={scanning}
          style={{
            padding: "8px 16px",
            background: scanning ? "#555" : "#3B82F6",
            color: "#fff",
            border: "none",
            borderRadius: "4px",
            cursor: scanning ? "not-allowed" : "pointer",
            fontSize: "12px",
            fontWeight: 600,
            whiteSpace: "nowrap",
          }}
        >
          {scanning ? "Scanning..." : "Scan Workspace"}
        </button>
      </div>

      {/* Error state */}
      {error && (
        <div
          role="alert"
          style={{
            background: "#7f1d1d",
            color: "#fecaca",
            border: "1px solid #991b1b",
            borderRadius: "8px",
            padding: "12px",
            fontSize: "12px",
            marginBottom: "16px",
          }}
        >
          <strong>Scan failed: </strong>
          {error}
        </div>
      )}

      {/* Results summary */}
      {result && (
        <div
          style={{
            background: "#1a1b26",
            borderRadius: "8px",
            padding: "16px",
            border: "1px solid #2a2b36",
            marginBottom: "16px",
          }}
        >
          <div style={{ display: "flex", gap: "24px", fontSize: "12px" }}>
            <div>
              <div style={{ opacity: 0.6 }}>Files Scanned</div>
              <div style={{ fontSize: "18px", fontWeight: 600 }}>
                {result.files_scanned ?? 0}
              </div>
            </div>
            <div>
              <div style={{ opacity: 0.6 }}>Total Findings</div>
              <div
                style={{
                  fontSize: "18px",
                  fontWeight: 600,
                  color: totalFindings === 0 ? "#4ade80" : "#fcd34d",
                }}
              >
                {totalFindings}
              </div>
            </div>
            <div>
              <div style={{ opacity: 0.6 }}>Duration</div>
              <div style={{ fontSize: "18px", fontWeight: 600 }}>
                {result.scan_duration_ms ?? 0}ms
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Empty state — no findings */}
      {result && totalFindings === 0 && (
        <div
          style={{
            background: "#064e3b",
            color: "#a7f3d0",
            border: "1px solid #065f46",
            borderRadius: "8px",
            padding: "20px",
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: "32px", marginBottom: "8px" }}>✓</div>
          <div style={{ fontSize: "14px", fontWeight: 600 }}>
            No security issues found
          </div>
          <div style={{ fontSize: "12px", opacity: 0.8, marginTop: "4px" }}>
            Your workspace passed all {result.files_scanned ?? 0} file checks.
          </div>
        </div>
      )}

      {/* Findings grouped by severity */}
      {totalFindings > 0 &&
        SEVERITY_ORDER.filter((sev) => grouped[sev]?.length > 0).map((sev) => (
          <div key={sev} style={{ marginBottom: "16px" }}>
            <h4
              style={{
                fontSize: "13px",
                marginBottom: "8px",
                display: "flex",
                alignItems: "center",
                gap: "8px",
              }}
            >
              <SeverityBadge severity={sev} />
              <span>
                {grouped[sev].length} {sev} issue{grouped[sev].length !== 1 ? "s" : ""}
              </span>
            </h4>
            <div style={{ display: "grid", gap: "8px" }}>
              {grouped[sev].map((f, idx) => (
                <div
                  key={`${f.rule_id}-${f.file_path}-${f.line_number}-${idx}`}
                  style={{
                    background: "#1a1b26",
                    borderRadius: "8px",
                    padding: "12px",
                    border: `1px solid ${SEVERITY_COLORS[sev]?.border || "#2a2b36"}`,
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "flex-start",
                      marginBottom: "6px",
                    }}
                  >
                    <div style={{ fontSize: "13px", fontWeight: 600 }}>{f.title}</div>
                    {f.cwe_id && (
                      <span
                        style={{
                          fontSize: "10px",
                          opacity: 0.6,
                          fontFamily: "monospace",
                        }}
                      >
                        {f.cwe_id}
                      </span>
                    )}
                  </div>
                  <div
                    style={{
                      fontSize: "11px",
                      fontFamily: "monospace",
                      opacity: 0.7,
                      marginBottom: "6px",
                    }}
                  >
                    {f.file_path}:{f.line_number}
                  </div>
                  {f.snippet && (
                    <pre
                      style={{
                        fontSize: "11px",
                        background: "#0d0e15",
                        padding: "8px",
                        borderRadius: "4px",
                        overflowX: "auto",
                        margin: "6px 0",
                        color: "#e0e7ff",
                      }}
                    >
                      {f.snippet}
                    </pre>
                  )}
                  {f.recommendation && (
                    <div
                      style={{
                        fontSize: "11px",
                        opacity: 0.8,
                        marginTop: "6px",
                        paddingTop: "6px",
                        borderTop: "1px solid #2a2b36",
                      }}
                    >
                      <strong>Fix: </strong>
                      {f.recommendation}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
    </div>
  );
}
