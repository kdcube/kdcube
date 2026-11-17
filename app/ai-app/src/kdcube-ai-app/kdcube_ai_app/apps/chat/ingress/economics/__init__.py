# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Economics API module.
File: api/economics/__init__.py
"""

from fastapi import FastAPI

from kdcube_ai_app.apps.chat.ingress.economics.stripe_router import router as _state_router
from kdcube_ai_app.apps.chat.ingress.economics.checkout import checkout_router
from kdcube_ai_app.apps.chat.ingress.economics.webhooks import webhook_router
from kdcube_ai_app.apps.chat.ingress.economics.admin import admin_router
from kdcube_ai_app.apps.chat.ingress.economics.me import me_router


def mount_economics_router(app: FastAPI):
    """Mount all economics-related routers to the FastAPI app."""
    _state_router.state = app.state

    # _state_router carries no routes but registers stripe_lifespan with the app
    app.include_router(_state_router, prefix="/api/economics")

    app.include_router(
        checkout_router,
        prefix="/api/economics",
        tags=["Economics: Checkout"])
    app.include_router(
        webhook_router,
        prefix="/api/economics",
        tags=["Economics: Webhooks"])
    app.include_router(
        admin_router,
        prefix="/api/economics",
        tags=["Economics: Admin"])
    app.include_router(
        me_router,
        prefix="/api/economics",
        tags=["Economics: User"])

    return app


__all__ = ["mount_economics_router"]
