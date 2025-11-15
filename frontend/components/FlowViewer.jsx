import React, { useEffect, useState } from "react";
import ReactFlow, { Background, Controls, MiniMap } from "reactflow";
import "reactflow/dist/style.css";

export default function FlowViewer() {
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const load = async () => {
      setLoading(true);
      setError("");
      try {
        const res = await fetch("/api/flow/current");
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed to load flow");

        // Position nodes in a horizontal layout
        const RFnodes = data.nodes.map((n, i) => ({
          id: n.id,
          data: {
            label: (
              <div style={{ textAlign: "center" }}>
                <div style={{ fontWeight: 600, marginBottom: 4 }}>
                  {n.label}
                </div>
                <div
                  style={{
                    fontSize: 10,
                    color: "#9a9bb0",
                    maxWidth: 140,
                    lineHeight: 1.3,
                  }}
                >
                  {n.description}
                </div>
              </div>
            ),
          },
          position: {
            x: 50 + (i % 3) * 250,
            y: 50 + Math.floor(i / 3) * 180,
          },
          type: "default",
          style: {
            borderRadius: 12,
            padding: "12px 16px",
            border:
              n.type === "agent"
                ? "2px solid #ff7a3c"
                : "2px solid #3a3b4d",
            background: n.type === "agent" ? "#20141a" : "#141821",
            color: "#f5f5f7",
            fontSize: 13,
            minWidth: 180,
            maxWidth: 200,
          },
        }));

        const RFedges = data.edges.map((e) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          label: e.label,
          animated: true,
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
        }));

        setNodes(RFnodes);
        setEdges(RFedges);
      } catch (e) {
        console.error(e);
        setError(e.message);
      } finally {
        setLoading(false);
      }
    };

    load();
  }, []);

  return (
    <div className="flow-root">
      <div className="flow-header">
        <div>
          <h1>Agent Workflow</h1>
          <p>
            Visual view of the CrewAI multi-agent system that GitPilot uses to
            plan and apply changes to your repositories.
          </p>
        </div>
        {loading && <span className="badge">Loading…</span>}
      </div>

      <div className="flow-canvas">
        {error ? (
          <div className="flow-error">
            <div className="error-icon">⚠️</div>
            <div className="error-text">{error}</div>
          </div>
        ) : (
          <ReactFlow nodes={nodes} edges={edges} fitView>
            <Background color="#272832" gap={16} />
            <MiniMap
              nodeColor={(node) =>
                node.style?.border?.includes("#ff7a3c") ? "#ff7a3c" : "#3a3b4d"
              }
              maskColor="rgba(0, 0, 0, 0.6)"
            />
            <Controls />
          </ReactFlow>
        )}
      </div>
    </div>
  );
}
