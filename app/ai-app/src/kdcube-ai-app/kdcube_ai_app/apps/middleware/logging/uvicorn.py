# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

# middleware/logging/uvicorn.py
import logging
import logging.config
from typing import Iterable

class UvicornAccessPathFilter(logging.Filter):
    """Hide access logs for selected paths/prefixes (health/monitoring/etc.)."""
    def __init__(self, silenced_paths: Iterable[str] = (), silenced_prefixes: Iterable[str] = ()):
        super().__init__()
        self.silenced_paths = set(silenced_paths)
        self.silenced_prefixes = tuple(silenced_prefixes)

    def filter(self, record: logging.LogRecord) -> bool:
        # Works across uvicorn variants
        req_line = getattr(record, "request_line", None)
        if not req_line and isinstance(record.args, tuple) and len(record.args) >= 2:
            req_line = record.args[1]  # fallback for older uvicorn

        if not isinstance(req_line, str):
            return True

        # "GET /path HTTP/1.1"
        parts = req_line.split()
        path = parts[1] if len(parts) >= 2 else ""

        if path in self.silenced_paths:
            return False
        if any(path.startswith(p) for p in self.silenced_prefixes):
            return False
        return True

def configure_logging(
        silenced_paths=("/health", "/landing/health"),
        silenced_prefixes=("/monitoring/", "/metrics"),
        socketio_level="WARNING",
        mcp_level="WARNING",
):
    """
    Call once, as early as possible (before app = FastAPI(...)).
    """
    LOGGING_CONFIG = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "uvicorn_access_filter": {
                "()": UvicornAccessPathFilter,   # callable directly
                "silenced_paths": list(silenced_paths),
                "silenced_prefixes": list(silenced_prefixes),
            }
        },
        "formatters": {
            "default": {"format": "%(levelname)s:%(name)s:%(message)s"},
            "access":  {"format": "%(levelname)s:%(name)s:%(message)s"},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
            },
            "access_console": {
                "class": "logging.StreamHandler",
                "formatter": "access",
                "filters": ["uvicorn_access_filter"],
            },
        },
        "loggers": {
            "uvicorn":        {"level": "INFO", "handlers": ["console"], "propagate": False},
            "uvicorn.error":  {"level": "INFO", "handlers": ["console"], "propagate": False},
            "uvicorn.access": {"level": "INFO", "handlers": ["access_console"], "propagate": False},

            # Quiet Socket.IO / Engine.IO
            "engineio.server": {"level": socketio_level},
            "socketio.server": {"level": socketio_level},

            # Quiet the MCP SDK's transport bookkeeping. It logs a session
            # manager starting and shutting down around every single request,
            # which on a bundle that serves an MCP surface is two lines per
            # call and says nothing a reader can act on. Failures still carry
            # their own warnings and exceptions.
            "mcp.server.streamable_http_manager": {"level": mcp_level},
            "mcp.server.sse": {"level": mcp_level},
            "mcp.server.lowlevel.server": {"level": mcp_level},
        },
    }

    logging.config.dictConfig(LOGGING_CONFIG)
