# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Modular monitoring api management system using FastAPI routers.
File: api/monitoring/__init__.py
"""
from typing import Callable

from fastapi import FastAPI

from .monitoring import router as monitoring_router


def mount_monitoring_routers(app: FastAPI):
    """
    Mount all monitoring routers to the FastAPI app.

    Args:
        app: Your existing FastAPI application
        auth_manager: AuthManager instance
    """

    # Mount content rebuild router
    monitoring_router.state = app.state
    app.include_router(
        monitoring_router,
        prefix="",
        tags=["CB monitoring"]
    )
    return app


# Export for convenience
__all__ = ["mount_monitoring_routers"]
