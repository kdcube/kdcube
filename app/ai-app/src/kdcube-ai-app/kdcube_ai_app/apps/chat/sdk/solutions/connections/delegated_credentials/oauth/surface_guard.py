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
import logging
from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry import CredentialEnvelope
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.grants import oauth_tenant_project
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.store import (
    GrantStore,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.metadata import (
    protected_resource_metadata_url,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.hub import (
    delegated_primary_user_id,
    normalize_delegated_identity_scope,
    resolve_delegated_authority_projection,
)


MANAGED_MCP_AUTH_MODE = "managed"
LOGGER = logging.getLogger("kdcube.connection_hub.oauth.mcp_guard")


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
class ManagedMcpToolPolicy:
    grants: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManagedMcpAuthPolicy:
    authority_id: str = ""
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    tool_policies: Mapping[str, ManagedMcpToolPolicy] | None = None
    selected_tool_grants: bool = True


def _parse_tool_policies(value: Any) -> dict[str, ManagedMcpToolPolicy]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, ManagedMcpToolPolicy] = {}
    for raw_name, raw_policy in value.items():
        name = str(raw_name or "").strip()
        if not name:
            continue
        data = raw_policy if isinstance(raw_policy, Mapping) else {}
        out[name] = ManagedMcpToolPolicy(
            grants=_as_list(data.get("grants") or data.get("scopes")),
            roles=_as_list(data.get("roles")),
            permissions=_as_list(data.get("permissions")),
        )
    return out


def managed_mcp_auth_policy(auth: Mapping[str, Any] | None) -> ManagedMcpAuthPolicy | None:
    if mcp_auth_mode(auth) != MANAGED_MCP_AUTH_MODE:
        return None
    data = dict(auth or {})
    return ManagedMcpAuthPolicy(
        authority_id=str(data.get("authority_id") or data.get("authority") or "").strip(),
        roles=_as_list(data.get("roles")),
        permissions=_as_list(data.get("permissions")),
        tool_policies=_parse_tool_policies(data.get("tools") or data.get("tool_policies")),
        selected_tool_grants=bool(data.get("selected_tool_grants", True)),
    )


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _json_response(
    status_code: int,
    error: str,
    description: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "error_description": description},
        headers=dict(headers or {}),
    )


def _split_first_header_value(value: Any) -> str:
    return str(value or "").split(",", 1)[0].strip()


def _forwarded_parts(value: Any) -> dict[str, str]:
    raw = _split_first_header_value(value)
    if not raw:
        return {}
    out: dict[str, str] = {}
    for item in raw.split(";"):
        if "=" not in item:
            continue
        key, raw_value = item.split("=", 1)
        key = key.strip().lower()
        value = raw_value.strip().strip('"')
        if key and value:
            out[key] = value
    return out


def _is_local_or_internal_host(host: str) -> bool:
    name = host.split(":", 1)[0].strip().lower()
    return (
        not name
        or name == "localhost"
        or name.startswith("127.")
        or name == "::1"
        or name.endswith(".local")
        or "." not in name
    )


def _public_proto(proto: str, host: str) -> str:
    value = (proto or "http").strip().lower()
    if value == "http" and not _is_local_or_internal_host(host):
        return "https"
    return value


def _request_public_origin(request: Request) -> str:
    headers = request.headers
    forwarded = _forwarded_parts(headers.get("forwarded"))
    raw_proto = (
        forwarded.get("proto")
        or _split_first_header_value(headers.get("x-forwarded-proto"))
        or str(request.url.scheme or "").strip()
        or "http"
    )
    host = (
        forwarded.get("host")
        or _split_first_header_value(headers.get("x-forwarded-host"))
        or _split_first_header_value(headers.get("host"))
        or str(request.url.netloc or "").strip()
    )
    if not host:
        return str(request.base_url).rstrip("/")
    proto = _public_proto(raw_proto, host)
    return f"{proto}://{host}".rstrip("/")


def _request_public_url_without_query(request: Request) -> str:
    return f"{_request_public_origin(request)}{request.url.path}".rstrip("/")


