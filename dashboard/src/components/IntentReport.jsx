export function IntentReport({ intent }) {
  const addressed = intent?.addressed || [];
  const missing = intent?.missing || [];
  const creep = intent?.scope_creep || [];

  return (
    <div className="border rounded-lg p-3 space-y-3">
      <h3 className="text-sm font-semibold">Intent alignment</h3>
      <Section title="Addressed" items={addressed} tone="text-green-700" empty="None" />
      <Section title="Missing" items={missing} tone="text-red-700" empty="None" />
      <Section title="Scope creep" items={creep} tone="text-amber-700" empty="None" />
    </div>
  );
}

function Section({ title, items, tone, empty }) {
  return (
    <div>
      <div className="text-xs font-medium text-gray-500 mb-1">{title}</div>
      {items.length === 0 ? (
        <div className="text-sm text-gray-400">{empty}</div>
      ) : (
        <ul className={`text-sm space-y-1 ${tone}`}>
          {items.map((item, i) => (
            <li key={i}>• {typeof item === 'string' ? item : JSON.stringify(item)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
