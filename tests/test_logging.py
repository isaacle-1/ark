"""Structured JSON logging: fields, per-module files, LogBus, runtime level."""

from __future__ import annotations

import json
import logging

from ark.logging_setup import (
    JsonFormatter,
    get_log_bus,
    set_root_level,
    tail_log_file,
)


def _read_json_lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_json_formatter_fields() -> None:
    record = logging.LogRecord(
        "ark.test", logging.INFO, "module.py", 7, "hello %s", ("world",), None
    )
    record.request_id = "rid123"
    record.bytes_total = 9
    payload = json.loads(JsonFormatter().format(record))
    assert payload["ts"].endswith("Z")
    assert payload["level"] == "INFO"
    assert payload["logger"] == "ark.test"
    assert payload["msg"] == "hello world"
    assert payload["request_id"] == "rid123"
    assert payload["src"].endswith(":7")
    assert payload["bytes_total"] == 9


def test_module_file_and_bus(client) -> None:
    paths = client.app.state.paths
    bus = get_log_bus()
    q = bus.subscribe()
    try:
        logging.getLogger("ark.testmodule").info("module line", extra={"key": "val"})
    finally:
        bus.unsubscribe(q)

    line = q.get(timeout=2) if q.empty() is False else None
    while q.qsize():
        line = q.get_nowait()
    assert line
    parsed = json.loads(line)
    assert parsed["logger"] == "ark.testmodule"
    assert parsed["key"] == "val"

    module_file = paths.logs_dir / "testmodule.log"
    assert module_file.is_file()
    lines = _read_json_lines(module_file)
    assert any(r["logger"] == "ark.testmodule" and r["key"] == "val" for r in lines)


def test_root_level_change_persists(client) -> None:
    paths = client.app.state.paths
    set_root_level("ERROR")
    try:
        logging.getLogger("ark.testmodule").info("should be dropped")
        logging.getLogger("ark.testmodule").error("kept line", extra={"x": 1})
    finally:
        set_root_level("DEBUG")
    lines = _read_json_lines(paths.logs_dir / "testmodule.log")
    assert not any(r["msg"] == "should be dropped" for r in lines)
    assert any(r["msg"] == "kept line" for r in lines)


def test_tail_log_file_bounds(paths) -> None:
    (paths.logs_dir / "x.log").write_text(
        "\n".join(f"line{i}" for i in range(50)), encoding="utf-8"
    )
    out = tail_log_file(paths, lines=10, filename="x.log")
    assert len(out) == 10
    assert out[0] == "line40"
    assert tail_log_file(paths, filename="../../etc/passwd") == []
    assert tail_log_file(paths, filename="nope.log") == []


def test_log_bus_drops_for_slow_subscriber() -> None:
    from ark.logging_setup import LogBus

    busy = LogBus()
    q = busy.subscribe()
    for i in range(12000):
        busy.publish(f"line{i}")
    assert q.qsize() <= 10000  # slow viewer: drops rather than blocks the app

    while q.qsize():
        try:
            q.get_nowait()
        except Exception:
            break

    busy.unsubscribe(q)
    busy.publish("after unsubscribe")
    assert q.qsize() == 0  # no lines after the subscriber left
