/* Library: offline Kiwix ZIMs + uploaded manuals. */
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, fmtBytes } from "../api/client";
import type { LibraryCatalog, LibraryEntry, LibraryItemView, LibraryItems, Me } from "../api/types";
import { useToast } from "../lib/toast";

function entryState(e: LibraryEntry): { label: string; tone: "ok" | "warn" | "muted" | "active" } {
  if (e.item_status === "downloading" || e.item_status === "queued" || e.active_job)
    return { label: e.active_job ? e.active_job.status : e.item_status ?? "active", tone: "active" };
  if (e.installed) return { label: "installed", tone: "ok" };
  if (e.item_status === "error") return { label: "error", tone: "warn" };
  if (e.item_status === "paused") return { label: "paused", tone: "warn" };
  if (e.status !== "verified") return { label: "unverified", tone: "warn" };
  return { label: "available", tone: "muted" };
}

const TONES = {
  ok: "bg-[color:var(--c-ok)]/15 text-[color:var(--c-ok)]",
  warn: "bg-[color:var(--c-warn)]/15 text-[color:var(--c-warn)]",
  muted: "bg-[var(--c-panel2)] text-[var(--c-muted)]",
  active: "bg-[color:var(--c-accent)]/15 text-[var(--c-accent)]",
} as const;

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
    if (!j) return null;
    const pct = Math.round((j.progress ?? 0) * 100);
    return (
      <div>
        <div className="mt-1 flex items-center justify-between gap-2 text-xs text-[var(--c-muted)]">
          <span>
            {j.status === "queued"
              ? "queued…"
              : `${fmtBytes(j.bytes_done)} / ${fmtBytes(j.bytes_total ?? e.size)}`}
            {j.speed_bps ? ` · ${fmtBytes(j.speed_bps)}/s` : ""}
          </span>
          <span>{pct}%</span>
        </div>
        <div className="mt-1 h-1.5 w-full overflow-hidden rounded bg-[var(--c-panel2)]">
          <div
            className="h-full bg-[var(--c-accent)] transition-[width] duration-500"
            style={{ width: `${Math.max(pct, 2)}%` }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-4">
        <h1 className="text-2xl font-bold">Information Library</h1>
        <p className="text-sm text-[var(--c-muted)]">
          Offline Kiwix ZIMs (Wikipedia, Gutenberg…) + survival manuals — no internet needed once
          downloaded.
        </p>
      </div>

      {installedZims.length > 0 && (
        <div className="card mb-4" data-testid="library-installed">
          <div className="mb-2 font-semibold">Installed ZIMs</div>
          <div className="flex flex-wrap gap-2">
            {installedZims.map((e) => (
              <span
                key={e.name}
                className="inline-flex items-center gap-2 rounded-full border border-[var(--c-line)] px-3 py-1 text-sm"
              >
                {e.title}
                <button
                  className="btn btn-ghost !px-2 !py-0.5 text-xs"
                  onClick={() => setViewerUrl(viewerUrl ? null : "/svc/kiwix/")}
                  data-testid="kiwix-open"
                >
                  Open reader
                </button>
              </span>
            ))}
          </div>
          {viewerUrl && (
            <div className="mt-3">
              <iframe
                title="Kiwix reader"
                src={viewerUrl}
                data-testid="kiwix-frame"
                className="h-[70vh] w-full rounded border border-[var(--c-line)] bg-[var(--c-panel)]"
              />
            </div>
          )}
        </div>
      )}

      <div className="card mb-4">
        <div className="mb-2 font-semibold">Catalog</div>
        <p className="mb-3 text-sm text-[var(--c-muted)]">
          {catalog?.entries.length ?? 0} verified items · sizes and checksums verified against
          kiwix.org mirrors
        </p>
        <div className="overflow-x-auto" data-testid="library-catalog">
          <table className="w-full min-w-[680px] text-left text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wide text-[var(--c-muted)]">
                <th className="py-2 pr-3 font-medium">Title</th>
                <th className="py-2 pr-3 font-medium">Category</th>
                <th className="py-2 pr-3 font-medium">License</th>
                <th className="py-2 pr-3 text-right font-medium">Size</th>
                <th className="py-2 pr-3 font-medium">Tier</th>
                <th className="py-2 pr-3 font-medium">Status</th>
                <th className="py-2 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => {
                const st = entryState(e);
                const downloading = Boolean(e.active_job) || e.item_status === "downloading";
                return (
                  <tr key={keyOf(e)} className="border-t border-[var(--c-line)]" data-testid={`row-${keyOf(e)}`}>
                    <td className="py-2 pr-3">
                      <div data-testid={`library-item-${keyOf(e)}`}>{e.title}</div>
                      {e.flavour && (
                        <div className="text-xs text-[var(--c-muted)]">flavour: {e.flavour}</div>
                      )}
                    </td>
                    <td className="py-2 pr-3 text-[var(--c-muted)]">{e.category}</td>
                    <td className="py-2 pr-3 text-xs text-[var(--c-muted)]">
                      <div>{e.license ?? "—"}</div>
                      {e.license_note && <div className="text-[color:var(--c-warn)]">{e.license_note}</div>}
                    </td>
                    <td className="py-2 pr-3 text-right tabular-nums">{fmtBytes(e.size)}</td>
                    <td className="py-2 pr-3">
                      {e.tier ? <span className="badge bg-[var(--c-panel2)] text-[var(--c-muted)]">tier {e.tier}</span> : "—"}
                    </td>
                    <td className="py-2 pr-3">
                      <span className={"badge " + TONES[st.tone]}>{st.label}</span>
                      {e.item_error && (
                        <div className="mt-0.5 max-w-[240px] text-xs text-[color:var(--c-warn)]" title={e.item_error}>
                          {e.item_error}
                        </div>
                      )}
                    </td>
                    <td className="py-2 text-right">
                      {downloading ? (
                        <div className="inline-block w-52">
                          {progress(e)}
                          <div className="mt-1 text-right">
                            <button
                              className="btn btn-ghost !px-2 !py-0.5 text-xs"
                              onClick={() => cancelJob(e)}
                              data-testid={`cancel-${keyOf(e)}`}
                            >
                              cancel
                            </button>
                          </div>
                        </div>
                      ) : e.installed ? (
                        <div className="flex justify-end gap-1">
                          {isAdmin && (
                            <button
                              className="btn btn-ghost !px-2 !py-0.5 text-xs"
                              onClick={() => remove(e)}
                              data-testid={`delete-${keyOf(e)}`}
                            >
                              delete
                            </button>
                          )}
                        </div>
                      ) : (
                        isAdmin && (
                          <button
                            className="btn btn-primary !px-3 !py-1 text-xs"
                            disabled={!e.downloadable || busyName === e.name}
                            onClick={() => install(e)}
                            data-testid={`install-${keyOf(e)}`}
                          >
                            {busyName === e.name ? "…" : "Install"}
                          </button>
                        )
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card" data-testid="manuals-list">
        <div className="mb-2 font-semibold">Survival Manuals</div>
        {isAdmin && (
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <input
              type="file"
              className="input !w-auto text-sm"
              onChange={(ev) => setUploadFile(ev.target.files?.[0] ?? null)}
              data-testid="manual-file"
            />
            <button
              className="btn btn-primary"
              disabled={!uploadFile || uploading}
              onClick={uploadManual}
              data-testid="manual-upload"
            >
              {uploading ? "Uploading…" : "Upload PDF/manual"}
            </button>
          </div>
        )}
        {manuals.length === 0 ? (
          <p className="text-sm text-[var(--c-muted)]">No manuals uploaded yet.</p>
        ) : (
          <ul className="divide-y divide-[var(--c-line)]">
            {manuals.map((m) => (
              <li key={m.id} className="flex items-center justify-between gap-3 py-2">
                <div>
                  <div className="text-sm">{m.filename}</div>
                  <div className="text-xs text-[var(--c-muted)]">
                    {fmtBytes(m.size)} · {m.installed_at?.slice(0, 10) ?? "uploaded"}
                  </div>
                </div>
                <div className="flex gap-1">
                  {m.file_url && (
                    <a className="btn btn-ghost !px-2 !py-0.5 text-xs" href={m.file_url} target="_blank" rel="noreferrer">
                      open
                    </a>
                  )}
                  {isAdmin && (
                    <button
                      className="btn btn-ghost !px-2 !py-0.5 text-xs"
                      onClick={() => removeManual(m)}
                      data-testid={`delete-manual-${m.id}`}
                    >
                      delete
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}