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
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Mapping, Optional

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from connection_hub.delegated_credentials.oauth.surface_policy import (
    DELEGATED_PROXY_AUTH_MODE,
    MANAGED_AUTH_MODE,
    DelegatedProxyAuthPolicy,
    ManagedMcpAuthPolicy,
    ManagedMcpToolPolicy,
    ManagedRestAuthPolicy,
    ManagedRestOperationPolicy,
    SurfacePolicyDenial,
    SurfacePolicyDecision,
    as_list as _as_list,
    auth_mode,
    authorize_credential_boundary,
    authorize_credential_identity_boundary,
    authorize_mcp_capabilities,
    authorize_principal_boundary,
    authorize_rest_capabilities,
    decode_json_body as _decode_json_body,
    delegated_proxy_auth_policy,
    extract_mcp_tool_calls,
    managed_mcp_auth_policy,
    managed_rest_auth_policy,
)

from connection_hub.authority_registry import CredentialEnvelope
from connection_hub.delegated_credentials.credential_view import (
    DelegatedCredentialView,
    normalize_resource,
)
from connection_hub.delegated_credentials.live_grant import (
    LiveGrantCardError,
    resolve_live_grant_card,
)
from connection_hub.delegated_credentials.catalog.authorization import (
    ActiveCatalogCapabilities,
    catalog_unavailable_denial,
)
from connection_hub.delegated_credentials.catalog.resolver import (
    CatalogUnavailable,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.serving import (
    delegated_serving_resolvers,
)
from connection_hub.authority_projection import (
    authority_has_platform_privilege,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.grants import oauth_tenant_project
from connection_hub.delegated_credentials.oauth.store import (
    GrantStore,
    GrantStoreUnavailable,
)
from connection_hub.delegated_credentials.oauth.metadata import (
    protected_resource_metadata_url,
)
from connection_hub.hub.resolver import (
    delegated_primary_user_id,
    normalize_delegated_identity_scope,
    resolve_delegated_authority_projection,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.named_service_admission import (
    DELEGATED_CARD_BINDING_SCHEMA,
    delegated_card_binding_from_request,
    store_managed_named_service_admission_snapshot,
)


MANAGED_MCP_AUTH_MODE = MANAGED_AUTH_MODE
DELEGATED_PROXY_MCP_AUTH_MODE = DELEGATED_PROXY_AUTH_MODE
LOGGER = logging.getLogger("kdcube.connection_hub.oauth.mcp_guard")
REST_LOGGER = logging.getLogger("kdcube.connection_hub.oauth.rest_guard")


@dataclass(frozen=True)
class DelegatedRestAdmissionResult:
    """Live managed-REST decision and the authority facts that produced it.

    Hosted REST dispatch only needs ``denial``. Trusted host adapters, such as
    Connection Hub's protected-service admission endpoint, also need the
    resolved card/catalog provenance and bounded runtime projection. Keeping
    those facts in one result prevents a second authorization implementation.
    """

    denial: Response | None = None
    runtime: Mapping[str, Any] | None = None
    user: Mapping[str, Any] | None = None
    envelope: CredentialEnvelope = field(default_factory=CredentialEnvelope)
    grant_record: Mapping[str, Any] | None = None
    decision: SurfacePolicyDecision | None = None
    catalog: ActiveCatalogCapabilities | None = None
    request_resource: str = ""
    operation: str = ""


def mcp_auth_mode(auth: Mapping[str, Any] | None) -> str:
    return auth_mode(auth)


def rest_auth_mode(auth: Mapping[str, Any] | None) -> str:
    return auth_mode(auth)


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
    """A refused tool call, shaped as the SDK's own `CallToolResult`.

    The guard answers before the MCP application runs, so this envelope is built
    here rather than by the server. `mcp_types.CallToolResult` carries
    `result_type` with a `"complete"` default, and a client that validates
    against that model rejects an envelope without it — never reaching the body,
    whatever the body says.
    """
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "resultType": "complete",
                "isError": True,
                "content": [{"type": "text", "text": message}],
            },
        }
    )


def _normalize_resource(value: Any) -> str:
    return normalize_resource(value)


def _request_resource(request: Request) -> str:
    return _normalize_resource(_request_public_url_without_query(request))


def delegated_request_resource(request: Request) -> str:
    """Return the public delegated-credential resource URL for a request."""
    return _request_resource(request)


