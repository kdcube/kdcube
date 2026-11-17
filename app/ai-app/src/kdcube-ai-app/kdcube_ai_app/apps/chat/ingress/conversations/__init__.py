# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Modular conversations api management system using FastAPI routers.
File: api/conversations/__init__.py
"""
from fastapi import FastAPI

from kdcube_ai_app.apps.chat.ingress.conversations.conversations import router as conversations_router
from kdcube_ai_app.apps.chat.ingress.conversations.search import router as conversations_search_router


def mount_conversations_router(app: FastAPI):
    """
    Mount all conversations routers to the FastAPI app.

    Args:
        app: Your existing FastAPI application
        auth_manager: AuthManager instance
    """

    # Search router first: its literal "/search" tail must win over the
    # sibling router's "/{conversation_id}"-style path parameters.
    for _router in (conversations_search_router, conversations_router):
        _router.state = app.state
        app.include_router(
            _router,
            prefix="/api/cb/conversations",
            tags=["CB Conversations"],
        )
    return app


# Export for convenience
__all__ = ["mount_conversations_router"]
