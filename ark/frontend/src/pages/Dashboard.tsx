/* Dashboard: system status board, stats, sidecars, installed content, module index. */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtBytes, fmtDuration } from "../api/client";
import type { Health, LibraryItemView } from "../api/types";
import { useToast } from "../lib/toast";
import { Badge, IndexRow, PageTitle, Panel, Stat } from "../components/ui";

interface ModuleDef {
  title: string;
  desc: string;
  href?: string;
}

const MODULES: ModuleDef[] = [
  { title: "Library", desc: "Offline Kiwix ZIMs + uploaded manuals", href: "/library" },
  { title: "Content Manager", desc: "Catalogs, resumable downloads, sideload" },
  { title: "Notes", desc: "Fast offline Markdown" },
  { title: "Computer Toolbox", desc: "Encryption, encodings, calculators, QR" },
  { title: "AI + RAG", desc: "Local LLM chat with sources it cites" },
  { title: "Manuals Library", desc: "PDFs, OCR, FTS, browse by brand/model" },
  { title: "Offline Maps", desc: "PMTiles, routing, GPS, MGRS" },
  { title: "Video Platform", desc: "Curated offline videos + comms guides" },
  { title: "Radio Reference", desc: "Frequencies, band plans, repeater lists" },
  { title: "Translation", desc: "Offline Argos/OPUS machine translation" },
  { title: "Document Suite", desc: "docx/xlsx/pdf view & edit" },
  { title: "System", desc: "Logs, jobs, settings, backups", href: "/logs" },
];

export default function Dashboard() {
  const { notifyError } = useToast();
  const [health, setHealth] = useState<Health | null>(null);
  const [items, setItems] = useState<LibraryItemView[]>([]);
  const [polling, setPolling] = useState(true);

  const load = useCallback(() => {
    const h = api.get<Health>("/api/health");
    const i = api.get<{ items: LibraryItemView[] }>("/api/library/items");
    return Promise.all([h, i]);
  }, []);

  useEffect(() => {
    let alive = true;
    const tick = () =>
      load()
        .then(([h, it]) => {
          if (!alive) return;
          setHealth(h);
          setItems(it.items);
        })
        .catch((e) => alive && notifyError(e, "Failed to load system status"));
    void tick();
    const timer = window.setInterval(() => polling && tick(), 10000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [load, polling, notifyError]);

  const moduleStatus = health?.modules ?? [];
  const sidecars = health?.sidecars ?? [];
  const zimCount = items.filter((i) => i.kind === "zim").length;
  const manualCount = items.filter((i) => i.kind === "manual").length;

  const accessMode = health?.auth_mode ?? "none";

  return (
    <div className="page">
      <PageTitle
        title="System status"
        meta={
          <label className="label flex cursor-pointer items-center gap-1.5">
            <input
              type="checkbox"
              checked={polling}
              onChange={(ev) => setPolling(ev.target.checked)}
              className="accent-[var(--c-accent)]"
            />
            live poll
          </label>
        }
      />

      <div className="panel-stack">
        {health && health.problems.length > 0 && (
          <Panel title="Degraded" className="border-[color:var(--c-err)]">
            <ul className="list-inside list-disc">
              {health.problems.map((p) => (
                <li key={p} className="mono">
                  {p}
                </li>
              ))}
            </ul>
          </Panel>
        )}

        {/* SYSTEM */}
        <Panel title="System">
          <div className="stat-grid">
            <Stat label="Disk" value={health ? `${health.disk.free_pct}% free` : "n/a"} foot={`${fmtBytes(health?.disk.free_bytes)} of ${fmtBytes(health?.disk.total_bytes)}`} />
            <Stat label="Memory" value={health ? `${health.system.memory_percent ?? "n/a"}%` : "n/a"} foot={`${fmtBytes(health?.system.memory_used_bytes)}/${fmtBytes(health?.system.memory_total_bytes)}`} />
            <Stat label="CPU" value={health ? `${health.system.cpu_percent ?? "n/a"}%` : "n/a"} foot="offline host" />
            <Stat
              label="Uptime"
              value={
                <span title={health ? `${health.uptime_seconds} seconds` : undefined}>{health ? fmtDuration(health.uptime_seconds) : "n/a"}</span>
              }
              foot={health?.started_at ? `started ${health.started_at.slice(0, 19).replace("T", " ")}` : ""}
            />
            <Stat label="Database" value={health ? (health.db.ok ? "ok" : "error") : "n/a"} foot={health?.db.journal_mode ?? ""} />
            <Stat
              label="Access"
              value={accessMode === "required" ? "restricted (login required)" : "open (no login required to read)"}
            />
          </div>
        </Panel>

        {/* SIDECARS */}
        <Panel title="Sidecars">
          {sidecars.length === 0 ? (
            <p className="fine py-1">No sidecars active. Installed ZIM content is served on demand.</p>
          ) : (
            <div className="panel-body-flush">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>service</th>
                    <th>port</th>
                    <th>status</th>
                  </tr>
                </thead>
                <tbody>
                  {sidecars.map((s) => (
                    <tr key={String(s.name ?? "")}>
                      <td className="mono">{String(s.name ?? "")}</td>
                      <td className="num">{String(s.port ?? "n/a")}</td>
                      <td>
                        <Badge tone={s.running ? "ok" : "muted"}>{s.running ? "running" : "stopped"}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        {/* INSTALLED CONTENT */}
        <Panel
          title="Installed content"
          actions={
            <Link className="panel-head-meta linked" to="/library">
              manage
            </Link>
          }
        >
          <p className="fine">
            <span className="num">{zimCount}</span> zim archive{zimCount === 1 ? "" : "s"},{" "}
            <span className="num">{manualCount}</span> manual{manualCount === 1 ? "" : "s"}. Managed in Library.
          </p>
        </Panel>

        {/* MODULE INDEX */}
        <Panel title="Module index" flush>
          {MODULES.map((m) => {
            const st = moduleStatus.find((x) => x.name === m.title.toLowerCase());
            const built = !!st && st.status !== "disabled";
            const status = st?.status ?? "disabled";
            return (
              <IndexRow
                key={m.title}
                title={m.title}
                desc={m.desc}
                href={m.href && built ? m.href : undefined}
                muted={!built}
                meta={
                  <span className="inline-flex items-center gap-2">
                    {built ? (
                      <Badge tone={status === "ok" ? "ok" : status === "degraded" ? "warn" : "muted"}>{status}</Badge>
                    ) : (
                      <Badge tone="muted">not installed</Badge>
                    )}
                  </span>
                }
              />
            );
          })}
        </Panel>
      </div>
    </div>
  );
}