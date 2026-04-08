# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Events API (metrics service).
File: metrics/rest/events/__init__.py
"""
from fastapi import FastAPI

from .events import (
    router as events_router,
)


def mount_events_routers(app: FastAPI):
    """
    Mount events router onto the metrics app and wire state.
    """
    events_router.state = app.state
    app.include_router(
        events_router,
        prefix="/api/events",
        tags=["Events"],
    )
    return app


__all__ = ["mount_events_routers"]