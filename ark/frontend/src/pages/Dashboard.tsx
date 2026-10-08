/* Dashboard: module tiles + live system status. */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { fmtBytes, fmtDuration } from "../api/client";
import type { Health } from "../api/types";
import { useToast } from "../lib/toast";

interface ModuleDef {
  key: string;
  title: string;
  desc: string;
  phase: string;
  planned: boolean;
  href?: string;
}

const MODULES: ModuleDef[] = [
  { key: "library", title: "Information Library", desc: "Offline Kiwix ZIMs + uploaded manuals", phase: "Phase 1", planned: false, href: "/library" },
  { key: "content", title: "Content Manager", desc: "Catalogs, resumable downloads, sideload", phase: "Phase 1", planned: true },
  { key: "notes", title: "Notes", desc: "Fast offline Markdown", phase: "Phase 2", planned: true },
  { key: "toolbox", title: "Computer Toolbox", desc: "Encryption, encodings, calculators, QR", phase: "Phase 2", planned: true },
  { key: "ai", title: "AI + RAG", desc: "Local LLM chat with sources it cites", phase: "Phase 3", planned: true },
  { key: "manuals", title: "Manuals Library", desc: "PDFs, OCR, FTS, browse by brand/model", phase: "Phase 4", planned: true },
  { key: "maps", title: "Offline Maps", desc: "PMTiles, routing, GPS, MGRS", phase: "Phase 5", planned: true },
  { key: "videos", title: "Video Platform", desc: "Curated offline videos + comms guides", phase: "Phase 6", planned: true },
  { key: "radio", title: "Radio Reference", desc: "Frequencies, band plans, repeater lists", phase: "Phase 7", planned: true },
  { key: "translate", title: "Translation", desc: "Offline Argos/OPUS machine translation", phase: "Phase 7", planned: true },
  { key: "docs", title: "Document Suite", desc: "docx/xlsx/pdf view & edit", phase: "Phase 8", planned: true },
  { key: "admin", title: "System", desc: "Logs, jobs, settings, backups (Phase 9)", phase: "this phase", planned: false, href: "/logs" },
];

function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={"badge " + (ok ? "bg-[color:var(--c-ok)]/15 text-[color:var(--c-ok)]" : "bg-[color:var(--c-warn)]/15 text-[color:var(--c-warn)]")}
    >
      {label}
    </span>
  );
}

export default function Dashboard() {
  const { notifyError } = useToast();
  const [health, setHealth] = useState<Health | null>(null);
  const [polling, setPolling] = useState(true);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .get<Health>("/api/health")
        .then((h) => alive && setHealth(h))
        .catch((e) => alive && notifyError(e, "Failed to load system status"));
    void load();
    const timer = window.setInterval(() => polling && load(), 10000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [polling, notifyError]);

  const moduleStatus = health?.modules ?? [];

  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Dashboard</h1>
          <p className="text-sm text-[var(--c-muted)]">ARK {health?.version ?? "…"} · {health?.ark_home ?? ""}</p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-sm text-[var(--c-muted)]">
            <input
              type="checkbox"
              checked={polling}
              onChange={(ev) => setPolling(ev.target.checked)}
              className="accent-[var(--c-accent)]"
            />
            live poll
          </label>
          <StatusDot ok={health?.status === "ok"} label={health?.status ?? "loading"} />
        </div>
      </div>

      {health && health.problems.length > 0 && (
        <div className="card mb-4 border-[color:var(--c-warn)]">
          <div className="font-semibold text-[color:var(--c-warn)]">Degraded</div>
          <ul className="mt-1 list-inside list-disc text-sm text-[var(--c-muted)]">
            {health.problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <div className="card">
          <div className="text-xs uppercase tracking-wide text-[var(--c-muted)]">Disk free</div>
          <div className="mt-1 text-lg font-semibold">{fmtBytes(health?.disk.free_bytes)}</div>
          <div className="text-xs text-[var(--c-muted)]">{health?.disk.free_pct ?? 0}% of {fmtBytes(health?.disk.total_bytes)}</div>
        </div>
        <div className="card">
          <div className="text-xs uppercase tracking-wide text-[var(--c-muted)]">Memory</div>
          <div className="mt-1 text-lg font-semibold">{health?.system.memory_percent ?? 0}%</div>
          <div className="text-xs text-[var(--c-muted)]">{fmtBytes(health?.system.memory_used_bytes)} / {fmtBytes(health?.system.memory_total_bytes)}</div>
        </div>
        <div className="card">
          <div className="text-xs uppercase tracking-wide text-[var(--c-muted)]">CPU</div>
          <div className="mt-1 text-lg font-semibold">{health?.system.cpu_percent ?? 0}%</div>
          <div className="text-xs text-[var(--c-muted)]">busy</div>
        </div>
        <div className="card">
          <div className="text-xs uppercase tracking-wide text-[var(--c-muted)]">Uptime</div>
          <div className="mt-1 text-lg font-semibold">{fmtDuration(health?.uptime_seconds)}</div>
          <div className="text-xs text-[var(--c-muted)]">pid {health?.pid ?? "—"}</div>
        </div>
        <div className="card">
          <div className="text-xs uppercase tracking-wide text-[var(--c-muted)]">Database</div>
          <div className="mt-1 text-lg font-semibold">
            {health?.db.ok ? "ok" : "ERROR"}
          </div>
          <div className="text-xs text-[var(--c-muted)]">{health?.db.journal_mode ?? ""}</div>
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {MODULES.map((m) => {
          const st = moduleStatus.find((x) => x.name === m.key);
          const disabled = st?.status === "disabled";
          return (
            <div key={m.key} className={"card transition-opacity " + (disabled ? "opacity-50" : "")}>
              <div className="flex items-start justify-between gap-2">
                <div className="font-semibold">{m.title}</div>
                <span className={"badge " + (m.planned ? "bg-[var(--c-panel2)] text-[var(--c-muted)]" : "bg-[color:var(--c-accent)]/15 text-[var(--c-accent)]")}>
                  {m.planned ? m.phase : "active"}
                </span>
              </div>
              <p className="mt-1 text-sm text-[var(--c-muted)]">{m.desc}</p>
              <div className="mt-3">
                {st ? (
                  <span className="text-xs text-[var(--c-muted)]">
                    {st.status}: {st.reason ?? "healthy"}
                  </span>
                ) : m.planned ? (
                  <span className="text-xs text-[var(--c-muted)]">arrives in {m.phase}</span>
                ) : null}
              </div>
              {!m.planned && m.href && (
                <Link className="btn btn-ghost mt-3" to={m.href}>
                  Open
                </Link>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}