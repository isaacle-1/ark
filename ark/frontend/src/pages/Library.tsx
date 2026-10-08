/* Library: offline Kiwix ZIMs + uploaded manuals. */
import { useCallback, useEffect, useMemo, useState } from "react";
import { FolderOpen, Trash2, Upload } from "lucide-react";
import { api, fmtBytes } from "../api/client";
import type { LibraryCatalog, LibraryEntry, LibraryItemView, LibraryItems, Me } from "../api/types";
import { useToast } from "../lib/toast";
import { Badge, Button, Field, Num, Panel, PageTitle, StatusLine } from "../components/ui";

function entryTone(e: LibraryEntry): "ok" | "warn" | "err" | "active" | "muted" {
  if (e.item_status === "downloading" || e.item_status === "queued" || e.active_job) return "active";
  if (e.installed) return "ok";
  if (e.item_status === "error") return "err";
  if (e.item_status === "paused") return "warn";
  if (e.status !== "verified") return "warn";
  return "muted";
}

function entryLabel(e: LibraryEntry): string {
  if (e.active_job) return e.active_job.status;
  if (e.item_status) return e.item_status;
  if (e.installed) return "installed";
  if (e.status !== "verified") return "unverified";
  return "available";
}

const CATS = ["wikipedia", "wikibooks", "gutenberg", "ifixit", "devdocs"];

function keyOf(e: LibraryEntry): string {
  return e.flavour ? `${e.name}:${e.flavour}` : e.name;
}

