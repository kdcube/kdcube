# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Modular Control Plane api management system using FastAPI routers.
File: api/control_plane/__init__.py
"""
from typing import Callable

from fastapi import FastAPI

from .control_plane import router as control_plane_router
from .conversations_browser import router as conversations_browser_router
from .redis_browser import router as redis_browser_router


def mount_control_plane_router(app: FastAPI):
    """
    Mount all monitoring routers to the FastAPI app.

    Args:
        app: Your existing FastAPI application
        auth_manager: AuthManager instance
    """

    control_plane_router.state = app.state
    conversations_browser_router.state = app.state
    redis_browser_router.state = app.state
    app.include_router(
        control_plane_router,
        prefix="/api/admin/control-plane",
        tags=["CP admin"],
    )
    app.include_router(
        conversations_browser_router,
        prefix="/api/admin/control-plane/conversations",
        tags=["CP admin conversations"],
    )
    app.include_router(
        redis_browser_router,
        prefix="/api/admin/control-plane/redis",
        tags=["CP admin redis"],
    )
    return app


# Export for convenience
__all__ = ["mount_control_plane_router"]
