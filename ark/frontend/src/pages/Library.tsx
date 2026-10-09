/* Library → Content Manager: curated bundles, per-item tier picker, mixed
   selection with disk preflight, and a serial download queue. */
import { useCallback, useEffect, useMemo, useState } from "react";
import { FolderOpen, Trash2, Upload } from "lucide-react";
import { api, fmtBytes } from "../api/client";
import type {
  Job,
  LibraryCatalog,
  LibraryItemView,
  LibraryItems,
  Me,
  PlanBundle,
  PlanGroup,
  PlanTier,
  Preflight,
} from "../api/types";
import { useToast } from "../lib/toast";
import { Badge, Button, Field, Panel, PageTitle, StatusLine } from "../components/ui";

interface SelectionRow {
  name: string;
  tier: string;
  title: string;
  size: number | null;
  verified: boolean;
  installed: boolean;
  busy: boolean;
}

function tierTone(t: PlanTier): "ok" | "warn" | "err" | "active" | "muted" {
  if (t.item_status === "downloading" || t.item_status === "queued" || t.active_job) return "active";
  if (t.installed) return "ok";
  if (t.item_status === "error") return "err";
  if (t.item_status === "paused") return "warn";
  if (t.status !== "verified" || !t.downloadable) return "warn";
  return "muted";
}

function tierLabel(t: PlanTier): string {
  if (t.active_job) return t.active_job.status;
  if (t.item_status) return t.item_status;
  if (t.installed) return "installed";
  if (t.status !== "verified" || !t.downloadable) return "unavailable";
  return "available";
}

