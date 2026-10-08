"""CLI: subprocess-level tests of `ark init/doctor/support-bundle/migrate`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import ark
from ark.paths import Paths


def _run(paths: Paths, *args: str, expect: int | None = 0) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ARK_HOME"] = str(paths.home)
    env["ARK_SKIP_NODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-m", "ark", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if expect is not None and proc.returncode != expect:
        raise AssertionError(
            f"`ark {' '.join(args)}` returned {proc.returncode} (want {expect})\n"
            f"--- stdout ---\n{proc.stdout[-2000:]}\n--- stderr ---\n{proc.stderr[-2000:]}"
        )
    return proc


def test_version(paths: Paths) -> None:
    proc = _run(paths, "version")
    assert proc.returncode == 0
    assert ark.__version__ in proc.stdout


def test_init_creates_layout_and_credentials(paths: Paths) -> None:
    proc = _run(paths, "init")
    assert proc.returncode == 0
    assert (paths.config_file).is_file()
    assert (paths.db_file).is_file()
    creds = paths.config_dir / "initial-credentials.txt"
    assert creds.is_file()
    assert (creds.stat().st_mode & 0o777) == 0o600
    # second init is a no-op that keeps the account
    assert "already exists" in _run(paths, "init").stdout


def test_doctor_json_has_expected_sections(paths: Paths) -> None:
    _run(paths, "init")
    proc = _run(paths, "doctor", "--json")
    assert proc.returncode == 0, proc.stdout
    checks = json.loads(proc.stdout)
    names = {c["name"] for c in checks}
    assert {
        "python",
        "ark_home",
        "config",
        "database",
        "logs",
        "server",
        "disk",
        "frontend",
        "sidecars",
    } <= names
    assert all(c["status"] in ("PASS", "WARN") for c in checks)  # nothing FAIL in this env


def test_doctor_reports_config_error(paths: Paths) -> None:
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text("[server\nbroken", encoding="utf-8")
    proc = _run(paths, "doctor", expect=None)
    assert proc.returncode == 1
    assert "config" in proc.stdout


def test_migrate_with_bad_config_exits_2(paths: Paths) -> None:
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text("[server]\nport = 'nope'\n", encoding="utf-8")
    proc = _run(paths, "migrate", expect=None)
    assert proc.returncode == 2


def test_support_bundle_redacts_secrets(paths: Paths) -> None:
    _run(paths, "init")
    # Put a secret in the config file to prove redaction.
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(
        "password = 'super-secret-value-xyz'\n[something]\napi_key = 'k-12345'\n",
        encoding="utf-8",
    )
    proc = _run(paths, "support-bundle")
    assert proc.returncode == 0
    bundle = Path(proc.stdout.strip().removeprefix("Support bundle: ").strip() or "")
    if not bundle.is_absolute():
        bundle = paths.home / bundle
    assert bundle.is_file(), proc.stdout

    from zipfile import ZipFile

    with ZipFile(bundle) as zf:
        names = set(zf.namelist())
        assert "info.json" in names
        assert "logs/ark.log" in names
        assert "ark.toml.redacted" in names
        redacted = zf.read("ark.toml.redacted").decode()
        assert "super-secret-value-xyz" not in redacted
        assert "REDACTED" in redacted
        archive_text = "".join(zf.read(n).decode(errors="ignore") for n in names)
        assert "super-secret-value-xyz" not in archive_text
