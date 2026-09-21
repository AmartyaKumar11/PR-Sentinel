# PR Sentinel — Dashboard Specification

> **Stack:** React 18 + Vite + Tailwind CSS + React Flow  
> **Deploy:** Vercel (free tier)  
> **Resolution target:** 1920×1080 (no mobile responsive needed)

---

## Pages

| Route | Component | Data Source |
|---|---|---|
| `/` | `Dashboard.jsx` | `GET /api/reviews` |
| `/review/:taskId` | `ReviewDetail.jsx` | `GET /api/reviews/:taskId` + `GET /api/stream/:taskId` (SSE) |

---

## Component Tree

```
App.jsx
├── Layout.jsx (sidebar + main content area)
│   ├── Sidebar
│   │   ├── Logo / title
│   │   ├── Nav: Dashboard, (future: Settings)
│   │   └── Health indicator (green dot + uptime)
│   └── <Outlet /> (React Router)
│
├── Dashboard.jsx (route: /)
│   ├── Stats row (total reviews, avg risk, active tasks)
│   └── PRList.jsx
│       └── List of review cards, each with:
│           ├── RiskBadge.jsx (severity pill)
│           ├── PR number + repo
│           ├── Status
│           └── Click → navigate to /review/:taskId
│
└── ReviewDetail.jsx (route: /review/:taskId)
    ├── Header: PR #{number}, severity badge, status, timestamps
    ├── Two-column layout:
    │   ├── Left column (60%):
    │   │   ├── AgentTrace.jsx (live reasoning chain)
    │   │   │   └── TraceStep.jsx (repeated for each step)
    │   │   └── ComposerPromptView.jsx (generated prompt + copy button)
    │   └── Right column (40%):
    │       ├── IntentReport.jsx (addressed/missing/scope creep)
    │       ├── BlastRadiusGraph.jsx (React Flow visualization)
    │       └── TaskLifecycle.jsx (status pipeline)
    └── Footer: link to GitHub PR comment
```

---

## Key Components

### useSSE.js

```javascript
import { useState, useEffect } from 'react';

export function useSSE(taskId) {
    const [steps, setSteps] = useState([]);
    const [status, setStatus] = useState('idle');

    useEffect(() => {
        if (!taskId) return;

        setStatus('connecting');
        const source = new EventSource(`/api/stream/${taskId}`);

        source.onopen = () => setStatus('running');

        source.onmessage = (event) => {
            const data = JSON.parse(event.data);
            setStatus('running');
            setSteps(prev => [...prev, data]);

            if (data.type === 'answer' && data.phase === 'dispatch') {
                setStatus('complete');
                source.close();
            }
            if (data.type === 'error') {
                setStatus('error');
                source.close();
            }
        };

        source.onerror = () => {
            setStatus('error');
            source.close();
        };

        return () => source.close();
    }, [taskId]);

    return { steps, status };
}
```

### AgentTrace.jsx

```jsx
// The demo-killer component. Vertical timeline of agent reasoning steps.

export function AgentTrace({ taskId }) {
    const { steps, status } = useSSE(taskId);
    const bottomRef = useRef(null);

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [steps]);

    return (
        <div className="space-y-3">
            <div className="flex items-center gap-2 mb-4">
                <StatusDot status={status} />
                <span className="text-sm text-gray-500">
                    {status === 'running' ? 'Agent is reasoning...' :
                     status === 'complete' ? 'Analysis complete' :
                     status === 'error' ? 'Error occurred' : 'Waiting...'}
                </span>
            </div>

            {steps.map((step, i) => (
                <TraceStep key={i} step={step} />
            ))}

            {status === 'running' && (
                <div className="animate-pulse text-sm text-gray-400">
                    Thinking...
                </div>
            )}
            <div ref={bottomRef} />
        </div>
    );
}
```

### TraceStep.jsx

