# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# logging_config.py
import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path


def _to_level(name: str, default: int) -> int:
    try:
        return getattr(logging, (name or "").upper())
    except Exception:
        return default


def configure_logging():
    # --- Global settings from env ---
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = os.getenv(
        "LOG_FORMAT",
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    level = _to_level(log_level_name, logging.INFO)

    # ---- Console handler ----
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(log_format))

    # ---- File handler with rotation ----
    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    file_prefix = os.getenv("LOG_FILE_PREFIX", "kdcube")
    base_log_path = log_dir / f"{file_prefix}.log"

    max_mb = int(os.getenv("LOG_MAX_MB", "20"))          # 20 MB default
    backup_count = int(os.getenv("LOG_BACKUP_COUNT", "10"))

    file_handler = RotatingFileHandler(
        base_log_path,
        maxBytes=max_mb * 1024 * 1024,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(log_format))

    # Rename rotated files to include timestamp instead of ".1", ".2", ...
    def _namer(default_name: str) -> str:
        """
        default_name is like '/path/to/chat.log.1'
        We want '/path/to/chat-YYYYMMDD-HHMMSS.log'
        """
        p = Path(default_name)

        # Strip the trailing numeric suffix (.1, .2, ...) first
        # p.name == 'chat.log.1'
        root, _idx = p.name.rsplit(".", 1)        # 'chat.log', '1'
        root_base, root_ext = os.path.splitext(root)  # 'chat', '.log'

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        new_name = f"{root_base}-{ts}{root_ext}"
        return str(p.with_name(new_name))

    file_handler.namer = _namer  # let RotatingFileHandler call this on rollover

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