def _oauth_challenge_headers(request: Request, auth: Mapping[str, Any] | None) -> dict[str, str]:
    auth = auth if isinstance(auth, Mapping) else {}
    configured_metadata_url = str(auth.get("resource_metadata_url") or "").strip()
    if configured_metadata_url:
        return {"WWW-Authenticate": f'Bearer resource_metadata="{configured_metadata_url}"'}

    path_params = getattr(request, "path_params", {}) or {}
    tenant = str(path_params.get("tenant") or "").strip()
    project = str(path_params.get("project") or "").strip()
    connection_hub_bundle_id = str(
        auth.get("connection_hub_bundle_id")
        or auth.get("connectionHubBundleId")
        or "connection-hub@1-0"
    ).strip()
    if not tenant or not project or not connection_hub_bundle_id:
        return {}

    issuer = (
        f"{_request_public_origin(request)}"
        f"/api/integrations/bundles/{tenant}/{project}/{connection_hub_bundle_id}/public/oauth"
    )
    resource = _request_public_url_without_query(request)
    metadata_url = protected_resource_metadata_url(issuer, resource=resource)
    return {"WWW-Authenticate": f'Bearer resource_metadata="{metadata_url}"'}


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


def _normalize_resource(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw.split("?", 1)[0].rstrip("/")


def _request_resource(request: Request) -> str:
    return _normalize_resource(_request_public_url_without_query(request))


def _connection_hub_tool_policies(request: Request) -> dict[str, ManagedMcpToolPolicy]:
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
        oauth_delegated_config,
    )

    cfg = oauth_delegated_config(request)
    tools = cfg.resource_tool_catalog(_request_resource(request))
    out: dict[str, ManagedMcpToolPolicy] = {}
    for tool in tools:
        name = str(getattr(tool, "name", "") or "").strip()
        if not name:
            continue
        out[name] = ManagedMcpToolPolicy(
            grants=_as_list(getattr(tool, "grants", ())),
        )
    return out


def _credential_resource(envelope: CredentialEnvelope) -> str:
    attrs = envelope.attrs or {}
    return _normalize_resource(attrs.get("resource"))


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


async def _authenticate_delegated_client_access_token(token: str) -> dict[str, Any] | None:
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


def _grant_record_credential(grant_record: Mapping[str, Any] | None) -> CredentialEnvelope:
    if not isinstance(grant_record, Mapping):
        return CredentialEnvelope()
    credential = grant_record.get("credential")
    if isinstance(credential, Mapping):
        return CredentialEnvelope.coerce(credential)
    return CredentialEnvelope()


