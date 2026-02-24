import React, { useEffect, useState, useCallback, useRef } from "react";
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

/* ------------------------------------------------------------------ */
/*  TopologySelector — dropdown grouped by category                    */
/* ------------------------------------------------------------------ */
function TopologySelector({ topologies, active, onChange, loading }) {
  const systems  = topologies.filter((t) => t.category === "system");
  const pipelines = topologies.filter((t) => t.category === "pipeline");

  return (
    <div className="topology-selector">
      <label htmlFor="topo-select">Topology:</label>
      <select
        id="topo-select"
        value={active || ""}
        onChange={(e) => onChange(e.target.value)}
        disabled={loading}
        style={{
          background: "#1a1b2e",
          color: "#e0e1f0",
          border: "1px solid #3a3b4d",
          borderRadius: 8,
          padding: "6px 12px",
          fontSize: 13,
          fontWeight: 500,
          cursor: "pointer",
          minWidth: 240,
        }}
      >
        <optgroup label="System Architectures">
          {systems.map((t) => (
            <option key={t.id} value={t.id}>
              {t.icon} {t.name}
            </option>
          ))}
        </optgroup>
        <optgroup label="Task Pipelines">
          {pipelines.map((t) => (
            <option key={t.id} value={t.id}>
              {t.icon} {t.name} ({t.agents_used.length} agents)
            </option>
          ))}
        </optgroup>
      </select>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  TopologyBadge — shows execution style + agent count                */
/* ------------------------------------------------------------------ */
function TopologyBadge({ topology }) {
  if (!topology) return null;

  const styleLabels = {
    single_task: "Single-Task",
    react_loop: "ReAct Loop",
    crew_pipeline: "Pipeline",
  };

  return (
    <div
      className="topology-badge"
      style={{
        display: "flex",
        gap: 8,
        alignItems: "center",
        fontSize: 12,
        color: "#9a9bb0",
      }}
    >
      <span
        style={{
          background: "#1e1f30",
          border: "1px solid #3a3b4d",
          borderRadius: 6,
          padding: "2px 8px",
        }}
      >
        {styleLabels[topology.execution_style] || topology.execution_style}
      </span>
      <span>{topology.agents_used?.length || 0} agents</span>
    </div>
  );
}

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
  const initialLoadDone = useRef(false);

  /* ---------- Load topology list on mount ---------- */
  useEffect(() => {
    (async () => {
      try {
        const [topoRes, prefRes] = await Promise.all([
          fetch("/api/flow/topologies"),
          fetch("/api/settings/topology"),
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
      const res = await fetch(url);
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

      // Use explicit positions from the topology, or fall back to grid layout
      const RFnodes = data.nodes.map((n, i) => {
        const nodeType = n.type || "default";
        const colour = colourFor(nodeType);
        const d = n.data || {};

        // Build the label content
        const label = d.label || n.label || n.id;
        const description = d.description || n.description || "";
        const model = d.model;
        const mode = d.mode;

        // Use position from topology data if available, otherwise grid fallback
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
                  <div
                    style={{
                      fontSize: 9,
                      color: "#6c8cff",
                      marginBottom: 2,
                      fontFamily: "monospace",
                    }}
                  >
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
                <div
                  style={{
                    fontSize: 10,
                    color: "#9a9bb0",
                    maxWidth: 160,
                    lineHeight: 1.3,
                  }}
                >
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

      const RFedges = data.edges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.label,
        animated: e.animated !== false,
        style: { stroke: "#7a7b8e", strokeWidth: 2 },
        labelStyle: {
          fill: "#c3c5dd",
          fontSize: 11,
          fontWeight: 500,
        },
        labelBgStyle: {
          fill: "#101117",
          fillOpacity: 0.9,
        },
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

  // Load graph on mount and whenever activeTopology changes
  useEffect(() => {
    loadGraph(activeTopology);
  }, [activeTopology, loadGraph]);

  /* ---------- Topology selection handler ---------- */
  const handleTopologyChange = useCallback(
    async (newTopologyId) => {
      setActiveTopology(newTopologyId);
      // Persist preference (fire-and-forget)
      try {
        await fetch("/api/settings/topology", {
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

  return (
    <div className="flow-root">
      <div className="flow-header">
        <div>
          <h1>Agent Workflow</h1>
          <p>
            Visual view of the multi-agent system that GitPilot uses to
            plan and apply changes to your repositories.
          </p>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
          {topologies.length > 0 && (
            <TopologySelector
              topologies={topologies}
              active={activeTopology}
              onChange={handleTopologyChange}
              loading={loading}
            />
          )}
          <TopologyBadge topology={topologyMeta} />
          {loading && <span className="badge">Loading...</span>}
        </div>
      </div>

      {topologyMeta && topologyMeta.description && (
        <div
          style={{
            padding: "8px 16px",
            margin: "0 0 8px",
            fontSize: 12,
            color: "#9a9bb0",
            background: "#13141f",
            borderRadius: 8,
            border: "1px solid #1e1f30",
          }}
        >
          {topologyMeta.icon} {topologyMeta.description}
        </div>
      )}

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
