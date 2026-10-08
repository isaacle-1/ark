import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Health } from "../api/types";
import { useTheme, THEMES, Theme } from "../lib/theme";
import { useToast } from "../lib/toast";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

export default function Settings() {
  const { notify, notifyError } = useToast();
  const [theme, setTheme] = useTheme();
  const [health, setHealth] = useState<Health | null>(null);
  const [level, setLevel] = useState<string>("");
  const [levelSource, setLevelSource] = useState<string>("");

  useEffect(() => {
    let alive = true;
    api
      .get<Health>("/api/health")
      .then((h) => alive && setHealth(h))
      .catch((e) => notifyError(e, "Cannot load health"));
    api
      .get<{ level: string; source: string }>("/api/admin/logs/level")
      .then((r) => {
        if (!alive) return;
        setLevel(r.level);
        setLevelSource(r.source);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [notifyError]);

  async function saveLevel(next: string) {
    if (next === level) return;
    try {
      const r = await api.post<{ level: string; source: string; note?: string }>(
        "/api/admin/logs/level",
        { level: next },
      );
      setLevel(r.level);
      setLevelSource(r.source);
      notify(r.note ?? `Log level set to ${r.level} (persisted, applies on restart too)`);
    } catch (e) {
      notifyError(e, "Cannot change log level");
    }
  }

  function pickTheme(t: Theme) {
    setTheme(t);
    notify(`Theme: ${t}`);
  }

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-4 text-2xl font-bold">Settings</h1>

      <div className="mb-4 space-y-3">
        <div className="card">
          <div className="text-sm font-semibold uppercase tracking-wide text-[var(--c-muted)]">
            Theme
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {THEMES.map((t) => (
              <button
                key={t.value}
                className={"btn " + (theme === t.value ? "btn-primary" : "btn-ghost")}
                onClick={() => pickTheme(t.value)}
                data-testid={`theme-${t.value}`}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div className="mt-2 text-xs text-[var(--c-muted)]">
            {THEMES.find((t) => t.value === theme)?.hint} — saved in this browser.
          </div>
        </div>

        <div className="card">
          <div className="text-sm font-semibold uppercase tracking-wide text-[var(--c-muted)]">
            Log level
          </div>
          <div className="mt-3 flex items-center gap-2">
            <select className="input !w-48" value={level} onChange={(e) => saveLevel(e.target.value)} data-testid="log-level">
              {LEVELS.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
            <span className="text-xs text-[var(--c-muted)]">
              source: {levelSource}
              {levelSource === "env" && " — LOG_LEVEL env var wins until unset"}
            </span>
          </div>
          <p className="mt-2 text-xs text-[var(--c-muted)]">
            Changes take effect immediately and are persisted in the DB.
          </p>
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold uppercase tracking-wide text-[var(--c-muted)]">
          About
        </div>
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-[var(--c-muted)]">Version</dt>
          <dd>{health?.version}</dd>
          <dt className="text-[var(--c-muted)]">ARK_HOME</dt>
          <dd className="break-all font-mono text-xs">{health?.ark_home}</dd>
          <dt className="text-[var(--c-muted)]">Auth mode</dt>
          <dd>{health?.auth_mode}</dd>
          <dt className="text-[var(--c-muted)]">Process</dt>
          <dd>pid {health?.pid} · up {Math.round(health?.uptime_seconds ?? 0)}s</dd>
        </dl>
      </div>
    </div>
  );
}