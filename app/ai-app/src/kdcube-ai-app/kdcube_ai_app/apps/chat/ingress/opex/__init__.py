# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Modular opex api management system using FastAPI routers.
File: api/opex/__init__.py
"""
from typing import Callable

from fastapi import FastAPI

from .opex import router as opex_router


def mount_opex_router(app: FastAPI):
    """
    Mount all monitoring routers to the FastAPI app.

    Args:
        app: Your existing FastAPI application
        auth_manager: AuthManager instance
    """

    # Mount content rebuild router
    opex_router.state = app.state
    app.include_router(
        opex_router,
        prefix="/api/opex",
        tags=["CB opex"],
    )
    return app


# Export for convenience
__all__ = ["mount_opex_router"]
