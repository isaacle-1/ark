import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Health } from "../api/types";
import { useTheme, THEMES, Theme } from "../lib/theme";
import { useToast } from "../lib/toast";
import { Button, Field, Panel, PageTitle } from "../components/ui";

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
      <PageTitle title="Settings" />

      <Panel no="01" title="Appearance" className="mb-4" meta="saved in this browser">
        <div className="flex flex-wrap gap-2">
          {THEMES.map((t) => (
            <Button
              key={t.value}
              variant={theme === t.value ? "primary" : "ghost"}
              onClick={() => pickTheme(t.value)}
              data-testid={`theme-${t.value}`}
            >
              {t.label}
            </Button>
          ))}
        </div>
        <p className="fine mt-2">{THEMES.find((t) => t.value === theme)?.hint}.</p>
      </Panel>

      <Panel no="02" title="Logging" className="mb-4">
        <Field label="log level" className="max-w-[180px]">
          <select className="input" value={level} onChange={(e) => saveLevel(e.target.value)} data-testid="log-level">
            {LEVELS.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </Field>
        <p className="fine mt-2">
          source: <code>{levelSource}</code>
          {levelSource === "env" && " — LOG_LEVEL env var wins until unset"} · changes take effect
          immediately and are persisted in the DB.
        </p>
      </Panel>

      <Panel no="03" title="System">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="label py-0.5">Version</dt>
          <dd className="mono">{health?.version ?? "—"}</dd>
          <dt className="label py-0.5">ARK_HOME</dt>
          <dd className="break-all mono text-xs">{health?.ark_home ?? "—"}</dd>
          <dt className="label py-0.5">Auth mode</dt>
          <dd className="mono">{health?.auth_mode ?? "—"}</dd>
          <dt className="label py-0.5">Process</dt>
          <dd className="mono">pid {health?.pid ?? "—"} · up {Math.round(health?.uptime_seconds ?? 0)}s</dd>
        </dl>
      </Panel>
    </div>
  );
}