def _connection_hub_rest_operation_policies(
    request: Request,
    *,
    request_resource: str = "",
) -> dict[str, ManagedRestOperationPolicy]:
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.config import (
        oauth_delegated_config,
    )

    cfg = oauth_delegated_config(request)
    operations = cfg.resource_operation_catalog(
        request_resource or _request_resource(request)
    )
    out: dict[str, ManagedRestOperationPolicy] = {}
    for operation in operations:
        name = str(getattr(operation, "name", "") or "").strip()
        if not name:
            continue
        out[name] = ManagedRestOperationPolicy(
            grants=_as_list(getattr(operation, "grants", ())),
        )
    return out


def _credential_grants_for_resource(envelope: CredentialEnvelope, request_resource: str) -> set[str]:
    return DelegatedCredentialView.from_envelope(envelope).grants_for_resource(request_resource)


async def _active_catalog(request: Request) -> tuple[ActiveCatalogCapabilities | None, str]:
    """The active catalog, or the reason it could not be established.

    Absent readers are a composition failure, not an absence of requirements:
    the caller denies rather than proceeding uncontrolled.
    """
    resolvers = delegated_serving_resolvers(request)
    if resolvers is None:
        return None, "delegated_serving_resolvers_absent"
    try:
        document = await resolvers.catalog.resolve_active()
    except CatalogUnavailable as exc:
        return None, exc.reason
    except Exception as exc:  # noqa: BLE001 - resolution failure is unavailability
        LOGGER.warning(
            "[connection-hub.oauth.guard] active catalog unreadable: %s", exc, exc_info=True
        )
        return None, "catalog_unavailable"
    return ActiveCatalogCapabilities(document), ""


def _catalog_unavailable_response(reason: str) -> JSONResponse:
    return JSONResponse(status_code=503, content=catalog_unavailable_denial(reason))


def _rpc_capability_error(rpc_id: Any, payload: Mapping[str, Any]) -> JSONResponse:
    """A removed capability inside a tool call, with its complete path.

    The path travels as the message body because the caller must be able to see
    which resource, namespace, and operation were refused.
    """
    return _rpc_tool_error(rpc_id, json.dumps(dict(payload), ensure_ascii=False))


def _surface_policy_denial_response(
    denial: SurfacePolicyDenial,
) -> JSONResponse:
    """Render a Connection Hub policy verdict on KDCube's HTTP/MCP transport."""

    if denial.is_rpc:
        if isinstance(denial.payload, Mapping):
            return _rpc_capability_error(denial.rpc_id, denial.payload)
        return _rpc_tool_error(
            denial.rpc_id,
            denial.rpc_message or denial.description or "delegated request denied",
        )
    if isinstance(denial.payload, Mapping):
        return JSONResponse(status_code=denial.status, content=dict(denial.payload))
    return _json_response(
        denial.status,
        denial.error,
        denial.description or "delegated request denied",
    )


def _grant_record_client_id(grant_record: Mapping[str, Any] | None) -> str:
    record = grant_record if isinstance(grant_record, Mapping) else {}
    direct = str(record.get("client_id") or "").strip()
    if direct:
        return direct
    credential = record.get("credential")
    credential = credential if isinstance(credential, Mapping) else {}
    attrs = credential.get("attrs")
    attrs = attrs if isinstance(attrs, Mapping) else {}
    return str(attrs.get("client_id") or "").strip()