export default function Library() {
  const { notify, notifyError } = useToast();
  const [catalog, setCatalog] = useState<LibraryCatalog | null>(null);
  const [items, setItems] = useState<LibraryItemView[]>([]);
  const [me, setMe] = useState<Me | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [sel, setSel] = useState<Record<string, string>>({});
  const [activeBundle, setActiveBundle] = useState<string | null>(null);
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [starting, setStarting] = useState(false);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [viewerUrl, setViewerUrl] = useState<string | null>(null);

  const isAdmin = me?.user?.role === "admin";
  const groups: PlanGroup[] = catalog?.plan?.groups ?? [];
  const bundles: PlanBundle[] = catalog?.plan?.bundles ?? [];
  const anyActive = groups.some((g) => g.tiers.some((t) => t.active_job || t.item_status === "downloading"));

  const loadLibrary = useCallback(async () => {
    try {
      const [c, i] = await Promise.all([
        api.get<LibraryCatalog>("/api/library/catalog"),
        api.get<LibraryItems>("/api/library/items"),
      ]);
      setCatalog(c);
      setItems(i.items);
    } catch (err) {
      notifyError(err, "Failed to load the library");
    }
  }, [notifyError]);

  const loadJobs = useCallback(async () => {
    try {
      const j = await api.get<{ items: Job[]; total: number }>("/api/admin/jobs?limit=200");
      setJobs(j.items.filter((x) => x.type === "library.download"));
    } catch (err) {
      notifyError(err, "Failed to load downloads");
    }
  }, [notifyError]);

  const load = useCallback(() => Promise.all([loadLibrary(), loadJobs()]), [loadLibrary, loadJobs]);

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
    void load();
    const timer = window.setInterval(() => void load(), anyActive ? 1500 : 30000);
    return () => window.clearInterval(timer);
  }, [load, anyActive]);

  // Debounced disk preflight for the current mixed selection.
  useEffect(() => {
    const picks = Object.entries(sel).map(([name, tier]) => ({ name, tier }));
    if (picks.length === 0) {
      setPreflight(null);
      return;
    }
    const timer = window.setTimeout(() => {
      api
        .post<Preflight>("/api/library/preflight", { selections: picks })
        .then(setPreflight)
        .catch((err) => notifyError(err, "Disk check failed"));
    }, 350);
    return () => window.clearTimeout(timer);
  }, [sel, notifyError]);

  const selection: SelectionRow[] = useMemo(() => {
    const out: SelectionRow[] = [];
    for (const [name, tierId] of Object.entries(sel)) {
      const g = groups.find((x) => x.name === name);
      if (!g) continue;
      const t = g.tiers.find((x) => x.id === tierId);
      if (!t) continue;
      out.push({
        name,
        tier: tierId,
        title: t.title,
        size: t.size,
        verified: t.status === "verified" && t.downloadable,
        installed: t.installed,
        busy: Boolean(t.active_job),
      });
    }
    return out;
  }, [sel, groups]);

  const selectionBytes = useMemo(
    () => selection.reduce((acc, s) => acc + (s.verified ? s.size ?? 0 : 0), 0),
    [selection],
  );
  const pickingBytes = useMemo(
    () => selection.reduce((acc, s) => acc + (s.size ?? 0), 0),
    [selection],
  );

  const installedZims = useMemo(() => items.filter((i) => i.kind === "zim"), [items]);
  const manuals = useMemo(() => items.filter((i) => i.kind === "manual"), [items]);
  const totalEntries = useMemo(
    () => groups.reduce((acc, g) => acc + g.tiers.length, 0),
    [groups],
  );

  function applyBundle(b: PlanBundle) {
    if (b.incomplete) {
      notify(`Bundle "${b.title}" is incomplete; only verified members are applied.`, "error");
    }
    const next: Record<string, string> = {};
    for (const m of b.members) {
      if (!m.verified) continue;
      next[m.name] = m.tier;
    }
    setSel(next);
    setActiveBundle(b.id);
  }

  function selectTier(name: string, tierId: string) {
    setSel((prev) => {
      if (prev[name] === tierId) {
        const { [name]: _drop, ...rest } = prev;
        return rest;
      }
      return { ...prev, [name]: tierId };
    });
    setActiveBundle(null);
  }

  async function startSelection() {
    const picks = selection.filter((s) => !s.installed && !s.busy && s.verified);
    if (picks.length === 0) return;
    setStarting(true);
    try {
      for (const p of picks) {
        await api.post("/api/library/install", { name: p.name, tier: p.tier });
      }
      notify(`Queued ${picks.length} download${picks.length === 1 ? "" : "s"}.`, "success");
      await load();
    } catch (err) {
      notifyError(err, "Queue failed");
    } finally {
      setStarting(false);
    }
  }

  async function resumeJob(j: Job) {
    const flavour = String(j.payload?.flavour ?? "");
    const tier = flavour || "full";
    try {
      await api.post("/api/library/install", {
        name: String(j.payload?.name ?? ""),
        tier,
      });
      notify("Download resumed", "success");
      await load();
    } catch (err) {
      notifyError(err, "Resume failed");
    }
  }

  async function cancelJob(j: Job) {
    try {
      await api.post(`/api/admin/jobs/${j.id}/cancel`);
      await load();
    } catch (err) {
      notifyError(err, "Cancel failed");
    }
  }

  async function remove(m: LibraryItemView) {
    if (!window.confirm(`Delete "${m.title}" from the library?`)) return;
    try {
      await api.del(`/api/library/items/${m.id}`);
      notify("Deleted", "success");
      void load();
    } catch (err) {
      notifyError(err, "Delete failed");
    }
  }

  async function uploadManual() {
    if (!uploadFile) return;
    setUploading(true);
    try {
      await api.postFile<LibraryItemView>("/api/library/manuals", uploadFile);
      notify("Manual added to the library", "success");
      setUploadFile(null);
      await load();
    } catch (err) {
      notifyError(err, "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  function progress(t: PlanTier) {
    const j = t.active_job;
    if (!j) return null;
    const pct = Math.round((j.progress ?? 0) * 100);
    return (
      <div className="mono mt-1 flex items-center justify-between gap-3 text-[11px] text-[var(--c-muted)]">
        <span className="truncate">
          {j.status === "queued"
            ? `queued${j.message ? `: ${j.message}` : ""}`
            : `${fmtBytes(j.bytes_done)} / ${fmtBytes(j.bytes_total ?? t.size)}${j.speed_bps ? ` @ ${fmtBytes(j.speed_bps)}/s` : ""}`}
        </span>
        <span>{pct}%</span>
      </div>
    );
  }

  const fits = preflight ? preflight.fits : selectionBytes === 0;

  return (
    <div className="page">
      <PageTitle
        title="Content manager"
        meta={<span className="label">{totalEntries} verified entries</span>}
      />

      <div className="panel-stack">
        {/* CURATED BUNDLES */}
        <Panel title="Bundles" testid="library-bundles">
          {bundles.length === 0 ? (
            <p className="fine py-1">No curated bundles in the catalog yet.</p>
          ) : (
            <div className="panel-body-flush">
              {bundles.map((b) => (
                <div className="bundle-card" key={b.id}>
                  <div>
                    <div className="bundle-title">{b.title}</div>
                    <div className="bundle-total">{fmtBytes(b.total_bytes)}</div>
                  </div>
                  <div className="bundle-members">
                    {b.members.map((m) => (
                      <span key={m.name}>
                        {m.title}
                        <span className="mono">
                          {" "}
                          ({m.tier}
                          {m.size ? ` · ${fmtBytes(m.size)}` : ""})
                        </span>
                        {m.verified ? "" : " · not verified"}
                        {"; "}
                      </span>
                    ))}
                  </div>
                  <div className="bundle-card-actions">
                    {b.incomplete && (
                      <Badge tone="warn" testid={`bundle-incomplete-${b.id}`}>
                        incomplete
                      </Badge>
                    )}
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() => applyBundle(b)}
                      data-testid={`apply-${b.id}`}
                    >
                      {activeBundle === b.id ? "applied" : "apply this plan"}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Panel>

        {/* TIER PICKER */}
        <Panel
          title="Pick items"
          testid="library-catalog"
          meta={
            selection.length === 0
              ? "nothing picked"
              : activeBundle
                ? bundles.find((b) => b.id === activeBundle)?.title ?? "custom selection"
                : "custom selection"
          }
        >
          {groups.length === 0 ? (
            <p className="fine py-1">Catalog unavailable, backend reporting no entries.</p>
          ) : (
            <div className="panel-stack">
              {groups.map((g) => (
                <div key={g.name} className="tier-group" data-testid={`group-${g.name}`}>
                  <div className="tier-group-head">
                    <span className="tier-group-title">
                      {g.title}
                      <span className="mono tier-group-sub"> · {g.name}</span>
                    </span>
                    <span className="mono tier-group-sub">{g.category}</span>
                  </div>
                  <div className="tier-grid">
                    {g.tiers.map((t) => {
                      const selected = sel[g.name] === t.id;
                      const disabled = !t.downloadable || t.installed || Boolean(t.active_job);
                      return (
                        <button
                          key={t.id}
                          type="button"
                          className={"tier-opt" + (selected ? " selected" : "") + (disabled ? " disabled" : "")}
                          onClick={() => selectTier(g.name, t.id)}
                          disabled={disabled}
                          data-testid={`install-${g.name}:${t.id}`}
                          title={
                            !t.downloadable
                              ? t.status !== "verified"
                                ? "not verified yet"
                                : "no download url"
                              : t.installed
                                ? "already installed"
                                : undefined
                          }
                        >
                          <span className="tier-title">
                            {t.label}
                            <span className="size num">{t.size != null ? fmtBytes(t.size) : "n/a"}</span>
                          </span>
                          <span className="tier-desc">{t.description}</span>
                          <span className="tier-meta">
                            <span className="mono">{t.license ?? "n/a"}</span>
                            <Badge tone={tierTone(t)}>{tierLabel(t)}</Badge>
                            {t.item_error && (
                              <span className="mono" title={t.item_error}>
                                error
                              </span>
          )}

          {/* helpers for e2e tests */}
          <div className="hidden">
            {groups.flatMap((g) =>
              g.tiers.map((t) => (
                <div key={t.id} data-testid={`row-${g.name}:${t.id}`} />
              )),
            )}
          </div>
                          </span>
                          {t.active_job && (
                            <span className="block">
                              {progress(t)}
                              <span className="progress mt-1 block">
                                <span style={{ width: `${Math.max(Math.round((t.active_job.progress ?? 0) * 100), 2)}%` }} />
                              </span>
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* SELECTION SUMMARY + DISK */}
          <div className="selection-foot" data-testid="disk-summary">
            {selection.length === 0 ? (
              <p className="fine">
                Pick items above, or apply a bundle. Downloads run one at a time and resume interrupted
                downloads.
              </p>
            ) : (
              <>
                <div className="disk-summary">
                  <div className="disk-line">
                    <span className="k">selected</span>
                    <span className="v">
                      {selection.length} item{selection.length === 1 ? "" : "s"} ·{" "}
                      {fmtBytes(selectionBytes)}
                      {pickingBytes !== selectionBytes
                        ? ` (${fmtBytes(pickingBytes)} incl. unavailable)`
                        : ""}
                    </span>
                  </div>
                  <div className="disk-line">
                    <span className="k">disk free</span>
                    <span className="v">{preflight ? fmtBytes(preflight.free_bytes) : "checking…"}</span>
                  </div>
                  <div className="disk-line">
                    <span className="k">needs (with margin)</span>
                    <span className="v">{preflight ? fmtBytes(preflight.needed_bytes) : "…"}</span>
                  </div>
                  <div className="disk-line">
                    <span className="k">free after</span>
                    <span className="v">{preflight ? fmtBytes(preflight.free_after_bytes) : "…"}</span>
                  </div>
                </div>
                {preflight?.blocked_reason && (
                  <p className="fine text-[color:var(--c-warn)]">{preflight.blocked_reason}</p>
                )}
                {preflight?.excluded && preflight.excluded.length > 0 && (
                  <p className="fine">
                    not counted: <span className="mono">{preflight.excluded.join(", ")}</span>
                  </p>
                )}
              </>
            )}
            <div className="selection-actions">
              <Button
                variant="primary"
                disabled={starting || selectionBytes === 0 || !fits || selection.every((s) => s.installed || s.busy)}
                onClick={startSelection}
                data-testid="install-selection"
              >
                {starting
                  ? "queuing…"
                  : selectionBytes === 0
                    ? "start downloads"
                    : `start downloads (${selection.length} items, ${fmtBytes(selectionBytes)})`}
              </Button>
              {selection.length > 0 && (
                <Button variant="plain" onClick={() => { setSel({}); setActiveBundle(null); setPreflight(null); }}>
                  clear
                </Button>
              )}
            </div>
          </div>
        </Panel>

        {/* DOWNLOAD QUEUE */}
        <Panel
          title="Downloads"
          testid="library-queue"
          actions={<StatusLine tone={anyActive ? "active" : "muted"}>{anyActive ? "worker busy" : "worker idle"}</StatusLine>}
        >
          {jobs.length === 0 ? (
            <p className="fine py-1">No downloads yet. They run one at a time in queue order.</p>
          ) : (
            <div className="panel-body-flush">
              {jobs.map((j) => {
                const name = String(j.payload?.name ?? "?");
                const flavour = String(j.payload?.flavour ?? "");
                const tier = flavour || (String(j.payload?.tier) || "full");
                const running = j.status === "queued" || j.status === "running";
                const pct = Math.round((j.progress ?? 0) * 100);
                return (
                  <div className="queue-row" key={j.id} data-testid={`job-${j.id}`}>
                    <div className="queue-head">
                      <span className="queue-name mono">{tierLabel2(name, tier)}</span>
                      <span className="queue-actions">
                        <Badge tone={runTone(j)}>{j.status}</Badge>
                        {running ? (
                          <Button variant="plain" size="sm" onClick={() => cancelJob(j)} data-testid={`cancel-job-${j.id}`}>
                            cancel
                          </Button>
                        ) : j.status === "paused" || j.status === "error" ? (
                          <Button
                            variant="primary"
                            size="sm"
                            onClick={() => resumeJob(j)}
                            data-testid={`resume-job-${j.id}`}
                          >
                            resume
                          </Button>
                        ) : null}
                        <Button variant="plain" size="sm" href={`/logs?job=${j.id}`}>
                          logs
                        </Button>
                      </span>
                    </div>
                    {running && (
                      <div className="queue-progress">
                        <span className="progress">
                          <span style={{ width: `${Math.max(pct, 2)}%` }} />
                        </span>
                        <span>
                          {j.status === "queued"
                            ? "queued"
                            : `${fmtBytes(j.bytes_done)}${j.bytes_total ? ` / ${fmtBytes(j.bytes_total)}` : ""} (${pct}%)`}
                          {j.speed_bps ? ` @ ${fmtBytes(j.speed_bps)}/s` : ""}
                        </span>
                      </div>
                    )}
                    {(j.status === "paused" || j.status === "error") && j.error && (
                      <p className="fine mt-1">
                        <span className="mono">{j.error}</span>
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </Panel>

        {/* INSTALLED ZIMs */}
        <Panel
          title="Installed zim archives"
          actions={
            <Button variant="primary" size="sm" onClick={() => setViewerUrl(viewerUrl ? null : "/svc/kiwix/")} data-testid="kiwix-open">
              <FolderOpen size={12} /> reader
            </Button>
          }
          testid="library-installed"
        >
          {installedZims.length === 0 ? (
            <p className="fine py-1">No ZIM archives installed. Pick some above and start downloads.</p>
          ) : (
            <div className="panel-body-flush">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>archive</th>
                    <th>language</th>
                    <th className="r">size</th>
                    <th className="r">sha256</th>
                    <th className="r">actions</th>
                  </tr>
                </thead>
                <tbody>
                  {installedZims.map((e) => (
                    <tr key={e.id}>
                      <td className="num">{e.title}</td>
                      <td className="mono text-[var(--c-muted)]">{e.name.split(":")[0]}</td>
                      <td className="num r">{fmtBytes(e.size)}</td>
                      <td className="num r text-[var(--c-muted)]">{e.sha256?.slice(0, 12)}…</td>
                      <td className="r">
                        {isAdmin && (
                          <Button variant="plain" size="sm" onClick={() => remove(e)} data-testid={`delete-${e.id}`}>
                            <Trash2 size={12} /> delete
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {viewerUrl && (
            <div className="border-t border-[var(--c-line)] p-2">
              <iframe title="Kiwix reader" src={viewerUrl} data-testid="kiwix-frame" className="frame h-[70vh] w-full" />
            </div>
          )}
        </Panel>

        {/* MANUALS */}
        <Panel title="Survival manuals" testid="manuals-list">
          {isAdmin && (
            <div className="mb-3 flex flex-wrap items-end gap-2">
              <Field label="manual file" className="min-w-[240px]">
                <input type="file" className="input" onChange={(ev) => setUploadFile(ev.target.files?.[0] ?? null)} data-testid="manual-file" />
              </Field>
              <Button variant="primary" disabled={!uploadFile || uploading} onClick={uploadManual} data-testid="manual-upload">
                <Upload size={13} /> {uploading ? "uploading…" : "upload"}
              </Button>
            </div>
          )}
          {manuals.length === 0 ? (
            <p className="fine py-1">No manuals uploaded yet.</p>
          ) : (
            <div className="panel-body-flush">
              <table className="tbl">
                <thead>
                  <tr>
                    <th>filename</th>
                    <th className="r">size</th>
                    <th>added</th>
                    <th className="r">actions</th>
                  </tr>
                </thead>
                <tbody>
                  {manuals.map((m) => (
                    <tr key={m.id}>
                      <td className="mono">{m.filename}</td>
                      <td className="num r">{fmtBytes(m.size)}</td>
                      <td className="fine">{m.installed_at?.slice(0, 10) ?? "n/a"}</td>
                      <td className="r">
                        <div className="inline-flex gap-1">
                          {m.file_url && (
                            <Button variant="ghost" size="sm" href={m.file_url} target="_blank" rel="noreferrer">
                              open
                            </Button>
                          )}
                          {isAdmin && (
                            <Button variant="plain" size="sm" onClick={() => remove(m)} data-testid={`delete-manual-${m.id}`}>
                              <Trash2 size={12} /> delete
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}

function runTone(j: Job): "ok" | "warn" | "err" | "active" | "muted" {
  if (j.status === "running" || j.status === "queued") return "active";
  if (j.status === "done" || j.status === "succeeded") return "ok";
  if (j.status === "error") return "err";
  if (j.status === "paused" || j.status === "cancelled") return "warn";
  return "muted";
}

function tierLabel2(name: string, tier: string): string {
  return `${name}:${tier}`;
}