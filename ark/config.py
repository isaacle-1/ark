"""Configuration: ``data/config/ark.toml`` loaded and validated with pydantic.

Priority (highest wins):
1. Environment variables (``ARK_DEBUG``, ``LOG_LEVEL``)
2. Runtime settings persisted in the DB (e.g. log level changed from the UI)
3. ``data/config/ark.toml``
4. Built-in defaults

Validation failures raise :class:`ConfigError` with a message that names the
key, what is wrong, and what is expected — never a raw traceback.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from ark.paths import Paths, ensure_data_dirs

DEFAULT_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

DEFAULT_CONFIG_TOML = """\
# ARK configuration — validated at startup; errors name the key and expected value.
# Restart the service after editing (runtime log-level changes: Settings page).

[server]
host = "0.0.0.0"        # bind address; 0.0.0.0 = reachable on the LAN
port = 8080             # single port for UI + API + /svc/<sidecar> proxy
docs = true             # OpenAPI UI at /api/docs

[logging]
level = "INFO"          # DEBUG | INFO | WARNING | ERROR  (env LOG_LEVEL overrides)
max_bytes = 10485760    # rotate data/logs/ark.log at 10 MiB
backup_count = 5        # keep 5 rotated files per log
console = true          # log to stdout (journald captures it)

[auth]
mode = "open"           # "open": LAN-wide read, admin endpoints need an admin
                        # login. "required": every /api call needs a session.
session_ttl_hours = 336 # logout after 14 days
login_max_attempts = 10 # per IP+username per window before 429
login_window_seconds = 300

[jobs]
enabled = true          # in-process background job worker
poll_interval = 1.0     # seconds between DB polls for queued jobs

[library]
enabled = true          # ZIM catalog, downloads and manual uploads (Phase 1)
kiwix_enabled = true    # supervise bin/kiwix-serve when at least one ZIM is installed
kiwix_port = 8139       # localhost-only kiwix-serve port (reached via /svc/kiwix/…)
download_chunk_bytes = 1048576   # download read/write chunk (resumable, sha256-checked)
request_timeout = 30.0  # per-read socket timeout while downloading (seconds)
max_manual_mb = 100     # upload size limit for manual/PDF uploads

[modules]
# Per-module toggles. Modules register their own defaults as they land;
# unknown keys are allowed so later phases can flip things off here.
"""


class ConfigError(Exception):
    """Raised when ark.toml is missing, unreadable, or invalid."""


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    docs: bool = True


class LoggingConfig(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = DEFAULT_LOG_LEVEL
    max_bytes: int = Field(default=10 * 1024 * 1024, ge=64 * 1024)
    backup_count: int = Field(default=5, ge=1, le=100)
    console: bool = True


class AuthConfig(BaseModel):
    mode: Literal["open", "required"] = "open"
    session_ttl_hours: int = Field(default=14 * 24, ge=1, le=24 * 365)
    login_max_attempts: int = Field(default=10, ge=1, le=1000)
    login_window_seconds: int = Field(default=300, ge=10, le=86400)


class JobsConfig(BaseModel):
    enabled: bool = True
    poll_interval: float = Field(default=1.0, ge=0.05, le=60.0)


class LibraryConfig(BaseModel):
    enabled: bool = True
    kiwix_enabled: bool = True
    kiwix_port: int = Field(default=8139, ge=1024, le=65535)
    download_chunk_bytes: int = Field(default=1_048_576, ge=4_096, le=64 * 1024 * 1024)
    request_timeout: float = Field(default=30.0, ge=5.0, le=300.0)
    max_manual_mb: int = Field(default=100, ge=1, le=10240)


class ArkConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    library: LibraryConfig = Field(default_factory=LibraryConfig)
    modules: dict[str, bool] = Field(default_factory=dict)

    # Not from the file: computed at load time.
    debug: bool = False

    @property
    def log_level(self) -> str:
        """Effective log level after env overrides are applied in load_config."""
        return self.logging.level


def write_default_config(path: Path) -> None:
    """Write the commented default ark.toml (idempotent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")


def _format_validation_error(err: ValidationError, source: str) -> str:
    lines = [f"Config error in {source}:"]
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"]) or "(root)"
        etype = e["type"]
        if etype == "missing":
            hint = "required key is missing"
        elif etype.startswith("int_") or etype.startswith("float_"):
            hint = "expected a number"
        elif etype == "greater_than_equal" or etype == "less_than_equal":
            hint = e["msg"]
        elif etype == "literal_error":
            hint = f"expected one of: {e.get('msg', '')}"
        elif etype.startswith("string_"):
            hint = "expected a string"
        else:
            hint = e["msg"]
        lines.append(f"  [{loc}]: {hint}")
    lines.append(
        "Fix the key(s) above in the file and restart. "
        "See .env.example / docs/ARCHITECTURE.md for a documented template."
    )
    return "\n".join(lines)


def _apply_env_overrides(cfg: ArkConfig) -> ArkConfig:
    debug = os.environ.get("ARK_DEBUG", "").strip() in {"1", "true", "yes", "on"}
    if debug:
        cfg.debug = True
        cfg.logging.level = "DEBUG"
    level = os.environ.get("LOG_LEVEL", "").strip().upper()
    if level:
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError(
                f"Environment variable LOG_LEVEL={level!r} is invalid: "
                "expected DEBUG, INFO, WARNING, ERROR or CRITICAL."
            )
        cfg.logging.level = level  # type: ignore[assignment]
    return cfg


def load_config(paths: Paths | None = None, *, write_default: bool = True) -> ArkConfig:
    """Load, validate and return the configuration.

    Raises :class:`ConfigError` with an actionable message on any problem.
    """
    p = paths or Paths.current()
    if write_default:
        ensure_data_dirs(p)
        write_default_config(p.config_file)
    if not p.config_file.exists():
        raise ConfigError(
            f"Config file not found: {p.config_file}. "
            "Run `ark init` (or scripts/install.sh) to create the default config."
        )
    try:
        raw: dict[str, Any] = tomllib.loads(p.config_file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"Config error in {p.config_file}: invalid TOML syntax: {exc}\n"
            "Fix the syntax (missing quote, bracket or =) and restart."
        ) from exc
    except OSError as exc:
        raise ConfigError(f"Cannot read config file {p.config_file}: {exc}") from exc
    try:
        cfg = ArkConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, str(p.config_file))) from exc
    return _apply_env_overrides(cfg)
