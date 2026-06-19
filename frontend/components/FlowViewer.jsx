import React, { useEffect, useState, useCallback, useRef } from "react";
import { apiUrl } from "../utils/api.js";
import ReactFlow, { Background, Controls, MiniMap } from "reactflow";
import "reactflow/dist/style.css";

/* ------------------------------------------------------------------ */
/*  Node type → colour mapping                                        */
/* ------------------------------------------------------------------ */
const NODE_COLOURS = {
  agent:      { border: "#ff7a3c", bg: "#20141a" },
  router:     { border: "#6c8cff", bg: "#141828" },
  tool:       { border: "#3a3b4d", bg: "#141821" },
  tool_group: { border: "#3a3b4d", bg: "#141821" },
  user:       { border: "#4caf88", bg: "#14211a" },
  output:     { border: "#9c6cff", bg: "#1a1428" },
};
const DEFAULT_COLOUR = { border: "#3a3b4d", bg: "#141821" };

function colourFor(type) {
  return NODE_COLOURS[type] || DEFAULT_COLOUR;
}

const STYLE_COLOURS = {
  single_task: "#6c8cff",
  react_loop: "#ff7a3c",
  crew_pipeline: "#4caf88",
};

const STYLE_LABELS = {
  single_task: "Dispatch",
  react_loop: "ReAct Loop",
  crew_pipeline: "Pipeline",
};

/* ------------------------------------------------------------------ */
/*  TopologyCard — single clickable topology card                      */
/* ------------------------------------------------------------------ */
function TopologyCard({ topology, isActive, onClick }) {
  const styleColor = STYLE_COLOURS[topology.execution_style] || "#9a9bb0";
  const agentCount = topology.agents_used?.length || 0;

  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        ...cardStyles.card,
        borderColor: isActive ? styleColor : "#1e1f30",
        backgroundColor: isActive ? `${styleColor}0D` : "#0c0d14",
      }}
    >
      <div style={cardStyles.cardTop}>
        <span style={cardStyles.icon}>{topology.icon}</span>
        <span
          style={{
            ...cardStyles.styleBadge,
            color: styleColor,
            borderColor: `${styleColor}40`,
          }}
        >
          {STYLE_LABELS[topology.execution_style] || topology.execution_style}
        </span>
      </div>
      <div
        style={{
          ...cardStyles.name,
          color: isActive ? "#f5f5f7" : "#c3c5dd",
        }}
      >
        {topology.name}
      </div>
      <div style={cardStyles.desc}>{topology.description}</div>
      <div style={cardStyles.agentCount}>
        {agentCount} agent{agentCount !== 1 ? "s" : ""}
      </div>
    </button>
  );
}

const cardStyles = {
  card: {
    display: "flex",
    flexDirection: "column",
    gap: 4,
    padding: "10px 12px",
    borderRadius: 8,
    border: "1px solid #1e1f30",
    cursor: "pointer",
    textAlign: "left",
    minWidth: 170,
    maxWidth: 200,
    flexShrink: 0,
    transition: "border-color 0.2s, background-color 0.2s",
  },
  cardTop: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 6,
  },
  icon: {
    fontSize: 18,
  },
  styleBadge: {
    fontSize: 9,
    fontWeight: 700,
    textTransform: "uppercase",
    letterSpacing: "0.05em",
    padding: "1px 6px",
    borderRadius: 4,
    border: "1px solid",
  },
  name: {
    fontSize: 12,
    fontWeight: 600,
    lineHeight: 1.3,
  },
  desc: {
    fontSize: 10,
    color: "#71717A",
    lineHeight: 1.3,
    overflow: "hidden",
    display: "-webkit-box",
    WebkitLineClamp: 2,
    WebkitBoxOrient: "vertical",
  },
  agentCount: {
    fontSize: 9,
    color: "#52525B",
    fontWeight: 600,
    marginTop: 2,
  },
};

