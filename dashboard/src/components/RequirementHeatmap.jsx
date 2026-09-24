export function RequirementHeatmap({ scores }) {
  const entries = Object.entries(scores || {});
  if (!entries.length) {
    return (
      <div className="border rounded-lg p-3 text-sm text-gray-400">
        No requirement scores
      </div>
    );
  }

  return (
    <div className="border rounded-lg p-3 space-y-2">
      <h3 className="text-sm font-semibold">Requirement confidence</h3>
      {entries.map(([req, score]) => {
        const n = Number(score);
        const pct = Math.round(Math.min(1, Math.max(0, n)) * 100);
        const bar =
          n > 0.6 ? 'bg-green-500' : n < 0.4 ? 'bg-red-500' : 'bg-amber-400';
        return (
          <div key={req}>
            <div className="flex justify-between text-xs mb-1">
              <span className="truncate pr-2">{req}</span>
              <span className="font-mono">{n.toFixed(2)}</span>
            </div>
            <div className="h-2 bg-gray-100 rounded overflow-hidden">
              <div className={`h-full ${bar}`} style={{ width: `${pct}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
