"""Managed MCP tool registration for KDCube service modules."""

from __future__ import annotations

from typing import Annotated, Any, Callable, Type

from pydantic import Field

from kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_metadata import (
    kdcube_mcp_icons,
    kdcube_website_url,
    read_only_annotations,
)

PoolFactory = Callable[[], Any]


def build_conversations_mcp_app(
    *,
    name: str,
    pool_factory: PoolFactory,
    request_model: Type[Any],
    service_cls: Type[Any],
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
            "Export KDCube conversation transcripts visible to the approving "
            "user. In the default descriptor this tool is delegable only by a "
            "super-admin and is intended for feedback triage and operational "
            "review through delegated external clients."
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
        tenant: Annotated[
            str,
            Field(
                description=(
                    "Optional tenant id. If provided, project must also be "
                    "provided. Leave empty to export across all registered "
                    "tenant/project pairs allowed by the service."
                )
            ),
        ] = "",
        project: Annotated[
            str,
            Field(
                description=(
                    "Optional project id. Used together with tenant to limit the "
                    "export to one project."
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
        service = service_cls(pg_pool=pool_factory())
        return await service.export(
            request_model(
                since=since,
                tenant=tenant,
                project=project,
                limit=limit,
            )
        )

    return mcp
