# ARK — Troubleshooting

The health-check order of business:

1. `make doctor` (/ `ark doctor`) — fast, actionable checks.
2. `systemctl status ark` + `journalctl -u ark -n 100` — service-level.
3. `make logs` or the in-app **Logs** page (`/logs`, admin) — app-level.
4. `data/logs/ark.log` — all lines as JSON, one per line. Per-module files in
   `data/logs/<module>.log`, sidecar output in `data/logs/<sidecar>.log`.

Every HTTP response carries `X-Request-ID`; every error body carries the same id
under `request_id`. Find the full trace:

```bash
grep '"request_id":"<id>"' data/logs/*.log
```

## Deliberate-break drill (acceptance test for us)
Cause an error, then locate its cause within **one minute** using only the Logs
page or log files. If a failure is ever hard to find, add the missing log line
to make it obvious — logging is a feature, not an afterthought.

## Common problems

### 502 / 503 from `/svc/…`
Sidecar not launched this phase (`get_sidecar_specs` is empty). Check
`data/logs/<name>.log` for the sidecar's own stderr. 503 = sidecar unknown/not
running yet, 502 = sidecar accepted the request but a dependency upstream failed.

### DB locked / "database is locked"
SQLite + multiple writer processes. Phase 0 uses a single process, but check:
- No stray `python -m ark serve` instances (`pgrep -af 'ark serve'`).
- `data/db/ark.sqlite3` writable by uid 1000.
- WAL files exist (`-wal` / `-shm`) — normal while running.

### Permissions meltdown (root-created files in `data/`)
`find data -not -user ark -not -path '*/cache/*' | xargs -r chown ark:ark`
then `sudo systemctl restart ark`.

### Port 8080 already in use
`ss -ltnp | grep 8080`. Change `port` in `data/config/ark.toml`; the unit reads
it at startup, update `Environment` in the unit or use `make restart`.

### Logs page shows "No matching lines"
- Not admin → login (Logs is admin-only).
- Filters too narrow (level ≥ INFO default, module/request_id/contains).
- Live tail only applies to `ark.log` in phase 0; per-module files refresh on
  filter change and via "Download".

### Frontend fallback page instead of the app
`ark/frontend/dist` is missing — run `make frontend` (needs Node at
`/opt/bin/node/bin`). In CI/VMs Node is only required to build once; dist is
served from disk thereafter.

### Browser can't reach ARK (LAN)
- Bind address: phase 0 unit listens on `0.0.0.0` (configurable in ark.toml);
  container maps 8080.
- Host firewall/proxmox — this is on the operator; `ark doctor` prints the
  listening address and LAN binds to investigate.
- Local-only use is fine: point the browser at `http://127.0.0.1:8080`.

## Support bundle
`ark support-bundle data/tmp/ark-bundle-<ts>.tar.gz` collects logs, config
(secrets redacted), `df`, env (redacted), systemd status and package versions —
attach it when filing issues. No secrets are written (checked against the
redaction list, verified by tests).