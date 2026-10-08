# ARK — Autonomous Resilience and Knowledge System

A self-hosted, **fully offline**, post-collapse survival toolkit (in the spirit of
Project N.O.M.A.D.). Runs on a home server (Proxmox LXC), serves on one port
(default 8080), and is used from any browser on the LAN. No internet is needed
at runtime except for the Content Manager's explicit download actions.

> **Phase status:** Phase 0 (Foundation) complete — PRs pass CI; Phase 1 (Library) in progress. Phases 2–9 planned.

## Why ARK

- **Offline-first.** All JS/CSS/fonts are vendored into the repo; the built
  frontend contains zero external URLs (enforced by CI).
- **Durable.** Structured JSON logs, per-module log files, request IDs on every
  response, health endpoints, and a self-diagnosing `doctor` command.
- **One unit.** A single systemd service (`ark`), one port, one admin.
- **Resilient by design.** Modules are isolated so a broken module never takes
  down the rest; graceful degradation everywhere.

## Install on a fresh LXC

**Online** (host has internet — needs the `gh`/`git` auth to clone):

```bash
git clone https://github.com/isaacle-1/ark.git /opt
cd /opt
sudo bash scripts/install.sh --dev
```

**Offline** (sneakernet): download the release bundle
`ark-<version>-offline.tar.gz` (built by the Release workflow — contains the
pre-built frontend, the Python `wheelhouse/`, scripts and docs), copy it over,
then:

```bash
tar -xzf ark-<version>-offline.tar.gz -C /opt   # arrives as /opt/... ready to go
cd /opt
sudo bash scripts/install.sh                     # wheelhouse install, no network
```

The installer is idempotent: apt deps (python3/venv/dev, curl, git, make,
xz-utils), `ark` service user + `data/` ownership, Node 22 (build-time only,
fully pinned), venv + pinned deps (offline via `wheelhouse/` when present),
frontend build (skipped if `dist/` shipped), `ark init` (config, DB migration,
bootstrap admin credentials at `data/config/initial-credentials.txt`), systemd
unit install + enable, then `ark doctor` as the service user.

```bash
systemctl status ark            # Makefile helpers: make logs / make doctor / make restart
```

Manual dev runs (do **not** run the live server as root):

```bash
sudo -u ark env HOME=/opt/data/home ARK_HOME=/opt .venv/bin/python -m ark serve
```

Open http://<server>:8080 — admin credentials in `data/config/initial-credentials.txt`.

## Develop

```bash
make test      # lint + typecheck + tests + frontend build + no-external-URL gate
make dev       # hot-reload backend (:8080) + Vite dev server (:5173)
make logs      # tail data/logs/ark.log
make doctor    # ark doctor — actionable health checks
sudo make browsers  # one-time: playwright chromium + system libs (for make e2e)
```

See `docs/ARCHITECTURE.md` and `docs/TROUBLESHOOTING.md` for details, and
`AGENTS.md` for repository conventions (branches, commits, boundaries).

## Legal & content

Everything bundled or downloadable is license-cleared for offline/personal use;
sources are tracked in `docs/CONTENT_SOURCES.md` and `catalog/`. ARK itself is
MIT licensed — see `LICENSE`.