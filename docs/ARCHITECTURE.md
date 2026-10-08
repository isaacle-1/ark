# ARK — Architecture

## Big picture

```
Browser (LAN)
   │  HTTP/1.1 :8080
   ▼
FastAPI app (ark.server)  ── serves ──▶ built React SPA (ark/frontend/dist)
   │  /api/…                                 & PWA (sw.js, manifest)
   │
   ├─ routers/  health · auth · logs · admin   per-module routers in later phases
   ├─ auth.py   argon2id · sessions · rate limit
   ├─ jobs.py   async worker over tasks table (jobs.*)
   ├─ supervisor.py sidecar lifecycle (kiwix, valhalla, … in later phases)
   ├─ db.py     SQLAlchemy + SQLite (WAL) + Alembic migrations
   └─ logging_setup.py JSON logs · per-module files · LogBus (SSE)
```

- Single process, single port. Everything lives under `ARK_HOME` (default `/opt`).
- Runtime data under `data/`: `config/`(gitignored secrets) `db/` `logs/`
  `library/` `manuals/` `videos/` `maps/` `notes/` `docs/` `radio/`
  `translate/` `rag/` `uploads/` `tmp/` `cache/` `backups/` `home/`.
- The service runs as user `ark` (uid 1000); `data/` is ark-owned. The repo
  source is root-owned and world-readable so root can test without touching
  `data/` (tests use a temp ARK_HOME).

## Backend

### Configuration (`ark/config.py`)
- Single source of truth: `data/config/ark.toml` (see `.env.example`).
  Missing file → defaults; malformed/interexceeded values → `ConfigError` with a
  human message ("which key, what is wrong").
- Env overrides: `ARK_DEBUG`, `LOG_LEVEL` (beats file AND DB), `ARK_ADMIN_PASSWORD`.
- pydantic-validated with runtime checks (no accidental writes outside ARK_HOME).

### Logging (`ark/logging_setup.py`) — read `docs/TROUBLESHOOTING.md` for the drill
- JSON, one object per line. Every record gets `ts` ISO-8601, `level`, `logger`,
  `msg`; the request middleware injects `request_id`, and jobs inject `job_id`.
- `ark.<module>` loggers automatically also land in `data/logs/<module>.log`.
  Everything always lands in `data/logs/ark.log` (rotating).
- In-file handler reads files back for the Logs page; `LogBus` + `BusHandler`
  fans live lines to SSE subscribers (`/api/admin/logs/stream?history=N`).
- Console/TTY gets human-readable, non-TTY gets JSON (pipes/CI stay clean).
- Dynamic root level follows `LOG_LEVEL` env → DB `Setting` → config default;
  changeable at runtime via `POST /api/admin/logs/level`, persisted.

### HTTP (`ark/server.py`)
- `create_app()` factory; lifespan: run migrations → bootstrap admin →
  set DB log level → start job worker → start supervisor.
- Middleware: `X-Request-ID` (accept incoming, else generate), access log with
  explicit `request_id` extra; exception handlers return `{"detail", "request_id"}`
  with 400/401/403/404/409/500 as appropriate. Security headers + CSP.
- `/healthz` (bare liveness, always 200 when up), `/api/health` (modules +
  sidecars + disk + db check). `/svc/<name>/*` reverse-proxies to sidecar ports
  (503 unknown, 404 not running this phase).
- SPA catch-all serves `frontend/dist`; without a build, a branded fallback page
  explains how to build with `make frontend`.
- Auth: `routes: open | required` (default `required`). Sessions in DB,
  cookie `ark_session` (HttpOnly, SameSite=Lax), login rate-limited 10/5 min.
  Admin-only API under `/api/admin/…` checked by role.

### Jobs (`ark/jobs.py`) & supervisor (`ark/supervisor.py`)
- `JobWorker` polls `jobs` table, runs a handler registry (registered via
  `@handles(kind)`), writes status/progress/result/timestamps; interrupted jobs
  are marked `failed` on startup.
- `Supervisor` spawns sidecars from `get_sidecar_specs()` (empty in phase 0),
  restarts with 1→30 s backoff, gives up after 30 crashes per minute (crash-loop
  lock), captures sidecar stdout/stderr to `data/logs/<name>.log`.

## Frontend (`ark/frontend`)
- React 18 + TypeScript + Vite 6 + Tailwind v4 (`@tailwindcss/vite`), no CDN.
- Manual PWA: `public/sw.js` runtime-cache-first after first load + manifest.
- Themes `dark` / `light` / `rednight` via `data-theme` + CSS custom properties
  (`src/lib/theme.ts`, persisted to localStorage).
- Toast system shows any failed API call's `request_id`
  (`src/lib/toast.tsx`); `ErrorBoundary` reports crashes to `/api/client-log`.
- API client (`src/api/client.ts`) returns typed responses and throws
  `ApiError` with `status` + `requestId`.
- Pages: Dashboard (module tiles + system status), Logs (admin), Settings
  (theme, log level), Search (placeholder), Login. Routes in `src/App.tsx`.

## Lifecycle & deployment
- Native: `scripts/install.sh` (idempotent) installs apt deps, `ark` user, venv,
  Node (build-time), frontend build, `ark init`, systemd unit `ark.service`
  (`User=ark`, hardened: `NoNewPrivileges`, `ProtectSystem=strict`, `ReadWritePaths=data`).
- Container: `Dockerfile` multi-stage (frontend build → slim python runtime),
  `docker-compose.yml` mounts `./data:/app/data`.
- CI (`ci.yml`): backend ruff+mypy+pytest, frontend lint/tsc/build + no-ext URL
  gate, playwright e2e, docker build. Release (`release.yml`): offline tarball +
  GHCR image on `v*` tags.

## Phase roadmap (design intent)
1. Library (kiwix ZIM) + manuals — first sidecars.
2. Maps (Valhalla offline routing) + geocoding (Photon) — big data.
3. Notes — SQLite-backed editor with offline-first sync.
4. Radio (SDR) — device-side; ARK frontend shows the UI.
5. Docs (Scan / print-to-PDF / photogrammetry) — sidecars plus internal doc store.
6. Translation (Argos translate) — offline NMT microservice.
7. Synthesis (RAG) — ELSER/exact keyword first; LLM optional offline.
8. On-sync search + drop-zone watch.
9. Onboarding hardening, full-firstboot auto-install, PWA app install, content packs.
   Each phase isolates behind its own router + health entry + tests.