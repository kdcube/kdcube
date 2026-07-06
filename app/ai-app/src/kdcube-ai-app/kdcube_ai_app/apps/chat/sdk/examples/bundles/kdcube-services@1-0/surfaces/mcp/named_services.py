from __future__ import annotations

from typing import Annotated, Any, Callable, Literal, Mapping

from pydantic import Field

from kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_metadata import (
    action_annotations,
    destructive_annotations,
    kdcube_mcp_icons,
    kdcube_website_url,
    read_only_annotations,
    write_annotations,
)


ConfigFactory = Callable[[], Mapping[str, Any]]
BridgeFactory = Callable[..., Any]

# Temporary connector-diagnostic switch. Keep this as an explicit one-line
# toggle while debugging Claude connector acceptance. The default must match
# the OAuth protected-resource metadata for this surface.
_REGISTER_EXTENDED_NAMED_SERVICE_TOOLS = True

NAMED_SERVICES_MCP_INSTRUCTIONS = """\
This MCP server exposes configured KDCube named-service namespaces.

Use this workflow:
1. Call named_services_list first, unless the user explicitly named a namespace
   and operation.
2. Use namespaces exactly as returned by named_services_list, for example mem,
   task, cnv, conv, mail, or slack.
3. For an unfamiliar namespace, call named_services_capabilities and
   named_services_schema before search/get/write operations.
4. Use named_services_search to find objects before named_services_get when the
   exact object ref is not known.
5. Only use write, action, host-file, delete, or generic call tools when the user
   clearly asks for that operation and the namespace capability allows it. For
   mail, actions include download_attachments, send, and forward. For slack,
   actions include post_message, upload_file, download_file, and
   assistant_search_info.
6. If a call reports missing grants or a forbidden operation, explain which
   namespace/tool needs additional consent instead of retrying blindly.
"""

NamedServiceOperation = Literal[
    "provider.about",
    "provider.capabilities",
    "object.list",
    "object.search",
    "object.get",
    "object.host_file",
    "object.schema",
    "object.upsert",
    "object.delete",
    "object.action",
]


