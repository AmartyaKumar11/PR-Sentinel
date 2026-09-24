import { useEffect, useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { getHealth } from '../api/client';

export function Layout() {
  const [health, setHealth] = useState(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
    const t = setInterval(() => {
      getHealth()
        .then(setHealth)
        .catch(() => setHealth(null));
    }, 15000);
    return () => clearInterval(t);
  }, []);

  const ok = health?.status === 'ok';

  return (
    <div className="min-h-screen flex bg-slate-50 text-slate-900">
      <aside className="w-56 border-r bg-white flex flex-col p-4">
        <div className="mb-8">
          <div className="text-lg font-bold tracking-tight">PR Sentinel</div>
          <div className="text-xs text-gray-400">Review dashboard</div>
        </div>
        <nav className="flex flex-col gap-1 text-sm">
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              `px-3 py-2 rounded ${isActive ? 'bg-slate-900 text-white' : 'hover:bg-slate-100'}`
            }
          >
            Dashboard
          </NavLink>
        </nav>
        <div className="mt-auto flex items-center gap-2 text-xs text-gray-500">
          <span className={`w-2 h-2 rounded-full ${ok ? 'bg-green-500' : 'bg-red-400'}`} />
          {ok ? `up ${Math.round(health.uptime_seconds || 0)}s` : 'backend offline'}
        </div>
      </aside>
      <main className="flex-1 p-6 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