export default function Library() {
  const { notify, notifyError } = useToast();
  const [catalog, setCatalog] = useState<LibraryCatalog | null>(null);
  const [items, setItems] = useState<LibraryItemView[]>([]);
  const [me, setMe] = useState<Me | null>(null);
  const [busyName, setBusyName] = useState<string | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [viewerUrl, setViewerUrl] = useState<string | null>(null);

  const isAdmin = me?.user?.role === "admin";
  const anyActive = catalog?.entries.some((e) => e.active_job) ?? false;

  const load = useCallback(async () => {
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

  const entries = useMemo(() => {
    const list = catalog?.entries ?? [];
    return CATS.flatMap((cat) =>
      list
        .filter((e) => e.category === cat)
        .sort((a, b) => (b.tier ?? 0) - (a.tier ?? 0) || a.title.localeCompare(b.title)),
    ).concat(list.filter((e) => !CATS.includes(e.category)));
  }, [catalog]);

  const installedZims = useMemo(() => entries.filter((e) => e.installed), [entries]);
  const manuals = useMemo(() => items.filter((i) => i.kind === "manual"), [items]);
  const verified = useMemo(() => entries.filter((e) => e.status === "verified").length, [entries]);

  async function install(e: LibraryEntry) {
    setBusyName(e.name);
    try {
      await api.post("/api/library/install", { name: e.name, flavour: e.flavour ?? undefined });
      notify(`Download queued: ${e.title}`, "success");
      await load();
    } catch (err) {
      notifyError(err, "Install failed");
    } finally {
      setBusyName(null);
    }
  }

  async function cancelJob(e: LibraryEntry) {
    if (!e.active_job) return;
    try {
      await api.post(`/api/admin/jobs/${e.active_job.id}/cancel`);
    } catch (err) {
      notifyError(err, "Cancel failed");
    }
  }

  async function remove(e: LibraryEntry) {
    if (!e.item) return;
    if (!window.confirm(`Remove "${e.title}" from the library?`)) return;
    try {
      await api.del(`/api/library/items/${e.item.id}`);
      notify("Removed", "success");
      void load();
    } catch (err) {
      notifyError(err, "Delete failed");
    }
  }

  async function removeManual(m: LibraryItemView) {
    if (!window.confirm(`Delete uploaded manual "${m.filename}"?`)) return;
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

  function progress(e: LibraryEntry) {
    const j = e.active_job;
    if (!j) return;
    const pct = Math.round((j.progress ?? 0) * 100);
    return (
      <div className="min-w-[190px]">
        <div className="mono flex items-center justify-between gap-3 text-[10px] text-[var(--c-muted)]">
          <span className="truncate">
            {j.status === "queued"
              ? `queued · ${j.message ?? ""}`
              : `${fmtBytes(j.bytes_done)} / ${fmtBytes(j.bytes_total ?? e.size)}${j.speed_bps ? ` @ ${fmtBytes(j.speed_bps)}/s` : ""}`}
          </span>
          <span>{pct}%</span>
        </div>
        <div className="progress mt-1">
          <span style={{ width: `${Math.max(pct, 2)}%` }} />
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl">
      <PageTitle
        kicker="01 · information acquisition"
        title="Library"
        meta={
          <span className="mono text-[11px] uppercase tracking-widest text-[var(--c-muted)]">
            {verified} verified entries · fixed & checksummed
          </span>
        }
      />

      {/* 01 — INSTALLED ZIMs */}
      <Panel
        no="01"
        title="Installed zim archives"
        className="mb-4"
        meta={`${installedZims.length} archive${installedZims.length === 1 ? "" : "s"}`}
        actions={
          <Button
            variant="primary"
            size="sm"
            onClick={() => setViewerUrl(viewerUrl ? null : "/svc/kiwix/")}
            data-testid="kiwix-open"
          >
            <FolderOpen size={12} /> reader
          </Button>
        }
        testid="library-installed"
      >
        {installedZims.length === 0 ? (
          <p className="fine py-1">No ZIM archives installed. Choose one from the catalog below.</p>
        ) : (
          <div className="panel-body-flush">
            <table className="tbl">
              <thead>
                <tr>
                  <th>archive</th>
                  <th>language</th>
                  <th className="r">size</th>
                  <th className="r">sha256</th>
                  <th>status</th>
                </tr>
              </thead>
              <tbody>
                {installedZims.map((e) => (
                  <tr key={e.name}>
                    <td className="num">{e.title}</td>
                    <td className="mono text-[var(--c-muted)]">{e.language}</td>
                    <td className="num r">{fmtBytes(e.size)}</td>
                    <td className="num r text-[var(--c-muted)]">
                      {e.sha256?.slice(0, 12)}…
                    </td>
                    <td>
                      <Badge tone="ok">installed</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {viewerUrl && (
          <div className="border-t border-[var(--c-line)] p-2">
            <iframe
              title="Kiwix reader"
              src={viewerUrl}
              data-testid="kiwix-frame"
              className="frame h-[70vh] w-full"
            />
          </div>
        )}
      </Panel>

      {/* 02 — CATALOG */}
      <Panel no="02" title="Catalog" className="mb-4" flush testid="library-catalog">
        <table className="tbl">
          <thead>
            <tr>
              <th>title</th>
              <th>category</th>
              <th>license</th>
              <th className="r">size</th>
              <th>tier</th>
              <th>status</th>
              <th className="r">action</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => {
              const key = keyOf(e);
              const downloading = Boolean(e.active_job) || e.item_status === "downloading";
              return (
                <tr key={key} data-testid={`row-${key}`}>
                  <td className="num">
                    <div data-testid={`library-item-${key}`}>{e.title}</div>
                    {e.flavour && <div className="fine">flavour · {e.flavour}</div>}
                  </td>
                  <td className="mono text-[var(--c-muted)]">{e.category}</td>
                  <td>
                    <div className="mono text-[11px]">{e.license ?? "—"}</div>
                    {e.license_note && (
                      <div className="mono text-[10px] text-[color:var(--c-warn)]" title={e.license_note}>
                        {e.license_note}
                      </div>
                    )}
                  </td>
                  <td className="num r">{fmtBytes(e.size)}</td>
                  <td>
                    {e.tier ? <Num className="text-[12px]">T{e.tier}</Num> : <span className="muted">—</span>}
                  </td>
                  <td>
                    <Badge tone={entryTone(e)} className={e.item_error ? "badge-err" : ""}>
                      {entryLabel(e)}
                    </Badge>
                    {e.item_error && (
                      <div className="fine mt-1 max-w-[220px] text-[color:var(--c-warn)]" title={e.item_error}>
                        {e.item_error}
                      </div>
                    )}
                  </td>
                  <td className="r">
                    {downloading ? (
                      <div className="inline-flex items-center gap-2">
                        {progress(e)}
                        <Button variant="plain" size="sm" onClick={() => cancelJob(e)} data-testid={`cancel-${key}`}>
                          cancel
                        </Button>
                      </div>
                    ) : e.installed ? (
                      isAdmin && (
                        <Button variant="plain" size="sm" onClick={() => remove(e)} data-testid={`delete-${key}`}>
                          <Trash2 size={12} /> delete
                        </Button>
                      )
                    ) : (
                      isAdmin && (
                        <Button
                          variant="primary"
                          size="sm"
                          disabled={!e.downloadable || busyName === e.name}
                          onClick={() => install(e)}
                          data-testid={`install-${key}`}
                        >
                          {busyName === e.name ? "…" : "install"}
                        </Button>
                      )
                    )}
                  </td>
                </tr>
              );
            })}
            {entries.length === 0 && (
              <tr>
                <td className="fine" colSpan={7}>
                  Catalog unavailable — backend reporting no entries.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        {anyActive && (
          <div className="border-t border-[var(--c-line)] p-2">
            <StatusLine tone="active">download worker busy — one job at a time</StatusLine>
          </div>
        )}
      </Panel>

      {/* 03 — MANUALS */}
      <Panel no="03" title="Survival manuals" className="mb-4" meta={`${manuals.length} upload${manuals.length === 1 ? "" : "s"}`} testid="manuals-list">
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
                    <td className="fine">{m.installed_at?.slice(0, 10) ?? "—"}</td>
                    <td className="r">
                      <div className="inline-flex gap-1">
                        {m.file_url && (
                          <Button variant="ghost" size="sm" href={m.file_url} target="_blank" rel="noreferrer">
                            open
                          </Button>
                        )}
                        {isAdmin && (
                          <Button variant="plain" size="sm" onClick={() => removeManual(m)} data-testid={`delete-manual-${m.id}`}>
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
  );
}