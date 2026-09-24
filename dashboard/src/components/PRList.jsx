import { Link } from 'react-router-dom';
import { RiskBadge } from './RiskBadge';

export function PRList({ reviews }) {
  if (!reviews?.length) {
    return (
      <div className="border rounded-lg p-8 text-center text-gray-400 text-sm">
        No reviews yet
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {reviews.map((r) => (
        <Link
          key={r.task_id}
          to={`/review/${r.task_id}`}
          className="flex items-center justify-between border rounded-lg bg-white px-4 py-3 hover:border-slate-400"
        >
          <div className="flex items-center gap-3">
            <RiskBadge severity={r.severity} />
            <div>
              <div className="text-sm font-medium">
                PR #{r.pr_number} · {r.repo}
              </div>
              <div className="text-xs text-gray-400">{r.created_at}</div>
            </div>
          </div>
          <span className="text-xs px-2 py-1 rounded bg-gray-100 text-gray-600">
            {r.status}
          </span>
        </Link>
      ))}
    </div>
  );
}
