const STEPS = ['pending', 'dispatched', 'accepted', 'in_progress', 'resolved'];

export function TaskLifecycle({ status }) {
  const idx = STEPS.indexOf(status);
  return (
    <div className="border rounded-lg p-3">
      <h3 className="text-sm font-semibold mb-3">Lifecycle</h3>
      <div className="flex flex-wrap gap-2">
        {STEPS.map((s, i) => {
          const active = i <= idx && idx >= 0;
          const current = s === status;
          return (
            <span
              key={s}
              className={`text-xs px-2 py-1 rounded border ${
                current
                  ? 'bg-blue-600 text-white border-blue-600'
                  : active
                    ? 'bg-blue-50 text-blue-700 border-blue-200'
                    : 'bg-gray-50 text-gray-400 border-gray-200'
              }`}
            >
              {s}
            </span>
          );
        })}
      </div>
      {status === 'error' || status === 'dismissed' ? (
        <div className="mt-2 text-xs text-red-600">Terminal: {status}</div>
      ) : null}
    </div>
  );
}
