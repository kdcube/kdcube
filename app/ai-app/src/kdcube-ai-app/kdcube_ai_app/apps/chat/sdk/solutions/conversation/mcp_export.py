# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Conversation export facade for MCP/agent surfaces.

This module owns the user-scoped export contract used by external-agent
surfaces. Transport adapters pass in an already-bound ConversationReadService
and the resolved runtime user; no router/app state and no control-plane
ingress code participates here.
"""

from __future__ import annotations

from typing import Any, Callable

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.export import (
    ConversationExportRequest,
    ConversationExportService,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.read import (
    ConversationReadScope,
)


ConversationReadServiceFactory = Callable[[], Any]
CurrentUserIdFactory = Callable[[], str]


async def export_current_user_conversations(
    *,
    read_service_factory: ConversationReadServiceFactory,
    current_user_id_factory: CurrentUserIdFactory,
    since: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """Export conversations for the resolved current platform/delegated user.

    The caller supplies factories so the surrounding runtime can bind tenant,
    project, storage, and identity before this SDK function runs. This is the
    same user-scoped contract used by the `conv` named service; direct MCP does
    not perform tenant/project bulk export.
    """

    user_id = str(current_user_id_factory() or "").strip()
    if not user_id:
        return {"ok": False, "error": "delegated user is required"}

    service = ConversationExportService(read_service_factory())
    return await service.export(
        ConversationExportRequest(
            scope=ConversationReadScope(current_user_id=user_id),
            since=str(since or "").strip(),
            limit=limit,
        )
    )


__all__ = [
    "ConversationReadServiceFactory",
    "CurrentUserIdFactory",
    "export_current_user_conversations",
]
