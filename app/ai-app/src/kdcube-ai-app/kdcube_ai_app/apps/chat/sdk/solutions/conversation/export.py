# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""SDK conversation export interface.

The export interface is intentionally user-scoped and storage-port based. It
wraps `ConversationReadService.export_conversations`; it does not know about
ingress routers, control-plane API routes, or global tenant/project bulk export.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.read import (
    DEFAULT_EXPORT_LIMIT,
    MAX_EXPORT_LIMIT,
    ConversationExportScope,
    ConversationReadScope,
    ConversationReadService,
)


@dataclass(frozen=True)
class ConversationExportRequest:
    scope: ConversationReadScope
    since: str = ""
    limit: int = DEFAULT_EXPORT_LIMIT

    @property
    def normalized_since(self) -> str:
        return str(self.since or "").strip()

    @property
    def normalized_limit(self) -> int:
        try:
            requested = int(self.limit or DEFAULT_EXPORT_LIMIT)
        except Exception:
            requested = DEFAULT_EXPORT_LIMIT
        return max(1, min(requested, MAX_EXPORT_LIMIT))


class ConversationExportService:
    """Thin export facade over the SDK conversation read service."""

    def __init__(self, read_service: ConversationReadService):
        self._read_service = read_service

    async def export(self, request: ConversationExportRequest) -> dict[str, Any]:
        return await self._read_service.export_conversations(
            ConversationExportScope(
                scope=request.scope,
                since=request.normalized_since,
                limit=request.normalized_limit,
            )
        )


__all__ = [
    "DEFAULT_EXPORT_LIMIT",
    "MAX_EXPORT_LIMIT",
    "ConversationExportRequest",
    "ConversationExportService",
]
