/* Layout: sidebar nav, status footer, current user menu. */
import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { Health, Me } from "../api/types";
import { useToast } from "../lib/toast";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/logs", label: "Logs", admin: true },
  { to: "/settings", label: "Settings" },
  { to: "/search", label: "Search" },
];

export default function Layout() {
  const { notify } = useToast();
  const navigate = useNavigate();
  const [me, setMe] = useState<Me | null>(null);
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    let alive = true;
    void api
      .get<Me>("/api/auth/me")
      .then((m) => alive && setMe(m))
      .catch(() => alive && setMe({ user: null, mode: "open" }));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .get<Health>("/api/health")
        .then((h) => alive && setHealth(h))
        .catch(() => undefined);
    void load();
    const timer = window.setInterval(load, 30000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const isAdmin = me?.user?.role === "admin";
  const nav = useMemo(() => NAV.filter((n) => !n.admin || isAdmin), [isAdmin]);

  async function logout() {
    try {
      await api.post("/api/auth/logout");
    } catch {
      /* ignore */
    }
    setMe({ user: null, mode: me?.mode ?? "open" });
    notify("Logged out", "success");
    navigate("/");
  }

  return (
    <div className="flex h-full flex-col lg:flex-row">
      <aside className="flex flex-row items-center justify-between gap-2 border-b border-[var(--c-line)] bg-[var(--c-panel)] px-4 py-2 lg:h-full lg:w-56 lg:flex-col lg:items-stretch lg:border-b-0 lg:border-r">
        <div className="flex items-center gap-2">
          <img src="/icon.svg" alt="" className="h-7 w-7" />
          <div className="leading-tight">
            <div className="font-bold tracking-wide">ARK</div>
            <div className="hidden text-[10px] uppercase tracking-wider text-[var(--c-muted)] lg:block">
              {health?.status === "degraded" ? "degraded" : "operational"}
            </div>
          </div>
        </div>
        <nav className="flex items-center gap-1 lg:flex-col lg:items-stretch lg:gap-1 lg:pt-4">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) =>
                `rounded-md px-3 py-1.5 text-sm transition-colors ${
                  isActive
                    ? "bg-[var(--c-panel2)] text-[var(--c-accent)]"
                    : "text-[var(--c-muted)] hover:text-[var(--c-ink)]"
                }`
              }
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="hidden lg:mt-auto lg:flex lg:flex-col lg:gap-2 lg:border-t lg:border-[var(--c-line)] lg:pt-3">
          {me?.user ? (
            <>
              <div className="text-sm">
                <span className="font-medium">{me.user.username}</span>{" "}
                <span className="badge text-[var(--c-accent)]">{me.user.role}</span>
              </div>
              <button className="btn btn-ghost" onClick={logout}>
                Log out
              </button>
            </>
          ) : (
            <button className="btn btn-primary" onClick={() => navigate("/login")}>
              Log in
            </button>
          )}
        </div>
      </aside>
      <main className="min-h-0 flex-1 overflow-y-auto p-4 lg:p-6">
        <Outlet />
      </main>
    </div>
  );
}