def build_named_services_mcp_app(
    *,
    name: str,
    config_factory: ConfigFactory,
    tenant_factory: Callable[[], str],
    project_factory: Callable[[], str],
    request: Any,
    bridge_factory: BridgeFactory,
):
    """Build the managed MCP surface for configured KDCube named services."""

    try:
        from mcp.server.fastmcp import FastMCP
        from mcp.types import Icon, ToolAnnotations
    except Exception as exc:  # pragma: no cover - runtime dependency
        raise ImportError("mcp server SDK is not installed") from exc

    # The proc MCP bridge dispatches every HTTP exchange independently.
    # Stateless FastMCP keeps initialize/tools/list/tools/call valid across
    # fresh app instances and distributed proc workers.
    icons = kdcube_mcp_icons(Icon, request=request)
    mcp = FastMCP(
        name,
        instructions=NAMED_SERVICES_MCP_INSTRUCTIONS,
        stateless_http=True,
        icons=icons,
        website_url=kdcube_website_url(request=request),
    )

    def _bridge():
        return bridge_factory(
            config=dict(config_factory() or {}),
            tenant=tenant_factory(),
            project=project_factory(),
            request=request,
        )

    @mcp.tool(
        name="named_services_list",
        title="List named services",
        description=(
            "List the named-service namespaces exposed through this KDCube MCP "
            "surface, including the configured per-operation delegated grants."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="List named services"),
        structured_output=False,
    )
    async def _named_services_list() -> dict[str, Any]:
        return _bridge().list_services()

    @mcp.tool(
        name="named_services_about",
        title="Named service about",
        description=(
            "Read a configured named-service provider's about payload. "
            "The namespace must be one of the values returned by named_services_list."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Named service about"),
        structured_output=False,
    )
    async def _named_services_about(
        namespace: Annotated[
            str,
            Field(description="Configured named-service namespace, for example 'mem' or 'mail'."),
        ],
        provider: Annotated[
            str,
            Field(description="Optional provider id when a namespace has more than one provider."),
        ] = "",
    ) -> dict[str, Any]:
        return await _bridge().about(namespace=namespace, provider=provider)

    @mcp.tool(
        name="named_services_capabilities",
        title="Named service capabilities",
        description=(
            "Read provider capabilities for a configured named-service namespace, "
            "including provider-declared operations and object behaviors."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Named service capabilities"),
        structured_output=False,
    )
    async def _named_services_capabilities(
        namespace: Annotated[
            str,
            Field(description="Configured named-service namespace, for example 'mem', 'task', 'cnv', 'conv', 'mail', or 'slack'."),
        ],
        provider: Annotated[
            str,
            Field(description="Optional provider id when a namespace has more than one provider."),
        ] = "",
    ) -> dict[str, Any]:
        return await _bridge().capabilities(namespace=namespace, provider=provider)

    @mcp.tool(
        name="named_services_schema",
        title="Named service schema",
        description=(
            "Read the object schema for a configured named-service namespace. "
            "For mail account/message refs use namespace='mail'."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Named service schema"),
        structured_output=False,
    )
    async def _named_services_schema(
        namespace: Annotated[
            str,
            Field(description="Configured named-service namespace, for example 'mem', 'mail', or 'slack'."),
        ],
        object_kind: Annotated[
            str,
            Field(description="Optional provider object kind when the namespace exposes several object shapes."),
        ] = "",
        provider: Annotated[
            str,
            Field(description="Optional provider id when a namespace has more than one provider."),
        ] = "",
    ) -> dict[str, Any]:
        return await _bridge().schema(namespace=namespace, object_kind=object_kind, provider=provider)

    @mcp.tool(
        name="named_services_search",
        title="Search named service",
        description=(
            "Search objects in a configured named-service namespace. Each namespace declares "
            "its OWN search filters and semantics — before searching, call named_services_call "
            "with operation='object.schema' (or 'provider.about') for the namespace to read its "
            "filter contract (ret.extra.schema.search.filters) and how to search it efficiently. "
            "filters_json must be a JSON object string; use '{}' when no filters are needed."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Search named service"),
        structured_output=False,
    )
    async def _named_services_search(
        namespace: Annotated[
            str,
            Field(description="Configured named-service namespace, for example 'mem', 'mail', or 'slack'."),
        ],
        query: Annotated[
            str,
            Field(description="Search query. Leave empty only when the provider supports recent/list reads."),
        ] = "",
        limit: Annotated[
            int,
            Field(ge=1, le=50, description="Maximum items to return; the bridge passes this to the provider."),
        ] = 10,
        filters_json: Annotated[
            str,
            Field(description=(
                "JSON object string with provider-specific filters. The available filters are "
                "namespace-specific: read them from object.schema (ret.extra.schema.search.filters) "
                "or provider.about for the namespace. Use '{}' when no filters are needed."
            )),
        ] = "{}",
        provider: Annotated[
            str,
            Field(description="Optional provider id when a namespace has more than one provider."),
        ] = "",
    ) -> dict[str, Any]:
        return await _bridge().search(
            namespace=namespace,
            query=query,
            limit=limit,
            filters_json=filters_json,
            provider=provider,
        )

    @mcp.tool(
        name="named_services_get",
        title="Read named-service object",
        description=(
            "Read one object from a configured named-service namespace by object ref, "
            "for example mem:<id>."
        ),
        annotations=read_only_annotations(ToolAnnotations, title="Read named-service object"),
        structured_output=False,
    )
    async def _named_services_get(
        namespace: Annotated[
            str,
            Field(description="Configured named-service namespace, for example 'mem' or 'mail'."),
        ],
        object_ref: Annotated[
            str,
            Field(description="Provider object ref, for example 'mem:<id>'."),
        ],
        provider: Annotated[
            str,
            Field(description="Optional provider id when a namespace has more than one provider."),
        ] = "",
    ) -> dict[str, Any]:
        return await _bridge().get(namespace=namespace, object_ref=object_ref, provider=provider)

    if not _REGISTER_EXTENDED_NAMED_SERVICE_TOOLS:
        return mcp

    @mcp.tool(
        name="named_services_upsert",
        title="Write named-service object",
        description=(
            "Create or update one object in a configured named-service namespace. "
            "The namespace must explicitly allow object.upsert and the delegated "
            "credential must include that namespace's write grant."
        ),
        annotations=write_annotations(ToolAnnotations, title="Write named-service object"),
        structured_output=False,
    )
    async def _named_services_upsert(
        namespace: Annotated[
            str,
            Field(description="Configured named-service namespace, for example 'mem', 'task', or 'cnv'."),
        ],
        object_json: Annotated[
            str,
            Field(description="JSON object payload to create or update."),
        ],
        object_ref: Annotated[
            str,
            Field(description="Optional object ref for updates, for example mem:<id>."),
        ] = "",
        object_id: Annotated[
            str,
            Field(description="Optional provider-local object id for updates."),
        ] = "",
        base_revision: Annotated[
            str,
            Field(description="Optional expected revision for optimistic concurrency."),
        ] = "",
        idempotency_key: Annotated[
            str,
            Field(description="Optional client operation id for idempotent creates or updates."),
        ] = "",
        provider: Annotated[
            str,
            Field(description="Optional provider id when a namespace has more than one provider."),
        ] = "",
    ) -> dict[str, Any]:
        return await _bridge().upsert(
            namespace=namespace,
            object_json=object_json,
            object_ref=object_ref,
            object_id=object_id,
            base_revision=base_revision,
            idempotency_key=idempotency_key,
            provider=provider,
        )

    @mcp.tool(
        name="named_services_host_file",
        title="Host named-service file",
        description=(
            "Host or register a runtime file/ref in a configured named-service namespace. "
            "The namespace must explicitly allow object.host_file."
        ),
        annotations=write_annotations(ToolAnnotations, title="Host named-service file"),
        structured_output=False,
    )
    async def _named_services_host_file(
        namespace: Annotated[
            str,
            Field(description="Configured named-service namespace, for example 'task'."),
        ],
        file_ref: Annotated[
            str,
            Field(description="A fi:/ef: artifact ref or provider-accepted file reference."),
        ],
        object_ref: Annotated[
            str,
            Field(description="Optional object/container ref to host the file under."),
        ] = "",
        object_id: Annotated[
            str,
            Field(description="Optional object/container id when object_ref is not known."),
        ] = "",
        filename: Annotated[str, Field(description="Optional filename override.")] = "",
        mime: Annotated[str, Field(description="Optional MIME type override.")] = "",
        description: Annotated[str, Field(description="Optional file description.")] = "",
        payload_json: Annotated[str, Field(description="Optional JSON object with provider-specific options.")] = "{}",
        provider: Annotated[
            str,
            Field(description="Optional provider id when a namespace has more than one provider."),
        ] = "",
    ) -> dict[str, Any]:
        return await _bridge().host_file(
            namespace=namespace,
            file_ref=file_ref,
            object_ref=object_ref,
            object_id=object_id,
            filename=filename,
            mime=mime,
            description=description,
            payload_json=payload_json,
            provider=provider,
        )

    @mcp.tool(
        name="named_services_action",
        title="Run named-service action",
        description=(
            "Run a bounded provider action against one object in a configured "
            "named-service namespace. The namespace must explicitly allow object.action."
        ),
        annotations=action_annotations(ToolAnnotations, title="Run named-service action"),
        structured_output=False,
    )
    async def _named_services_action(
        namespace: Annotated[
            str,
            Field(description="Configured named-service namespace, for example 'mem' or 'mail'."),
        ],
        object_ref: Annotated[
            str,
            Field(description="Object ref to act on, for example mem:<id> or mail:gmail:<account_id>:message:<message_id>."),
        ],
        action: Annotated[
            str,
            Field(description="Provider action, for example preview, open, download, describe, download_attachments, send, or forward."),
        ] = "preview",
        payload_json: Annotated[str, Field(description="Optional JSON object with action parameters.")] = "{}",
        provider: Annotated[
            str,
            Field(description="Optional provider id when a namespace has more than one provider."),
        ] = "",
    ) -> dict[str, Any]:
        return await _bridge().object_action(
            namespace=namespace,
            object_ref=object_ref,
            action=action,
            payload_json=payload_json,
            provider=provider,
        )

    @mcp.tool(
        name="named_services_delete",
        title="Delete named-service object",
        description=(
            "Delete or archive one object in a configured named-service namespace. "
            "The namespace must explicitly allow object.delete."
        ),
        annotations=destructive_annotations(ToolAnnotations, title="Delete named-service object"),
        structured_output=False,
    )
    async def _named_services_delete(
        namespace: Annotated[
            str,
            Field(description="Configured named-service namespace, for example 'mem' or 'task'."),
        ],
        object_ref: Annotated[
            str,
            Field(description="Object ref to delete or archive."),
        ],
        base_revision: Annotated[
            str,
            Field(description="Optional expected revision for optimistic concurrency."),
        ] = "",
        payload_json: Annotated[str, Field(description="Optional JSON object with delete/archive options.")] = "{}",
        provider: Annotated[
            str,
            Field(description="Optional provider id when a namespace has more than one provider."),
        ] = "",
    ) -> dict[str, Any]:
        return await _bridge().delete(
            namespace=namespace,
            object_ref=object_ref,
            base_revision=base_revision,
            payload_json=payload_json,
            provider=provider,
        )

    @mcp.tool(
        name="named_services_call",
        title="Generic named-service call",
        description=(
            "Generic named-service call for configured namespaces. "
            "Allowed operation values: provider.about, provider.capabilities, "
            "object.list, object.search, object.get, object.host_file, object.schema, "
            "object.upsert, object.delete, object.action. JSON fields must be valid JSON strings. "
            "Batch get: object.get with filters_json={\"refs\": [\"<ref1>\", \"<ref2>\", ...]} fetches "
            "several objects in one call and returns them as items (uniform across all namespaces)."
        ),
        annotations=destructive_annotations(ToolAnnotations, title="Generic named-service call"),
        structured_output=False,
    )
    async def _named_services_call(
        operation: Annotated[
            NamedServiceOperation,
            Field(
                description=(
                    "Named-service operation. Allowed values: provider.about, "
                    "provider.capabilities, object.list, object.search, object.get, "
                    "object.host_file, object.schema, object.upsert, object.delete, object.action."
                )
            ),
        ],
        namespace: Annotated[
            str,
            Field(description="Configured named-service namespace, for example 'mem' or 'mail'."),
        ],
        provider: Annotated[
            str,
            Field(description="Optional provider id when a namespace has more than one provider."),
        ] = "",
        object_ref: Annotated[str, Field(description="Optional object ref for a single object.get, for example mem:<id> or mail:gmail:<account_id>:message:<message_id>.")] = "",
        object_id: Annotated[str, Field(description="Optional provider-local object id.")] = "",
        query: Annotated[str, Field(description="Optional search query for object.search.")] = "",
        action: Annotated[str, Field(description="Optional provider action for object.action.")] = "",
        limit: Annotated[int, Field(ge=0, le=50, description="Optional result limit.")] = 0,
        filters_json: Annotated[str, Field(description="JSON object string for filters. For object.get, {\"refs\": [ref, ...]} batch-fetches many objects in one call.")] = "{}",
        include_json: Annotated[str, Field(description="JSON list string for include fields.")] = "[]",
        object_json: Annotated[str, Field(description="JSON object string for object payload.")] = "{}",
        payload_json: Annotated[str, Field(description="JSON object string for provider payload.")] = "{}",
        base_revision: Annotated[str, Field(description="Optional expected revision for optimistic concurrency.")] = "",
        idempotency_key: Annotated[str, Field(description="Optional client operation id for idempotent creates or updates.")] = "",
    ) -> dict[str, Any]:
        return await _bridge().generic_call(
            operation=operation,
            namespace=namespace,
            provider=provider,
            object_ref=object_ref,
            object_id=object_id,
            query=query,
            action=action,
            limit=limit,
            filters_json=filters_json,
            include_json=include_json,
            object_json=object_json,
            payload_json=payload_json,
            base_revision=base_revision,
            idempotency_key=idempotency_key,
        )

    return mcp


__all__ = ["build_named_services_mcp_app"]
