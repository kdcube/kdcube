# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Managed auth guards for proc-served bundle endpoints.

This module is intentionally owned by the Connection Hub delegated-credential
SDK, not by individual bundles. Bundle MCP/REST apps may still perform
domain-specific authorization after dispatch, but platform-managed credential,
grant, and selected-operation checks happen at the proc bridge boundary.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Any, Iterable, Mapping

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry import CredentialEnvelope
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection import (
    authority_has_platform_privilege,
)
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
REST_LOGGER = logging.getLogger("kdcube.connection_hub.oauth.rest_guard")


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


def rest_auth_mode(auth: Mapping[str, Any] | None) -> str:
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


@dataclass(frozen=True)
class ManagedRestOperationPolicy:
    grants: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ManagedRestAuthPolicy:
    authority_id: str = ""
    grants: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    operation_policies: Mapping[str, ManagedRestOperationPolicy] | None = None
    selected_operation_grants: bool = False


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


def _parse_rest_operation_policies(value: Any) -> dict[str, ManagedRestOperationPolicy]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, ManagedRestOperationPolicy] = {}
    for raw_name, raw_policy in value.items():
        name = str(raw_name or "").strip()
        if not name:
            continue
        data = raw_policy if isinstance(raw_policy, Mapping) else {}
        out[name] = ManagedRestOperationPolicy(
            grants=_as_list(data.get("grants") or data.get("scopes") or data.get("required_grants")),
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


def managed_rest_auth_policy(auth: Mapping[str, Any] | None) -> ManagedRestAuthPolicy | None:
    if rest_auth_mode(auth) != MANAGED_MCP_AUTH_MODE:
        return None
    data = dict(auth or {})
    operation_policies = _parse_rest_operation_policies(
        data.get("operations")
        or data.get("operation_policies")
        or data.get("tools")
        or data.get("tool_policies")
    )
    selected_operation_grants = data.get("selected_operation_grants")
    if selected_operation_grants is None:
        selected_operation_grants = data.get("selected_tool_grants")
    if selected_operation_grants is None:
        selected_operation_grants = bool(operation_policies)
    return ManagedRestAuthPolicy(
        authority_id=str(data.get("authority_id") or data.get("authority") or "").strip(),
        grants=_as_list(data.get("grants") or data.get("scopes") or data.get("required_grants")),
        roles=_as_list(data.get("roles")),
        permissions=_as_list(data.get("permissions")),
        operation_policies=operation_policies,
        selected_operation_grants=bool(selected_operation_grants),
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


def delegated_request_resource(request: Request) -> str:
    """Return the public delegated-credential resource URL for a request."""
    return _request_resource(request)


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


def _connection_hub_rest_operation_policies(request: Request) -> dict[str, ManagedRestOperationPolicy]:
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
        oauth_delegated_config,
    )

    cfg = oauth_delegated_config(request)
    operations = cfg.resource_operation_catalog(_request_resource(request))
    out: dict[str, ManagedRestOperationPolicy] = {}
    for operation in operations:
        name = str(getattr(operation, "name", "") or "").strip()
        if not name:
            continue
        out[name] = ManagedRestOperationPolicy(
            grants=_as_list(getattr(operation, "grants", ())),
        )
    return out


def _credential_resources(envelope: CredentialEnvelope) -> tuple[str, ...]:
    attrs = envelope.attrs or {}
    resource_grants = attrs.get("resource_grants")
    resource_keys = resource_grants.keys() if isinstance(resource_grants, Mapping) else []
    out: list[str] = []
    seen: set[str] = set()
    for item in resource_keys:
        normalized = _normalize_resource(item)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return tuple(out)


def _credential_grants_for_resource(envelope: CredentialEnvelope, request_resource: str) -> set[str]:
    attrs = envelope.attrs or {}
    resource_grants = attrs.get("resource_grants")
    if not isinstance(resource_grants, Mapping):
        return set()
    out: set[str] = set()
    for resource, grants in resource_grants.items():
        if _resource_matches(str(resource or ""), request_resource):
            out.update(_as_list(grants))
    return out


def _resource_matches(credential_resource: str, request_resource: str) -> bool:
    credential_resource = _normalize_resource(credential_resource)
    request_resource = _normalize_resource(request_resource)
    if not credential_resource or not request_resource:
        return False
    return credential_resource == request_resource or fnmatch(request_resource, credential_resource)


def _any_resource_matches(credential_resources: Iterable[str], request_resource: str) -> bool:
    return any(_resource_matches(resource, request_resource) for resource in credential_resources)


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


def _delegated_runtime_projection(request: Request, *, surface: str) -> dict[str, Any]:
    """Return request-local runtime identity facts for an accepted delegated token.

    Managed surface guards authenticate a delegated-client bearer after the
    proc bridge has built a request session. This projection is the handoff that
    lets the proc bridge upgrade the request-local session/comm-context before
    invoking the app surface and any nested app/named-service calls.
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
    resource_grants = dict(attrs.get("resource_grants") or {})
    user = delegated.get("user")
    user = user if isinstance(user, Mapping) else {}
    request_resource = _request_resource(request)
    grants = sorted(_credential_grants_for_resource(envelope, request_resource))
    operations = _as_list(grant_record.get("operations")) or _as_list(attrs.get("operations"))
    grantor_user_id = str(projection.get("grantor_user_id") or delegated_primary_user_id(envelope)).strip()
    delegate_identity = str(projection.get("delegate_identity") or envelope.subject or "").strip()
    economics = projection.get("economics")
    economics = dict(economics) if isinstance(economics, Mapping) else {}

    identity_authority: dict[str, Any] = dict(economics)
    identity_authority.update(
        {
            "schema": f"connection_hub.delegated_{surface}_runtime_authority.v1",
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
            "operations": list(operations),
            "resource_grants": resource_grants,
            "identity_scope": normalize_delegated_identity_scope(attrs.get("identity_scope")),
            "delegation": dict(projection.get("delegation") or {}),
            "provenance": dict(projection.get("provenance") or economics.get("provenance") or {}),
        }
    )
    identity_authority = {
        key: value for key, value in identity_authority.items()
        if value not in ("", None, [], {})
    }

    roles = (
        _as_list(identity_authority.get("roles"))
        or _as_list(grantor_authority.get("grantor_roles"))
        or _as_list(user.get("roles"))
    )
    permissions = (
        _as_list(identity_authority.get("permissions"))
        or _as_list(grantor_authority.get("grantor_permissions"))
        or _as_list(user.get("permissions"))
        or tuple(grants)
    )
    return {
        "schema": f"connection_hub.delegated_{surface}_runtime_projection.v1",
        "user_id": grantor_user_id,
        "user_type": "external",
        "username": delegate_identity or str(user.get("sub") or "").strip() or None,
        "roles": list(roles),
        "permissions": list(permissions),
        "identity_authority": identity_authority,
        "delegate_identity": delegate_identity,
        "grantor_user_id": grantor_user_id,
        "identity_scope": identity_authority.get("identity_scope") or "",
        "grants": grants,
        "operations": list(operations),
    }


def delegated_mcp_runtime_projection(request: Request) -> dict[str, Any]:
    return _delegated_runtime_projection(request, surface="mcp")


def delegated_rest_runtime_projection(request: Request) -> dict[str, Any]:
    return _delegated_runtime_projection(request, surface="rest")


async def delegated_platform_admin_runtime_projection(
    request: Request,
    *,
    authority_id: str = "",
) -> dict[str, Any]:
    """Project an all-resource admin delegated token into a platform session.

    This is the generic platform/API auth path used before a route-specific
    managed REST guard exists. It accepts only credentials whose resource matches
    the current request and whose grantor authority carries a platform admin
    role. Non-admin delegated credentials remain resource/operation bounded and
    are handled by managed REST/MCP guards.
    """

    token = _extract_bearer(request)
    if not token:
        return {}

    user = await _authenticate_delegated_client_access_token(token)
    if user is None:
        return {}

    grant_store = await _default_grant_store(request)
    grant_record = await grant_store.get_access_grant_record(token)
    envelope = _grant_record_credential(grant_record)
    if authority_id and envelope.issuer_authority_id != authority_id:
        return {}

    credential_resources = _credential_resources(envelope)
    request_resource = _request_resource(request)
    if not _any_resource_matches(credential_resources, request_resource):
        return {}

    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
        oauth_delegated_config,
    )

    resource_cfg = oauth_delegated_config(request).resource_config(request_resource)
    required_grants = set(_as_list(getattr(resource_cfg, "grants", ())))
    credential_grants = _credential_grants_for_resource(envelope, request_resource)
    if required_grants and not required_grants.issubset(credential_grants):
        return {}

    try:
        request.state.delegated_credential = {
            "user": dict(user or {}),
            "credential": envelope.to_dict(),
            "grant_record": dict(grant_record or {}),
        }
    except Exception:
        pass

    runtime = delegated_rest_runtime_projection(request)
    if not authority_has_platform_privilege(runtime.get("roles") or ()):
        return {}

    REST_LOGGER.info(
        "[connection-hub.oauth.rest_guard] accepted all-resource admin token resource=%s subject=%s grantor=%s delegate=%s authority=%s scopes=%s",
        request_resource,
        user.get("sub") or "",
        runtime.get("grantor_user_id") or "",
        runtime.get("delegate_identity") or "",
        envelope.issuer_authority_id,
        sorted(credential_grants),
    )
    return runtime


async def _authorize_delegated_managed_request(
    *,
    request: Request,
    auth: Mapping[str, Any] | None,
    authority_id: str,
    roles: tuple[str, ...],
    permissions: tuple[str, ...],
    logger: logging.Logger,
    surface_label: str,
) -> tuple[JSONResponse | None, dict[str, Any], CredentialEnvelope, Mapping[str, Any]]:
    token = _extract_bearer(request)
    if not token:
        logger.info(
            "[connection-hub.oauth.%s_guard] denied reason=missing_bearer resource=%s",
            surface_label,
            _request_resource(request),
        )
        return (
            _json_response(
                401,
                "unauthorized",
                "Bearer access token is required",
                headers=_oauth_challenge_headers(request, auth),
            ),
            {},
            CredentialEnvelope(),
            {},
        )

    user = await _authenticate_delegated_client_access_token(token)
    if user is None:
        logger.info(
            "[connection-hub.oauth.%s_guard] denied reason=invalid_bearer resource=%s",
            surface_label,
            _request_resource(request),
        )
        return (
            _json_response(
                401,
                "unauthorized",
                "Bearer access token is invalid",
                headers=_oauth_challenge_headers(request, auth),
            ),
            {},
            CredentialEnvelope(),
            {},
        )

    user_roles = set(user.get("roles") or [])
    user_permissions = set(user.get("permissions") or [])

    if roles and not user_roles.intersection(roles):
        logger.info(
            "[connection-hub.oauth.%s_guard] denied reason=missing_role required=%s roles=%s resource=%s",
            surface_label,
            list(roles),
            sorted(user_roles),
            _request_resource(request),
        )
        return _json_response(403, "forbidden", "required role is missing"), user, CredentialEnvelope(), {}
    if permissions and not user_permissions.issuperset(permissions):
        logger.info(
            "[connection-hub.oauth.%s_guard] denied reason=missing_permission required=%s permissions=%s resource=%s",
            surface_label,
            list(permissions),
            sorted(user_permissions),
            _request_resource(request),
        )
        return _json_response(403, "forbidden", "required permission is missing"), user, CredentialEnvelope(), {}

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

    if authority_id and envelope.issuer_authority_id != authority_id:
        logger.info(
            "[connection-hub.oauth.%s_guard] denied reason=authority_mismatch required=%s got=%s resource=%s",
            surface_label,
            authority_id,
            envelope.issuer_authority_id,
            _request_resource(request),
        )
        return _json_response(403, "forbidden", "delegated credential authority mismatch"), user, envelope, grant_record or {}

    credential_resources = _credential_resources(envelope)
    request_resource = _request_resource(request)
    if not credential_resources:
        logger.info(
            "[connection-hub.oauth.%s_guard] denied reason=credential_resource_missing request_resource=%s",
            surface_label,
            request_resource,
        )
        return _json_response(403, "forbidden", "delegated credential resource is missing"), user, envelope, grant_record or {}
    if not _any_resource_matches(credential_resources, request_resource):
        logger.info(
            "[connection-hub.oauth.%s_guard] denied reason=resource_mismatch credential_resources=%s request_resource=%s",
            surface_label,
            list(credential_resources),
            request_resource,
        )
        return _json_response(403, "forbidden", "delegated credential resource mismatch"), user, envelope, grant_record or {}

    return None, user, envelope, grant_record or {}


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

    credential_resources = _credential_resources(envelope)
    request_resource = _request_resource(request)
    if not credential_resources:
        LOGGER.info(
            "[connection-hub.oauth.mcp_guard] denied reason=credential_resource_missing request_resource=%s",
            request_resource,
        )
        return _json_response(403, "forbidden", "delegated credential resource is missing")
    if not _any_resource_matches(credential_resources, request_resource):
        LOGGER.info(
            "[connection-hub.oauth.mcp_guard] denied reason=resource_mismatch credential_resources=%s request_resource=%s",
            list(credential_resources),
            request_resource,
        )
        return _json_response(403, "forbidden", "delegated credential resource mismatch")

    available_grants = _credential_grants_for_resource(envelope, request_resource)
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
            sorted(available_grants),
            list(_as_list(grant_record.get("operations"))) if isinstance(grant_record, Mapping) else [],
            runtime.get("identity_scope") or "",
        )
        return None

    granted_operations = None
    if isinstance(grant_record, Mapping):
        granted_operations = set(_as_list(grant_record.get("operations")))
    tool_policies = _connection_hub_tool_policies(request)
    if not tool_policies:
        tool_policies = dict(policy.tool_policies or {})

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
            if granted_operations is None or tool_name not in granted_operations:
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
        sorted(granted_operations or []),
        runtime.get("identity_scope") or "",
        [tool for _, tool in tool_calls],
    )
    return None


async def authorize_delegated_rest_request(
    *,
    request: Request,
    auth: Mapping[str, Any] | None,
    operation: str,
    method: str = "",
) -> Response | None:
    """Return a denial response or None when the REST operation may run.

    This is the REST analogue of the managed MCP guard. It is intentionally
    independent of the platform authority that originally authenticated the
    grantor. The bearer proves a delegated-client credential; the stored grant
    record projects the runtime platform user.
    """

    policy = managed_rest_auth_policy(auth)
    if policy is None:
        return None

    denial, user, envelope, grant_record = await _authorize_delegated_managed_request(
        request=request,
        auth=auth,
        authority_id=policy.authority_id,
        roles=policy.roles,
        permissions=policy.permissions,
        logger=REST_LOGGER,
        surface_label="rest",
    )
    if denial is not None:
        return denial

    request_resource = _request_resource(request)
    available_grants = _credential_grants_for_resource(envelope, request_resource)
    if policy.grants and not available_grants.issuperset(policy.grants):
        REST_LOGGER.info(
            "[connection-hub.oauth.rest_guard] denied reason=missing_grant required=%s available=%s resource=%s operation=%s",
            list(policy.grants),
            sorted(available_grants),
            request_resource,
            operation,
        )
        return _json_response(403, "forbidden", "required delegated grant is missing")

    operation_policies = _connection_hub_rest_operation_policies(request)
    if not operation_policies:
        operation_policies = dict(policy.operation_policies or {})
    selected_operation_grants = policy.selected_operation_grants or bool(operation_policies)
    operation_name = str(operation or "").strip()
    operation_policy = operation_policies.get(operation_name)
    if operation_policies and operation_policy is None:
        REST_LOGGER.info(
            "[connection-hub.oauth.rest_guard] denied reason=operation_not_allowed operation=%s configured=%s resource=%s",
            operation_name,
            sorted(operation_policies.keys()),
            request_resource,
        )
        return _json_response(403, "forbidden", f"operation not allowed by endpoint policy: {operation_name}")

    roles = set(user.get("roles") or [])
    permissions = set(user.get("permissions") or [])
    if operation_policy is not None:
        if operation_policy.roles and not roles.intersection(operation_policy.roles):
            return _json_response(403, "forbidden", f"required role is missing for operation: {operation_name}")
        if operation_policy.permissions and not permissions.issuperset(operation_policy.permissions):
            return _json_response(403, "forbidden", f"required permission is missing for operation: {operation_name}")
        if operation_policy.grants and not available_grants.issuperset(operation_policy.grants):
            REST_LOGGER.info(
                "[connection-hub.oauth.rest_guard] denied reason=missing_operation_grant operation=%s required=%s available=%s resource=%s",
                operation_name,
                list(operation_policy.grants),
                sorted(available_grants),
                request_resource,
            )
            return _json_response(403, "forbidden", f"required delegated grant is missing for operation: {operation_name}")

    granted_operations = None
    if isinstance(grant_record, Mapping):
        granted_operations = set(_as_list(grant_record.get("operations")))
    if selected_operation_grants:
        if granted_operations is None or operation_name not in granted_operations:
            REST_LOGGER.info(
                "[connection-hub.oauth.rest_guard] denied reason=operation_not_consented operation=%s consented=%s resource=%s",
                operation_name,
                sorted(granted_operations or []),
                request_resource,
            )
            return _json_response(403, "forbidden", f"operation not consented for this connection: {operation_name}")

    runtime = delegated_rest_runtime_projection(request)
    REST_LOGGER.info(
        "[connection-hub.oauth.rest_guard] accepted resource=%s method=%s operation=%s subject=%s grantor=%s delegate=%s authority=%s scopes=%s operations=%s identity_scope=%s",
        request_resource,
        method,
        operation_name,
        user.get("sub") or "",
        runtime.get("grantor_user_id") or "",
        runtime.get("delegate_identity") or "",
        envelope.issuer_authority_id,
        sorted(available_grants),
        sorted(granted_operations or []),
        runtime.get("identity_scope") or "",
    )
    return None


__all__ = [
    "MANAGED_MCP_AUTH_MODE",
    "ManagedMcpAuthPolicy",
    "ManagedMcpToolPolicy",
    "ManagedRestAuthPolicy",
    "ManagedRestOperationPolicy",
    "authorize_delegated_mcp_request",
    "authorize_delegated_rest_request",
    "delegated_request_resource",
    "delegated_platform_admin_runtime_projection",
    "delegated_mcp_runtime_projection",
    "delegated_rest_runtime_projection",
    "extract_mcp_tool_calls",
    "managed_mcp_auth_policy",
    "managed_rest_auth_policy",
    "mcp_auth_mode",
    "rest_auth_mode",
]
