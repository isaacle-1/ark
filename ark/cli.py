"""``ark`` command line: init, serve, doctor, support-bundle, migrate, version.

Run from ARK_HOME: ``.venv/bin/python -m ark <command>`` (or scripts/ark).
Exit codes: 0 = ok, 1 = doctor found failures / command failed, 2 = config error.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import re
import shutil
import socket
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import ark
from ark.config import ArkConfig, ConfigError, load_config
from ark.paths import Paths, ensure_data_dirs

logger = logging.getLogger("ark.cli")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _load_or_fail(paths: Paths, *, allow_fail: bool = False) -> ArkConfig | None:
    try:
        return load_config(paths)
    except ConfigError as exc:
        if allow_fail:
            return None
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _setup(paths: Paths) -> ArkConfig:
    cfg = _load_or_fail(paths)
    assert cfg is not None
    from ark.logging_setup import setup_logging

    ensure_data_dirs(paths)
    setup_logging(cfg, paths, force=True)
    return cfg


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    paths = Paths.current()
    ensure_data_dirs(paths)
    cfg = _setup(paths)
    from ark.auth import bootstrap_admin
    from ark.db import Database, run_migrations

    run_migrations(paths)
    db = Database(paths.db_file, echo=False)
    with db.session() as session:
        created = bootstrap_admin(session, cfg, paths)
    db.dispose()
    print(f"ARK initialized at {paths.home}")
    print(f"  config : {paths.config_file}")
    print(f"  db     : {paths.db_file}")
    if created is not None:
        print(f"  admin credentials written to: {paths.config_dir / 'initial-credentials.txt'}")
    else:
        print("  admin account already exists")
    print("Run `ark doctor` to verify the installation.")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    paths = Paths.current()
    cfg = _setup(paths)
    host = args.host or cfg.server.host
    port = args.port or cfg.server.port

    import uvicorn

    if args.reload:
        # Dev mode: uvicorn spawns a child that rebuilds the app factory.
        uvicorn.run(
            "ark.server:create_app",
            factory=True,
            host=host,
            port=port,
            reload=True,
            log_config=None,
            access_log=False,
        )
    else:
        from ark.server import create_app

        app = create_app(cfg, paths)
        uvicorn.run(app, host=host, port=port, log_config=None, access_log=False)
    return 0


def _check(name: str, status: str, detail: str, fix: str | None = None) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail, "fix": fix or ""}


def _run_doctor(paths: Paths) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    # 1. Python
    py = sys.version_info
    checks.append(
        _check(
            "python",
            "PASS" if py >= (3, 12) else "FAIL",
            f"{platform.python_version()} (need >= 3.12)",
            None if py >= (3, 12) else "install python3.12 (scripts/install.sh does this)",
        )
    )

    # 2. ARK_HOME + data writability
    try:
        ensure_data_dirs(paths)
        probe = paths.tmp_dir / f"doctor-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(_check("ark_home", "PASS", f"{paths.home} (data/ writable)"))
    except OSError as exc:
        checks.append(
            _check(
                "ark_home",
                "FAIL",
                f"cannot write under {paths.home}: {exc}",
                "fix ownership: chown -R ark:ark <ARK_HOME> (service user must own data/)",
            )
        )

    # 3. Config
    cfg = _load_or_fail(paths, allow_fail=True)
    if cfg is None:
        try:
            load_config(paths)
        except ConfigError as exc:
            checks.append(
                _check("config", "FAIL", str(exc).splitlines()[0], "fix data/config/ark.toml")
            )
    else:
        checks.append(_check("config", "PASS", str(paths.config_file)))

    cfg = cfg or ArkConfig()

    # 4. Database
    if not paths.db_file.exists():
        checks.append(_check("database", "FAIL", "no database file", "run: ark init"))
    else:
        from sqlalchemy import text as sa_text

        from ark.db import make_engine

        engine = make_engine(paths.db_file)
        try:
            with engine.connect() as conn:
                integrity = conn.execute(sa_text("PRAGMA quick_check")).scalar()
                journal = conn.execute(sa_text("PRAGMA journal_mode")).scalar()
                try:
                    version = conn.execute(
                        sa_text("SELECT version_num FROM alembic_version")
                    ).scalar()
                except Exception:
                    version = None
            status = "PASS" if integrity == "ok" else "FAIL"
            detail = f"integrity={integrity}, journal={journal}, migration={version or 'NONE'}"
            fix = None
            if integrity != "ok":
                fix = "restore newest backup from data/backups/ (see docs/TROUBLESHOOTING.md)"
            elif version is None:
                status, fix = "FAIL", "schema missing — run: ark init"
            checks.append(_check("database", status, detail, fix))
        finally:
            engine.dispose()

    # 5. Logs dir writable
    try:
        paths.logs_dir.mkdir(parents=True, exist_ok=True)
        t = paths.logs_dir / ".doctor"
        t.write_text("", encoding="utf-8")
        t.unlink()
        n = len(list(paths.logs_dir.glob("*.log")))
        checks.append(_check("logs", "PASS", f"{paths.logs_dir} writable, {n} log file(s)"))
    except OSError as exc:
        checks.append(_check("logs", "FAIL", f"not writable: {exc}", "fix ownership of data/logs"))

    # 6. Server / port
    port = cfg.server.port
    try:
        import httpx

        resp = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2.0)
        if resp.status_code == 200:
            checks.append(_check("server", "PASS", f"responding on :{port} ({resp.text[:60]})"))
        else:
            checks.append(
                _check(
                    "server",
                    "FAIL",
                    f"HTTP {resp.status_code} from /healthz on :{port}",
                    "check journalctl -u ark -n 100 and data/logs/ark.log",
                )
            )
    except Exception as exc:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            occupied = s.connect_ex(("127.0.0.1", port)) == 0
        if occupied:
            checks.append(
                _check(
                    "server",
                    "FAIL",
                    f"port {port} is in use but /healthz does not respond: {exc}",
                    "another process owns the port: ss -ltnp | grep :" + str(port),
                )
            )
        else:
            checks.append(
                _check(
                    "server",
                    "WARN",
                    f"not running on :{port} ({type(exc).__name__})",
                    "start it: systemctl start ark  (or: make dev)",
                )
            )

    # 7. Disk space
    usage = shutil.disk_usage(paths.home)
    free_pct = usage.free / usage.total * 100 if usage.total else 0
    if usage.free < 500 * 1024 * 1024:
        checks.append(
            _check(
                "disk",
                "FAIL",
                f"{usage.free // (1 << 30)} GiB free ({free_pct:.1f}%)",
                "free space or move data/ to a bigger disk",
            )
        )
    elif free_pct < 10:
        checks.append(
            _check(
                "disk",
                "WARN",
                f"{usage.free // (1 << 30)} GiB free ({free_pct:.1f}%)",
                "below 10% free — content downloads will be blocked",
            )
        )
    else:
        checks.append(
            _check("disk", "PASS", f"{usage.free // (1 << 30)} GiB free ({free_pct:.1f}%)")
        )

    # 8. Frontend
    if (paths.frontend_dist / "index.html").is_file():
        checks.append(_check("frontend", "PASS", str(paths.frontend_dist)))
    else:
        node = shutil.which("node")
        fix = "run: make frontend (needs node) or install the release bundle"
        if node is None and os.environ.get("ARK_SKIP_NODE") != "1":
            checks.append(_check("frontend", "WARN", "not built; node not on PATH", fix))
        else:
            checks.append(_check("frontend", "WARN", "not built", fix))

    # 9. Sidecars (later phases register specs; empty now = nothing to check)
    from ark.supervisor import get_sidecar_specs

    specs = get_sidecar_specs(paths)
    if not specs:
        checks.append(_check("sidecars", "PASS", "none registered (phase 0)"))
    for spec in specs:
        binary = spec.command[0]
        found = shutil.which(binary) or (Path(binary).is_file() if "/" in binary else False)
        checks.append(
            _check(
                f"sidecar:{spec.name}",
                "PASS" if found else "WARN",
                f"{binary} found" if found else f"{binary} not installed",
                None if found else "scripts/install.sh installs it (or disable the module)",
            )
        )

    # 10. systemd service state (informational)
    if shutil.which("systemctl"):
        import subprocess

        proc = subprocess.run(
            ["systemctl", "is-active", "ark"], capture_output=True, text=True, timeout=5
        )
        state = proc.stdout.strip() or proc.stderr.strip() or "unknown"
        if state == "active":
            checks.append(_check("service", "PASS", "systemd unit 'ark' is active"))
        elif state == "inactive":
            checks.append(
                _check(
                    "service", "WARN", "unit installed but not running", "sudo systemctl start ark"
                )
            )
        elif state == "not-found":
            checks.append(
                _check(
                    "service",
                    "WARN",
                    "systemd unit not installed",
                    "run scripts/install.sh (installs ark.service)",
                )
            )
        else:
            checks.append(
                _check("service", "FAIL", f"unit state: {state}", "journalctl -u ark -n 100")
            )

    # 11. AI endpoint (config comes in phase 3; report only if configured later)
    return checks


def cmd_doctor(args: argparse.Namespace) -> int:
    paths = Paths.current()
    # Doctor must work even with a broken config: logging falls back to console.
    cfg = _load_or_fail(paths, allow_fail=True) or ArkConfig()
    from ark.logging_setup import setup_logging

    setup_logging(cfg, paths, force=True)

    checks = _run_doctor(paths)
    if args.json:
        # Machine-readable mode: emit the raw JSON only.
        print(json.dumps(checks, indent=2))
        fails = sum(1 for c in checks if c["status"] == "FAIL")
        return 1 if fails else 0
    width = max(len(c["name"]) for c in checks)
    fails = 0
    warns = 0
    print(f"ark doctor — {paths.home}")
    for c in checks:
        icon = {"PASS": " ok ", "WARN": "WARN", "FAIL": "FAIL"}[c["status"]]
        print(f"  [{icon}] {c['name']:<{width}}  {c['detail']}")
        if c["fix"]:
            print(f"         {'':<{width}}  fix: {c['fix']}")
        if c["status"] == "FAIL":
            fails += 1
        elif c["status"] == "WARN":
            warns += 1
    print(f"{len(checks)} checks: {fails} failed, {warns} warnings")
    return 1 if fails else 0


_REDACT_RE = re.compile(
    r"^(\s*[A-Za-z0-9_.-]*(?:password|passwd|secret|token|api_key|apikey|key)\w*\s*=\s*)(.+)$",
    re.IGNORECASE,
)


def _redact_config(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        m = _REDACT_RE.match(line)
        out.append(f'{m.group(1)}"REDACTED"' if m else line)
    return "\n".join(out)


def cmd_support_bundle(args: argparse.Namespace) -> int:
    paths = Paths.current()
    _setup(paths)
    ts = time.strftime("%Y%m%d-%H%M%S")
    staging = paths.tmp_dir / f"support-{ts}"
    staging.mkdir(parents=True, exist_ok=True)

    # 1. Logs (tail each file to keep the bundle small)
    logs_out = staging / "logs"
    logs_out.mkdir(exist_ok=True)
    for f in sorted(paths.logs_dir.glob("*.log*")):
        if not f.is_file():
            continue
        try:
            with f.open("rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                keep = min(size, 2 * 1024 * 1024)
                fh.seek(size - keep)
                data = fh.read()
            (logs_out / f.name).write_bytes(data)
        except OSError:
            continue

    # 2. Config, redacted
    if paths.config_file.exists():
        (staging / "ark.toml.redacted").write_text(
            _redact_config(paths.config_file.read_text(encoding="utf-8")),
            encoding="utf-8",
        )

    # 3. Health + versions (best effort: probe ourselves if running)
    info: dict[str, Any] = {
        "ark_version": ark.__version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "node": platform.node(),
        "time": ts,
        "ark_home": str(paths.home),
    }
    cfg = _load_or_fail(paths, allow_fail=True)
    if cfg is not None:
        try:
            import httpx

            resp = httpx.get(f"http://127.0.0.1:{cfg.server.port}/api/health", timeout=3.0)
            info["health"] = resp.json()
        except Exception as exc:
            info["health"] = f"unreachable: {type(exc).__name__}: {exc}"
    try:
        from importlib.metadata import distributions

        info["packages"] = sorted(
            f"{d.metadata['Name']}=={d.version}" for d in distributions() if d.metadata["Name"]
        )
    except Exception:
        pass
    (staging / "info.json").write_text(json.dumps(info, indent=2, default=str), encoding="utf-8")

    # 4. journalctl excerpt (if systemd unit exists)
    if shutil.which("journalctl"):
        import subprocess

        try:
            proc = subprocess.run(
                ["journalctl", "-u", "ark", "-n", "300", "--no-pager"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.stdout:
                (staging / "journal-ark.txt").write_text(proc.stdout, encoding="utf-8")
        except Exception:
            pass

    out = paths.tmp_dir / f"ark-support-{ts}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(staging.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(staging))
    shutil.rmtree(staging, ignore_errors=True)
    print(f"Support bundle: {out}")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    paths = Paths.current()
    _setup(paths)
    from ark.db import run_migrations

    run_migrations(paths)
    print(f"Database is up to date: {paths.db_file}")
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    print(f"ark {ark.__version__} (python {platform.python_version()}, {platform.platform()})")
    return 0


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ark",
        description="ARK — Autonomous Resilience and Knowledge System",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create data dirs, default config, DB, admin account")

    sp = sub.add_parser("serve", help="run the API + UI server")
    sp.add_argument("--host", default=None)
    sp.add_argument("--port", type=int, default=None)
    sp.add_argument("--reload", action="store_true", help="dev: auto-reload on code changes")

    dp = sub.add_parser("doctor", help="diagnose the installation; actionable fixes")
    dp.add_argument("--json", action="store_true", help="also print machine-readable output")

    sub.add_parser(
        "support-bundle", help="zip logs/config/health into data/tmp/ for troubleshooting"
    )
    sub.add_parser("migrate", help="apply pending DB migrations")
    sub.add_parser("version", help="print version")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    commands = {
        "init": cmd_init,
        "serve": cmd_serve,
        "doctor": cmd_doctor,
        "support-bundle": cmd_support_bundle,
        "migrate": cmd_migrate,
        "version": cmd_version,
    }
    try:
        return commands[args.command](args)
    except KeyboardInterrupt:
        return 130
    except SystemExit as exc:
        return int(exc.code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
