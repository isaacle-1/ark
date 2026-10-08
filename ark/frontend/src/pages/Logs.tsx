/* Logs page — live SSE tail, filter, pause, copy-as-text, download. Admin. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { LogEntry, LogFile, LogHistory } from "../api/types";
import { useToast } from "../lib/toast";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
const MAX_ROWS = 2000;

function LevelChip({ level }: { level: string }) {
  const color =
    level === "ERROR" || level === "CRITICAL"
      ? "text-[color:var(--c-err)]"
      : level === "WARNING"
        ? "text-[color:var(--c-warn)]"
        : level === "DEBUG"
          ? "text-[var(--c-muted)]"
          : "text-[var(--c-ink)]";
  return <span className={"font-mono text-[11px] font-bold " + color}>{level}</span>;
}

export default function LogsPage() {
  const { notify, notifyError } = useToast();
  const [files, setFiles] = useState<LogFile[]>([]);
  const [file, setFile] = useState("ark.log");
  const [level, setLevel] = useState<string>("INFO");
  const [module, setModule] = useState("");
  const [reqId, setReqId] = useState("");
  const [contains, setContains] = useState("");
  const [paused, setPaused] = useState(false);
  const [connected, setConnected] = useState(false);
  const [rows, setRows] = useState<LogEntry[]>([]);
  const [detail, setDetail] = useState<LogEntry | null>(null);
  const seen = useRef<Set<string>>(new Set());

  const query = useMemo(
    () =>
      new URLSearchParams({
        file,
        level,
        ...(module ? { module } : {}),
        ...(reqId ? { request_id: reqId } : {}),
        ...(contains ? { contains } : {}),
      }),
    [file, level, module, reqId, contains],
  );

  const fullQuery = query.toString();

  useEffect(() => {
    api
      .get<{ files: LogFile[] }>("/api/admin/logs/files")
      .then((r) => setFiles(r.files))
      .catch((e) => notifyError(e, "Cannot list log files"));
  }, [notifyError]);

  // Reset + (re)load history whenever filters change.
  useEffect(() => {
    let alive = true;
    setRows([]);
    seen.current.clear();
    setConnected(false);
    api
      .get<LogHistory>(`/api/admin/logs/history?${fullQuery}`)
      .then((h) => {
        if (!alive) return;
        setRows(h.items.slice(-MAX_ROWS));
        h.items.forEach((it) => seen.current.add(key(it)));
      })
      .catch((e) => notifyError(e, "Cannot load log history"));
    return () => {
      alive = false;
    };
  }, [fullQuery, notifyError]);

  // Live SSE tail (only for ark.log in this phase; module logs via history).
  useEffect(() => {
    if (paused || file !== "ark.log") {
      setConnected(false);
      return;
    }
    let closed = false;
    const es = new EventSource(`/api/admin/logs/stream?${fullQuery}&history=0`);
    es.onopen = () => !closed && setConnected(true);
    es.onerror = () => {
      if (!closed) {
        setConnected(false);
        setTimeout(() => "reconnecting…", 0);
      }
    };
    es.onmessage = (ev) => {
      if (closed) return;
      try {
        const entry = JSON.parse(ev.data) as LogEntry;
        const k = key(entry);
        if (seen.current.has(k)) {
          seen.current.delete(k);
          return;
        }
        setRows((r) => [...r.slice(-(MAX_ROWS - 1)), entry]);
      } catch {
        /* skip malformed line */
      }
    };
    return () => {
      closed = true;
      es.close();
    };
  }, [fullQuery, paused, file]);

  const visibleText = useMemo(
    () =>
      rows
        .map((r) =>
          [
            r.ts,
            r.level,
            r.logger,
            r.request_id ?? "",
            r.msg,
            Object.entries(r)
              .filter(([k]) => !["ts", "level", "logger", "request_id", "msg"].includes(k))
              .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
              .join(" "),
          ]
            .join("\t")
            .trimEnd(),
        )
        .join("\n"),
    [rows],
  );

  function copyText() {
    void navigator.clipboard.writeText(visibleText).then(
      () => undefined,
      () => notify("Clipboard blocked — select text to copy manually", "info"),
    );
  }

  const togglePause = useCallback(() => setPaused((p) => !p), []);

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold">Logs</h1>
        <div className="flex items-center gap-2 text-sm">
          <span
            className={
              "badge " + (paused ? "bg-[var(--c-panel2)] text-[var(--c-muted)]" : connected ? "bg-[color:var(--c-ok)]/15 text-[color:var(--c-ok)]" : "bg-[color:var(--c-warn)]/15 text-[color:var(--c-warn)]")
            }
            data-testid="log-status"
          >
            {paused ? "paused" : connected ? "live" : "connecting…"}
          </span>
          {file !== "ark.log" && (
            <span className="text-xs text-[var(--c-muted)]">live tail limited to ark.log this phase</span>
          )}
        </div>
      </div>

      <div className="card flex flex-wrap items-center gap-2">
        <select className="input !w-auto" value={file} onChange={(e) => setFile(e.target.value)}>
          {files.map((f) => (
            <option key={f.name} value={f.name}>
              {f.name} ({f.mtime ? new Date(f.mtime * 1000).toLocaleDateString() : ""})
            </option>
          ))}
        </select>
        <select className="input !w-auto" value={level} onChange={(e) => setLevel(e.target.value)}>
          {LEVELS.map((l) => (
            <option key={l} value={l}>
              ≥ {l}
            </option>
          ))}
        </select>
        <input
          className="input !w-40"
          placeholder="module (library, maps…)"
          value={module}
          onChange={(e) => setModule(e.target.value)}
        />
        <input
          className="input !w-40"
          placeholder="request_id"
          value={reqId}
          onChange={(e) => setReqId(e.target.value)}
        />
        <input
          className="input !w-40"
          placeholder="text contains…"
          value={contains}
          onChange={(e) => setContains(e.target.value)}
        />
        <button className="btn btn-ghost" onClick={togglePause} data-testid="pause-btn">
          {paused ? "Resume" : "Pause"}
        </button>
        <button className="btn btn-ghost" onClick={copyText}>
          Copy as text
        </button>
        <a className="btn btn-ghost" href={`/api/admin/logs/download?file=${encodeURIComponent(file)}`}>
          Download
        </a>
        <button className="btn btn-ghost" onClick={() => { setRows([]); seen.current.clear(); }}>
          Clear view
        </button>
      </div>

      <div className="card overflow-hidden !p-0">
        <div className="max-h-[65vh] overflow-y-auto font-mono text-[12px] leading-5" data-testid="log-rows">
          {rows.length === 0 ? (
            <div className="p-4 text-[var(--c-muted)]">No matching lines yet…</div>
          ) : (
            <table className="w-full border-collapse">
              <tbody>
                {rows.map((r, i) => (
                  <Row key={i} entry={r} onExpand={() => setDetail(r)} />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {detail && <DetailPanel entry={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}

function key(e: LogEntry): string {
  return `${e.ts}|${e.level}|${e.logger}|${e.msg}|${e.request_id ?? ""}`;
}

function Row({ entry, onExpand }: { entry: LogEntry; onExpand: () => void }) {
  return (
    <tr onDoubleClick={onExpand} className="border-b border-[var(--c-line)]/40 hover:bg-[var(--c-panel2)]">
      <td className="whitespace-nowrap px-2 py-0.5 text-[var(--c-muted)]">{entry.ts}</td>
      <td className="whitespace-nowrap px-2 py-0.5">
        <LevelChip level={entry.level} />
      </td>
      <td className="max-w-[10rem] truncate px-2 py-0.5 text-[var(--c-muted)]">{entry.logger}</td>
      <td className="max-w-[30rem] truncate px-2 py-0.5">{entry.msg}</td>
      {entry.request_id ? (
        <td className="whitespace-nowrap px-2 py-0.5 text-[color:var(--c-accent)]">{entry.request_id}</td>
      ) : (
        <td />
      )}
    </tr>
  );
}

function DetailPanel({ entry, onClose }: { entry: LogEntry; onClose: () => void }) {
  return (
    <div className="card border-[color:var(--c-accent)]">
      <div className="flex items-center justify-between">
        <div className="font-semibold">Full JSON log line</div>
        <button className="btn btn-ghost" onClick={onClose}>
          Close
        </button>
      </div>
      <pre className="mt-2 overflow-x-auto rounded-md bg-[var(--c-surface)] p-3 text-[12px]">
        {JSON.stringify(entry, null, 1)}
      </pre>
    </div>
  );
}