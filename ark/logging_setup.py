"""Structured JSON logging with request/job context and per-module log files.

Layout of one log line (one JSON object per line in ``data/logs/ark.log``)::

    {"ts":"2026-10-08T03:41:12.341Z","level":"INFO","logger":"ark.http",
     "msg":"request finished","method":"GET","path":"/healthz","status":200,
     "duration_ms":3,"request_id":"ab12…"}

- Every line carries ``request_id`` (from a contextvar set by middleware) and
  ``job_id`` when a background job is executing.
- Loggers named ``ark.<module>`` are *also* written to ``data/logs/<module>.log``
  automatically (e.g. ``logging.getLogger("ark.library")`` -> ``library.log``).
- Subscribers (the in-app log viewer) receive the exact same JSON lines via the
  in-process :class:`LogBus`.
- Console output: colored human format on a TTY, JSON otherwise (journald).
"""

from __future__ import annotations

import json
import logging
import queue
import re
import sys
import threading
import time
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from ark.config import ArkConfig
from ark.paths import Paths

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)

_LEVEL_COLORS = {
    "DEBUG": "\033[36m",  # cyan
    "WARNING": "\033[33m",  # yellow
    "ERROR": "\033[31m",  # red
    "CRITICAL": "\033[41m",  # red bg
}
_RESET = "\033[0m"
_DIM = "\033[2m"

# Attributes present on every LogRecord (never treated as structured extras).
_BUILTIN = frozenset(
    set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}
)

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_.-]+")


def utc_iso(ts: float | None = None) -> str:
    """ISO-8601 UTC timestamp with milliseconds and a Z suffix."""
    t = time.time() if ts is None else ts
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"


