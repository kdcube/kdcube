# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Modular search api management system using FastAPI routers.
File: api/search/__init__.py
"""
from typing import Callable

from fastapi import FastAPI

from kdcube_ai_app.auth.AuthManager import AuthManager
from kdcube_ai_app.apps.knowledge_base.api.search.search import router as search_router


def mount_search_routers(app: FastAPI,
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
    search_router.kb_getter = kb_getter_func
    search_router.auth_manager = auth_manager
    app.include_router(
        search_router,
        prefix="/api/kb",
        tags=["KB Search"]
    )
    return app


# Export for convenience
__all__ = ["mount_search_routers"]
