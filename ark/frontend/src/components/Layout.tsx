/* Layout: module-index sidebar, system status footer, user menu. */
import { useEffect, useMemo, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { LogOut, UserRound } from "lucide-react";
import { api } from "../api/client";
import type { Health, Me } from "../api/types";
import { useToast } from "../lib/toast";
import { Button, StatusLine } from "./ui";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/library", label: "Library" },
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
      <aside className="no-print flex flex-row items-center justify-between gap-2 border-b border-[var(--c-line)] bg-[var(--c-panel)] px-3 py-1.5 lg:h-full lg:w-60 lg:flex-col lg:items-stretch lg:border-b-0 lg:border-r lg:py-0">
        {/* masthead */}
        <div className="flex items-center gap-2 py-1 lg:border-b lg:border-[var(--c-line)] lg:px-3 lg:py-3">
          <img src="/icon.svg" alt="" className="h-6 w-6" />
          <div className="leading-tight">
            <div className="display text-[17px] font-bold tracking-[0.08em]">ARK</div>
            <div className="label">resilience &amp; knowledge</div>
          </div>
        </div>

        {/* module index */}
        <nav className="flex items-center gap-0.5 overflow-x-auto lg:flex-col lg:overflow-visible lg:pt-2" aria-label="Module index">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              className={({ isActive }) => "nav-item " + (isActive ? "active" : "")}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>

        {/* status + user */}
        <div className="hidden shrink-0 lg:mt-auto lg:flex lg:flex-col lg:gap-2 lg:border-t lg:border-[var(--c-line)] lg:px-3 lg:py-3">
          <div className="flex items-center justify-between gap-2">
            <span className="label">system</span>
            <StatusLine
              tone={health?.status === "degraded" ? "warn" : "ok"}
              className="!text-[10px]"
            >
              {health?.status === "degraded" ? "degraded" : "operational"}
            </StatusLine>
          </div>
          {me?.user ? (
            <div className="flex items-center justify-between gap-2">
              <span className="mono flex min-w-0 items-center gap-1.5 text-xs">
                <UserRound size={13} className="shrink-0" />
                <span className="truncate">{me.user.username}</span>
              </span>
              <button
                className="btn-plain btn !py-1 text-[10px]"
                onClick={logout}
                title="Log out"
                data-testid="logout-btn"
              >
                <LogOut size={12} />
                out
              </button>
            </div>
          ) : (
            <Button
              variant="primary"
              onClick={() => navigate("/login")}
              className="w-full justify-center"
            >
              Log in
            </Button>
          )}
        </div>
      </aside>

      <main className="min-h-0 flex-1 overflow-y-auto p-3 lg:p-5">
        <Outlet />
      </main>
    </div>
  );
}