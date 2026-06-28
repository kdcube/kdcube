# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Managed auth guard for proc-served bundle MCP endpoints.

This module is intentionally owned by the Connection Hub delegated-credential
SDK, not by individual bundles. Bundle MCP apps may still perform
domain-specific authorization after dispatch, but platform-managed credential,
grant, and selected-tool checks happen at the proc bridge boundary.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry import (
    CredentialEnvelope,
    OAUTH_MCP_AUTHORITY_ID,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.grants import (
    ADMIN_ROLES,
    FEEDBACK_READER_ROLE,
    can_call_tool,
    oauth_tenant_project,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth_mcp.store import (
    GrantStore,
)


MANAGED_MCP_AUTH_MODE = "managed"


def _as_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.replace(",", " ").split() if item.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def mcp_auth_mode(auth: Mapping[str, Any] | None) -> str:
    if not isinstance(auth, Mapping):
        return ""
    return str(auth.get("mode") or "").strip().lower()


@dataclass(frozen=True)
class ManagedMcpAuthPolicy:
    authority_id: str = ""
    grants: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    selected_tool_grants: bool = True


def managed_mcp_auth_policy(auth: Mapping[str, Any] | None) -> ManagedMcpAuthPolicy | None:
    if mcp_auth_mode(auth) != MANAGED_MCP_AUTH_MODE:
        return None
    data = dict(auth or {})
    return ManagedMcpAuthPolicy(
        authority_id=str(data.get("authority_id") or data.get("authority") or "").strip(),
        grants=_as_list(data.get("grants") or data.get("scopes")),
        roles=_as_list(data.get("roles")),
        permissions=_as_list(data.get("permissions")),
        tools=_as_list(data.get("tools") or data.get("allowed_tools")),
        selected_tool_grants=bool(data.get("selected_tool_grants", True)),
    )


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _json_response(status_code: int, error: str, description: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "error_description": description},
    )


def _rpc_tool_error(rpc_id: Any, message: str) -> JSONResponse:
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": message}],
            },
        }
    )


def _decode_json_body(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def extract_mcp_tool_calls(body: bytes) -> list[tuple[Any, str]]:
    """Return JSON-RPC ids and tool names for `tools/call` messages."""

    message = _decode_json_body(body)
    rows = message if isinstance(message, list) else [message]
    out: list[tuple[Any, str]] = []
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        if item.get("method") != "tools/call":
            continue
        params = item.get("params")
        if not isinstance(params, Mapping):
            continue
        name = str(params.get("name") or "").strip()
        if name:
            out.append((item.get("id"), name))
    return out


def _credential_scopes(envelope: CredentialEnvelope) -> set[str]:
    attrs = envelope.attrs or {}
    out: set[str] = set()
    out.update(_as_list(attrs.get("scopes")))
    out.update(_as_list(attrs.get("scope")))
    out.update(_as_list(attrs.get("grants")))
    return out


async def _default_grant_store(request: Request) -> GrantStore:
    override = getattr(request.app.state, "oauth_grant_store", None)
    if override is not None:
        return override

    redis = getattr(request.app.state, "redis_async", None)
    if redis is None:
        from kdcube_ai_app.apps.chat.sdk.config import get_settings
        from kdcube_ai_app.infra.redis.client import get_async_redis_client

        redis = get_async_redis_client(get_settings().REDIS_URL)
    tenant, project = oauth_tenant_project(request)
    return GrantStore(redis, tenant, project)


async def _authenticate_oauth_mcp_access_token(token: str) -> dict[str, Any] | None:
    from kdcube_ai_app.auth.AuthManager import AuthenticationError
    from kdcube_ai_app.auth.bundle import BundleSessionAuthManager, get_bundle_session_authority

    tenant, project = oauth_tenant_project()
    manager = BundleSessionAuthManager(
        authority=get_bundle_session_authority(tenant=tenant, project=project)
    )
    try:
        user = await manager.authenticate(token)
    except AuthenticationError:
        return None
    except Exception:
        return None
    return {
        "sub": getattr(user, "sub", None) or getattr(user, "username", None),
        "roles": list(getattr(user, "roles", None) or []),
        "permissions": list(getattr(user, "permissions", None) or []),
    }


def _grant_record_authority(grant_record: Mapping[str, Any] | None) -> CredentialEnvelope:
    if not isinstance(grant_record, Mapping):
        return CredentialEnvelope()
    authority = grant_record.get("authority")
    if isinstance(authority, Mapping):
        return CredentialEnvelope.coerce(authority)
    return CredentialEnvelope()


async def authorize_delegated_mcp_request(
    *,
    request: Request,
    body: bytes,
    auth: Mapping[str, Any] | None,
) -> Response | None:
    """Return a denial response or None when the request may enter the MCP app."""

    policy = managed_mcp_auth_policy(auth)
    if policy is None:
        return None

    token = _extract_bearer(request)
    if not token:
        return _json_response(401, "unauthorized", "Bearer access token is required")

    user = await _authenticate_oauth_mcp_access_token(token)
    if user is None:
        return _json_response(401, "unauthorized", "Bearer access token is invalid")

    roles = set(user.get("roles") or [])
    permissions = set(user.get("permissions") or [])
    is_admin = bool(roles & ADMIN_ROLES)

    if policy.roles and not roles.intersection(policy.roles):
        return _json_response(403, "forbidden", "required role is missing")
    if policy.permissions and not permissions.issuperset(policy.permissions):
        return _json_response(403, "forbidden", "required permission is missing")

    grant_store = await _default_grant_store(request)
    grant_record = await grant_store.get_access_grant_record(token)
    envelope = _grant_record_authority(grant_record)

    if policy.authority_id:
        if envelope.issuer_authority_id != policy.authority_id:
            return _json_response(403, "forbidden", "delegated credential authority mismatch")

    if policy.grants:
        available_grants = _credential_scopes(envelope)
        if not available_grants.issuperset(policy.grants):
            return _json_response(403, "forbidden", "required delegated grant is missing")

    tool_calls = extract_mcp_tool_calls(body)
    if not tool_calls:
        return None

    granted_tools = None
    if isinstance(grant_record, Mapping):
        granted_tools = set(_as_list(grant_record.get("tools")))
    policy_tools = set(policy.tools)

    for rpc_id, tool_name in tool_calls:
        if policy_tools and tool_name not in policy_tools:
            return _rpc_tool_error(rpc_id, f"tool not allowed by endpoint policy: {tool_name}")

        if policy.authority_id in {"", OAUTH_MCP_AUTHORITY_ID}:
            if not can_call_tool(roles, tool_name):
                return _rpc_tool_error(rpc_id, f"not authorized for tool: {tool_name}")
        elif not is_admin and FEEDBACK_READER_ROLE in roles and not can_call_tool(roles, tool_name):
            return _rpc_tool_error(rpc_id, f"not authorized for tool: {tool_name}")

        if policy.selected_tool_grants and not is_admin:
            if granted_tools is None or tool_name not in granted_tools:
                return _rpc_tool_error(
                    rpc_id,
                    f"tool not consented for this connection: {tool_name}",
                )

    return None


__all__ = [
    "MANAGED_MCP_AUTH_MODE",
    "ManagedMcpAuthPolicy",
    "authorize_delegated_mcp_request",
    "extract_mcp_tool_calls",
    "managed_mcp_auth_policy",
    "mcp_auth_mode",
]