```jsx
const STEP_CONFIG = {
    thought:     { icon: '💭', label: 'Thought',     bg: 'bg-gray-50',   border: 'border-gray-200' },
    action:      { icon: '🔧', label: 'Action',      bg: 'bg-amber-50',  border: 'border-amber-200' },
    observation: { icon: '👁',  label: 'Observation', bg: 'bg-teal-50',   border: 'border-teal-200' },
    answer:      { icon: '✅', label: 'Answer',      bg: 'bg-purple-50', border: 'border-purple-200' },
    error:       { icon: '❌', label: 'Error',       bg: 'bg-red-50',    border: 'border-red-200' },
};

export function TraceStep({ step }) {
    const [expanded, setExpanded] = useState(step.type !== 'observation');
    const config = STEP_CONFIG[step.type] || STEP_CONFIG.thought;

    const content = step.type === 'action'
        ? `${step.tool}(${JSON.stringify(step.args || {})})`
        : step.content;

    const isLong = content?.length > 300;

    return (
        <div className={`rounded-lg border ${config.border} ${config.bg} p-3`}>
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm font-medium">
                    <span>{config.icon}</span>
                    <span>{config.label}</span>
                    <span className="text-gray-400">Step {step.step}</span>
                    <span className="text-xs px-2 py-0.5 rounded-full bg-gray-200 text-gray-600">
                        {step.phase}
                    </span>
                </div>
                {step.elapsed_ms && (
                    <span className="text-xs text-gray-400">{step.elapsed_ms}ms</span>
                )}
            </div>
            <div className={`mt-2 text-sm whitespace-pre-wrap ${!expanded && isLong ? 'max-h-20 overflow-hidden' : ''}`}>
                {step.type === 'answer' ? (
                    <pre className="text-xs bg-white/50 p-2 rounded overflow-x-auto">
                        {JSON.stringify(JSON.parse(content), null, 2)}
                    </pre>
                ) : content}
            </div>
            {isLong && (
                <button
                    onClick={() => setExpanded(!expanded)}
                    className="mt-1 text-xs text-blue-600 hover:underline"
                >
                    {expanded ? 'Collapse' : 'Expand'}
                </button>
            )}
        </div>
    );
}
```

### BlastRadiusGraph.jsx

```jsx
import { ReactFlow, Background } from '@xyflow/react';
import dagre from 'dagre';
import '@xyflow/react/dist/style.css';

const NODE_COLORS = {
    changed:  { background: '#FEE2E2', border: '#EF4444', text: '#991B1B' },
    depth_1:  { background: '#FEF3C7', border: '#F59E0B', text: '#92400E' },
    depth_2:  { background: '#FEF9C3', border: '#EAB308', text: '#854D0E' },
    depth_3:  { background: '#F3F4F6', border: '#9CA3AF', text: '#374151' },
    untested: { background: '#FEE2E2', border: '#EF4444', borderStyle: 'dashed' },
};

export function BlastRadiusGraph({ blastRadius, diagnosis }) {
    // 1. Convert diagnosis data to React Flow nodes and edges
    // 2. Use dagre for automatic hierarchical layout
    // 3. Color nodes by depth
    // 4. Animate: nodes light up sequentially (useEffect with setTimeout per depth)
    // 5. Click node → show file path, line, test status in a tooltip

    const { nodes, edges } = useMemo(() => {
        return buildReactFlowGraph(blastRadius, diagnosis);
    }, [blastRadius, diagnosis]);

    const layouted = useMemo(() => {
        return applyDagreLayout(nodes, edges);
    }, [nodes, edges]);

    return (
        <div style={{ height: 400 }}>
            <ReactFlow
                nodes={layouted.nodes}
                edges={layouted.edges}
                fitView
                nodesDraggable={false}
            >
                <Background />
            </ReactFlow>
        </div>
    );
}

function buildReactFlowGraph(blastRadius, diagnosis) {
    const nodes = [];
    const edges = [];

    // Changed nodes (red)
    for (const id of blastRadius.directly_changed || []) {
        nodes.push({ id, data: { label: id.split('.').pop() }, style: NODE_COLORS.changed, type: 'default' });
    }
    // Depth 1 (orange)
    for (const id of blastRadius.depth_1_impacted || []) {
        nodes.push({ id, data: { label: id.split('.').pop() }, style: NODE_COLORS.depth_1 });
    }
    // Depth 2 (yellow)
    for (const id of blastRadius.depth_2_impacted || []) {
        nodes.push({ id, data: { label: id.split('.').pop() }, style: NODE_COLORS.depth_2 });
    }

    // Edges from the full graph data
    if (diagnosis?.blast_radius_json?.edges) {
        for (const edge of diagnosis.blast_radius_json.edges) {
            if (nodes.find(n => n.id === edge.from) && nodes.find(n => n.id === edge.to)) {
                edges.push({ id: `${edge.from}-${edge.to}`, source: edge.to, target: edge.from, animated: true });
            }
        }
    }

    return { nodes, edges };
}

function applyDagreLayout(nodes, edges) {
    const g = new dagre.graphlib.Graph();
    g.setDefaultEdgeLabel(() => ({}));
    g.setGraph({ rankdir: 'TB', nodesep: 60, ranksep: 80 });

    nodes.forEach(node => g.setNode(node.id, { width: 150, height: 40 }));
    edges.forEach(edge => g.setEdge(edge.source, edge.target));
    dagre.layout(g);

    return {
        nodes: nodes.map(node => {
            const pos = g.node(node.id);
            return { ...node, position: { x: pos.x - 75, y: pos.y - 20 } };
        }),
        edges,
    };
}
```