def _outer_operation_consent_payload(
    payload: Mapping[str, Any],
    *,
    request: Request,
    auth: Mapping[str, Any] | None,
    grant_record: Mapping[str, Any] | None,
    resource: str,
    operation: str,
) -> dict[str, Any]:
    """Add the exact recovery facts to an outer-operation card denial.

    The catalog denial remains intact. The consent block gives chat clients and
    external MCP clients one concrete route to extend this caller's live card.
    """
    client_id = _grant_record_client_id(grant_record)
    if not client_id or not resource or not operation:
        return dict(payload)
    ret = payload.get("ret")
    ret = ret if isinstance(ret, Mapping) else {}
    missing_grants = sorted(set(_as_list(ret.get("missing_grants"))))
    from connection_hub.delegated_credentials.consent_denial import (
        AGENT_CLIENT_PREFIX,
        connection_hub_grant_url,
    )

    tenant, project = oauth_tenant_project(request)
    auth = auth if isinstance(auth, Mapping) else {}
    hub_bundle_id = str(
        auth.get("connection_hub_bundle_id")
        or auth.get("connectionHubBundleId")
        or "connection-hub@1-0"
    ).strip()
    hub_url = connection_hub_grant_url(
        tenant=tenant,
        project=project,
        client_id=client_id,
        resource=resource,
        claims=missing_grants,
        hub_bundle_id=hub_bundle_id,
        outer_operation=operation,
    )
    consent: dict[str, Any] = {
        "kind": "delegated_agent_grant",
        "reason": "delegated_capability_not_granted",
        "agent_client_id": client_id,
        "resource": resource,
        "claims": missing_grants,
        "tool_name": operation,
        "outer_operation": operation,
    }
    if hub_url:
        consent["connection_hub_url"] = hub_url
    if client_id.startswith(AGENT_CLIENT_PREFIX):
        consent["grant"] = {
            "operation": "delegated_agent_grant_create",
            "payload": {
                "client_id": client_id,
                "resource": resource,
                "claims": missing_grants,
                "resource_operations": {resource: [operation]},
            },
        }
    return {**dict(payload), "consent": consent}


async def _default_grant_store(request: Request) -> GrantStore:
    override = getattr(request.app.state, "oauth_grant_store", None)
    if override is not None:
        return override

    try:
        redis = getattr(request.app.state, "redis_async", None)
        if redis is None:
            from kdcube_ai_app.apps.chat.sdk.config import get_settings
            from kdcube_ai_app.infra.redis.client import get_async_redis_client

            redis = get_async_redis_client(get_settings().REDIS_URL)
        tenant, project = oauth_tenant_project(request)
        return GrantStore(redis, tenant, project)
    except GrantStoreUnavailable:
        raise
    except Exception as exc:
        raise GrantStoreUnavailable("initialize") from exc


async def _access_grant_record(
    *,
    request: Request,
    token: str,
    logger: logging.Logger,
    surface_label: str,
    request_resource: str = "",
) -> tuple[Optional[Dict[str, Any]], JSONResponse | None]:
    try:
        grant_store = await _default_grant_store(request)
        return await grant_store.get_access_grant_record(token), None
    except GrantStoreUnavailable as exc:
        logger.exception(
            "[connection-hub.oauth.%s_guard] unavailable operation=%s resource=%s",
            surface_label,
            exc.operation,
            request_resource or _request_resource(request),
        )
        return None, _json_response(
            503,
            "temporarily_unavailable",
            "Current delegated authorization state is unavailable",
        )


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



