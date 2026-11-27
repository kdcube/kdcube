# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

"""
Modular orchestrator api management system using FastAPI routers.
File: api/orchestrator/__init__.py
"""
from typing import Callable

from fastapi import FastAPI

from kdcube_ai_app.auth.AuthManager import AuthManager
from kdcube_ai_app.apps.orchestration.api.orchestration_app import router as distributed_locks_router


def mount_orchestrator_routers(app: FastAPI,
                               kb_getter_func: Callable[[str], 'KnowledgeBase'],
                               auth_manager: AuthManager):
    """
    Mount all orchestrator routers to the FastAPI app.

    Args:
        app: Your existing FastAPI application
    """

    distributed_locks_router.auth_manager = auth_manager
    app.include_router(
        distributed_locks_router,
        prefix="/orchestrator",
        tags=["KDKUBE Orchestrator"]
    )
    return app


# Export for convenience
__all__ = ["mount_orchestrator_routers"]
