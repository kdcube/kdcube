# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
Minimal MCP server at /mcp (JSON-RPC 2.0 over HTTP POST).

OAuth-protected: an unauthenticated request gets the RFC 9728 challenge; a valid
``kst1`` bearer must resolve to the read-only ``feedback-reader`` role (or an
admin) to reach any method. ``tools/call`` additionally enforces per-tool
permission. The transport is deliberately hand-rolled (no SDK dependency) so it
is unit-testable in CI; full streamable-http compatibility with Claude Code's
client is validated live during Phase 2 and the official SDK can replace this
layer there if needed.
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from kdcube_ai_app.apps.chat.ingress.oauth_mcp.deps import (
    ADMIN_ROLES,
    extract_bearer,
    get_authenticate,
    get_grant_store,
)
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.discovery import resolve_issuer, unauthorized_challenge
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.grants import FEEDBACK_READER_ROLE, can_call_tool

router = APIRouter()

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "kdcube", "version": "1"}

ToolRunner = Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[Any]]

CONVERSATIONS_EXPORT_TOOL = {
    "name": "conversations_export",
    "description": (
        "Export normalized conversation transcripts (read-only) across all "
        "tenants/projects/bundles. Supports an incremental `since` watermark."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "since": {"type": "string", "description": "ISO timestamp; only conversations at/after this are returned"},
            "tenant": {"type": "string", "description": "Optional: narrow to one tenant"},
            "project": {"type": "string", "description": "Optional: narrow to one project"},
        },
        "additionalProperties": False,
    },
}

TOOL_SCHEMAS = [CONVERSATIONS_EXPORT_TOOL]


def is_authorized_for_mcp(roles) -> bool:
    roles = set(roles or [])
    return bool(roles & ADMIN_ROLES) or FEEDBACK_READER_ROLE in roles


def get_mcp_tools(request: Request) -> Dict[str, ToolRunner]:
    tools = getattr(request.app.state, "mcp_tools", None)
    if tools is not None:
        return tools
    # Production: build the conversations_browser-backed tool set.
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.export_adapter import build_default_tools

    return build_default_tools(request)


def _rpc_error(rpc_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _rpc_result(rpc_id, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


async def handle_rpc(
    message: dict, *, user: dict, tools: Dict[str, ToolRunner], granted_tools=None
) -> Optional[dict]:
    rpc_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if method == "initialize":
        return _rpc_result(rpc_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None  # notification: no response

    if method == "ping":
        return _rpc_result(rpc_id, {})

    if method == "tools/list":
        return _rpc_result(rpc_id, {"tools": TOOL_SCHEMAS})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in tools:
            return _rpc_result(rpc_id, {
                "isError": True,
                "content": [{"type": "text", "text": f"unknown tool: {name}"}],
            })
        if not can_call_tool(user.get("roles"), name):
            return _rpc_result(rpc_id, {
                "isError": True,
                "content": [{"type": "text", "text": f"not authorized for tool: {name}"}],
            })
        # Consent enforcement: a non-admin (feedback-reader) grant may only call the
        # tools the admin selected on the consent screen. Admins bypass (no grant
        # record). Fail closed: a feedback-reader token with no grant grants nothing.
        if not (set(user.get("roles") or []) & ADMIN_ROLES):
            if granted_tools is None or name not in granted_tools:
                return _rpc_result(rpc_id, {
                    "isError": True,
                    "content": [{"type": "text", "text": f"tool not consented for this connection: {name}"}],
                })
        try:
            data = await tools[name](arguments, user)
        except Exception as exc:  # surface tool failures as MCP tool errors, not 500s
            return _rpc_result(rpc_id, {
                "isError": True,
                "content": [{"type": "text", "text": f"tool error: {exc}"}],
            })
        return _rpc_result(rpc_id, {
            "isError": False,
            "content": [{"type": "text", "text": json.dumps(data, default=str)}],
        })

    return _rpc_error(rpc_id, -32601, f"method not found: {method}")


@router.get("/mcp", include_in_schema=False)
async def mcp_get(request: Request) -> Response:
    # RFC 9728 handshake entry point. An unauthenticated GET advertises the
    # protected-resource metadata; an authenticated GET (SSE stream) is not yet
    # implemented by this hand-rolled transport.
    token = extract_bearer(request)
    if not token or not await get_authenticate(request)(token):
        return unauthorized_challenge(resolve_issuer(request))
    return JSONResponse(
        status_code=405,
        content={"error": "method_not_allowed", "error_description": "POST JSON-RPC to /mcp"},
    )


@router.post("/mcp", include_in_schema=False)
async def mcp_endpoint(request: Request) -> Response:
    token = extract_bearer(request)
    if not token:
        return unauthorized_challenge(resolve_issuer(request))
    user = await get_authenticate(request)(token)
    if not user:
        return unauthorized_challenge(resolve_issuer(request))
    if not is_authorized_for_mcp(user.get("roles")):
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "error_description": "feedback-reader role required"},
        )

    try:
        message = await request.json()
    except Exception:
        return JSONResponse(_rpc_error(None, -32700, "parse error"))

    # The consented tool allowlist and authority envelope bound to this access
    # token at issue time. Tool enforcement remains the hard gate here; the
    # envelope is propagated so downstream code can reason in the same
    # Connection Hub authority vocabulary as other delegated connections.
    grant_record = await get_grant_store(request).get_access_grant_record(token)
    granted_tools = list((grant_record or {}).get("tools") or []) if grant_record is not None else None
    if isinstance((grant_record or {}).get("authority"), dict):
        user = dict(user)
        user["credential"] = grant_record["authority"]
        user["identity_authority"] = grant_record["authority"]
    response = await handle_rpc(
        message, user=user, tools=get_mcp_tools(request), granted_tools=granted_tools
    )
    if response is None:
        return Response(status_code=202)
    return JSONResponse(response)
