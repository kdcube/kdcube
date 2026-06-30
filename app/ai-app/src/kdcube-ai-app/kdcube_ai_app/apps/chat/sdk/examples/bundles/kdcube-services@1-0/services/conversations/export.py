"""Conversation export service.

This module is product logic, not platform auth. It assumes the calling surface
has already authorized `conversations:read` for the current delegated credential.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.export_adapter import (
    ControlPlaneDataSource,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.export_tool import (
    export_conversations,
)


MAX_EXPORT_LIMIT = 500
DEFAULT_EXPORT_LIMIT = 100


@dataclass(frozen=True)
class ConversationExportRequest:
    since: str = ""
    tenant: str = ""
    project: str = ""
    limit: int = DEFAULT_EXPORT_LIMIT

    @property
    def normalized_tenant(self) -> str:
        return str(self.tenant or "").strip()

    @property
    def normalized_project(self) -> str:
        return str(self.project or "").strip()

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

    def validate(self) -> str | None:
        if bool(self.normalized_tenant) != bool(self.normalized_project):
            return "tenant and project must be provided together"
        return None


class ConversationExportService:
    def __init__(self, *, pg_pool: Any):
        self._pg_pool = pg_pool

    async def export(self, request: ConversationExportRequest) -> dict[str, Any]:
        if self._pg_pool is None:
            return {"ok": False, "error": "database pool unavailable"}

        validation_error = request.validate()
        if validation_error:
            return {"ok": False, "error": validation_error}

        records = await export_conversations(
            ControlPlaneDataSource(self._pg_pool),
            since=request.normalized_since or None,
            tenant=request.normalized_tenant or None,
            project=request.normalized_project or None,
        )
        max_records = request.normalized_limit
        return {
            "ok": True,
            "count": min(len(records), max_records),
            "total_available": len(records),
            "limited": len(records) > max_records,
            "conversations": records[:max_records],
        }
