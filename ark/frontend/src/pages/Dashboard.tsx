/* Dashboard: system status board — stats, sidecars, installed content, module index. */
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, fmtBytes, fmtDuration } from "../api/client";
import type { Health, LibraryItemView } from "../api/types";
import { useToast } from "../lib/toast";
import { Badge, IndexRow, PageTitle, Panel, Stat } from "../components/ui";

interface ModuleDef {
  no: string;
  title: string;
  desc: string;
  phase: string;
  href?: string;
}

const MODULES: ModuleDef[] = [
  { no: "01", title: "Library", desc: "Offline Kiwix ZIMs + uploaded manuals", phase: "1", href: "/library" },
  { no: "02", title: "Content Manager", desc: "Catalogs, resumable downloads, sideload", phase: "1" },
  { no: "03", title: "Notes", desc: "Fast offline Markdown", phase: "2" },
  { no: "04", title: "Computer Toolbox", desc: "Encryption, encodings, calculators, QR", phase: "2" },
  { no: "05", title: "AI + RAG", desc: "Local LLM chat with sources it cites", phase: "3" },
  { no: "06", title: "Manuals Library", desc: "PDFs, OCR, FTS, browse by brand/model", phase: "4" },
  { no: "07", title: "Offline Maps", desc: "PMTiles, routing, GPS, MGRS", phase: "5" },
  { no: "08", title: "Video Platform", desc: "Curated offline videos + comms guides", phase: "6" },
  { no: "09", title: "Radio Reference", desc: "Frequencies, band plans, repeater lists", phase: "7" },
  { no: "10", title: "Translation", desc: "Offline Argos/OPUS machine translation", phase: "7" },
  { no: "11", title: "Document Suite", desc: "docx/xlsx/pdf view & edit", phase: "8" },
  { no: "12", title: "System", desc: "Logs, jobs, settings, backups", phase: "this", href: "/logs" },
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

  return (
    <div className="mx-auto max-w-6xl">
      <PageTitle title="System status" />

      <div className="mb-4 flex items-center gap-3">
        <label className="label flex cursor-pointer items-center gap-1.5">
          <input
            type="checkbox"
            checked={polling}
            onChange={(ev) => setPolling(ev.target.checked)}
            className="accent-[var(--c-accent)]"
          />
          live poll
        </label>
      </div>

      {health && health.problems.length > 0 && (
        <Panel no="!" title="Degraded" className="mb-4 border-[color:var(--c-err)]">
          <ul className="list-inside list-disc text-sm text-[var(--c-ink)]">
            {health.problems.map((p) => (
              <li key={p} className="mono text-xs">
                {p}
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {/* 01 — SYSTEM */}
      <Panel no="01" title="System" className="mb-4">
        <div className="stat-grid">
          <Stat label="Disk" value={health ? `${health.disk.free_pct}% free` : "—"} foot={`${fmtBytes(health?.disk.free_bytes)} of ${fmtBytes(health?.disk.total_bytes)}`} />
          <Stat label="Memory" value={health ? `${health.system.memory_percent ?? "—"}%` : "—"} foot={`${fmtBytes(health?.system.memory_used_bytes)}/${fmtBytes(health?.system.memory_total_bytes)}`} />
          <Stat label="CPU" value={health ? `${health.system.cpu_percent ?? "—"}%` : "—"} foot="offline host" />
          <Stat label="Uptime" value={health ? fmtDuration(health.uptime_seconds) : "—"} foot={`started ${health?.started_at?.slice(0, 19).replace("T", " ") ?? ""}`} />
          <Stat label="Database" value={health ? (health.db.ok ? "ok" : "error") : "—"} foot={health?.db.journal_mode ?? ""} />
          <Stat label="Auth" value={health?.auth_mode ?? "—"} foot={`log ${health?.log_level ?? "—"}`} />
        </div>
      </Panel>

      {/* 02 — SIDECARS */}
      <Panel no="02" title="Sidecars">
        {sidecars.length === 0 ? (
          <p className="fine py-1">No sidecars active — installed ZIM content is served on demand.</p>
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
                    <td className="num">{String(s.port ?? "—")}</td>
                    <td>
                      <Badge tone={s.running ? "ok" : "muted"}>{s.running ? "running" : "—"}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {/* 03 — INSTALLED CONTENT */}
      <Panel
        no="03"
        title="Installed content"
        className="mb-4"
        actions={
          <Link className="panel-head-meta linked" to="/library">
            manage
          </Link>
        }
      >
        <p className="fine px-1 py-1">
          <span className="num text-sm">{zimCount}</span> zim archive{zimCount === 1 ? "" : "s"} ·{" "}
          <span className="num text-sm">{manualCount}</span> manual{manualCount === 1 ? "" : "s"} — managed in Library (01).
        </p>
      </Panel>

      {/* 04 — MODULE INDEX */}
      <Panel no="04" title="Module index" flush>
        {MODULES.map((m) => {
          const st = moduleStatus.find((x) => x.name === m.title.toLowerCase());
          const disabled = st?.status === "disabled";
          return (
            <IndexRow
              key={m.no}
              no={m.no}
              title={m.title}
              desc={m.desc}
              href={m.href}
              meta={
                <span className="flex items-center justify-end gap-2">
                  <span className="label">{m.phase === "this" ? "active" : `phase ${m.phase}`}</span>
                  {disabled && <Badge tone="muted">disabled</Badge>}
                  {st && !disabled && <Badge tone={st.status === "ok" ? "ok" : st.status === "degraded" ? "warn" : "muted"}>{st.status}</Badge>}
                </span>
              }
            />
          );
        })}
      </Panel>
    </div>
  );
}