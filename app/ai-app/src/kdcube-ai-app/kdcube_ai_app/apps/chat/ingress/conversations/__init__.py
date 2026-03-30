# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Modular conversations api management system using FastAPI routers.
File: api/conversations/__init__.py
"""
from fastapi import FastAPI

from .conversations import router as conversations_router


def mount_conversations_router(app: FastAPI):
    """
    Mount all conversations routers to the FastAPI app.

    Args:
        app: Your existing FastAPI application
        auth_manager: AuthManager instance
    """

    # Mount content rebuild router
    conversations_router.state = app.state
    app.include_router(
        conversations_router,
        prefix="/api/cb/conversations",
        tags=["CB Conversations"],
    )
    return app


# Export for convenience
__all__ = ["mount_conversations_router"]