class JsonFormatter(logging.Formatter):
    """Format records as one compact JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": utc_iso(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = getattr(record, "request_id", None) or request_id_var.get()
        if rid:
            payload["request_id"] = rid
        jid = getattr(record, "job_id", None) or job_id_var.get()
        if jid:
            payload["job_id"] = jid
        payload["src"] = f"{record.module}:{record.lineno}"
        for key, value in record.__dict__.items():
            if key not in _BUILTIN and key not in payload and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = record.stack_info
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Human-readable colored console output for interactive (TTY) runs."""

    def format(self, record: logging.LogRecord) -> str:
        t = time.strftime("%H:%M:%S", time.gmtime(record.created))
        color = _LEVEL_COLORS.get(record.levelname, "")
        level = f"{color}{record.levelname:<7}{_RESET}" if color else f"{record.levelname:<7}"
        msg = record.getMessage()
        extras = " ".join(
            f"{_DIM}{k}={_RESET}{v}"
            for k, v in record.__dict__.items()
            if k not in _BUILTIN and k not in {"exc_info", "stack_info"} and not k.startswith("_")
        )
        line = f"{_DIM}{t}{_RESET} {level} {record.name} | {msg}"
        if extras:
            line += f" {extras}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class ModuleFileHandler(logging.Handler):
    """Route ``ark.<module>`` records to ``data/logs/<module>.log`` (rotating)."""

    def __init__(
        self,
        logs_dir: Path,
        *,
        max_bytes: int,
        backup_count: int,
        formatter: logging.Formatter,
    ) -> None:
        super().__init__(level=logging.DEBUG)
        self._logs_dir = logs_dir
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._formatter = formatter
        self._handlers: dict[str, RotatingFileHandler] = {}
        self._mu = threading.Lock()

    def _handler_for(self, module: str) -> RotatingFileHandler:
        safe = _SAFE_NAME.sub("_", module)[:64] or "misc"
        with self._mu:
            h = self._handlers.get(safe)
            if h is None:
                self._logs_dir.mkdir(parents=True, exist_ok=True)
                h = RotatingFileHandler(
                    self._logs_dir / f"{safe}.log",
                    maxBytes=self._max_bytes,
                    backupCount=self._backup_count,
                    encoding="utf-8",
                )
                h.setFormatter(self._formatter)
                h.setLevel(logging.DEBUG)
                self._handlers[safe] = h
        return h

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith("ark."):
            return
        module = record.name.split(".", 2)[1] if record.name.count(".") >= 2 else record.name[4:]
        try:
            self._handler_for(module).emit(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self._mu:
            for h in self._handlers.values():
                h.close()
            self._handlers.clear()
        super().close()


class LogBus:
    """Fan out formatted log lines to in-process subscribers (SSE viewers)."""

    def __init__(self) -> None:
        self._subs: list[queue.Queue[str]] = []
        self._mu = threading.Lock()

    def subscribe(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue(maxsize=10000)
        with self._mu:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[str]) -> None:
        with self._mu:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def publish(self, line: str) -> None:
        with self._mu:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(line)
            except queue.Full:
                pass  # slow viewer: drop rather than block the app


class BusHandler(logging.Handler):
    """Push every record (formatted as JSON) onto the LogBus."""

    def __init__(self, bus: LogBus, formatter: logging.Formatter, level: int) -> None:
        super().__init__(level=level)
        self._bus = bus
        self._formatter = formatter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno < self.level:
                return
            self._bus.publish(self._formatter.format(record))
        except Exception:
            self.handleError(record)


_bus = LogBus()


def get_log_bus() -> LogBus:
    return _bus


def setup_logging(config: ArkConfig, paths: Paths, *, force: bool = False) -> None:
    """Configure the root logger (idempotent unless ``force``).

    Attaches: console (TTY color or JSON), ``data/logs/ark.log`` (rotating
    JSON), per-module files, and the LogBus for the in-app viewer.
    """
    root = logging.getLogger()
    if root.handlers and not force:
        # Already configured (e.g. uvicorn re-import): just apply the level.
        root.setLevel(config.logging.level)
        return
    if force:
        for h in list(root.handlers):
            root.removeHandler(h)
            h.close()

    root.setLevel(config.logging.level)
    json_fmt = JsonFormatter()

    if config.logging.console:
        console = logging.StreamHandler(sys.stderr)
        if sys.stderr.isatty():
            console.setFormatter(ConsoleFormatter())
        else:
            console.setFormatter(JsonFormatter())
        console.setLevel(config.logging.level)
        root.addHandler(console)

    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    file_h = RotatingFileHandler(
        paths.logs_dir / "ark.log",
        maxBytes=config.logging.max_bytes,
        backupCount=config.logging.backup_count,
        encoding="utf-8",
    )
    file_h.setFormatter(json_fmt)
    file_h.setLevel(logging.DEBUG)
    root.addHandler(file_h)

    root.addHandler(
        ModuleFileHandler(
            paths.logs_dir,
            max_bytes=config.logging.max_bytes,
            backup_count=config.logging.backup_count,
            formatter=json_fmt,
        )
    )
    root.addHandler(BusHandler(_bus, json_fmt, level=logging.DEBUG))

    # Third-party noise control: uvicorn's access log is off (our middleware
    # logs requests instead); keep its error log but at WARNING+ in normal mode.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("uvicorn.error").setLevel(
        logging.DEBUG if config.debug else config.logging.level
    )
    logging.getLogger("multipart").setLevel(logging.WARNING)


def set_root_level(level: str) -> None:
    """Change the effective log level at runtime (applies to all handlers)."""
    numeric = logging.getLevelName(level.upper())
    if not isinstance(numeric, int):
        raise ValueError(f"Unknown log level: {level!r} (use DEBUG/INFO/WARNING/ERROR/CRITICAL)")
    logging.getLogger().setLevel(numeric)
    for h in logging.getLogger().handlers:
        if not isinstance(h, BusHandler):
            h.setLevel(numeric if not isinstance(h, RotatingFileHandler) else logging.DEBUG)


def tail_log_file(paths: Paths, lines: int = 200, filename: str = "ark.log") -> list[str]:
    """Return the last ``lines`` raw lines of a log file inside data/logs."""
    path = (paths.logs_dir / filename).resolve()
    if path.parent != paths.logs_dir.resolve() or not path.is_file():
        return []
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            block = 4096
            data = b""
            while size > 0 and data.count(b"\n") <= lines:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
                if size == 0:
                    break
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    out = [ln for ln in text.splitlines() if ln.strip()]
    return out[-lines:]
