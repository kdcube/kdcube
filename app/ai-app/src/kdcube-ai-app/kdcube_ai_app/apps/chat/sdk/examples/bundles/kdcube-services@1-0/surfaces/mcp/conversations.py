"""Managed MCP tool registration for KDCube service modules."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.mcp_export import (
    ConversationReadServiceFactory,
    CurrentUserIdFactory,
    export_current_user_conversations,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_metadata import (
    kdcube_mcp_icons,
    kdcube_website_url,
    read_only_annotations,
)

def build_conversations_mcp_app(
    *,
    name: str,
    read_service_factory: ConversationReadServiceFactory,
    current_user_id_factory: CurrentUserIdFactory,
    request: Any = None,
):
    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.types import Icon, ToolAnnotations
    except Exception as exc:  # pragma: no cover - runtime dependency
        raise ImportError("mcp server SDK is not installed") from exc

    icons = kdcube_mcp_icons(Icon, request=request)
    mcp = FastMCP(
        name,
        stateless_http=True,
        icons=icons,
        website_url=kdcube_website_url(request=request),
    )

    @mcp.tool(
        name="conversations_export",
        title="Export conversations",
        description=(
            "Export conversation transcripts for the approving KDCube user in "
            "the current project. The server resolves tenant, project, and user "
            "from the delegated MCP credential; callers should normally provide "
            "only limit and optional since."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Export conversations"),
    )
    async def _conversations_export(
        since: Annotated[
            str,
            Field(
                description=(
                    "Optional ISO timestamp. When set, only conversations started "
                    "at or after this time are returned, for example "
                    "2026-06-01T00:00:00Z."
                )
            ),
        ] = "",
        limit: Annotated[
            int,
            Field(
                ge=1,
                le=500,
                description=(
                    "Maximum number of conversation records to return. The server "
                    "clamps this to 1..500."
                ),
            ),
        ] = 100,
    ) -> dict[str, Any]:
        return await export_current_user_conversations(
            read_service_factory=read_service_factory,
            current_user_id_factory=current_user_id_factory,
            since=since,
            limit=limit,
        )

    return mcp
