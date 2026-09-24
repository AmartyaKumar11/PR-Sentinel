import { useEffect, useState } from 'react';
import { DEFAULT_REPO, getReviews } from '../api/client';
import { PRList } from '../components/PRList';

export function Dashboard() {
  const [data, setData] = useState({ reviews: [], total: 0 });
  const [error, setError] = useState(null);

  useEffect(() => {
    getReviews(DEFAULT_REPO)
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  const reviews = data.reviews || [];
  const critical = reviews.filter((r) => r.severity === 'CRITICAL').length;
  const avgRisk =
    reviews.length === 0
      ? 0
      : reviews.reduce((s, r) => s + Number(r.risk_score || 0), 0) / reviews.length;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <p className="text-sm text-gray-500">{DEFAULT_REPO}</p>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <Stat label="Reviews" value={data.total ?? reviews.length} />
        <Stat label="Critical" value={critical} />
        <Stat label="Avg risk" value={avgRisk.toFixed(2)} />
      </div>

      {error && <div className="text-sm text-red-600">{error}</div>}
      <PRList reviews={reviews} />
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="border rounded-lg bg-white p-4">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
    </div>
  );
}
