// frontend/components/AdminTabs/MCPServersTab.jsx
//
// Settings tab for managing MCP Context Forge servers.
//
// UX layout (mirrors industry-standard plugin/extension managers):
//
//   ┌─ Header ─ gateway pill, totals, global "MCP enabled" toggle ─┐
//   ├─ Sub-tabs: Installed · Catalog · Custom · Gateway ──────────┤
//   ├─ ServerCard list (Installed)                                 │
//   │     ▸ status / description / tags / tool count               │
//   │     ▸ Test · Configure · Disable · Uninstall                 │
//   │     ▸ Expandable per-tool list with risk badges + toggles    │
//   └──────────────────────────────────────────────────────────────┘

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiUrl, safeFetchJSON } from "../../utils/api.js";

import ServerCard from "./mcp/ServerCard.jsx";
import CatalogList from "./mcp/CatalogList.jsx";
import CustomInstallForm from "./mcp/CustomInstallForm.jsx";
import GatewayHeader from "./mcp/GatewayHeader.jsx";
import GatewayForm from "./mcp/GatewayForm.jsx";
import SyncReport from "./mcp/SyncReport.jsx";

const TAB_INSTALLED = "installed";
const TAB_CATALOG = "catalog";
const TAB_CUSTOM = "custom";
const TAB_GATEWAY = "gateway";

