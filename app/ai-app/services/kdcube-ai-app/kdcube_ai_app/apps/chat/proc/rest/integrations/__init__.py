# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Integrations API (processor service).
File: chat/proc/rest/integrations/__init__.py
"""
from fastapi import FastAPI

from .integrations import router as integrations_router, admin_router as integrations_admin_router


def mount_integrations_routers(app: FastAPI):
    """
    Mount integrations router onto the processor app and wire state.
    """
    integrations_router.state = app.state
    integrations_admin_router.state = app.state
    app.include_router(
        integrations_router,
        prefix="/api/integrations",
        tags=["Integrations"],
    )
    app.include_router(
        integrations_admin_router,
        prefix="",
        tags=["Integrations Admin"],
    )
    return app


__all__ = ["mount_integrations_routers"]
