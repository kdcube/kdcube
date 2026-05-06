# SPDX-License-Identifier: MIT

from __future__ import annotations

import logging
import logging.handlers
from types import SimpleNamespace

from kdcube_ai_app.apps.utils import logging_config


def test_configure_logging_prefers_runtime_env_log_dir_and_prefix(monkeypatch, tmp_path):
    runtime_log_dir = tmp_path / "runtime-logs"
    monkeypatch.setenv("LOG_DIR", str(runtime_log_dir))
    monkeypatch.setenv("LOG_FILE_PREFIX", "supervisor")
    monkeypatch.setenv("LOG_LEVEL", "INFO")

    fake_settings = SimpleNamespace(
        PLATFORM=SimpleNamespace(
            LOG=SimpleNamespace(
                LOG_LEVEL="WARNING",
                LOG_MAX_MB=20,
                LOG_BACKUP_COUNT=10,
                LOG_DIR="/Users/elenaviter/.kdcube/host-log-dir",
                LOG_FILE_PREFIX="host-service",
            )
        )
    )
    monkeypatch.setattr(logging_config, "get_settings", lambda: fake_settings)

    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level

    try:
        logging_config.configure_logging()

        assert runtime_log_dir.exists()
        assert (runtime_log_dir / "supervisor.log").exists()
        assert not (runtime_log_dir / "host-service.log").exists()
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        for handler in old_handlers:
            root.addHandler(handler)
        root.setLevel(old_level)


def test_configure_logging_uses_deterministic_default_rotation_names(monkeypatch, tmp_path):
    runtime_log_dir = tmp_path / "runtime-logs"
    monkeypatch.setenv("LOG_DIR", str(runtime_log_dir))
    monkeypatch.setenv("LOG_FILE_PREFIX", "chat-ingress")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_MAX_MB", "1")
    monkeypatch.setenv("LOG_BACKUP_COUNT", "3")

    fake_settings = SimpleNamespace(
        PLATFORM=SimpleNamespace(
            LOG=SimpleNamespace(
                LOG_LEVEL="WARNING",
                LOG_MAX_MB=20,
                LOG_BACKUP_COUNT=10,
                LOG_DIR="/Users/elenaviter/.kdcube/host-log-dir",
                LOG_FILE_PREFIX="host-service",
            )
        )
    )
    monkeypatch.setattr(logging_config, "get_settings", lambda: fake_settings)

    root = logging.getLogger()
    old_handlers = list(root.handlers)
    old_level = root.level

    try:
        logging_config.configure_logging()
        file_handlers = [
            handler
            for handler in root.handlers
            if isinstance(handler, logging_config.SafeRotatingFileHandler)
        ]

        assert len(file_handlers) == 1
        handler = file_handlers[0]
        assert handler.namer is None
        assert handler.rotation_filename(str(runtime_log_dir / "chat-ingress.log.1")) == str(
            runtime_log_dir / "chat-ingress.log.1"
        )
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        for handler in old_handlers:
            root.addHandler(handler)
        root.setLevel(old_level)


def test_safe_rotating_file_handler_tolerates_missing_file_rollover_race(monkeypatch, tmp_path):
    log_path = tmp_path / "service.log"
    handler = logging_config.SafeRotatingFileHandler(
        log_path,
        maxBytes=1,
        backupCount=1,
        encoding="utf-8",
    )
    try:
        handler.emit(logging.makeLogRecord({"msg": "before rollover"}))

        def raise_enoent(_src, _dst):
            raise FileNotFoundError(2, "No such file or directory", str(log_path))

        monkeypatch.setattr(logging.handlers.os, "rename", raise_enoent)

        handler.doRollover()

        assert handler.stream is not None
    finally:
        handler.close()