### RiskBadge.jsx

```jsx
const SEVERITY_CONFIG = {
    TRIVIAL:  { color: 'bg-gray-100 text-gray-600',   dot: 'bg-gray-400' },
    LOW:      { color: 'bg-green-100 text-green-700',  dot: 'bg-green-500' },
    MEDIUM:   { color: 'bg-yellow-100 text-yellow-700', dot: 'bg-yellow-500' },
    HIGH:     { color: 'bg-orange-100 text-orange-700', dot: 'bg-orange-500' },
    CRITICAL: { color: 'bg-red-100 text-red-700',      dot: 'bg-red-500' },
};

export function RiskBadge({ severity }) {
    const config = SEVERITY_CONFIG[severity] || SEVERITY_CONFIG.TRIVIAL;
    return (
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${config.color}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${config.dot}`} />
            {severity}
        </span>
    );
}
```

### ComposerPromptView.jsx

```jsx
export function ComposerPromptView({ prompt }) {
    const [copied, setCopied] = useState(false);

    const copy = async () => {
        await navigator.clipboard.writeText(prompt);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div className="border rounded-lg overflow-hidden">
            <div className="flex items-center justify-between px-3 py-2 bg-gray-50 border-b">
                <span className="text-sm font-medium">Cursor Composer prompt</span>
                <button onClick={copy} className="text-xs px-2 py-1 rounded border hover:bg-gray-100">
                    {copied ? 'Copied!' : 'Copy'}
                </button>
            </div>
            <pre className="p-3 text-xs overflow-x-auto whitespace-pre-wrap font-mono bg-white">
                {prompt}
            </pre>
        </div>
    );
}
```

---

## API Client

```javascript
// dashboard/src/api/client.js

import axios from 'axios';

const api = axios.create({
    baseURL: '/api',  // Proxied by Vite in dev, Vercel in prod
});

export const getReviews = (repo, limit = 20, offset = 0) =>
    api.get('/reviews', { params: { repo, limit, offset } }).then(r => r.data);

export const getReviewDetail = (taskId) =>
    api.get(`/reviews/${taskId}`).then(r => r.data);

export const getHealth = () =>
    api.get('/health').then(r => r.data);
```

---

## Vercel Deployment

`dashboard/vercel.json`:
```json
{
  "rewrites": [
    { "source": "/api/(.*)", "destination": "https://pr-sentinel-backend.up.railway.app/api/$1" },
    { "source": "/(.*)", "destination": "/index.html" }
  ]
}
```

```bash
cd dashboard
npx vercel --prod
```
