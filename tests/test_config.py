"""Config loading, validation and env overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from ark.config import ConfigError, load_config


def test_missing_config_file_uses_defaults(paths: Path) -> None:
    cfg = load_config(paths)
    assert cfg.server.port == 8080
    assert cfg.auth.mode == "open"
    assert cfg.jobs.enabled is True


def test_config_roundtrip_and_env_log_level(paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths.config_file.write_text(
        "[server]\nhost = '127.0.0.1'\nport = 9000\n"
        "[logging]\nlevel = 'INFO'\n"
        "[auth]\nmode = 'required'\n",
        encoding="utf-8",
    )
    cfg = load_config(paths)
    assert cfg.server.port == 9000
    assert cfg.auth.mode == "required"
    assert cfg.logging.level == "INFO"

    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    assert load_config(paths).log_level == "DEBUG"


def test_invalid_toml_is_reported(paths: Path) -> None:
    paths.config_file.write_text("[server\n  broken", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(paths)


def test_invalid_value_human_error(paths: Path) -> None:
    paths.config_file.write_text("[server]\nport = 9999999\n", encoding="utf-8")
    with pytest.raises(ConfigError) as err:
        load_config(paths)
    msg = str(err.value)
    assert "server.port" in msg
    assert any(word in msg for word in ("number", "expected", "value"))
