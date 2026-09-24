const SEVERITY_CONFIG = {
  TRIVIAL: { color: 'bg-gray-100 text-gray-600', dot: 'bg-gray-400' },
  LOW: { color: 'bg-green-100 text-green-700', dot: 'bg-green-500' },
  MEDIUM: { color: 'bg-yellow-100 text-yellow-700', dot: 'bg-yellow-500' },
  HIGH: { color: 'bg-orange-100 text-orange-700', dot: 'bg-orange-500' },
  CRITICAL: { color: 'bg-red-100 text-red-700', dot: 'bg-red-500' },
};

export function RiskBadge({ severity }) {
  const config = SEVERITY_CONFIG[severity] || SEVERITY_CONFIG.TRIVIAL;
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${config.color}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${config.dot}`} />
      {severity}
    </span>
  );
}
