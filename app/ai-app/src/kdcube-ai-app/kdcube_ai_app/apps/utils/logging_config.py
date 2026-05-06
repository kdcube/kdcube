# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# logging_config.py
import logging
import os
import errno
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path
from kdcube_ai_app.apps.chat.sdk.config import get_settings


def _to_level(name: str, default: int) -> int:
    try:
        return getattr(logging, (name or "").upper())
    except Exception:
        return default


def _env_text(name: str) -> str | None:
    value = str(os.getenv(name) or "").strip()
    return value or None


def _env_int(name: str) -> int | None:
    raw = _env_text(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except Exception:
        return None


class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler variant that tolerates concurrent rollover races."""

    def doRollover(self):
        try:
            super().doRollover()
        except OSError as exc:
            if getattr(exc, "errno", None) != errno.ENOENT:
                raise
            # Another worker rotated or removed the file between exists() and
            # rename(). Keep logging alive; losing one rollover race is better
            # than emitting logging tracebacks into the service log stream.
            if self.stream:
                try:
                    self.stream.close()
                except Exception:
                    pass
                self.stream = None
            if not self.delay:
                self.stream = self._open()


def configure_logging():
    # --- Global settings from config ---
    _log = get_settings().PLATFORM.LOG
    log_level_name = (_env_text("LOG_LEVEL") or _log.LOG_LEVEL or "INFO").upper()
    log_format = _env_text("LOG_FORMAT") or "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    level = _to_level(log_level_name, logging.INFO)

    # ---- Console handler ----
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(log_format))

    # ---- File handler with rotation ----
    log_dir = Path(_env_text("LOG_DIR") or _log.LOG_DIR or "logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    file_prefix = _env_text("LOG_FILE_PREFIX") or _log.LOG_FILE_PREFIX or "kdcube"
    base_log_path = log_dir / f"{file_prefix}.log"

    max_mb = _env_int("LOG_MAX_MB") or _log.LOG_MAX_MB
    backup_count = _env_int("LOG_BACKUP_COUNT") or _log.LOG_BACKUP_COUNT

    file_handler = SafeRotatingFileHandler(
        base_log_path,
        maxBytes=max_mb * 1024 * 1024,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(log_format))

    # ---- Root logger as single source of truth ----
    root = logging.getLogger()
    root.setLevel(level)

    # Clear any existing handlers (important when reloading / in tests)
    for h in list(root.handlers):
        root.removeHandler(h)

    # Install our handlers
    root.addHandler(console_handler)
    root.addHandler(file_handler)
    _ensure_exec_banner(file_handler)

    # Route warnings.warn(...) into logging
    logging.captureWarnings(True)

    # --- Normalize noisy / framework loggers ---
    # You can tune these via env if needed.
    desired_levels = {
        # uvicorn (you run with log_config=None, but set levels anyway)
        "uvicorn": os.getenv("UVICORN_LEVEL", log_level_name),
        "uvicorn.error": os.getenv("UVICORN_ERROR_LEVEL", log_level_name),
        "uvicorn.access": os.getenv("UVICORN_ACCESS_LEVEL", "WARNING"),

        # python-socketio / python-engineio
        "socketio": os.getenv("SOCKETIO_LEVEL", "INFO"),
        "socketio.server": os.getenv("SOCKETIO_LEVEL", "INFO"),
        "socketio.client": os.getenv("SOCKETIO_LEVEL", "INFO"),
        "engineio": os.getenv("ENGINEIO_LEVEL", "WARNING"),
        "engineio.server": os.getenv("ENGINEIO_LEVEL", "WARNING"),
        "engineio.client": os.getenv("ENGINEIO_LEVEL", "WARNING"),


        # asyncio, etc.
        "asyncio": os.getenv("ASYNCIO_LEVEL", "WARNING"),
        "watchfiles": os.getenv("WATCHFILES_LEVEL", "WARNING"),
        "aiohttp.access": os.getenv("AIOHTTP_ACCESS_LEVEL", "WARNING"),

        # 🧊 quiet AWS creds noise
        "aiobotocore.credentials": os.getenv("AIOBOTOCORE_CREDENTIALS_LEVEL", "WARNING"),
        # (optional, sometimes botocore itself also logs similarly)
        "botocore.credentials": os.getenv("BOTOCORE_CREDENTIALS_LEVEL", "WARNING"),
    }

    for name, lvl_name in desired_levels.items():
        lg = logging.getLogger(name)
        # Remove any handlers these libs may have attached (causes duplicates)
        for h in list(lg.handlers):
            lg.removeHandler(h)
        lg.propagate = True                 # bubble up to root only
        lg.setLevel(_to_level(lvl_name, level))

    # Mute noisy socket.io chatter
    logging.getLogger("socketio").setLevel(logging.WARNING)
    logging.getLogger("socketio.server").setLevel(logging.WARNING)
    # (optional) also quiet engineio ping/pong
    logging.getLogger("engineio").setLevel(logging.WARNING)
    logging.getLogger("engineio.server").setLevel(logging.WARNING)


def _ensure_exec_banner(handler: logging.Handler) -> None:
    if not os.getenv("EXECUTION_SANDBOX"):
        return
    exec_id = os.getenv("EXECUTION_ID")
    if not exec_id:
        return
    base = getattr(handler, "baseFilename", None)
    if not base:
        return
    path = Path(base)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        banner = f"===== EXECUTION {exec_id} START {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} =====\n"
        if path.exists():
            try:
                with open(path, "rb") as f:
                    f.seek(max(0, path.stat().st_size - 2048))
                    tail = f.read().decode("utf-8", errors="ignore")
                if f"===== EXECUTION {exec_id} START" in tail:
                    return
            except Exception:
                pass
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n" + banner)
    except Exception:
        pass
