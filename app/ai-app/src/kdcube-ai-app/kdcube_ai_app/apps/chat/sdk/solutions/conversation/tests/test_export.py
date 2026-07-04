# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.export import (
    DEFAULT_EXPORT_LIMIT,
    MAX_EXPORT_LIMIT,
    ConversationExportRequest,
    ConversationExportService,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.mcp_export import (
    export_current_user_conversations,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.read import (
    ConversationExportScope,
    ConversationReadScope,
)


class _ReadService:
    def __init__(self):
        self.requests = []

    async def export_conversations(self, request: ConversationExportScope):
        self.requests.append(request)
        user_id = request.scope.resolve()
        return {
            "ok": True,
            "count": 1,
            "total_available": 1,
            "limited": False,
            "conversations": [{"conversation_id": "c1", "user_id": user_id}],
        }


def test_request_normalization_and_limit_clamping():
    req = ConversationExportRequest(
        scope=ConversationReadScope(current_user_id="u1"),
        since="  2026-01-01  ",
        limit=0,
    )
    assert req.normalized_since == "2026-01-01"
    assert req.normalized_limit == DEFAULT_EXPORT_LIMIT
    assert ConversationExportRequest(
        scope=ConversationReadScope(current_user_id="u1"),
        limit=10_000,
    ).normalized_limit == MAX_EXPORT_LIMIT
    assert ConversationExportRequest(
        scope=ConversationReadScope(current_user_id="u1"),
        limit=5,
    ).normalized_limit == 5


@pytest.mark.asyncio
async def test_export_service_delegates_to_user_scoped_read_service():
    read_service = _ReadService()
    service = ConversationExportService(read_service)  # type: ignore[arg-type]

    result = await service.export(
        ConversationExportRequest(
            scope=ConversationReadScope(current_user_id="user-1"),
            since="2026-01-01T00:00:00Z",
            limit=5,
        )
    )

    assert result["ok"] is True
    assert result["conversations"][0]["user_id"] == "user-1"
    assert len(read_service.requests) == 1
    request = read_service.requests[0]
    assert request.scope.resolve() == "user-1"
    assert request.since == "2026-01-01T00:00:00Z"
    assert request.limit == 5


@pytest.mark.asyncio
async def test_mcp_export_uses_current_user_factory():
    read_service = _ReadService()

    result = await export_current_user_conversations(
        read_service_factory=lambda: read_service,
        current_user_id_factory=lambda: "platform-user-1",
        limit=3,
    )

    assert result["ok"] is True
    assert result["conversations"][0]["user_id"] == "platform-user-1"


@pytest.mark.asyncio
async def test_mcp_export_rejects_missing_current_user():
    result = await export_current_user_conversations(
        read_service_factory=_ReadService,
        current_user_id_factory=lambda: "",
        limit=3,
    )

    assert result == {"ok": False, "error": "delegated user is required"}
