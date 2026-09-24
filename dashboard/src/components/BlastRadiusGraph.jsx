import { useEffect, useMemo, useState } from 'react';
import { ReactFlow, Background } from '@xyflow/react';
import dagre from 'dagre';
import '@xyflow/react/dist/style.css';

const NODE_COLORS = {
  changed: { background: '#FEE2E2', border: '#EF4444', color: '#991B1B' },
  depth_1: { background: '#FEF3C7', border: '#F59E0B', color: '#92400E' },
  depth_2: { background: '#FEF9C3', border: '#EAB308', color: '#854D0E' },
};

function buildReactFlowGraph(blastRadius) {
  const nodes = [];
  const edges = [];
  const seen = new Set();

  const add = (id, depthKey) => {
    if (!id || seen.has(id)) return;
    seen.add(id);
    const c = NODE_COLORS[depthKey] || NODE_COLORS.depth_2;
    nodes.push({
      id,
      data: { label: String(id).split('.').pop() },
      style: {
        background: c.background,
        border: `2px solid ${c.border}`,
        color: c.color,
        fontSize: 11,
        padding: 6,
        borderRadius: 6,
        opacity: 0.15,
      },
      type: 'default',
      depthKey,
    });
  };

  for (const id of blastRadius?.directly_changed || []) add(id, 'changed');
  for (const id of blastRadius?.depth_1_impacted || []) add(id, 'depth_1');
  for (const id of blastRadius?.depth_2_impacted || []) add(id, 'depth_2');

  // Simple chain edges: changed → depth1 → depth2 (approx for viz)
  const changed = blastRadius?.directly_changed || [];
  const d1 = blastRadius?.depth_1_impacted || [];
  const d2 = blastRadius?.depth_2_impacted || [];
  changed.forEach((c) => {
    d1.forEach((n) => {
      edges.push({ id: `${c}-${n}`, source: c, target: n, animated: true });
    });
  });
  d1.forEach((n) => {
    d2.forEach((m) => {
      edges.push({ id: `${n}-${m}`, source: n, target: m, animated: true });
    });
  });

  return { nodes, edges };
}

function applyDagreLayout(nodes, edges) {
  if (!nodes.length) return { nodes, edges };
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: 60, ranksep: 80 });
  nodes.forEach((node) => g.setNode(node.id, { width: 150, height: 40 }));
  edges.forEach((edge) => g.setEdge(edge.source, edge.target));
  dagre.layout(g);
  return {
    nodes: nodes.map((node) => {
      const pos = g.node(node.id);
      return { ...node, position: { x: pos.x - 75, y: pos.y - 20 } };
    }),
    edges,
  };
}

export function BlastRadiusGraph({ blastRadius }) {
  const base = useMemo(() => buildReactFlowGraph(blastRadius || {}), [blastRadius]);
  const layouted = useMemo(
    () => applyDagreLayout(base.nodes, base.edges),
    [base],
  );
  const [nodes, setNodes] = useState(layouted.nodes);

  useEffect(() => {
    setNodes(layouted.nodes.map((n) => ({ ...n, style: { ...n.style, opacity: 0.15 } })));
    const order = ['changed', 'depth_1', 'depth_2'];
    order.forEach((depth, i) => {
      setTimeout(() => {
        setNodes((prev) =>
          prev.map((n) =>
            n.depthKey === depth || (i === 0 && n.depthKey === 'changed')
              ? { ...n, style: { ...n.style, opacity: 1 } }
              : n,
          ),
        );
      }, 400 * (i + 1));
    });
  }, [layouted]);

  if (!layouted.nodes.length) {
    return (
      <div className="border rounded-lg p-3 h-[200px] flex items-center justify-center text-sm text-gray-400">
        No blast radius data
      </div>
    );
  }

  return (
    <div className="border rounded-lg overflow-hidden" style={{ height: 320 }}>
      <ReactFlow nodes={nodes} edges={layouted.edges} fitView nodesDraggable={false}>
        <Background />
      </ReactFlow>
    </div>
  );
}
