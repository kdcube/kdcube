# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Modular resources api management system using FastAPI routers.
File: api/resources/__init__.py
"""
from typing import Callable

from fastapi import FastAPI

from .resources import router as resources_router


def mount_resources_router(app: FastAPI):
    """
    Mount all resources routers to the FastAPI app.

    Args:
        app: Your existing FastAPI application
        auth_manager: AuthManager instance
    """

    # Mount content rebuild router
    resources_router.state = app.state
    app.include_router(
        resources_router,
        prefix="/api/cb/resources",
        tags=["CB Resources"],
    )
    return app


# Export for convenience
__all__ = ["mount_resources_router"]
