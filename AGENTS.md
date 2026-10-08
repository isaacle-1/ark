# AGENTS.md — instructions for coding agents working on ARK

**Read this file and `CHANGELOG.md` first when a session starts to recover context.**
Keep this file updated whenever environment, workflow, or layout changes.

## What ARK is

A self-hosted, fully offline, post-collapse survival toolkit (in the spirit of
Project N.O.M.A.D.). It runs on a home server (Proxmox LXC), served on one port
(default 8080) and used from any browser on the LAN. No internet is needed at
runtime except the Content Manager's explicit download actions.

## Environment (this container)

- `ARK_HOME = /opt` — the repo root. **All project files live inside ARK_HOME.**
- Ubuntu 24.04 LXC, systemd is PID 1, Python 3.12.3, Node 22.23.3 at
  `/opt/bin/node/bin` (added to PATH by Makefile/scripts), git 2.43.
- Dedicated service user: `ark` (uid 1000). The systemd unit runs as `ark`.
  `data/` must stay owned by `ark`. **Never run the live server as root** —
  root-created files in `data/` break the service. Manual runs:
  `sudo -u ark env HOME=/opt/data/home ARK_HOME=/opt <cmd>`.
- Root runs git/npm/pytest freely (repo source is root-owned, readable by all).
  Tests isolate themselves in a temp `ARK_HOME`, so they don't touch real `data/`.
- Package caches are redirected inside ARK_HOME:
  `export PIP_CACHE_DIR=/opt/data/cache/pip npm_config_cache=/opt/data/cache/npm`
- Playwright browsers: `PLAYWRIGHT_BROWSERS_PATH=/opt/data/cache/ms-playwright`.
- Disk: check `df -h` before large downloads. Large content packs (Wikipedia,
  map extracts, videos) are downloaded **only when the user asks** or via the
  app's Content Manager. Test fixtures stay < 1 MB.

## Boundaries (hard rules)

1. **Offline-first.** No CDN links, Google Fonts, telemetry, or external API
   calls at runtime. All JS/CSS/fonts vendored (bundled via npm, no CDN).
   CI greps the built frontend for external URLs and fails on any.
2. **Everything inside ARK_HOME.** Runtime data goes under `./data/`
   (temp files → `./data/tmp`). No writes to `~`, `/var`, `/tmp`, or `/opt`
   outside the repo. Only exceptions: apt packages and the `ark` systemd
   unit/user.
3. **No destructive actions** without asking the user: `rm -rf` on anything but
   build/cache dirs we created, deleting `data/`, wiping the DB, network/firewall
   changes.
4. **No secrets in the repo.** `.gitignore` excludes `data/`, `.env`, `bin/`.
   Secrets live only in `data/config/` (which is gitignored).
5. **Licensing:** only content whose license allows redistribution/personal
   offline copies. Record license + source URL for every catalog item. Never
   hardcode unverified URLs — mark them `UNVERIFIED` in the catalog and tell
   the user.
6. Ask before anything hard to reverse. No force-pushes, no rewriting `main`.

## Git workflow

- One feature branch per phase: `phase-0-foundation`, `phase-1-library`, …
- Small commits, conventional messages (`feat(core): …`, `fix(logs): …`,
  `test(health): …`, `chore(ci): …`).
- Push the branch, open a PR with `gh pr create` (summary, how to test,
  known limitations). The user merges to `main`; never push directly to `main`.
- GitHub auth: `gh` is already logged in (repo + workflow scopes). Never write
  tokens into any file in the repo.
- `make test` must pass before every push. CI must be green before a phase is
  declared done.

## How to run and debug

```bash
make test          # lint + typecheck + unit/integration tests + frontend build + no-external-URL check
make e2e           # playwright smoke (needs: playwright install chromium, PLAYWRIGHT_BROWSERS_PATH set)
make dev           # hot-reload backend (:8080) + Vite dev server (:5173)
make logs          # tail data/logs/ark.log (JSON lines)
make doctor        # ark doctor — actionable health checks
make restart       # sudo systemctl restart ark
```

- The service runs as systemd unit **`ark`**: `systemctl status ark`,
  `journalctl -u ark -n 100`, restart with `sudo systemctl restart ark`.
- **Log locations (the first place to look):**
  - `data/logs/ark.log` — all log lines, JSON, one per line (rotating).
  - `data/logs/<module>.log` — per-module (http, library, maps, rag, …).
  - `data/logs/<sidecar>.log` — per-sidecar stdout/stderr (kiwix.log, …).
  - In-app viewer: `/admin/logs` (SSE live tail, filter by level/module/
    request_id/text, download, copy as text).
  - Every HTTP response carries `X-Request-ID`; error bodies include
    `request_id` — grep it: `grep '"request_id":"<id>"' data/logs/*.log`.
- Deliberate-break drill (acceptance test): cause an error, find its cause via
  the Logs page or log files **within one minute**. If a failure was hard to
  diagnose, add the log line/error message that would have made it obvious.

## Project layout

```
ARK_HOME (= repo root)
  ark/                 Python package: server.py (FastAPI app), cli.py, config.py,
                       logging_setup.py, db.py, models.py, auth.py, jobs.py,
                       supervisor.py, routers/, frontend/ (React+Vite+TS+Tailwind)
  scripts/             install.sh, doctor.sh, update.sh, backup.sh, lxc-install.sh,
                       check_no_external_urls.py, ark.service (unit template)
  catalog/             JSON content catalogs (versioned, offline, URLs verified
                       by scripts/verify_catalog.py)
  data/                ALL runtime data (gitignored): config/, db/, logs/, library/,
                       manuals/, videos/, maps/, notes/, docs/, radio/, translate/,
                       rag/, uploads/, tmp/, cache/, backups/, home/
  tests/               pytest unit + integration tests
  docs/                README (root), ARCHITECTURE, TROUBLESHOOTING, CONTENT_SOURCES
  bin/                 downloaded sidecar/tool binaries (gitignored)
  .github/workflows/   ci.yml (lint/typecheck/tests/build/no-ext-URL/docker), release.yml
```

## Conventions

- Python 3.12, full type hints, `mypy` clean, `ruff` clean.
  Deps in `requirements.txt` (runtime) / `requirements-dev.txt` (tools);
  run from repo root with `.venv/bin/python -m ark …` (no pip install of the
  package itself, so source edits take effect directly).
- Logging: `logging.getLogger("ark.<module>")` → automatically also lands in
  `data/logs/<module>.log`. Always log with `extra=` structured fields; on
  errors include enough context to act (`request_id`, file path, key name…).
  Never use bare `print()`.
- Config: everything configurable lives in `data/config/ark.toml`, validated by
  pydantic with human-readable errors ("which key, what is wrong, expected").
- API: JSON errors are `{"detail": …, "request_id": …}`. Admin-only endpoints
  under `/api/admin/…`. File endpoints must be path-traversal safe.
- Frontend: React 18 + Vite + TS + Tailwind (v4), no external URLs, all pages
  reachable from the dashboard; failed API calls show a toast with the
  `request_id`.
- Modules are isolated (own router, health entry, tests); a broken module must
  never crash the others. Graceful degradation: missing dependency → clear
  "not installed / not reached" state, not an error page.

## Phase status

- **Phase 0 (Foundation): COMPLETE — PR #1 open (`phase-0-foundation` → `main`), awaiting user merge.**
  All gates green: `make test` + `make e2e` locally, CI (backend/frontend/e2e/docker) pass,
  systemd service running in this LXC, deliberate-break drill 32 ms.
- Phases 1–9: not started (see the master prompt / PR descriptions for scope).