def delegated_mcp_runtime_projection(request: Request) -> dict[str, Any]:
    """Return request-local runtime identity facts for an accepted delegated token.

    The managed MCP guard authenticates a delegated-client bearer after the proc
    bridge has already built an anonymous MCP request session. This projection
    is the single handoff that lets the proc bridge upgrade the request-local
    session/comm-context before invoking the bundle MCP app and any nested
    bundle/named-service calls.
    """

    delegated = getattr(getattr(request, "state", None), "delegated_credential", None)
    if not isinstance(delegated, Mapping):
        return {}
    credential = delegated.get("credential")
    if not isinstance(credential, Mapping):
        return {}
    envelope = CredentialEnvelope.coerce(credential)
    if not envelope.credential_kind and not envelope.subject:
        return {}

    grant_record = delegated.get("grant_record")
    grant_record = grant_record if isinstance(grant_record, Mapping) else {}
    grantor_authority = grant_record.get("grantor_authority")
    grantor_authority = grantor_authority if isinstance(grantor_authority, Mapping) else {}
    projection = resolve_delegated_authority_projection(
        credential=envelope,
        grantor_authority=grantor_authority,
    )
    if not projection.get("ok"):
        return {}

    attrs = envelope.attrs or {}
    user = delegated.get("user")
    user = user if isinstance(user, Mapping) else {}
    grants = sorted(_credential_scopes(envelope))
    tools = _as_list(grant_record.get("tools")) or _as_list(attrs.get("tools"))
    grantor_user_id = str(projection.get("grantor_user_id") or delegated_primary_user_id(envelope)).strip()
    delegate_identity = str(projection.get("delegate_identity") or envelope.subject or "").strip()
    economics = projection.get("economics")
    economics = dict(economics) if isinstance(economics, Mapping) else {}

    identity_authority: dict[str, Any] = dict(economics)
    identity_authority.update(
        {
            "schema": "connection_hub.delegated_mcp_runtime_authority.v1",
            "authority_id": envelope.issuer_authority_id or "delegated_client",
            "issuer_authority_id": envelope.issuer_authority_id,
            "issuer_authenticator_id": envelope.issuer_authenticator_id,
            "credential_kind": envelope.credential_kind,
            "credential_id": envelope.credential_id,
            "delegate_identity": delegate_identity,
            "actor_identity": delegate_identity,
            "actor_user_id": delegate_identity,
            "grantor_user_id": grantor_user_id,
            "platform_user_id": grantor_user_id,
            "economics_user_id": str(economics.get("user_id") or grantor_user_id).strip(),
            "economics_projection": "platform_user",
            "grants": grants,
            "scopes": grants,
            "tools": list(tools),
            "resource": str(attrs.get("resource") or "").strip(),
            "identity_scope": normalize_delegated_identity_scope(attrs.get("identity_scope")),
            "delegation": dict(projection.get("delegation") or {}),
            "provenance": dict(projection.get("provenance") or economics.get("provenance") or {}),
        }
    )
    identity_authority = {
        key: value for key, value in identity_authority.items()
        if value not in ("", None, [], {})
    }

    roles = _as_list(user.get("roles"))
    permissions = _as_list(user.get("permissions")) or tuple(grants)
    return {
        "schema": "connection_hub.delegated_mcp_runtime_projection.v1",
        "user_id": grantor_user_id,
        "user_type": "external",
        "username": delegate_identity or str(user.get("sub") or "").strip() or None,
        "roles": list(roles),
        "permissions": list(permissions),
        "identity_authority": identity_authority,
        "delegate_identity": delegate_identity,
        "grantor_user_id": grantor_user_id,
        "identity_scope": identity_authority.get("identity_scope") or "",
        "resource": identity_authority.get("resource") or "",
        "grants": grants,
        "tools": list(tools),
    }


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
        LOGGER.info(
            "[connection-hub.oauth.mcp_guard] denied reason=missing_bearer resource=%s",
            _request_resource(request),
        )
        return _json_response(
            401,
            "unauthorized",
            "Bearer access token is required",
            headers=_oauth_challenge_headers(request, auth),
        )

    user = await _authenticate_delegated_client_access_token(token)
    if user is None:
        LOGGER.info(
            "[connection-hub.oauth.mcp_guard] denied reason=invalid_bearer resource=%s",
            _request_resource(request),
        )
        return _json_response(
            401,
            "unauthorized",
            "Bearer access token is invalid",
            headers=_oauth_challenge_headers(request, auth),
        )

    roles = set(user.get("roles") or [])
    permissions = set(user.get("permissions") or [])

    if policy.roles and not roles.intersection(policy.roles):
        LOGGER.info(
            "[connection-hub.oauth.mcp_guard] denied reason=missing_role required=%s roles=%s resource=%s",
            list(policy.roles),
            sorted(roles),
            _request_resource(request),
        )
        return _json_response(403, "forbidden", "required role is missing")
    if policy.permissions and not permissions.issuperset(policy.permissions):
        LOGGER.info(
            "[connection-hub.oauth.mcp_guard] denied reason=missing_permission required=%s permissions=%s resource=%s",
            list(policy.permissions),
            sorted(permissions),
            _request_resource(request),
        )
        return _json_response(403, "forbidden", "required permission is missing")

    grant_store = await _default_grant_store(request)
    grant_record = await grant_store.get_access_grant_record(token)
    envelope = _grant_record_credential(grant_record)
    try:
        request.state.delegated_credential = {
            "user": dict(user or {}),
            "credential": envelope.to_dict(),
            "grant_record": dict(grant_record or {}),
        }
    except Exception:
        pass

    if policy.authority_id:
        if envelope.issuer_authority_id != policy.authority_id:
            LOGGER.info(
                "[connection-hub.oauth.mcp_guard] denied reason=authority_mismatch required=%s got=%s resource=%s",
                policy.authority_id,
                envelope.issuer_authority_id,
                _request_resource(request),
            )
            return _json_response(403, "forbidden", "delegated credential authority mismatch")

    credential_resource = _credential_resource(envelope)
    request_resource = _request_resource(request)
    if not credential_resource:
        LOGGER.info(
            "[connection-hub.oauth.mcp_guard] denied reason=credential_resource_missing request_resource=%s",
            request_resource,
        )
        return _json_response(403, "forbidden", "delegated credential resource is missing")
    if credential_resource != request_resource:
        LOGGER.info(
            "[connection-hub.oauth.mcp_guard] denied reason=resource_mismatch credential_resource=%s request_resource=%s",
            credential_resource,
            request_resource,
        )
        return _json_response(403, "forbidden", "delegated credential resource mismatch")

    tool_calls = extract_mcp_tool_calls(body)
    if not tool_calls:
        runtime = delegated_mcp_runtime_projection(request)
        LOGGER.info(
            "[connection-hub.oauth.mcp_guard] accepted resource=%s subject=%s grantor=%s delegate=%s authority=%s scopes=%s tools=%s identity_scope=%s tool_calls=0",
            request_resource,
            user.get("sub") or "",
            runtime.get("grantor_user_id") or "",
            runtime.get("delegate_identity") or "",
            envelope.issuer_authority_id,
            sorted(_credential_scopes(envelope)),
            list(_as_list(grant_record.get("tools"))) if isinstance(grant_record, Mapping) else [],
            runtime.get("identity_scope") or "",
        )
        return None

    granted_tools = None
    if isinstance(grant_record, Mapping):
        granted_tools = set(_as_list(grant_record.get("tools")))
    tool_policies = _connection_hub_tool_policies(request)
    if not tool_policies:
        tool_policies = dict(policy.tool_policies or {})
    available_grants = _credential_scopes(envelope)

    for rpc_id, tool_name in tool_calls:
        tool_policy = tool_policies.get(tool_name)
        if tool_policies and tool_policy is None:
            return _rpc_tool_error(rpc_id, f"tool not allowed by endpoint policy: {tool_name}")

        if tool_policy is not None:
            if tool_policy.roles and not roles.intersection(tool_policy.roles):
                return _rpc_tool_error(rpc_id, f"required role is missing for tool: {tool_name}")
            if tool_policy.permissions and not permissions.issuperset(tool_policy.permissions):
                return _rpc_tool_error(rpc_id, f"required permission is missing for tool: {tool_name}")
            if tool_policy.grants and not available_grants.issuperset(tool_policy.grants):
                return _rpc_tool_error(rpc_id, f"required delegated grant is missing for tool: {tool_name}")

        if policy.selected_tool_grants:
            if granted_tools is None or tool_name not in granted_tools:
                return _rpc_tool_error(
                    rpc_id,
                    f"tool not consented for this connection: {tool_name}",
                )

    runtime = delegated_mcp_runtime_projection(request)
    LOGGER.info(
        "[connection-hub.oauth.mcp_guard] accepted resource=%s subject=%s grantor=%s delegate=%s authority=%s scopes=%s tools=%s identity_scope=%s tool_calls=%s",
        request_resource,
        user.get("sub") or "",
        runtime.get("grantor_user_id") or "",
        runtime.get("delegate_identity") or "",
        envelope.issuer_authority_id,
        sorted(available_grants),
        sorted(granted_tools or []),
        runtime.get("identity_scope") or "",
        [tool for _, tool in tool_calls],
    )
    return None


__all__ = [
    "MANAGED_MCP_AUTH_MODE",
    "ManagedMcpAuthPolicy",
    "ManagedMcpToolPolicy",
    "authorize_delegated_mcp_request",
    "delegated_mcp_runtime_projection",
    "extract_mcp_tool_calls",
    "managed_mcp_auth_policy",
    "mcp_auth_mode",
]