export default function MCPServersTab({ showToast }) {
  const [activeSubTab, setActiveSubTab] = useState(TAB_INSTALLED);
  const [status, setStatus] = useState(null);
  const [servers, setServers] = useState([]);
  const [catalog, setCatalog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [syncReport, setSyncReport] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusData, serversData, catalogData] = await Promise.all([
        safeFetchJSON(apiUrl("/api/mcp/status"), { timeout: 5000 }),
        safeFetchJSON(apiUrl("/api/mcp/servers"), { timeout: 5000 }),
        safeFetchJSON(apiUrl("/api/mcp/catalog"), { timeout: 5000 }),
      ]);
      setStatus(statusData);
      setServers(serversData?.servers || []);
      setCatalog(catalogData?.items || []);
    } catch (err) {
      setError(err?.message || "Failed to load MCP server state");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const post = useCallback(
    async (path, body) => {
      try {
        const res = await fetch(apiUrl(path), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: body ? JSON.stringify(body) : undefined,
        });
        if (!res.ok) {
          const detail = await res.json().catch(() => ({}));
          throw new Error(detail?.detail || `HTTP ${res.status}`);
        }
        return await res.json();
      } catch (err) {
        showToast?.("MCP error", err?.message || String(err));
        throw err;
      }
    },
    [showToast]
  );

  // ---- Server actions -----------------------------------------------------
  const onEnableServer = async (id) => {
    await post(`/api/mcp/servers/${id}/enable`);
    showToast?.("Server enabled", id);
    await refresh();
  };
  const onDisableServer = async (id) => {
    await post(`/api/mcp/servers/${id}/disable`);
    showToast?.("Server disabled", id);
    await refresh();
  };
  const onUninstallServer = async (id) => {
    if (
      !window.confirm(
        `Uninstall ${id}? GitPilot will stop calling its tools immediately.`
      )
    )
      return;
    await post(`/api/mcp/servers/${id}/uninstall`);
    showToast?.("Server uninstalled", id);
    await refresh();
  };
  const onTestServer = async (id) => {
    const result = await post(`/api/mcp/servers/${id}/test`);
    if (result?.ok) {
      showToast?.("Server healthy", id);
    } else {
      showToast?.(
        "Server unreachable",
        result?.reason || result?.error || "Unknown error"
      );
    }
    return result;
  };
  const onSync = useCallback(async () => {
    setSyncing(true);
    setSyncReport(null);
    try {
      const report = await post("/api/mcp/sync", {});
      setSyncReport(report);
      const total =
        (report.added?.length || 0) +
        (report.kept?.length || 0) +
        (report.orphaned?.length || 0);
      showToast?.(
        report.forge_unreachable ? "Sync failed" : "Sync complete",
        report.forge_unreachable
          ? report.error || "forge unreachable"
          : `+${report.added?.length || 0} added · ${total} total`
      );
      await refresh();
    } catch {
      // post() already toasted; nothing more to do.
    } finally {
      setSyncing(false);
    }
  }, [post, refresh, showToast]);

  const onForgetOrphan = async (id) => {
    if (
      !window.confirm(
        `Forget ${id}? It will be removed from the local list.\n` +
          "Re-attach it to MCP Context Forge then click Sync to bring it back."
      )
    )
      return;
    await post(`/api/mcp/servers/${id}/forget`);
    showToast?.("Server forgotten", id);
    await refresh();
  };

  const onToggleTool = async (serverId, toolName, enabled) => {
    await post(
      `/api/mcp/servers/${serverId}/tools/${encodeURIComponent(
        toolName
      )}/toggle`,
      { enabled }
    );
    await refresh();
  };

  const onInstallFromCatalog = async (serverId) => {
    await post("/api/mcp/servers/install", { server_id: serverId });
    showToast?.("Installed", `${serverId} (disabled until you enable it)`);
    await refresh();
    setActiveSubTab(TAB_INSTALLED);
  };

  const onInstallCustom = async (registerJson) => {
    await post("/api/mcp/servers/install-custom", {
      register_json: registerJson,
    });
    showToast?.("Custom server added", registerJson.name);
    await refresh();
    setActiveSubTab(TAB_INSTALLED);
  };

  // ---- Derived totals -----------------------------------------------------
  const installedCount = servers.filter((s) => s.installed).length;
  const enabledCount = servers.filter((s) => s.installed && s.enabled).length;
  const totalTools = useMemo(
    () => servers.reduce((acc, s) => acc + (s.tool_count || 0), 0),
    [servers]
  );

  return (
    <div>
      <GatewayHeader
        status={status}
        installedCount={installedCount}
        enabledCount={enabledCount}
        totalTools={totalTools}
        onRefresh={refresh}
        onSync={onSync}
        syncing={syncing}
      />

      {syncReport && (
        <SyncReport
          report={syncReport}
          onDismiss={() => setSyncReport(null)}
        />
      )}

      {/* Sub-tab strip */}
      <div
        role="tablist"
        style={{
          display: "flex",
          gap: "4px",
          marginBottom: "16px",
          borderBottom: "1px solid #2a2b36",
        }}
      >
        {[
          { id: TAB_INSTALLED, label: `Installed (${installedCount})` },
          { id: TAB_CATALOG, label: `Catalog (${catalog.length})` },
          { id: TAB_CUSTOM, label: "Custom" },
          { id: TAB_GATEWAY, label: "Gateway" },
        ].map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={activeSubTab === t.id}
            onClick={() => setActiveSubTab(t.id)}
            style={{
              padding: "10px 16px",
              border: "none",
              background: "transparent",
              color: activeSubTab === t.id ? "#93c5fd" : "#a0a0b0",
              borderBottom:
                activeSubTab === t.id
                  ? "2px solid #3B82F6"
                  : "2px solid transparent",
              cursor: "pointer",
              fontSize: "13px",
              fontWeight: activeSubTab === t.id ? 600 : 400,
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && (
        <div
          role="alert"
          style={{
            padding: "12px",
            background: "#5c1a1a",
            border: "1px solid #8a2a2a",
            borderRadius: "6px",
            marginBottom: "16px",
            color: "#fecaca",
            fontSize: "13px",
          }}
        >
          {error}
        </div>
      )}

      {loading && !servers.length && (
        <p style={{ color: "#a0a0b0", fontSize: "13px" }}>Loading…</p>
      )}

      {activeSubTab === TAB_INSTALLED && !loading && (
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          {servers.length === 0 && (
            <EmptyState
              title="No MCP servers installed"
              hint="Browse the catalog or paste a register.json under Custom."
              actionLabel="Browse catalog"
              onAction={() => setActiveSubTab(TAB_CATALOG)}
            />
          )}
          {servers.map((s) => (
            <ServerCard
              key={s.id}
              server={s}
              onEnable={() => onEnableServer(s.id)}
              onDisable={() => onDisableServer(s.id)}
              onUninstall={() => onUninstallServer(s.id)}
              onTest={() => onTestServer(s.id)}
              onForget={s.orphan ? () => onForgetOrphan(s.id) : undefined}
              onToggleTool={(tool, enabled) =>
                onToggleTool(s.id, tool, enabled)
              }
            />
          ))}
        </div>
      )}

      {activeSubTab === TAB_CATALOG && !loading && (
        <CatalogList items={catalog} onInstall={onInstallFromCatalog} />
      )}

      {activeSubTab === TAB_CUSTOM && (
        <CustomInstallForm onSubmit={onInstallCustom} />
      )}

      {activeSubTab === TAB_GATEWAY && (
        <GatewayForm
          showToast={showToast}
          // A new gateway means a different registry: reload so the Installed
          // list describes the gateway you just pointed at, not the old one.
          onSaved={refresh}
        />
      )}
    </div>
  );
}

function EmptyState({ title, hint, actionLabel, onAction }) {
  return (
    <div
      style={{
        padding: "32px",
        background: "#1a1b26",
        border: "1px dashed #2a2b36",
        borderRadius: "8px",
        textAlign: "center",
      }}
    >
      <h4 style={{ margin: "0 0 8px 0", color: "#e0e0e7" }}>{title}</h4>
      <p style={{ margin: "0 0 16px 0", color: "#a0a0b0", fontSize: "13px" }}>
        {hint}
      </p>
      {actionLabel && (
        <button
          onClick={onAction}
          style={{
            padding: "8px 16px",
            background: "#3B82F6",
            color: "#fff",
            border: "none",
            borderRadius: "6px",
            cursor: "pointer",
            fontSize: "13px",
          }}
        >
          {actionLabel}
        </button>
      )}
    </div>
  );
}
