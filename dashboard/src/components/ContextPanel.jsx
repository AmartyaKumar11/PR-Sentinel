import { useEffect, useState } from 'react';
import { getContext } from '../api/client';

export function ContextPanel({ repo }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    getContext(repo)
      .then((row) => {
        if (live) setData(row);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, [repo]);

  const facts = Object.entries(data?.hard_facts || {});
  const conventions = Object.entries(data?.conventions || {});

  return (
    <section className="border rounded-lg bg-white p-4">
      <h2 className="text-sm font-semibold">Repo context</h2>
      <p className="text-xs text-gray-500 mt-1">{repo}</p>
      {error && <p className="text-sm text-red-600 mt-2">{error}</p>}
      {!data && !error && <p className="text-sm text-gray-400 mt-2">Loading…</p>}
      {data && facts.length === 0 && conventions.length === 0 && (
        <p className="text-sm text-gray-400 mt-2">No context yet. It appears after the first review.</p>
      )}
      {facts.length > 0 && (
        <table className="w-full text-sm mt-3">
          <thead>
            <tr className="text-left text-xs text-gray-500">
              <th className="py-1 pr-2">Fact</th>
              <th className="py-1 pr-2">Value</th>
              <th className="py-1 pr-2">Confidence</th>
              <th className="py-1 pr-2">Source</th>
              <th className="py-1">Last seen</th>
            </tr>
          </thead>
          <tbody>
            {facts.map(([key, fact]) => (
              <tr key={key} className="border-t">
                <td className="py-1 pr-2">{key}</td>
                <td className="py-1 pr-2">{fact.value}</td>
                <td className="py-1 pr-2">{pct(fact.confidence)}</td>
                <td className="py-1 pr-2">{fact.source}</td>
                <td className="py-1">{fact.last_seen || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {conventions.length > 0 && (
        <table className="w-full text-sm mt-3">
          <thead>
            <tr className="text-left text-xs text-gray-500">
              <th className="py-1 pr-2">Convention</th>
              <th className="py-1 pr-2">Dominant</th>
              <th className="py-1 pr-2">Confidence</th>
              <th className="py-1">Last seen</th>
            </tr>
          </thead>
          <tbody>
            {conventions.map(([key, item]) => (
              <tr key={key} className="border-t">
                <td className="py-1 pr-2">{key}</td>
                <td className="py-1 pr-2">{item.dominant}</td>
                <td className="py-1 pr-2">{pct(item.confidence)}</td>
                <td className="py-1">{item.last_seen || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function pct(value) {
  if (value === undefined || value === null) return '—';
  return `${Math.round(Number(value) * 100)}%`;
}