/* ------------------------------------------------------------------ */
/*  TopologyPanel — card grid grouped by category                      */
/* ------------------------------------------------------------------ */
function TopologyPanel({
  topologies,
  activeTopology,
  autoMode,
  autoResult,
  onSelect,
  onToggleAuto,
}) {
  const systems = topologies.filter((t) => t.category === "system");
  const pipelines = topologies.filter((t) => t.category === "pipeline");

  return (
    <div style={panelStyles.root}>
      {/* Auto-detect toggle */}
      <div style={panelStyles.autoRow}>
        <button
          type="button"
          onClick={onToggleAuto}
          style={{
            ...panelStyles.autoBtn,
            borderColor: autoMode ? "#ff7a3c" : "#27272A",
            color: autoMode ? "#ff7a3c" : "#71717A",
            backgroundColor: autoMode ? "rgba(255, 122, 60, 0.06)" : "transparent",
          }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <path d="M12 6v6l4 2" />
          </svg>
          Auto
        </button>
        {autoMode && autoResult && (
          <span style={panelStyles.autoHint}>
            Detected: {autoResult.icon} {autoResult.name}
            {autoResult.confidence != null && (
              <span style={{ opacity: 0.6 }}>
                {" "}({Math.round(autoResult.confidence * 100)}%)
              </span>
            )}
          </span>
        )}
      </div>

      {/* System architectures */}
      <div style={panelStyles.section}>
        <div style={panelStyles.sectionLabel}>System Architectures</div>
        <div style={panelStyles.cardRow}>
          {systems.map((t) => (
            <TopologyCard
              key={t.id}
              topology={t}
              isActive={activeTopology === t.id}
              onClick={() => onSelect(t.id)}
            />
          ))}
        </div>
      </div>

      {/* Task pipelines */}
      <div style={panelStyles.section}>
        <div style={panelStyles.sectionLabel}>Task Pipelines</div>
        <div style={panelStyles.cardRow}>
          {pipelines.map((t) => (
            <TopologyCard
              key={t.id}
              topology={t}
              isActive={activeTopology === t.id}
              onClick={() => onSelect(t.id)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

const panelStyles = {
  root: {
    padding: "8px 16px 12px",
    borderBottom: "1px solid #1e1f30",
    backgroundColor: "#08090e",
  },
  autoRow: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    marginBottom: 10,
  },
  autoBtn: {
    display: "flex",
    alignItems: "center",
    gap: 5,
    padding: "4px 10px",
    borderRadius: 6,
    border: "1px solid #27272A",
    background: "transparent",
    fontSize: 11,
    fontWeight: 600,
    cursor: "pointer",
    transition: "border-color 0.15s, color 0.15s",
  },
  autoHint: {
    fontSize: 11,
    color: "#9a9bb0",
  },
  section: {
    marginBottom: 8,
  },
  sectionLabel: {
    fontSize: 9,
    fontWeight: 700,
    textTransform: "uppercase",
    letterSpacing: "0.08em",
    color: "#52525B",
    marginBottom: 6,
  },
  cardRow: {
    display: "flex",
    gap: 8,
    overflowX: "auto",
    scrollbarWidth: "none",
    paddingBottom: 2,
  },
};

/* ------------------------------------------------------------------ */
/*  Main FlowViewer component                                          */
/* ------------------------------------------------------------------ */
export default function FlowViewer() {
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Topology state
  const [topologies, setTopologies] = useState([]);
  const [activeTopology, setActiveTopology] = useState(null);
  const [topologyMeta, setTopologyMeta] = useState(null);

  // Auto-detection state
  const [autoMode, setAutoMode] = useState(false);
  const [autoResult, setAutoResult] = useState(null);
  const [autoTestMessage, setAutoTestMessage] = useState("");

  const initialLoadDone = useRef(false);

  /* ---------- Load topology list on mount ---------- */
  useEffect(() => {
    (async () => {
      try {
        const [topoRes, prefRes] = await Promise.all([
          fetch(apiUrl("/api/flow/topologies")),
          fetch(apiUrl("/api/settings/topology")),
        ]);
        if (topoRes.ok) {
          const data = await topoRes.json();
          setTopologies(data);
        }
        if (prefRes.ok) {
          const { topology } = await prefRes.json();
          if (topology) {
            setActiveTopology(topology);
          }
        }
      } catch (e) {
        console.warn("Failed to load topologies:", e);
      }
      initialLoadDone.current = true;
    })();
  }, []);

  /* ---------- Load graph when topology changes ---------- */
  const loadGraph = useCallback(async (topologyId) => {
    setLoading(true);
    setError("");
    try {
      const url = topologyId
        ? `/api/flow/current?topology=${encodeURIComponent(topologyId)}`
        : "/api/flow/current";
      const res = await fetch(apiUrl(url));
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to load flow");

      // Track topology metadata from response
      if (data.topology_id) {
        setTopologyMeta({
          id: data.topology_id,
          name: data.topology_name,
          icon: data.topology_icon,
          description: data.topology_description,
          execution_style: data.execution_style,
          agents_used: topologies.find((t) => t.id === data.topology_id)?.agents_used || [],
        });
      }

      // Build ReactFlow nodes
      const RFnodes = data.nodes.map((n, i) => {
        const nodeType = n.type || "default";
        const colour = colourFor(nodeType);
        const d = n.data || {};

        const label = d.label || n.label || n.id;
        const description = d.description || n.description || "";
        const model = d.model;
        const mode = d.mode;

        const pos = n.position || {
          x: 50 + (i % 3) * 250,
          y: 50 + Math.floor(i / 3) * 180,
        };

        return {
          id: n.id,
          data: {
            label: (
              <div style={{ textAlign: "center" }}>
                <div style={{ fontWeight: 600, marginBottom: 2 }}>
                  {label}
                </div>
                {model && (
                  <div style={{ fontSize: 9, color: "#6c8cff", marginBottom: 2, fontFamily: "monospace" }}>
                    {model}
                  </div>
                )}
                {mode && (
                  <div
                    style={{
                      fontSize: 9,
                      color: mode === "read-only" ? "#4caf88" : mode === "git-ops" ? "#9c6cff" : "#ff7a3c",
                      marginBottom: 2,
                    }}
                  >
                    {mode}
                  </div>
                )}
                <div style={{ fontSize: 10, color: "#9a9bb0", maxWidth: 160, lineHeight: 1.3 }}>
                  {description}
                </div>
              </div>
            ),
          },
          position: pos,
          type: "default",
          style: {
            borderRadius: 12,
            padding: "12px 16px",
            border: `2px solid ${colour.border}`,
            background: colour.bg,
            color: "#f5f5f7",
            fontSize: 13,
            minWidth: 180,
            maxWidth: 220,
          },
        };
      });

      // Build ReactFlow edges
      const RFedges = data.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.label,
        animated: e.animated !== false,
        style: { stroke: "#7a7b8e", strokeWidth: 2 },
        labelStyle: { fill: "#c3c5dd", fontSize: 11, fontWeight: 500 },
        labelBgStyle: { fill: "#101117", fillOpacity: 0.9 },
        ...(e.type === "bidirectional" && {
          markerEnd: { type: "arrowclosed", color: "#7a7b8e" },
          markerStart: { type: "arrowclosed", color: "#7a7b8e" },
          animated: false,
          style: { stroke: "#555670", strokeWidth: 1.5, strokeDasharray: "5 5" },
        }),
      }));

      setNodes(RFnodes);
      setEdges(RFedges);
    } catch (e) {
      console.error(e);
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [topologies]);

  // Load graph whenever activeTopology changes
  useEffect(() => {
    loadGraph(activeTopology);
  }, [activeTopology, loadGraph]);

  /* ---------- Topology selection handler ---------- */
  const handleTopologyChange = useCallback(
    async (newTopologyId) => {
      setActiveTopology(newTopologyId);
      setAutoMode(false); // Manual selection disables auto
      // Persist preference (fire-and-forget)
      try {
        await fetch(apiUrl("/api/settings/topology"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ topology: newTopologyId }),
        });
      } catch (e) {
        console.warn("Failed to save topology preference:", e);
      }
    },
    []
  );

  /* ---------- Auto-detection ---------- */
  const handleToggleAuto = useCallback(() => {
    setAutoMode((prev) => !prev);
    if (!autoMode) {
      setAutoResult(null);
    }
  }, [autoMode]);

  const handleAutoClassify = useCallback(
    async (message) => {
      if (!message.trim()) return;
      try {
        const res = await fetch(apiUrl("/api/flow/classify"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message }),
        });
        if (!res.ok) return;
        const data = await res.json();
        const recommendedId = data.recommended_topology;
        const topo = topologies.find((t) => t.id === recommendedId);
        setAutoResult({
          id: recommendedId,
          name: topo?.name || recommendedId,
          icon: topo?.icon || "",
          confidence: data.confidence,
          alternatives: data.alternatives || [],
        });
        setActiveTopology(recommendedId);
      } catch (e) {
        console.warn("Auto-classify failed:", e);
      }
    },
    [topologies]
  );

  // Debounced auto-classify when test message changes
  useEffect(() => {
    if (!autoMode || !autoTestMessage.trim()) return;
    const t = setTimeout(() => handleAutoClassify(autoTestMessage), 500);
    return () => clearTimeout(t);
  }, [autoTestMessage, autoMode, handleAutoClassify]);

  /* ---------- Render ---------- */
  const activeStyleColor = STYLE_COLOURS[topologyMeta?.execution_style] || "#9a9bb0";

  return (
    <div className="flow-root">
      {/* Header */}
      <div className="flow-header">
        <div>
          <h1>Agent Workflow</h1>
          <p>
            Visual view of the multi-agent system that GitPilot uses to
            plan and apply changes to your repositories.
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {topologyMeta && (
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "#9a9bb0" }}>
              <span style={{ fontSize: 18 }}>{topologyMeta.icon}</span>
              <span style={{ fontWeight: 600, color: "#e0e1f0" }}>{topologyMeta.name}</span>
              <span
                style={{
                  padding: "2px 8px",
                  borderRadius: 6,
                  border: `1px solid ${activeStyleColor}40`,
                  color: activeStyleColor,
                  fontSize: 10,
                  fontWeight: 700,
                  textTransform: "uppercase",
                }}
              >
                {STYLE_LABELS[topologyMeta.execution_style] || topologyMeta.execution_style}
              </span>
              <span>{topologyMeta.agents_used?.length || 0} agents</span>
            </div>
          )}
          {loading && <span style={{ fontSize: 11, color: "#6c8cff" }}>Loading...</span>}
        </div>
      </div>

      {/* Topology selector panel */}
      {topologies.length > 0 && (
        <TopologyPanel
          topologies={topologies}
          activeTopology={activeTopology}
          autoMode={autoMode}
          autoResult={autoResult}
          onSelect={handleTopologyChange}
          onToggleAuto={handleToggleAuto}
        />
      )}

      {/* Auto-detection test input (shown when auto mode is on) */}
      {autoMode && (
        <div style={autoInputStyles.wrap}>
          <div style={autoInputStyles.label}>
            Test auto-detection: type a task description to see which topology is recommended
          </div>
          <input
            type="text"
            placeholder='e.g. "Fix the 403 error in auth middleware" or "Add a REST endpoint for users"'
            value={autoTestMessage}
            onChange={(e) => setAutoTestMessage(e.target.value)}
            style={autoInputStyles.input}
          />
          {autoResult && autoResult.alternatives?.length > 0 && (
            <div style={autoInputStyles.altRow}>
              <span style={{ color: "#52525B", fontSize: 10 }}>Alternatives:</span>
              {autoResult.alternatives.slice(0, 3).map((alt) => {
                const altTopo = topologies.find((t) => t.id === alt.id);
                return (
                  <button
                    key={alt.id}
                    type="button"
                    style={autoInputStyles.altBtn}
                    onClick={() => handleTopologyChange(alt.id)}
                  >
                    {altTopo?.icon} {altTopo?.name || alt.id}
                    <span style={{ opacity: 0.5 }}>
                      {alt.confidence != null ? ` ${Math.round(alt.confidence * 100)}%` : ""}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Description bar */}
      {topologyMeta && topologyMeta.description && !autoMode && (
        <div
          style={{
            padding: "8px 16px",
            fontSize: 12,
            color: "#9a9bb0",
            background: "#0a0b12",
            borderBottom: "1px solid #1e1f30",
          }}
        >
          {topologyMeta.icon} {topologyMeta.description}
        </div>
      )}

      {/* ReactFlow canvas */}
      <div className="flow-canvas">
        {error ? (
          <div className="flow-error">
            <div className="error-icon">!!!</div>
            <div className="error-text">{error}</div>
          </div>
        ) : (
          <ReactFlow nodes={nodes} edges={edges} fitView>
            <Background color="#272832" gap={16} />
            <MiniMap
              nodeColor={(node) => {
                const border = node.style?.border || "";
                if (border.includes("#ff7a3c")) return "#ff7a3c";
                if (border.includes("#6c8cff")) return "#6c8cff";
                if (border.includes("#4caf88")) return "#4caf88";
                if (border.includes("#9c6cff")) return "#9c6cff";
                return "#3a3b4d";
              }}
              maskColor="rgba(0, 0, 0, 0.6)"
            />
            <Controls />
          </ReactFlow>
        )}
      </div>
    </div>
  );
}

const autoInputStyles = {
  wrap: {
    padding: "8px 16px 10px",
    borderBottom: "1px solid #1e1f30",
    backgroundColor: "#0c0d14",
  },
  label: {
    fontSize: 10,
    color: "#71717A",
    marginBottom: 6,
  },
  input: {
    width: "100%",
    padding: "8px 12px",
    borderRadius: 6,
    border: "1px solid #27272A",
    background: "#08090e",
    color: "#e0e1f0",
    fontSize: 12,
    fontFamily: "monospace",
    outline: "none",
    boxSizing: "border-box",
  },
  altRow: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    marginTop: 6,
    flexWrap: "wrap",
  },
  altBtn: {
    padding: "2px 8px",
    borderRadius: 4,
    border: "1px solid #27272A",
    background: "transparent",
    color: "#9a9bb0",
    fontSize: 10,
    cursor: "pointer",
  },
};
