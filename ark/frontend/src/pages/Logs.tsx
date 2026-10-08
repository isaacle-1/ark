/* Logs page — live SSE tail, filter, pause, copy-as-text, download. Admin. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Copy, Download, Pause, Play, X } from "lucide-react";
import { api } from "../api/client";
import type { LogEntry, LogFile, LogHistory } from "../api/types";
import { useToast } from "../lib/toast";
import { Button, Field, Panel, PageTitle, StatusLine } from "../components/ui";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;
const MAX_ROWS = 2000;

function LevelChip({ level }: { level: string }) {
  return (
    <span className="mono text-[11px] font-bold">
      <span className={level === "ERROR" || level === "CRITICAL" ? "text-[color:var(--c-err)]" : level === "WARNING" ? "text-[color:var(--c-warn)]" : level === "DEBUG" ? "text-[var(--c-muted)]" : "text-[var(--c-ink)]"}>
        {level}
      </span>
    </span>
  );
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

  useEffect(() => {
    if (paused || file !== "ark.log") {
      setConnected(false);
      return;
    }
    let closed = false;
    const es = new EventSource(`/api/admin/logs/stream?${fullQuery}&history=0`);
    es.onopen = () => !closed && setConnected(true);
    es.onerror = () => {
      if (!closed) setConnected(false);
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

  const liveTone = paused ? "warn" : connected ? "ok" : "err";
  const liveText = paused ? "paused" : connected ? "live" : "connecting…";

  return (
    <div className="mx-auto max-w-7xl">
      <PageTitle
        title="Log stream"
        meta={
          <span data-testid="log-status">
            <StatusLine tone={liveTone} className="!text-[11px]">
              {liveText}
            </StatusLine>
          </span>
        }
      />

      <Panel no="01" title="Filter" className="mb-3">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="source" className="min-w-[180px]">
            <select className="input" value={file} onChange={(e) => setFile(e.target.value)}>
              {files.map((f) => (
                <option key={f.name} value={f.name}>
                  {f.name}
                  {f.mtime ? ` (${new Date(f.mtime * 1000).toLocaleDateString()})` : ""}
                </option>
              ))}
            </select>
          </Field>
          <Field label="level ≥" className="w-28">
            <select className="input" value={level} onChange={(e) => setLevel(e.target.value)}>
              {LEVELS.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </Field>
          <Field label="module" className="w-44">
            <input
              className="input"
              placeholder="library, maps…"
              value={module}
              onChange={(e) => setModule(e.target.value)}
            />
          </Field>
          <Field label="request id" className="w-56">
            <input
              className="input"
              placeholder="request_id"
              value={reqId}
              onChange={(e) => setReqId(e.target.value)}
            />
          </Field>
          <Field label="contains" className="w-44">
            <input
              className="input"
              placeholder="text contains…"
              value={contains}
              onChange={(e) => setContains(e.target.value)}
            />
          </Field>
          <div className="flex flex-wrap gap-2">
            <Button variant={paused ? "primary" : "ghost"} onClick={togglePause} data-testid="pause-btn">
              {paused ? (
                <>
                  <Play size={12} /> resume
                </>
              ) : (
                <>
                  <Pause size={12} /> pause
                </>
              )}
            </Button>
            <Button onClick={copyText}>
              <Copy size={12} /> copy
            </Button>
            <Button href={`/api/admin/logs/download?file=${encodeURIComponent(file)}`}>
              <Download size={12} /> download
            </Button>
            <Button
              variant="plain"
              onClick={() => {
                setRows([]);
                seen.current.clear();
              }}
            >
              <X size={12} /> clear
            </Button>
          </div>
        </div>
        {file !== "ark.log" && (
          <p className="fine mt-2">Live tail is limited to ark.log this phase.</p>
        )}
      </Panel>

      <Panel no="02" title="Lines" flush testid="log-panel">
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
      </Panel>

      {detail && <DetailPanel entry={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}

function key(e: LogEntry): string {
  return `${e.ts}|${e.level}|${e.logger}|${e.msg}|${e.request_id ?? ""}`;
}

function Row({ entry, onExpand }: { entry: LogEntry; onExpand: () => void }) {
  return (
    <tr onDoubleClick={onExpand} className="border-b border-[var(--c-line)]/60 hover:bg-[var(--c-panel2)]">
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
    <Panel no="·" title="Full json line" className="border-[color:var(--c-accent)]" actions={<Button variant="plain" onClick={onClose}>close</Button>}>
      <pre className="overflow-x-auto p-2 text-[12px]" style={{ background: "var(--c-surface)", border: "1px solid var(--c-line)" }}>
        {JSON.stringify(entry, null, 1)}
      </pre>
    </Panel>
  );
}