async def _live_grant_record(request: Any, grant_record: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The registry card is the authority: a binding carrying
    ``registry_access_id`` re-derives its grant facts from the card AS IT IS
    NOW — a hub-side extension applies to this bearer's next call, a narrowing
    narrows it, and a revoked (absent) card ends its authority. A binding
    without the pointer keeps its embedded snapshot (legacy)."""
    if not isinstance(grant_record, dict):
        return grant_record
    access_id = str(grant_record.get("registry_access_id") or "").strip()
    if not access_id:
        return grant_record
    credential = grant_record.get("credential")
    credential = credential if isinstance(credential, Mapping) else {}
    attrs = credential.get("attrs")
    attrs = attrs if isinstance(attrs, Mapping) else {}
    tenant, project = oauth_tenant_project(request)
    store = await _default_grant_store(request)
    # A projection lost to eviction is not a revoked card: the committed
    # revision restores it. Expired or revoked durable state still denies and
    # is not re-cached.
    resolvers = delegated_serving_resolvers(request)
    card = await resolve_live_grant_card(
        store.redis,
        tenant=tenant,
        project=project,
        access_id=access_id,
        expected_client_id=str(attrs.get("client_id") or ""),
        expected_grantor_subject=str(attrs.get("grantor_subject") or ""),
        expected_delegate_subject=str(credential.get("subject") or ""),
        card_store=getattr(resolvers, "cards", None),
    )
    if card is None:
        LOGGER.info("[connection-hub.oauth.guard] registry card %s gone — binding treated as revoked", access_id)
        return None
    LOGGER.info(
        "[connection-hub.oauth.guard] registry card resolved access_id=%s client_id=%s "
        "resources=%s account_scope_providers=%s",
        access_id,
        card.client_id,
        sorted(card.resource_grants.keys()),
        sorted(card.account_scope.keys()) or "-",
    )
    resource_grants = card.resource_grants
    all_grants = sorted({str(g) for grants in resource_grants.values() for g in (grants or [])})
    resolved = dict(grant_record)
    credential = dict(resolved.get("credential") or {})
    attrs = dict(credential.get("attrs") or {})
    attrs["client_id"] = card.client_id
    attrs["grantor_subject"] = card.grantor_subject
    attrs["resource_grants"] = {res: list(grants or []) for res, grants in resource_grants.items()}
    attrs["scopes"] = all_grants
    attrs["grants"] = all_grants
    attrs["operations"] = list(card.operations)
    attrs["resource_operations"] = {
        resource: list(operations or ())
        for resource, operations in card.resource_operations.items()
    }
    attrs["account_scope"] = {
        provider: {account_id: list(claims) for account_id, claims in accounts.items()}
        for provider, accounts in card.account_scope.items()
    }
    credential["subject"] = card.delegate_subject
    credential["attrs"] = attrs
    resolved["credential"] = credential
    resolved["registry_access_id"] = card.access_id
    resolved["client_id"] = card.client_id
    resolved["grantor_subject"] = card.grantor_subject
    resolved["delegate_subject"] = card.delegate_subject
    resolved["expires_at"] = int(card.expires_at or 0)
    resolved["source"] = str(card.source or "")
    resolved["operations"] = list(card.operations)
    resolved["resource_operations"] = {
        resource: list(operations or ())
        for resource, operations in card.resource_operations.items()
    }
    resolved["grants"] = all_grants
    # Carry the card's per-agent account binding so the door can enforce it
    # (which connected account(s) this client may use per provider).
    resolved["account_scope"] = {
        provider: {account_id: list(claims) for account_id, claims in accounts.items()}
        for provider, accounts in card.account_scope.items()
    }
    # The named-service boundary tree, narrowed from the descriptor when the
    # card was written. Always set, including empty: an absent key leaves the
    # descriptor as the only ceiling, so a card that materialized nothing would
    # reach everything the deployment configures. A pre-encoding card is bounded
    # by what it materialized, per the design's pre-migration rule.
    resolved["named_services"] = dict(card.named_services or {})
    # Card provenance travels with the facts so a denial can name the card and
    # the catalog generation its selection was saved against.
    resolved["card_revision"] = int(card.card_revision or 0)
    resolved["catalog_version"] = str(card.catalog_version or "")
    return resolved


def _grant_record_credential(grant_record: Mapping[str, Any] | None) -> CredentialEnvelope:
    if not isinstance(grant_record, Mapping):
        return CredentialEnvelope()
    credential = grant_record.get("credential")
    if isinstance(credential, Mapping):
        return CredentialEnvelope.coerce(credential)
    return CredentialEnvelope()


def _delegated_runtime_projection(
    request: Request,
    *,
    surface: str,
    request_resource: str = "",
) -> dict[str, Any]:
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
    effective_resource = request_resource or _request_resource(request)
    grants = sorted(_credential_grants_for_resource(envelope, effective_resource))
    credential_view = DelegatedCredentialView.from_parts(credential, grant_record)
    resource_operations = {
        resource: list(operations)
        for resource, operations in credential_view.resource_operations.items()
    }
    operations = sorted(credential_view.operations_for_resource(effective_resource))
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
            "resource_operations": resource_operations,
            "resource_grants": resource_grants,
            "identity_scope": normalize_delegated_identity_scope(attrs.get("identity_scope")),
            "delegation": dict(projection.get("delegation") or {}),
            "provenance": dict(projection.get("provenance") or economics.get("provenance") or {}),
        }
    )
    delegated_card_binding = delegated_card_binding_from_request(request)
    if not delegated_card_binding and credential_view.registry_access_id:
        delegated_card_binding = {
            "schema": DELEGATED_CARD_BINDING_SCHEMA,
            "access_id": credential_view.registry_access_id,
            "client_id": credential_view.client_id,
            "grantor_user_id": grantor_user_id,
            "delegate_identity": delegate_identity,
            "expires_at": int(grant_record.get("expires_at") or 0),
        }
    if delegated_card_binding:
        identity_authority["delegated_card_binding"] = delegated_card_binding
        identity_authority["gateway_rate_limit_subject"] = (
            f"card:{delegated_card_binding['access_id']}"
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
        "resource_operations": resource_operations,
    }


def delegated_mcp_runtime_projection(request: Request) -> dict[str, Any]:
    return _delegated_runtime_projection(request, surface="mcp")


def delegated_rest_runtime_projection(
    request: Request,
    *,
    request_resource: str = "",
) -> dict[str, Any]:
    return _delegated_runtime_projection(
        request,
        surface="rest",
        request_resource=request_resource,
    )


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

    grant_record, unavailable = await _access_grant_record(
        request=request,
        token=token,
        logger=REST_LOGGER,
        surface_label="rest",
    )
    if unavailable is not None:
        return {}
    try:
        grant_record = await _live_grant_record(request, grant_record)
    except LiveGrantCardError as exc:
        REST_LOGGER.warning(
            "[connection-hub.oauth.rest_guard] denied reason=live_grant_%s resource=%s",
            exc.reason,
            _request_resource(request),
        )
        return {}
    envelope = _grant_record_credential(grant_record)
    request_resource = _request_resource(request)
    boundary = authorize_credential_boundary(
        authority_id=authority_id,
        required_roles=(),
        required_permissions=(),
        user_roles=user.get("roles") or (),
        user_permissions=user.get("permissions") or (),
        envelope=envelope,
        grant_record=grant_record,
        request_resource=request_resource,
    )
    if not boundary.allowed:
        return {}

    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.config import (
        oauth_delegated_config,
    )

    resource_cfg = oauth_delegated_config(request).resource_config(request_resource)
    required_grants = set(_as_list(getattr(resource_cfg, "grants", ())))
    credential_grants = set(boundary.stored_grants)
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
    token: str = "",
    request_resource: str = "",
    match_request_resource: bool = True,
) -> tuple[JSONResponse | None, dict[str, Any], CredentialEnvelope, Mapping[str, Any]]:
    effective_token = token or _extract_bearer(request)
    effective_resource = request_resource or _request_resource(request)
    if not effective_token:
        logger.info(
            "[connection-hub.oauth.%s_guard] denied reason=missing_bearer resource=%s",
            surface_label,
            effective_resource,
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

    user = await _authenticate_delegated_client_access_token(effective_token)
    if user is None:
        logger.info(
            "[connection-hub.oauth.%s_guard] denied reason=invalid_bearer resource=%s",
            surface_label,
            effective_resource,
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

    principal = authorize_principal_boundary(
        required_roles=roles,
        required_permissions=permissions,
        user_roles=user.get("roles") or (),
        user_permissions=user.get("permissions") or (),
    )
    if not principal.allowed:
        assert principal.denial is not None
        logger.info(
            "[connection-hub.oauth.%s_guard] denied reason=%s resource=%s",
            surface_label,
            principal.denial.reason,
            effective_resource,
        )
        return (
            _surface_policy_denial_response(principal.denial),
            user,
            CredentialEnvelope(),
            {},
        )

    grant_record, unavailable = await _access_grant_record(
        request=request,
        token=effective_token,
        logger=logger,
        surface_label=surface_label,
        request_resource=effective_resource,
    )
    if unavailable is not None:
        return unavailable, user, CredentialEnvelope(), {}
    try:
        grant_record = await _live_grant_record(request, grant_record)
    except LiveGrantCardError as exc:
        logger.warning(
            "[connection-hub.oauth.%s_guard] denied reason=live_grant_%s resource=%s",
            surface_label,
            exc.reason,
            effective_resource,
        )
        return (
            _json_response(
                503,
                "temporarily_unavailable",
                "Current delegated authorization state is unavailable",
            ),
            user,
            CredentialEnvelope(),
            {},
        )
    envelope = _grant_record_credential(grant_record)
    try:
        request.state.delegated_credential = {
            "user": dict(user or {}),
            "credential": envelope.to_dict(),
            "grant_record": dict(grant_record or {}),
        }
    except Exception:
        pass

    if match_request_resource:
        boundary = authorize_credential_boundary(
            authority_id=authority_id,
            required_roles=(),
            required_permissions=(),
            user_roles=user.get("roles") or (),
            user_permissions=user.get("permissions") or (),
            envelope=envelope,
            grant_record=grant_record,
            request_resource=effective_resource,
        )
    else:
        boundary = authorize_credential_identity_boundary(
            authority_id=authority_id,
            required_roles=(),
            required_permissions=(),
            user_roles=user.get("roles") or (),
            user_permissions=user.get("permissions") or (),
            envelope=envelope,
            grant_record=grant_record,
        )
    if not boundary.allowed:
        assert boundary.denial is not None
        logger.info(
            "[connection-hub.oauth.%s_guard] denied reason=%s request_resource=%s",
            surface_label,
            boundary.denial.reason,
            effective_resource,
        )
        return (
            _surface_policy_denial_response(boundary.denial),
            user,
            envelope,
            grant_record or {},
        )

    return None, user, envelope, grant_record or {}


async def resolve_delegated_card_session_projection(
    request: Request,
    *,
    authority_id: str,
) -> dict[str, Any]:
    """Resolve a live card bearer into gateway session facts.

    This is the identity-only entrance used before generic gateway admission.
    It reuses the managed guard's bearer verification, live-card restoration,
    and request-resource boundary. Tool and operation authorization remain with
    the managed MCP/REST guard that owns the concrete surface call.
    """

    denial, _user, envelope, grant_record = await _authorize_delegated_managed_request(
        request=request,
        auth=None,
        authority_id=authority_id,
        roles=(),
        permissions=(),
        logger=LOGGER,
        surface_label="gateway_identity",
    )
    if denial is not None:
        return {}

    view = DelegatedCredentialView.from_envelope(envelope, grant_record)
    if not view.registry_access_id:
        return {}

    runtime = _delegated_runtime_projection(request, surface="gateway")
    if not runtime:
        return {}

    card_principal = f"card:{view.registry_access_id}"
    identity_authority = dict(runtime.get("identity_authority") or {})
    identity_authority["gateway_rate_limit_subject"] = card_principal
    return {
        **runtime,
        "user_id": card_principal,
        "username": runtime.get("delegate_identity") or card_principal,
        "identity_authority": identity_authority,
        "card_principal": card_principal,
    }


async def authorize_delegated_mcp_proxy_request(
    *,
    request: Request,
    body: bytes,
    auth: Mapping[str, Any] | None,
) -> Response | None:
    """Authenticate a live delegated card for a governed inner-resource proxy.

    This entrance intentionally does not match the card against the proxy URL.
    The proxy application must resolve and enforce an exact card resource,
    grant, and operation before calling any upstream service.
    """

    policy = delegated_proxy_auth_policy(auth)
    if policy is None:
        return None
    if isinstance(_decode_json_body(body), list):
        return _json_response(
            400,
            "invalid_request",
            "JSON-RPC batch requests are not supported by this MCP transport",
        )
    denial, user, envelope, grant_record = await _authorize_delegated_managed_request(
        request=request,
        auth=auth,
        authority_id=policy.authority_id,
        roles=policy.roles,
        permissions=policy.permissions,
        logger=LOGGER,
        surface_label="mcp_proxy",
        match_request_resource=False,
    )
    if denial is not None:
        return denial
    view = DelegatedCredentialView.from_envelope(envelope, grant_record)
    LOGGER.info(
        "[connection-hub.oauth.mcp_proxy_guard] accepted subject=%s grantor=%s "
        "delegate=%s authority=%s card=%s resources=%s",
        user.get("sub") or "",
        view.grantor_user_id,
        view.subject,
        envelope.issuer_authority_id,
        view.registry_access_id,
        sorted(view.resources),
    )
    return None


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
    if isinstance(_decode_json_body(body), list):
        return _json_response(
            400,
            "invalid_request",
            "JSON-RPC batch requests are not supported by this MCP transport",
        )

    denial, user, envelope, grant_record = await _authorize_delegated_managed_request(
        request=request,
        auth=auth,
        authority_id=policy.authority_id,
        roles=policy.roles,
        permissions=policy.permissions,
        logger=LOGGER,
        surface_label="mcp",
    )
    if denial is not None:
        return denial

    request_resource = _request_resource(request)
    catalog, unavailable = await _active_catalog(request)
    if catalog is None:
        LOGGER.warning(
            "[connection-hub.oauth.mcp_guard] denied reason=catalog_%s resource=%s",
            unavailable,
            request_resource,
        )
        return _catalog_unavailable_response(unavailable)

    boundary = authorize_credential_boundary(
        authority_id=policy.authority_id,
        required_roles=policy.roles,
        required_permissions=policy.permissions,
        user_roles=user.get("roles") or (),
        user_permissions=user.get("permissions") or (),
        envelope=envelope,
        grant_record=grant_record,
        request_resource=request_resource,
    )
    tool_calls = extract_mcp_tool_calls(body)
    decision = authorize_mcp_capabilities(
        boundary=boundary,
        policy=policy,
        catalog=catalog,
        grant_record=grant_record,
        request_resource=request_resource,
        user_roles=user.get("roles") or (),
        user_permissions=user.get("permissions") or (),
        tool_calls=tool_calls,
    )
    if not decision.allowed:
        assert decision.denial is not None
        LOGGER.info(
            "[connection-hub.oauth.mcp_guard] denied reason=%s resource=%s",
            decision.denial.reason,
            request_resource,
        )
        policy_denial = decision.denial
        if (
            policy_denial.reason == "operation_not_consented"
            and isinstance(policy_denial.payload, Mapping)
            and tool_calls
        ):
            policy_denial = replace(
                policy_denial,
                payload=_outer_operation_consent_payload(
                    policy_denial.payload,
                    request=request,
                    auth=auth,
                    grant_record=grant_record,
                    resource=decision.matched_resource or request_resource,
                    operation=tool_calls[0][1],
                ),
            )
        return _surface_policy_denial_response(policy_denial)

    try:
        store_managed_named_service_admission_snapshot(
            request,
            catalog=catalog,
            grant_record=grant_record or {},
            credential=envelope,
            resource=decision.matched_resource,
            request_resource=request_resource,
            outer_operation=tool_calls[0][1] if tool_calls else "",
        )
    except ValueError as exc:
        LOGGER.warning(
            "[connection-hub.oauth.mcp_guard] admission snapshot unavailable: %s",
            exc,
        )
        return _json_response(
            503,
            "temporarily_unavailable",
            "Exact delegated-card authority is unavailable for this request",
        )

    runtime = delegated_mcp_runtime_projection(request)
    LOGGER.info(
        "[connection-hub.oauth.mcp_guard] accepted resource=%s subject=%s grantor=%s "
        "delegate=%s authority=%s scopes=%s tools=%s identity_scope=%s tool_calls=%s",
        request_resource,
        user.get("sub") or "",
        runtime.get("grantor_user_id") or "",
        runtime.get("delegate_identity") or "",
        envelope.issuer_authority_id,
        sorted(decision.available_grants),
        sorted(decision.granted_operations),
        runtime.get("identity_scope") or "",
        [tool for _, tool in tool_calls],
    )
    return None


async def evaluate_delegated_rest_admission(
    *,
    request: Request,
    auth: Mapping[str, Any] | None,
    operation: str,
    method: str = "",
    token: str = "",
    request_resource: str = "",
    log_identity_details: bool = True,
) -> DelegatedRestAdmissionResult:
    """Evaluate one managed REST operation against live card/catalog state.

    ``request_resource`` is normally derived from the hosted door URL. A
    trusted adapter may supply an external protected resource after separately
    authenticating and resource-binding that service.
    """

    policy = managed_rest_auth_policy(auth)
    if policy is None:
        return DelegatedRestAdmissionResult()

    effective_resource = request_resource or _request_resource(request)
    operation_name = str(operation or "").strip()

    denial, user, envelope, grant_record = await _authorize_delegated_managed_request(
        request=request,
        auth=auth,
        authority_id=policy.authority_id,
        roles=policy.roles,
        permissions=policy.permissions,
        logger=REST_LOGGER,
        surface_label="rest",
        token=token,
        request_resource=effective_resource,
    )
    if denial is not None:
        return DelegatedRestAdmissionResult(
            denial=denial,
            user=user,
            envelope=envelope,
            grant_record=grant_record,
            request_resource=effective_resource,
            operation=operation_name,
        )

    catalog, unavailable = await _active_catalog(request)
    if catalog is None:
        REST_LOGGER.warning(
            "[connection-hub.oauth.rest_guard] denied reason=catalog_%s resource=%s operation=%s",
            unavailable,
            effective_resource,
            operation_name,
        )
        return DelegatedRestAdmissionResult(
            denial=_catalog_unavailable_response(unavailable),
            user=user,
            envelope=envelope,
            grant_record=grant_record,
            request_resource=effective_resource,
            operation=operation_name,
        )

    boundary = authorize_credential_boundary(
        authority_id=policy.authority_id,
        required_roles=policy.roles,
        required_permissions=policy.permissions,
        user_roles=user.get("roles") or (),
        user_permissions=user.get("permissions") or (),
        envelope=envelope,
        grant_record=grant_record,
        request_resource=effective_resource,
    )
    decision = authorize_rest_capabilities(
        boundary=boundary,
        policy=policy,
        catalog=catalog,
        grant_record=grant_record,
        request_resource=effective_resource,
        operation=operation_name,
        user_roles=user.get("roles") or (),
        user_permissions=user.get("permissions") or (),
        operation_policies=_connection_hub_rest_operation_policies(
            request,
            request_resource=effective_resource,
        ),
    )
    if not decision.allowed:
        assert decision.denial is not None
        REST_LOGGER.info(
            "[connection-hub.oauth.rest_guard] denied reason=%s resource=%s operation=%s",
            decision.denial.reason,
            effective_resource,
            operation_name,
        )
        return DelegatedRestAdmissionResult(
            denial=_surface_policy_denial_response(decision.denial),
            user=user,
            envelope=envelope,
            grant_record=grant_record,
            decision=decision,
            catalog=catalog,
            request_resource=effective_resource,
            operation=operation_name,
        )

    runtime = delegated_rest_runtime_projection(
        request,
        request_resource=effective_resource,
    )
    if log_identity_details:
        REST_LOGGER.info(
            "[connection-hub.oauth.rest_guard] accepted resource=%s method=%s operation=%s "
            "subject=%s grantor=%s delegate=%s authority=%s scopes=%s operations=%s "
            "identity_scope=%s",
            effective_resource,
            method,
            operation_name,
            user.get("sub") or "",
            runtime.get("grantor_user_id") or "",
            runtime.get("delegate_identity") or "",
            envelope.issuer_authority_id,
            sorted(decision.available_grants),
            sorted(decision.granted_operations),
            runtime.get("identity_scope") or "",
        )
    else:
        REST_LOGGER.info(
            "[connection-hub.oauth.rest_guard] accepted resource=%s method=%s operation=%s "
            "authority=%s scopes=%s operations=%s identity_details=suppressed",
            effective_resource,
            method,
            operation_name,
            envelope.issuer_authority_id,
            sorted(decision.available_grants),
            sorted(decision.granted_operations),
        )
    return DelegatedRestAdmissionResult(
        runtime=runtime,
        user=user,
        envelope=envelope,
        grant_record=grant_record,
        decision=decision,
        catalog=catalog,
        request_resource=effective_resource,
        operation=operation_name,
    )


async def authorize_delegated_rest_request(
    *,
    request: Request,
    auth: Mapping[str, Any] | None,
    operation: str,
    method: str = "",
) -> Response | None:
    """Return a denial response or None when the REST operation may run."""

    result = await evaluate_delegated_rest_admission(
        request=request,
        auth=auth,
        operation=operation,
        method=method,
    )
    return result.denial


__all__ = [
    "DELEGATED_PROXY_MCP_AUTH_MODE",
    "MANAGED_MCP_AUTH_MODE",
    "DelegatedProxyAuthPolicy",
    "ManagedMcpAuthPolicy",
    "ManagedMcpToolPolicy",
    "ManagedRestAuthPolicy",
    "ManagedRestOperationPolicy",
    "DelegatedRestAdmissionResult",
    "authorize_delegated_mcp_proxy_request",
    "authorize_delegated_mcp_request",
    "authorize_delegated_rest_request",
    "delegated_request_resource",
    "delegated_platform_admin_runtime_projection",
    "delegated_mcp_runtime_projection",
    "delegated_rest_runtime_projection",
    "extract_mcp_tool_calls",
    "evaluate_delegated_rest_admission",
    "managed_mcp_auth_policy",
    "managed_rest_auth_policy",
    "mcp_auth_mode",
    "resolve_delegated_card_session_projection",
    "rest_auth_mode",
]
