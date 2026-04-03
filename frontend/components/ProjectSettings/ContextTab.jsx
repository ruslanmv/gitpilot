import React, { useEffect, useMemo, useRef, useState } from "react";

export default function ContextTab({ owner, repo }) {
  const [assets, setAssets] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [uploadHint, setUploadHint] = useState("");
  const inputRef = useRef(null);

  const canUse = useMemo(() => Boolean(owner && repo), [owner, repo]);

  async function loadAssets() {
    if (!canUse) return;
    setError("");
    try {
      const res = await fetch(`/api/repos/${owner}/${repo}/context/assets`);
      if (!res.ok) throw new Error(`Failed to list assets (${res.status})`);
      const data = await res.json();
      setAssets(data.assets || []);
    } catch (e) {
      setError(e?.message || "Failed to load assets");
    }
  }

  useEffect(() => {
    loadAssets();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [owner, repo]);

  async function uploadFiles(fileList) {
    if (!canUse) return;
    const files = Array.from(fileList || []);
    if (!files.length) return;

    setBusy(true);
    setError("");
    setUploadHint(`Uploading ${files.length} file(s)...`);

    try {
      for (const f of files) {
        const form = new FormData();
        form.append("file", f);

        const res = await fetch(
          `/api/repos/${owner}/${repo}/context/assets/upload`,
          { method: "POST", body: form }
        );
        if (!res.ok) {
          const txt = await res.text().catch(() => "");
          throw new Error(`Upload failed (${res.status}) ${txt}`);
        }
      }
      setUploadHint("Upload complete. Refreshing list...");
      await loadAssets();
      setUploadHint("");
    } catch (e) {
      setError(e?.message || "Upload failed");
      setUploadHint("");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function deleteAsset(assetId) {
    if (!canUse) return;
    const ok = window.confirm("Delete this asset? This cannot be undone.");
    if (!ok) return;

    setBusy(true);
    setError("");
    try {
      const res = await fetch(
        `/api/repos/${owner}/${repo}/context/assets/${assetId}`,
        { method: "DELETE" }
      );
      if (!res.ok) throw new Error(`Delete failed (${res.status})`);
      await loadAssets();
    } catch (e) {
      setError(e?.message || "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  function downloadAsset(assetId) {
    if (!canUse) return;
    window.open(
      `/api/repos/${owner}/${repo}/context/assets/${assetId}/download`,
      "_blank"
    );
  }

  const empty = !assets || assets.length === 0;

  return (
    <div style={styles.wrap}>
      <div style={styles.topRow}>
        <div style={styles.left}>
          <div style={styles.h1}>Project Context</div>
          <div style={styles.h2}>
            Upload documents, transcripts, screenshots, etc. (non-destructive,
            additive).
          </div>
        </div>

        <div style={styles.right}>
          <input
            ref={inputRef}
            type="file"
            multiple
            disabled={!canUse || busy}
            onChange={(e) => uploadFiles(e.target.files)}
            style={styles.fileInput}
          />
          <button
            style={styles.btn}
            disabled={!canUse || busy}
            onClick={() => inputRef.current?.click()}
          >
            Upload
          </button>
          <button
            style={styles.btn}
            disabled={!canUse || busy}
            onClick={loadAssets}
          >
            Refresh
          </button>
        </div>
      </div>

      <div
        style={styles.dropzone}
        onDragOver={(e) => {
          e.preventDefault();
          e.stopPropagation();
        }}
        onDrop={(e) => {
          e.preventDefault();
          e.stopPropagation();
          if (busy) return;
          uploadFiles(e.dataTransfer.files);
        }}
      >
        <div style={styles.dropText}>
          Drag & drop files here, or click <b>Upload</b>.
        </div>
        <div style={styles.dropSub}>
          Tip: For audio/video, upload a transcript file too.
        </div>
      </div>

      {uploadHint ? <div style={styles.hint}>{uploadHint}</div> : null}
      {error ? <div style={styles.error}>{error}</div> : null}

      <div style={styles.tableWrap}>
        <div style={styles.tableHeader}>
          <div style={{ ...styles.col, ...styles.colName }}>File</div>
          <div style={{ ...styles.col, ...styles.colMeta }}>Type</div>
          <div style={{ ...styles.col, ...styles.colMeta }}>Size</div>
          <div style={{ ...styles.col, ...styles.colMeta }}>Indexed</div>
          <div style={{ ...styles.col, ...styles.colActions }}>Actions</div>
        </div>

        {empty ? (
          <div style={styles.empty}>
            No context assets yet. Upload docs, transcripts, and screenshots to
            improve planning quality.
          </div>
        ) : (
          assets.map((a) => (
            <div key={a.asset_id} style={styles.row}>
              <div style={{ ...styles.col, ...styles.colName }}>
                <div style={styles.fileName}>{a.filename}</div>
                <div style={styles.small}>
                  Added: {a.created_at || "-"} | Extracted:{" "}
                  {Number(a.extracted_chars || 0).toLocaleString()} chars
                </div>
              </div>

              <div style={{ ...styles.col, ...styles.colMeta }}>
                <span style={styles.badge}>{a.mime || "unknown"}</span>
              </div>

              <div style={{ ...styles.col, ...styles.colMeta }}>
                {formatBytes(a.size_bytes || 0)}
              </div>

              <div style={{ ...styles.col, ...styles.colMeta }}>
                {a.indexed_chunks || 0} chunks
              </div>

              <div style={{ ...styles.col, ...styles.colActions }}>
                <button
                  style={styles.smallBtn}
                  disabled={busy}
                  onClick={() => downloadAsset(a.asset_id)}
                >
                  Download
                </button>
                <button
                  style={{ ...styles.smallBtn, ...styles.dangerBtn }}
                  disabled={busy}
                  onClick={() => deleteAsset(a.asset_id)}
                >
                  Delete
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function formatBytes(bytes) {
  const b = Number(bytes || 0);
  if (!b) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = b;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

const styles = {
  wrap: { display: "flex", flexDirection: "column", gap: 12 },
  topRow: {
    display: "flex",
    justifyContent: "space-between",
    gap: 12,
    alignItems: "flex-start",
    flexWrap: "wrap",
  },
  left: { minWidth: 280 },
  right: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" },
  h1: { fontSize: 14, fontWeight: 800, color: "#fff" },
  h2: { fontSize: 12, color: "rgba(255,255,255,0.65)", marginTop: 4 },
  fileInput: { display: "none" },
  btn: {
    background: "rgba(255,255,255,0.10)",
    border: "1px solid rgba(255,255,255,0.18)",
    color: "#fff",
    borderRadius: 10,
    padding: "8px 10px",
    cursor: "pointer",
    fontSize: 13,
  },
  dropzone: {
    border: "1px dashed rgba(255,255,255,0.22)",
    borderRadius: 12,
    padding: 16,
    background: "rgba(255,255,255,0.03)",
  },
  dropText: { color: "rgba(255,255,255,0.85)", fontSize: 13 },
  dropSub: { color: "rgba(255,255,255,0.55)", fontSize: 12, marginTop: 6 },
  hint: {
    color: "rgba(255,255,255,0.75)",
    fontSize: 12,
    padding: "8px 10px",
    border: "1px solid rgba(255,255,255,0.12)",
    borderRadius: 10,
    background: "rgba(255,255,255,0.03)",
  },
  error: {
    color: "#ffb3b3",
    fontSize: 12,
    padding: "8px 10px",
    border: "1px solid rgba(255,120,120,0.25)",
    borderRadius: 10,
    background: "rgba(255,80,80,0.08)",
  },
  tableWrap: {
    border: "1px solid rgba(255,255,255,0.12)",
    borderRadius: 12,
    overflow: "hidden",
  },
  tableHeader: {
    display: "grid",
    gridTemplateColumns: "1.6fr 1fr 0.6fr 0.6fr 0.8fr",
    gap: 0,
    padding: "10px 12px",
    background: "rgba(255,255,255,0.03)",
    borderBottom: "1px solid rgba(255,255,255,0.10)",
    fontSize: 12,
    color: "rgba(255,255,255,0.65)",
  },
  row: {
    display: "grid",
    gridTemplateColumns: "1.6fr 1fr 0.6fr 0.6fr 0.8fr",
    padding: "10px 12px",
    borderBottom: "1px solid rgba(255,255,255,0.08)",
    alignItems: "center",
  },
  col: { minWidth: 0 },
  colName: {},
  colMeta: { color: "rgba(255,255,255,0.75)", fontSize: 12 },
  colActions: { display: "flex", gap: 8, justifyContent: "flex-end" },
  fileName: {
    color: "#fff",
    fontSize: 13,
    fontWeight: 700,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  small: {
    color: "rgba(255,255,255,0.55)",
    fontSize: 11,
    marginTop: 4,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  badge: {
    display: "inline-flex",
    alignItems: "center",
    padding: "2px 8px",
    borderRadius: 999,
    border: "1px solid rgba(255,255,255,0.16)",
    background: "rgba(255,255,255,0.04)",
    fontSize: 11,
    color: "rgba(255,255,255,0.80)",
    maxWidth: "100%",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  smallBtn: {
    background: "rgba(255,255,255,0.08)",
    border: "1px solid rgba(255,255,255,0.16)",
    color: "#fff",
    borderRadius: 10,
    padding: "6px 8px",
    cursor: "pointer",
    fontSize: 12,
  },
  dangerBtn: {
    border: "1px solid rgba(255,90,90,0.35)",
    background: "rgba(255,90,90,0.10)",
  },
  empty: {
    padding: 14,
    color: "rgba(255,255,255,0.65)",
    fontSize: 13,
  },
};
