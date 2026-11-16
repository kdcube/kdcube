# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Modular registry api management system using FastAPI routers.
File: api/registry/__init__.py
"""
from typing import Callable

from fastapi import FastAPI

from kdcube_ai_app.auth.AuthManager import AuthManager
from kdcube_ai_app.apps.knowledge_base.api.registry.registry import router as registry_router


def mount_registry_routers(app: FastAPI,
                           kb_getter_func: Callable[[str], 'KnowledgeBase'],
                           auth_manager: AuthManager):
    """
    Mount all registry routers to the FastAPI app.

    Args:
        app: Your existing FastAPI application
        kb_getter_func: Function that returns KB instance
        auth_manager: AuthManager instance
    """

    # Mount content rebuild router
    registry_router.kb_getter = kb_getter_func
    registry_router.auth_manager = auth_manager

    app.include_router(
        registry_router,
        prefix="/api/kb",
        tags=["KB Registry"]
    )
    return app


# Export for convenience
__all__ = ["mount_registry_routers"]
