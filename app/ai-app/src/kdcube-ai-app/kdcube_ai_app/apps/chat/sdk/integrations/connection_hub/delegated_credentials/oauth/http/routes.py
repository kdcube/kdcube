# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
/oauth/authorize + /oauth/authorize/consent routes.

GET renders the consent screen for an authenticated user; POST issues an
authorization code (on approve) or bounces ``access_denied`` (on deny). The
consent POST re-validates client/redirect/PKCE; it never trusts the rendered
hidden fields blindly. MCP calls still name form entries ``tools``, but issued
records store them as generic delegated operations.
"""
from __future__ import annotations

import inspect
import json
import logging
import secrets
from functools import wraps
from typing import Any, Iterable, Mapping, Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from connection_hub.delegated_credentials.oauth.clients import (
    CLIENT_REGISTRATION_PRE_REGISTERED,
    client_from_record,
    dcr_redirect_allowed,
    get_client,
    normalize_public_client_metadata,
)
from connection_hub.delegated_credentials.live_grant import (
    LiveGrantCardError,
    live_grants_for_resource,
    resolve_live_grant_card,
    whole_card_grants,
)
from connection_hub.delegated_credentials.oauth.client_metadata import (
    ClientMetadataError,
    is_client_metadata_id,
    resolve_client_metadata_document,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.config import (
    OAuthDelegatedClientConfig,
    oauth_delegated_config,
    oauth_delegated_config_from_connections,
)
from connection_hub.delegated_credentials.oauth.consent import (
    CONSENT_CONTRACT_VERSION,
    named_service_selection_rows,
    platform_edge_grants_for_scopes,
    requested_card_selection,
    render_consent_html,
    resource_selection_rows,
    tools_for_scopes,
)
from connection_hub.delegated_credentials.oauth.authority import build_delegated_client_credential
from connection_hub.delegated_credentials.resource_operations import (
    normalize_resource_grants,
    normalize_resource_operations,
    operation_union,
    project_legacy_operations,
    resolve_declared_resource,
    resolve_declared_resource_keys,
)
from connection_hub.delegated_credentials.cards.resolver import (
    CardUnavailable,
)
from connection_hub.delegated_credentials.cards.identity import (
    CARD_KIND_AGENT,
    CARD_KIND_AUTOMATION,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.serving import (
    delegated_card_store,
    delegated_serving_resolvers,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.cards.service import (
    CardCommitFailed,
    CardConflict,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http.deps import (
    AutomationAccessUnavailable,
    get_automation_access,
    extract_bearer,
    get_access_token_minter,
    get_authenticate,
    get_grant_store,
    is_admin,
    oauth_tenant_project,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http.discovery import resolve_issuer
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.oauth.http.device import (
    DEVICE_CONSENT_SCHEMA,
    device_completion_page,
    device_consent_binding,
    device_oauth_error,
    device_verification_page,
    get_device_grant_store,
    verification_uri,
    verification_uri_complete,
)
from connection_hub.delegated_credentials.oauth.device import (
    DEVICE_GRANT_TYPE,
    DEVICE_POLL_APPROVED,
    DEVICE_POLL_AUTHORIZATION_PENDING,
    DEVICE_POLL_SLOW_DOWN,
)
from connection_hub.delegated_credentials.oauth.flow import (
    AuthorizeError,
    AuthorizeRequest,
    build_redirect,
    parse_authorize_request,
)
from connection_hub.delegated_credentials.oauth.pkce import verify_s256
from connection_hub.delegated_credentials.oauth.authority_store import (
    RefreshTokenReuseDetected,
)
from connection_hub.delegated_credentials.oauth.store import (
    GrantStoreUnavailable,
)
from connection_hub.authority_registry_config import (
    resolve_authority_provider_instance,
)
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import call_bundle_operation
from kdcube_ai_app.auth.bundle.login_lane import sign_in_bounce_path
from connection_hub.authority_inventory import (
    AuthorityGrantInventory,
    PlatformAuthorityInventoryProvider,
    platform_identity_from_user,
    selected_delegation_edge,
)
from connection_hub.mcp_metadata import (
    kdcube_icon_url,
    kdcube_website_url,
)

router = APIRouter()
LOGGER = logging.getLogger("kdcube.connection_hub.oauth")

_AUTHORIZE_FORM_KEYS = (
    "client_id", "redirect_uri", "response_type", "scope",
    "resource", "state", "code_challenge", "code_challenge_method",
)

_CONSENT_DRAFT_SCHEMA = "connection_hub.oauth_consent_draft.v1"


def _declared_invocation_policies(
    config: OAuthDelegatedClientConfig,
    policies: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    """Key invocation choices by the same declared resources as the Card."""

    resolved: dict[str, dict[str, str]] = {}
    for raw_resource, raw_operations in dict(policies or {}).items():
        if not isinstance(raw_operations, Mapping):
            continue
        resource, _literal = resolve_declared_resource(config, raw_resource)
        target = resolved.setdefault(resource, {})
        for operation, mode in raw_operations.items():
            operation_value = str(operation or "").strip()
            mode_value = str(mode or "").strip().lower()
            if operation_value:
                target[operation_value] = mode_value
    return resolved


def _declared_named_service_operations(
    config: OAuthDelegatedClientConfig,
    selection: Mapping[str, Any] | str,
) -> Mapping[str, Any] | str:
    """Canonicalize the resource level of an exact named-service choice."""

    if isinstance(selection, str):
        return selection
    resolved: dict[str, dict[str, list[str]]] = {}
    for raw_resource, raw_namespaces in dict(selection or {}).items():
        if not isinstance(raw_namespaces, Mapping):
            continue
        resource, _literal = resolve_declared_resource(config, raw_resource)
        target = resolved.setdefault(resource, {})
        for namespace, raw_operations in raw_namespaces.items():
            held = target.setdefault(str(namespace), [])
            values = (
                [raw_operations]
                if isinstance(raw_operations, str)
                else list(raw_operations or ())
            )
            for operation in values:
                operation_value = str(operation or "").strip()
                if operation_value and operation_value not in held:
                    held.append(operation_value)
    return resolved


def _declared_catalog_rows(
    config: OAuthDelegatedClientConfig,
    rows: Mapping[str, Any],
) -> dict[str, str]:
    """Keep presentation rows aligned with canonical authority resources."""

    resolved: dict[str, str] = {}
    for raw_resource, raw_row in dict(rows or {}).items():
        resource, _literal = resolve_declared_resource(config, raw_resource)
        row = str(raw_row or "").strip()
        if not row:
            continue
        row_key, _row_literal = resolve_declared_resource(config, row)
        resolved[resource] = row_key
    return resolved


def _normalize_grant_store_unavailable(fn):
    """Expose shared-state outages as OAuth's retryable 503 response."""

    @wraps(fn)
    async def _wrapped(*args: Any, **kwargs: Any) -> Response:
        try:
            return await fn(*args, **kwargs)
        except GrantStoreUnavailable as exc:
            LOGGER.exception(
                "[connection-hub.oauth] grant_store_unavailable route=%s operation=%s",
                fn.__name__,
                exc.operation,
            )
            return JSONResponse(
                status_code=503,
                content={
                    "error": "temporarily_unavailable",
                    "error_description": (
                        "OAuth authorization state is temporarily unavailable"
                    ),
                },
                headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
            )

    return _wrapped


def _same_origin_authorize_referrer_params(request: Request) -> dict[str, str]:
    referrer = str(request.headers.get("referer") or request.headers.get("referrer") or "").strip()
    if not referrer:
        return {}
    try:
        current = urlsplit(str(request.url))
        got = urlsplit(referrer)
    except Exception:
        return {}
    if not got.scheme or not got.netloc:
        return {}
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip()
    forwarded_host = str(request.headers.get("x-forwarded-host") or "").split(",", 1)[0].strip()
    current_scheme = forwarded_proto or current.scheme
    current_host = forwarded_host or str(request.headers.get("host") or "").strip() or current.netloc
    if (got.scheme, got.netloc) != (current_scheme, current_host):
        return {}
    if not got.path.rstrip("/").endswith("/oauth/authorize"):
        return {}
    parsed = parse_qs(got.query, keep_blank_values=True)
    out: dict[str, str] = {}
    for key in _AUTHORIZE_FORM_KEYS:
        values = parsed.get(key)
        if values:
            out[key] = str(values[-1] or "")
    return out


def _consent_authorize_params(request: Request, form: Any) -> dict[str, Any]:
    params = {k: form.get(k) for k in _AUTHORIZE_FORM_KEYS}
    missing = [key for key, value in params.items() if not str(value or "").strip()]
    if not missing:
        return params
    ref_params = _same_origin_authorize_referrer_params(request)
    filled: list[str] = []
    for key in missing:
        value = ref_params.get(key)
        if value is None:
            continue
        params[key] = value
        filled.append(key)
    if filled:
        tenant, project = oauth_tenant_project(request)
        LOGGER.info(
            "[connection-hub.oauth] consent params recovered from authorize referrer "
            "tenant=%s project=%s client_id=%s filled=%s form_keys=%s",
            tenant,
            project,
            str(params.get("client_id") or ""),
            filled,
            sorted(str(key) for key in form.keys()),
        )
    return params


def _consent_action(request: Request) -> str:
    path = str(request.url.path or "").rstrip("/")
    if path.endswith("/authorize"):
        return f"{path}/consent"
    return "/oauth/authorize/consent"


def _consent_payload(
    *,
    req: AuthorizeRequest,
    issuer: str,
    csrf_token: str,
    trusted: bool,
    cfg: OAuthDelegatedClientConfig,
    form_action: str,
    grantor_subject: str,
    grantor_label: str,
    signout_action: str,
    return_to: str,
    catalog_version: str = "",
    connected_accounts: list | None = None,
    seeded_account_scope: Mapping[str, Any] | None = None,
    seeded_named_service_operations: Mapping[str, Any] | None = None,
    seeded_resource_operations: Mapping[str, Iterable[str]] | None = None,
    existing_card: bool = False,
) -> dict[str, Any]:
    authorize_request = {
        "client_id": req.client_id,
        "redirect_uri": req.redirect_uri,
        "response_type": req.response_type,
        "scopes": list(req.scopes),
        "scope": " ".join(req.scopes),
        "resource": req.resource or "",
        "state": req.state or "",
        "code_challenge": req.code_challenge,
        "code_challenge_method": req.code_challenge_method,
        "client": req.client.snapshot() if req.client is not None else {},
    }
    return {
        "consent_contract": {"version": CONSENT_CONTRACT_VERSION},
        "catalog_version": catalog_version,
        # `request` is kept for existing renderers. `oauth_request` avoids a
        # common bundle-operation kwarg collision with the framework request.
        "request": authorize_request,
        "oauth_request": authorize_request,
        "issuer": issuer,
        "csrf_token": csrf_token,
        "trusted": bool(trusted),
        "selection_source": "existing_card" if existing_card else "request",
        "brand": cfg.brand,
        "form_action": form_action,
        "grantor_subject": grantor_subject,
        "grantor_label": grantor_label,
        "signout_action": signout_action,
        "return_to": return_to,
        "platform_grants": [
            {"grant": grant, "label": label, "description": description}
            for grant, label, description in platform_edge_grants_for_scopes(req.scopes, config=cfg)
        ],
        "tools": [
            {
                "name": tool.name,
                "label": tool.label,
                "description": tool.description,
                "grants": list(tool.grants),
            }
            for tool in cfg.tools_for_scopes(req.scopes, resource=req.resource)
        ],
        "resources": resource_selection_rows(
            req.scopes,
            config=cfg,
            resource=req.resource,
            seeded_operations=seeded_resource_operations,
        ),
        "named_service_operations": named_service_selection_rows(
            req.scopes,
            config=cfg,
            resource=req.resource,
            seeded=seeded_named_service_operations,
        ),
        "connected_accounts": list(connected_accounts or []),
        "seeded_account_scope": dict(seeded_account_scope or {}),
        # What the client's card covers today. A submission replaces it, so a
        # renderer that cannot see it cannot offer an informed choice.
        "seeded_named_service_operations": {
            str(namespace): [str(op) for op in (operations or ())]
            for namespace, operations in dict(seeded_named_service_operations or {}).items()
        },
        "seeded_resource_operations": {
            str(selector): [str(operation) for operation in (operations or ())]
            for selector, operations in dict(seeded_resource_operations or {}).items()
        },
    }


def _extract_custom_consent_html(result: Any, *, operation: str) -> str:
    if isinstance(result, str):
        return result
    if not isinstance(result, Mapping):
        return ""
    candidates = [
        result.get("html"),
        result.get("body"),
        result.get(operation),
        result.get("result"),
        result.get("data"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            return candidate
        if isinstance(candidate, Mapping):
            nested = candidate.get("html") or candidate.get("body")
            if isinstance(nested, str):
                return nested
    return ""


def _authority_registry_for_request(request: Request) -> Mapping[str, Any]:
    state = getattr(request, "state", None)
    raw = getattr(state, "connection_hub_authority_registry", None) if state is not None else None
    return raw if isinstance(raw, Mapping) else {}


def _custom_consent_endpoint(request: Request, cfg: OAuthDelegatedClientConfig) -> Mapping[str, Any]:
    ui = cfg.consent_ui
    if ui.mode == "connection_hub":
        return {}
    if ui.mode == "bundle_hosted" and isinstance(ui.host, Mapping) and ui.host:
        return ui.host
    if ui.mode == "authority_provider" and ui.authority_id and ui.provider_id:
        resolved = resolve_authority_provider_instance(
            _authority_registry_for_request(request),
            authority_id=ui.authority_id,
            provider_id=ui.provider_id,
        )
        if not resolved.get("ok"):
            return {"error": resolved.get("error") or "authority_provider_not_found"}
        entrypoints = resolved.get("entrypoints") if isinstance(resolved.get("entrypoints"), Mapping) else {}
        endpoint = entrypoints.get(ui.entrypoint or "consent")
        return endpoint if isinstance(endpoint, Mapping) else {"error": "consent_entrypoint_not_found"}
    return {}


async def _render_custom_consent_if_configured(
    request: Request,
    *,
    req: AuthorizeRequest,
    issuer: str,
    csrf_token: str,
    trusted: bool,
    cfg: OAuthDelegatedClientConfig,
    grantor_subject: str,
    grantor_label: str,
    catalog_version: str = "",
    connected_accounts: list | None = None,
    seeded_account_scope: Mapping[str, Any] | None = None,
    seeded_named_service_operations: Mapping[str, Any] | None = None,
    seeded_resource_operations: Mapping[str, Iterable[str]] | None = None,
    existing_card: bool = False,
) -> Response | None:
    endpoint = _custom_consent_endpoint(request, cfg)
    if not endpoint:
        return None
    if endpoint.get("error"):
        LOGGER.warning(
            "[connection-hub.oauth] consent_ui unresolved mode=%s authority=%s provider=%s entrypoint=%s error=%s",
            cfg.consent_ui.mode,
            cfg.consent_ui.authority_id,
            cfg.consent_ui.provider_id,
            cfg.consent_ui.entrypoint,
            endpoint.get("error"),
        )
        return JSONResponse(
            status_code=500,
            content={"error": "consent_ui_unavailable", "error_description": str(endpoint.get("error") or "")},
        )

    bundle_id = str(endpoint.get("bundle_id") or endpoint.get("app_id") or "").strip()
    operation = str(endpoint.get("operation") or endpoint.get("alias") or "").strip()
    route = str(endpoint.get("route") or "public").strip() or "public"
    if not bundle_id or not operation:
        return JSONResponse(
            status_code=500,
            content={"error": "consent_ui_unavailable", "error_description": "bundle_id and operation are required"},
        )

    payload = _consent_payload(
        req=req,
        issuer=issuer,
        csrf_token=csrf_token,
        trusted=trusted,
        cfg=cfg,
        form_action=_consent_action(request),
        grantor_subject=grantor_subject,
        grantor_label=grantor_label,
        signout_action=_logout_action(request),
        return_to=_return_to(request),
        catalog_version=catalog_version,
        connected_accounts=connected_accounts,
        seeded_account_scope=seeded_account_scope,
        seeded_named_service_operations=seeded_named_service_operations,
        seeded_resource_operations=seeded_resource_operations,
        existing_card=existing_card,
    )
    try:
        result = await call_bundle_operation(
            bundle_id=bundle_id,
            operation=operation,
            route=route,
            http_method="POST",
            data=payload,
        )
    except Exception as exc:
        LOGGER.exception(
            "[connection-hub.oauth] consent_ui render failed bundle=%s route=%s operation=%s",
            bundle_id,
            route,
            operation,
        )
        return JSONResponse(
            status_code=500,
            content={"error": "consent_ui_render_failed", "error_description": str(exc)},
        )
    html = _extract_custom_consent_html(result, operation=operation)
    if not html:
        return JSONResponse(
            status_code=500,
            content={"error": "consent_ui_render_failed", "error_description": "renderer did not return html"},
        )
    declared = _extract_custom_consent_contract_version(result)
    if declared != CONSENT_CONTRACT_VERSION:
        LOGGER.error(
            "[connection-hub.oauth] consent_ui contract mismatch bundle=%s operation=%s "
            "declared=%s expected=%s",
            bundle_id, operation, declared or "<absent>", CONSENT_CONTRACT_VERSION,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "consent_ui_contract_mismatch",
                "error_description": (
                    f"consent renderer {bundle_id}:{operation} declares "
                    f"{declared or 'no'} contract version; this deployment serves "
                    f"{CONSENT_CONTRACT_VERSION}. Update the renderer to the current "
                    "consent view model, or remove `consent_ui` to use the built-in page."
                ),
            },
        )
    return HTMLResponse(html)


def _extract_custom_consent_contract_version(result: Any) -> str:
    if not isinstance(result, Mapping):
        return ""
    for key in ("consent_contract_version", "contract_version"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    contract = result.get("consent_contract")
    if isinstance(contract, Mapping):
        value = contract.get("version")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _selected_named_service_operations(
    form: Any,
    *,
    scopes: Iterable[str],
    cfg: OAuthDelegatedClientConfig,
    resource: str,
) -> Any:
    """The card-level selection a consent submission carries.

    ``"*"`` when every offered operation was chosen, otherwise the exact
    namespace map keyed by resource. Values outside what the page offered are
    dropped; an empty result is a deliberate empty selection.
    """
    offered = named_service_selection_rows(scopes, config=cfg, resource=resource)
    if str(form.get("named_service_operations_all") or "").strip():
        return "*"
    allowed = {(row["namespace"], row["operation"]) for row in offered}
    picked: dict[str, list[str]] = {}
    for raw in form.getlist("named_service_operations"):
        namespace, _, operation = str(raw or "").partition(":")
        key = (namespace.strip(), operation.strip())
        if key not in allowed:
            continue
        operations = picked.setdefault(key[0], [])
        if key[1] not in operations:
            operations.append(key[1])
    if not picked:
        return {}
    # Ticking every offered box is the same choice the "select all" control
    # makes, and the panel already encodes it that way. One user action must
    # not store two different things depending on which surface took it.
    chosen = {(namespace, op) for namespace, ops in picked.items() for op in ops}
    if allowed and chosen >= allowed:
        return "*"
    return {resource or "*": picked}


def _selected_resource_authority(
    form: Any,
    *,
    scopes: Iterable[str],
    cfg: OAuthDelegatedClientConfig,
    resource: str,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Validate owner-resource operation picks against the live consent offer."""
    rows = resource_selection_rows(scopes, config=cfg, resource=resource)
    allowed: dict[tuple[str, str], dict[str, Any]] = {}
    row_grants: dict[str, list[str]] = {}
    for row in rows:
        selector = str(row.get("resource") or "").strip()
        if not selector:
            continue
        row_grants[selector] = [str(grant) for grant in row.get("grants") or ()]
        for operation in row.get("operations") or ():
            name = str(operation.get("name") or "").strip()
            if name:
                allowed[(selector, name)] = operation

    selected_operations: dict[str, list[str]] = {}
    for raw in form.getlist("resource_operations"):
        try:
            value = json.loads(str(raw or ""))
        except (TypeError, ValueError) as exc:
            raise ValueError("resource operation selection is malformed") from exc
        if not isinstance(value, Mapping):
            raise ValueError("resource operation selection is malformed")
        selector = str(value.get("resource") or "").strip()
        operation = str(value.get("operation") or "").strip()
        if (selector, operation) not in allowed:
            raise ValueError("resource operation was not offered to this user")
        held = selected_operations.setdefault(selector, [])
        if operation not in held:
            held.append(operation)

    selected_grants = {
        selector: row_grants[selector]
        for selector in selected_operations
    }
    return selected_grants, selected_operations


def _direct_operation_authority(
    operations: Iterable[str],
    *,
    scopes: Iterable[str],
    cfg: OAuthDelegatedClientConfig,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Project resource-free compact selections onto one declared row each."""
    allowed_grants = {
        str(scope).strip() for scope in scopes if str(scope).strip()
    }
    row_by_operation: dict[str, list[tuple[str, list[str]]]] = {}
    for resource_cfg in cfg.resources:
        selector, _literal = resolve_declared_resource(
            cfg,
            resource_cfg.resource,
        )
        if not selector:
            continue
        for tool in resource_cfg.tools:
            grants = [str(grant) for grant in (tool.grants or resource_cfg.grants)]
            if grants and not set(grants).issubset(allowed_grants):
                continue
            row_by_operation.setdefault(tool.name, []).append((selector, grants))

    grants_by_resource: dict[str, list[str]] = {}
    operations_by_resource: dict[str, list[str]] = {}
    for operation in operations:
        matches = row_by_operation.get(str(operation), [])
        if len(matches) != 1:
            raise ValueError(
                "resource-free operation selection must identify one exact catalog row"
            )
        selector, grants = matches[0]
        grants_by_resource[selector] = grants
        selected = operations_by_resource.setdefault(selector, [])
        if str(operation) not in selected:
            selected.append(str(operation))
    return grants_by_resource, operations_by_resource


async def _active_catalog_document(request: Request) -> Any | None:
    resolvers = delegated_serving_resolvers(request)
    if resolvers is None:
        return None
    try:
        return await resolvers.catalog.resolve_active()
    except Exception:
        LOGGER.warning("[connection-hub.oauth] active catalog unreadable for consent", exc_info=True)
        return None


async def _active_catalog_version_for_consent(request: Request) -> str:
    document = await _active_catalog_document(request)
    return str(getattr(document, "version", "") or "")


async def _consent_config(
    request: Request,
    *,
    owner_subject: str = "",
) -> OAuthDelegatedClientConfig | None:
    """What the consent page may offer: the registered catalog, the same
    document the card writer decides against. ``None`` when no catalog can be
    established — a card cannot be written then either, so offering the
    descriptor would promise what token exchange refuses."""
    owner = str(owner_subject or "").strip()
    if owner:
        try:
            return await get_automation_access(request).oauth_consent_config(
                grantor_subject=owner
            )
        except (AutomationAccessUnavailable, CardUnavailable):
            LOGGER.warning(
                "[connection-hub.oauth] owner-scoped consent catalog unavailable subject=%s",
                owner,
                exc_info=True,
            )
            return None
        except Exception:
            LOGGER.exception(
                "[connection-hub.oauth] owner-scoped consent catalog failed subject=%s",
                owner,
            )
            return None
    document = await _active_catalog_document(request)
    if document is None:
        return None
    return oauth_delegated_config_from_connections(
        getattr(document, "connections", None) or {}
    )


def _catalog_unavailable_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": "delegated_catalog_unavailable",
            "error_description": (
                "The delegated catalog is not established, so this deployment "
                "cannot say what may be granted. Retry once it is published."
            ),
        },
    )


def _logout_action(request: Request) -> str:
    path = str(request.url.path or "").rstrip("/")
    if path.endswith("/authorize"):
        return f"{path.rsplit('/authorize', 1)[0]}/logout"
    return "/oauth/logout"


def _return_to(request: Request) -> str:
    out = request.url.path
    if request.url.query:
        out += "?" + request.url.query
    return out


def _user_label(user: Mapping[str, object]) -> str:
    for key in ("email", "name", "username", "sub", "user_id", "id"):
        value = user.get(key)
        if value:
            return str(value)
    return ""


_LABEL_SEPARATOR = " · "


def _asserted_client_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    asserted = metadata.get("client_metadata")
    return asserted if isinstance(asserted, Mapping) else {}


def _asserted_agent_id(asserted: Mapping[str, Any]) -> str:
    return str(
        asserted.get("kdcube_agent_id") or asserted.get("kdcube_worker_id") or ""
    ).strip()


def _asserted_worker_identity(asserted: Mapping[str, Any]) -> str:
    """``<provider>:<alias>:<session>`` for a client asserting the full worker shape, else "".

    The shape is what a KDCube host worker registers (``kdcube_worker_id``,
    ``kdcube_worker_alias``, ``kdcube_agent_provider`` and
    ``kdcube_agent_session_id``), for example every Problem Board worker. A
    client asserting only some of these keeps the general naming rule.
    """

    provider = str(asserted.get("kdcube_agent_provider") or "").strip()
    alias = str(asserted.get("kdcube_worker_alias") or "").strip()
    session = str(asserted.get("kdcube_agent_session_id") or "").strip()
    worker_id = str(asserted.get("kdcube_worker_id") or "").strip()
    if provider and alias and session and worker_id:
        return f"{provider}:{alias}:{session}"
    return ""


def _identity_forms(asserted: Mapping[str, Any]) -> frozenset[str]:
    """Every spelling of the asserted agent identity, lowercased for segment comparison."""

    forms = {_asserted_agent_id(asserted)}
    provider = str(asserted.get("kdcube_agent_provider") or "").strip()
    session = str(asserted.get("kdcube_agent_session_id") or "").strip()
    alias = str(asserted.get("kdcube_worker_alias") or "").strip()
    if provider and session:
        forms.add(f"{provider}:{session}")
        if alias:
            forms.add(f"{provider}:{alias}:{session}")
    return frozenset(form.lower() for form in forms if form)


def _label_segments(label: str) -> list[str]:
    """The label's segments with byte-identical repeats removed, order kept."""

    seen: set[str] = set()
    segments: list[str] = []
    for segment in (part.strip() for part in str(label or "").split(_LABEL_SEPARATOR.strip())):
        if segment and segment.lower() not in seen:
            seen.add(segment.lower())
            segments.append(segment)
    return segments


def _registered_name(metadata: Mapping[str, Any]) -> str:
    return str(
        metadata.get("client_name")
        or metadata.get("name")
        or metadata.get("client_uri")
        or ""
    ).strip()


def _door_alias(resource: str) -> str:
    door_path = str(resource or "").split("?", 1)[0].rstrip("*").rstrip("/")
    return door_path.rsplit("/mcp/", 1)[-1].strip("/") if "/mcp/" in door_path else ""


def _oauth_card_label(
    client_metadata: Mapping[str, Any] | None,
    *,
    resource: str,
    explicit: str = "",
) -> str:
    """The owner-visible card name: product, entry door and agent identity, each once.

    A client asserting the full KDCube worker shape is named
    ``<client product> · <entry door> · <provider>:<alias>:<session>``, so a
    first connect and a reconnect give the same name whatever its registered
    name spelled. Any other client keeps its registered name and gains the
    entry door and its asserted agent id only when no segment already equals
    them. On 2026-09-21 the alias and plain forms of one worker identity were
    compared as substrings, missed each other, and every worker Card carried
    its identity twice.
    """

    if str(explicit or "").strip():
        return str(explicit).strip()
    metadata = dict(client_metadata or {})
    asserted = _asserted_client_metadata(metadata)
    door = _door_alias(resource)
    segments = _label_segments(_registered_name(metadata))
    forms = _identity_forms(asserted)

    worker_identity = _asserted_worker_identity(asserted)
    if worker_identity:
        product = segments[0] if segments else ""
        if product.lower() in forms or (door and product.lower() == door.lower()):
            product = ""
        return _LABEL_SEPARATOR.join(part for part in (product, door, worker_identity) if part)

    present = {segment.lower() for segment in segments}
    if door and door.lower() not in present:
        segments.append(door)
        present.add(door.lower())
    agent_id = _asserted_agent_id(asserted)
    if agent_id and not (present & forms):
        segments.append(agent_id)
    label = _LABEL_SEPARATOR.join(segments)
    return label or str(metadata.get("client_id") or "Connected client")


def _legacy_card_label(metadata: Mapping[str, Any], door: str) -> str:
    """The name the rule before 2026-09-21 generated for this client at ``door``."""

    label = _registered_name(metadata)
    if door and door.lower() not in label.lower():
        label = f"{label}{_LABEL_SEPARATOR}{door}" if label else door
    agent_id = _asserted_agent_id(_asserted_client_metadata(metadata))
    if agent_id and agent_id.lower() not in label.lower():
        label = f"{label}{_LABEL_SEPARATOR}{agent_id}" if label else agent_id
    return label or str(metadata.get("client_id") or "Connected client")


def _generated_card_labels(metadata: Mapping[str, Any], *, resource: str) -> frozenset[str]:
    """Every name this service could have generated for this client, current or earlier rule.

    A Card may have been named at another entry door, so the earlier rule is
    replayed at the current door, at every door the registered name spells, and
    with no door.
    """

    doors = {_door_alias(resource), ""}
    doors.update(_label_segments(_registered_name(metadata)))
    labels = {_legacy_card_label(metadata, door) for door in doors}
    labels.add(_registered_name(metadata))
    labels.add(_oauth_card_label(metadata, resource=resource))
    return frozenset(label for label in labels if label)


def _consent_label(
    existing_label: str,
    derived_label: str,
    client_metadata: Mapping[str, Any] | None,
    *,
    resource: str,
) -> str:
    """The name the consent page proposes: the existing Card's, unless this service generated it.

    A reconnect edits an existing Card, and a name a person chose is theirs to
    keep. Only an exact match with a name this service generates, under the
    current rule or the earlier one, is replaced by the current derivation.
    """

    existing = str(existing_label or "").strip()
    if not existing:
        return derived_label
    if existing in _generated_card_labels(dict(client_metadata or {}), resource=resource):
        return derived_label
    return existing


def _error_response(err: AuthorizeError, issuer: str) -> Response:
    if err.redirectable and err.redirect_uri:
        url = build_redirect(
            err.redirect_uri,
            {"error": err.error, "error_description": err.error_description,
             "state": err.state, "iss": issuer},
        )
        return RedirectResponse(url, status_code=302)
    return JSONResponse(
        status_code=400,
        content={"error": err.error, "error_description": err.error_description},
    )


async def _require_user(request: Request) -> Tuple[Optional[dict], Optional[Response]]:
    token = extract_bearer(request)
    if not token:
        return None, JSONResponse(status_code=401, content={"error": "login_required"})
    user = await get_authenticate(request)(token)
    if not user:
        return None, JSONResponse(status_code=401, content={"error": "login_required"})
    return user, None


def _user_subject(user: Mapping[str, object]) -> str:
    """Return the platform subject used as the grantor for delegated credentials."""
    for key in ("user_id", "sub", "id"):
        value = user.get(key)
        if value:
            return str(value)
    return ""


def _as_set(value: Iterable[str] | None) -> set[str]:
    return {str(item).strip() for item in (value or []) if str(item).strip()}


async def _platform_grant_inventory(
    user: Mapping[str, object],
    scopes: Iterable[str],
    *,
    cfg: OAuthDelegatedClientConfig,
    resource: str | None = None,
) -> AuthorityGrantInventory:
    provider = PlatformAuthorityInventoryProvider(cfg.capabilities)
    return await provider.list_delegable_grants(
        platform_identity_from_user(user),
        requested_grants=scopes,
        context={"resource": resource or ""},
    )


def _profile_marker_grants(cfg: OAuthDelegatedClientConfig, scopes: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """The real grants each requested authorization-profile scope stands for (W272).

    A profile scope such as ``work:profile:worker`` is a request-only selector:
    it names a set of catalog operations, never a grant a person holds. The
    delegation check therefore judges the grants those operations require,
    and a whole-card request (no resource) expands the profile on every
    resource that declares it.
    """
    profile_scopes = getattr(cfg, "authorization_profile_scopes", None)
    requested = _as_set(scopes)
    markers = requested & set(profile_scopes() if callable(profile_scopes) else ())
    expanded: dict[str, list[str]] = {}
    for resource_cfg in getattr(cfg, "resources", ()) or ():
        for profile in getattr(resource_cfg, "authorization_profiles", ()) or ():
            if profile.scope not in markers:
                continue
            operations = set(profile.operations or ())
            tools = tuple(getattr(resource_cfg, "tools", ()) or ())
            selected = tools if "*" in operations else tuple(tool for tool in tools if tool.name in operations)
            bucket = expanded.setdefault(profile.scope, [])
            for tool in selected:
                for grant in tool.grants or ():
                    if str(grant).strip() and str(grant) not in bucket:
                        bucket.append(str(grant))
    return {marker: tuple(expanded.get(marker, ())) for marker in markers}


def _delegation_grants(cfg: OAuthDelegatedClientConfig, scopes: Iterable[str]) -> list[str]:
    """Requested scopes with every profile selector replaced by its real grants."""
    markers = _profile_marker_grants(cfg, scopes)
    out: list[str] = []
    for scope in scopes or ():
        for grant in markers.get(str(scope), (str(scope),)):
            if grant not in out:
                out.append(grant)
    return out


def _visible_scopes(cfg: OAuthDelegatedClientConfig, scopes: Iterable[str], inventory: AuthorityGrantInventory) -> list[str]:
    """Requested scopes the user may delegate; a profile is visible when all its grants are."""
    available = set(inventory.grant_names())
    markers = _profile_marker_grants(cfg, scopes)
    visible: list[str] = []
    for scope in scopes or ():
        scope = str(scope)
        if scope in markers:
            grants = markers[scope]
            if grants and set(grants) <= available:
                visible.append(scope)
        elif scope in available:
            visible.append(scope)
    return visible


def _delegation_denial(scopes: Iterable[str], inventory: AuthorityGrantInventory, *, resource: str | None = None) -> JSONResponse | None:
    available = set(inventory.grant_names())
    denied: list[str] = []
    for scope in scopes or ():
        if str(scope) not in available:
            denied.append(str(scope))
    if not denied:
        return None
    return JSONResponse(
        status_code=403,
        content={
            "error": "forbidden",
            "error_description": "user is not allowed to delegate the requested grant(s)",
            "grants": denied,
            "resource": resource or "",
        },
    )


def _log_inventory(
    *,
    stage: str,
    user: Mapping[str, object],
    scopes: Iterable[str],
    inventory: AuthorityGrantInventory,
    resource: str | None = None,
) -> None:
    LOGGER.info(
        "[connection-hub.oauth] %s grant_inventory subject=%s user_id=%s roles=%s permissions=%s requested=%s available=%s resource=%s",
        stage,
        user.get("sub") or "",
        user.get("user_id") or "",
        sorted(_as_set(user.get("roles") if isinstance(user, Mapping) else ())),
        sorted(_as_set(user.get("permissions") if isinstance(user, Mapping) else ())),
        sorted(_as_set(scopes)),
        sorted(inventory.grant_names()),
        resource or "",
    )


def _grantor_authority(
    user: Mapping[str, object],
    *,
    scopes: Iterable[str],
    inventory: AuthorityGrantInventory,
) -> dict[str, object]:
    """Authority facts captured at consent time for later token exchange.

    The OAuth code is exchanged outside the browser consent request, so the
    grantor's role/permission facts must be carried through the code/refresh
    record rather than re-read from a browser session at /oauth/token.
    """

    roles = sorted(_as_set(user.get("roles") if isinstance(user, Mapping) else ()))
    edge = selected_delegation_edge(
        inventory,
        scopes,
        economics_budget_bypass=bool(is_admin(set(roles))),
    )
    edges = [edge.to_dict()] if edge is not None else []
    permissions = sorted(set(edge.permissions if edge is not None else ()))
    out: dict[str, object] = {}
    out["schema"] = "connection_hub.grantor_authority.v1"
    if roles:
        out["grantor_roles"] = roles
    if permissions:
        out["grantor_permissions"] = permissions
    if edges:
        out["delegation_edges"] = edges
    out["economics_budget_bypass"] = bool(is_admin(set(roles)))
    return out


def _request_with_scopes(req: AuthorizeRequest, scopes: Iterable[str]) -> AuthorizeRequest:
    return AuthorizeRequest(
        client_id=req.client_id,
        redirect_uri=req.redirect_uri,
        response_type=req.response_type,
        scopes=[str(scope) for scope in scopes if str(scope).strip()],
        state=req.state,
        code_challenge=req.code_challenge,
        code_challenge_method=req.code_challenge_method,
        resource=req.resource,
        client=req.client,
    )


def _request_state_value(request: Request, name: str) -> Any:
    request_state = getattr(request, "state", None)
    value = getattr(request_state, name, None) if request_state is not None else None
    if value is not None:
        return value
    return getattr(getattr(request.app, "state", None), name, None)


async def _dynamic_client_resolver(request: Request, client_id: Optional[str]):
    """Resolve the request's non-pre-registered client before pure validation."""
    if not client_id:
        return None
    if get_client(client_id, request) is not None:
        return None
    cfg = oauth_delegated_config(request)
    if is_client_metadata_id(client_id):
        if not cfg.client_id_metadata_documents.enabled:
            return None
        client = await resolve_client_metadata_document(
            client_id,
            config=cfg.client_id_metadata_documents,
            store=get_grant_store(request),
            fetcher=_request_state_value(request, "oauth_client_metadata_fetcher"),
        )
        return lambda cid: client if cid == client_id else None
    if not cfg.dynamic_client_registration.enabled:
        return None
    record = await get_grant_store(request).get_client_record(client_id)
    if record is None:
        tenant, project = oauth_tenant_project(request)
        LOGGER.warning(
            "[connection-hub.oauth] dynamic_client_missing tenant=%s project=%s client_id=%s",
            tenant,
            project,
            client_id,
        )
        return None
    client = client_from_record(record)
    return lambda cid: client if cid == client_id else None


def _client_metadata_error_response(error: ClientMetadataError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"error": error.code, "error_description": error.description},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/oauth/register", include_in_schema=False)
@_normalize_grant_store_unavailable
async def register_client(request: Request) -> Response:
    cfg = oauth_delegated_config(request)
    if not cfg.dynamic_client_registration.enabled:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found"},
        )
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, Mapping):
        body = {}
    server_assigned = {
        "client_id",
        "client_secret",
        "client_secret_expires_at",
        "registration_access_token",
        "registration_client_uri",
    }
    if server_assigned.intersection(body):
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_client_metadata",
                "error_description": "client metadata contains a server-assigned field",
            },
        )
    try:
        asserted_metadata = normalize_public_client_metadata(body)
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_client_metadata",
                "error_description": "client metadata is too large, unsupported, or sensitive",
            },
        )
    application_type = str(
        body.get("application_type")
        or cfg.dynamic_client_registration.default_application_type
        or "native"
    ).strip().lower()
    if application_type not in {"native", "web"}:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_client_metadata",
                "error_description": "application_type must be 'native' or 'web'",
            },
        )
    token_auth_method = str(body.get("token_endpoint_auth_method") or "none").strip()
    if token_auth_method != "none":
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_client_metadata",
                "error_description": "only public clients using token_endpoint_auth_method 'none' are supported",
            },
        )
    requested_grant_types = body.get("grant_types")
    if requested_grant_types is None:
        requested_grant_types = ["authorization_code", "refresh_token"]
    supported_grant_types = {
        "authorization_code",
        "refresh_token",
        DEVICE_GRANT_TYPE,
    }
    if (
        not isinstance(requested_grant_types, list)
        or not requested_grant_types
        or any(
            not isinstance(value, str) or value not in supported_grant_types
            for value in requested_grant_types
        )
    ):
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_client_metadata",
                "error_description": "grant_types contains an unsupported grant",
            },
        )
    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_redirect_uri", "error_description": "redirect_uris is required"},
        )
    # DCR is open (pre-auth), so restrict registrable redirects to the trusted set
    # (claude.ai callback + loopback) — an attacker cannot register a client that
    # delivers a stolen code to their own server.
    if not all(
        isinstance(uri, str)
        and dcr_redirect_allowed(uri, request, application_type=application_type)
        for uri in redirect_uris
    ):
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_redirect_uri",
                "error_description": "redirect_uri not permitted for dynamic registration",
            },
        )
    issuer = resolve_issuer(request)
    logo_uri = kdcube_icon_url(request=request, public_base_url=issuer)
    client_uri = kdcube_website_url(request=request, public_base_url=issuer)
    metadata = {
        "client_name": body.get("client_name"),
        "logo_uri": logo_uri,
        "client_uri": client_uri,
        "client_metadata": asserted_metadata,
    }
    # What the client ACTUALLY sends at registration decides the card's default
    # name. A client that registers one fixed name for every connector it opens
    # (Claude Code always sends "Claude") cannot be told apart by name alone -
    # log the raw body keys so that is verifiable, not assumed.
    LOGGER.info(
        "[connection_hub.oauth] dcr_register client_name=%r software_id=%r "
        "application_type=%s body_keys=%s redirect_uris=%s",
        body.get("client_name"), body.get("software_id"),
        application_type,
        sorted(str(k) for k in body.keys()), redirect_uris,
    )
    record = await get_grant_store(request).register_client(
        redirect_uris=redirect_uris,
        grant_types=list(dict.fromkeys(requested_grant_types)),
        application_type=application_type,
        metadata=metadata,
    )
    content = dict(asserted_metadata)
    content.update({
        "client_id": record["client_id"],
        "redirect_uris": record["redirect_uris"],
        "token_endpoint_auth_method": "none",
        "application_type": application_type,
        "grant_types": list(dict.fromkeys(requested_grant_types)),
        "response_types": ["code"],
        "client_name": body.get("client_name"),
    })
    if logo_uri:
        content["logo_uri"] = logo_uri
    if client_uri:
        content["client_uri"] = client_uri
    return JSONResponse(status_code=201, content=content)


@router.post("/oauth/device_authorization", include_in_schema=False)
@_normalize_grant_store_unavailable
async def device_authorization(request: Request) -> Response:
    """Create one short-lived RFC 8628 authorization request."""

    issuer = resolve_issuer(request)
    form = await request.form()
    client_id = str(form.get("client_id") or "").strip()
    resource = str(form.get("resource") or "").strip()
    scope = str(form.get("scope") or "").strip()
    requested_access_id = str(form.get("access_id") or "").strip()
    if len(requested_access_id) > 256 or any(
        ord(character) < 0x20 or ord(character) == 0x7F
        for character in requested_access_id
    ):
        return device_oauth_error(
            "invalid_request",
            "access_id is invalid",
        )
    expected_card_revision: int | None = None
    raw_revision = str(form.get("expected_card_revision") or "").strip()
    if raw_revision:
        try:
            expected_card_revision = int(raw_revision)
        except ValueError:
            expected_card_revision = 0
        if expected_card_revision < 1:
            return device_oauth_error(
                "invalid_request",
                "expected_card_revision must be a positive integer",
            )

    cfg = await _consent_config(request)
    if cfg is None:
        return device_oauth_error(
            "temporarily_unavailable",
            "the delegated catalog is unavailable",
            status=503,
        )
    try:
        resolver = await _dynamic_client_resolver(request, client_id)
    except ClientMetadataError as error:
        return device_oauth_error(error.code, error.description, status=error.status_code)
    client = get_client(client_id, request)
    if client is None and resolver is not None:
        client = resolver(client_id)
    if client is None or not client.redirect_uris:
        return device_oauth_error("invalid_client", "unknown client_id")
    if DEVICE_GRANT_TYPE not in client.grant_types:
        return device_oauth_error(
            "unauthorized_client",
            "client_id is not registered for device authorization",
        )
    authorize_params = {
        "client_id": client_id,
        # Device authorization never redirects here. Keeping the registered
        # URI in the common request shape lets the existing consent validator
        # enforce the same client snapshot and scopes.
        "redirect_uri": client.redirect_uris[0],
        "response_type": "code",
        "scope": scope,
        "resource": resource,
        "state": secrets.token_urlsafe(24),
        "code_challenge": secrets.token_urlsafe(32),
        "code_challenge_method": "S256",
    }
    try:
        parsed = parse_authorize_request(
            authorize_params,
            client_resolver=resolver,
            public_client_resolver=lambda cid: get_client(cid, request),
            supported_scopes=cfg.supported_scopes(resource),
        )
    except AuthorizeError as error:
        return device_oauth_error(
            error.error,
            error.error_description or "the device authorization request is invalid",
        )

    catalog_version = await _active_catalog_version_for_consent(request)
    if not catalog_version:
        return device_oauth_error(
            "temporarily_unavailable",
            "the delegated catalog is unavailable",
            status=503,
        )
    issued = await get_device_grant_store(request).create(
        client_id=parsed.client_id,
        scopes=parsed.scopes,
        resource=parsed.resource or "",
        client_metadata=(parsed.client.snapshot() if parsed.client is not None else {}),
        requested_access_id=requested_access_id,
        expected_card_revision=expected_card_revision,
        context={
            "authorize_params": authorize_params,
            "client_metadata_digest": (
                parsed.client.snapshot_digest() if parsed.client is not None else ""
            ),
            "catalog_version": catalog_version,
        },
    )
    return JSONResponse(
        {
            "device_code": issued.device_code,
            "user_code": issued.user_code,
            "verification_uri": verification_uri(issuer),
            "verification_uri_complete": verification_uri_complete(
                issuer, issued.user_code
            ),
            "expires_in": issued.expires_in,
            "interval": issued.interval,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get("/oauth/device", include_in_schema=False)
@_normalize_grant_store_unavailable
async def verify_device(request: Request) -> Response:
    issuer = resolve_issuer(request)
    user_code = str(request.query_params.get("user_code") or "").strip()
    if not user_code:
        return device_verification_page(issuer)

    user, denied = await _require_user(request)
    if denied is not None:
        if getattr(denied, "status_code", None) == 401:
            return_to = _return_to(request)
            return RedirectResponse(
                f"{sign_in_bounce_path()}?next={quote(return_to, safe='')}",
                status_code=302,
            )
        return denied
    subject = _user_subject(user or {})
    if not subject:
        return JSONResponse(status_code=401, content={"error": "login_required"})

    found = await get_device_grant_store(request).read_user_code(
        user_code,
        attempt_key=subject,
    )
    if found.status == "rate_limited":
        return device_verification_page(
            issuer,
            user_code=user_code,
            error="Too many incorrect codes. Try again later.",
            status=429,
        )
    if found.status != "found" or found.request is None:
        return device_verification_page(
            issuer,
            user_code=user_code,
            error="This code is invalid or expired.",
            status=400,
        )
    device_request = found.request
    context = device_request.get("context")
    if not isinstance(context, Mapping):
        return device_verification_page(
            issuer,
            error="This authorization request is unavailable.",
            status=400,
        )
    authorize_params = context.get("authorize_params")
    if not isinstance(authorize_params, Mapping):
        return device_verification_page(
            issuer,
            error="This authorization request is unavailable.",
            status=400,
        )
    widget_base = _connection_hub_widget_base(request)
    owner_cfg = await _consent_config(request, owner_subject=subject)
    if owner_cfg is None or owner_cfg.consent_ui.mode != "connection_hub" or not widget_base:
        return device_verification_page(
            issuer,
            error="The authorization editor is unavailable.",
            status=503,
        )
    draft = await get_grant_store(request).create_consent_draft(
        subject,
        context={
            "schema": _CONSENT_DRAFT_SCHEMA,
            "authorize_params": dict(authorize_params),
            "client_id": str(device_request.get("client_id") or ""),
            "client_metadata_digest": str(
                context.get("client_metadata_digest") or ""
            ),
            "catalog_version": str(context.get("catalog_version") or ""),
            "device_authorization": {
                "schema": DEVICE_CONSENT_SCHEMA,
                "device_digest": found.device_digest,
                "user_digest": found.user_digest,
                "user_code": user_code,
                "requested_access_id": str(
                    device_request.get("requested_access_id") or ""
                ),
                "expected_card_revision": device_request.get(
                    "expected_card_revision"
                ),
            },
        },
    )
    separator = "&" if "?" in widget_base else "?"
    return RedirectResponse(
        f"{widget_base}{separator}"
        + urlencode({"tab": "delegatedAccess", "oauth_consent": draft}),
        status_code=302,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/oauth/device/complete", include_in_schema=False)
async def device_complete(request: Request) -> Response:
    return device_completion_page(
        approved=str(request.query_params.get("result") or "") == "approved"
    )


@router.get("/oauth/authorize", include_in_schema=False)
@_normalize_grant_store_unavailable
async def authorize(request: Request) -> Response:
    issuer = resolve_issuer(request)
    params = dict(request.query_params)
    # The page offers from the registered catalog, the same document the card
    # writer decides against.
    cfg = await _consent_config(request)
    if cfg is None:
        return _catalog_unavailable_response()
    try:
        resolver = await _dynamic_client_resolver(request, params.get("client_id"))
    except ClientMetadataError as error:
        LOGGER.warning(
            "[connection-hub.oauth] client_metadata_rejected client_id=%s error=%s",
            str(params.get("client_id") or ""),
            error.code,
        )
        return _client_metadata_error_response(error)
    try:
        req = parse_authorize_request(
            params,
            client_resolver=resolver,
            public_client_resolver=lambda cid: get_client(cid, request),
            supported_scopes=cfg.supported_scopes(params.get("resource")),
        )
    except AuthorizeError as err:
        return _error_response(err, issuer)

    user, denied = await _require_user(request)
    if denied is not None:
        # Browser entry point: on a missing session, send the user to the platform
        # login with a return-to (url-encoded so the multi-param authorize URL
        # survives) instead of a dead-end JSON 401. Authenticated denials still
        # return their JSON payload.
        if getattr(denied, "status_code", None) == 401:
            return_to = _return_to(request)
            return RedirectResponse(f"{sign_in_bounce_path()}?next={quote(return_to, safe='')}", status_code=302)
        return denied

    subject = _user_subject(user or {})
    if not subject:
        return JSONResponse(status_code=401, content={"error": "login_required"})
    owner_cfg = await _consent_config(request, owner_subject=subject)
    if owner_cfg is None:
        return _catalog_unavailable_response()
    try:
        req = parse_authorize_request(
            params,
            client_resolver=resolver,
            public_client_resolver=lambda cid: get_client(cid, request),
            supported_scopes=owner_cfg.supported_scopes(params.get("resource")),
        )
    except AuthorizeError as err:
        return _error_response(err, issuer)
    cfg = owner_cfg

    delegation_grants = _delegation_grants(cfg, req.scopes)
    inventory = await _platform_grant_inventory(user or {}, delegation_grants, cfg=cfg, resource=req.resource)
    _log_inventory(stage="authorize", user=user or {}, scopes=req.scopes, inventory=inventory, resource=req.resource)
    visible_scopes = _visible_scopes(cfg, req.scopes, inventory)
    if not visible_scopes:
        delegation_denied = _delegation_denial(delegation_grants, inventory, resource=req.resource)
        if delegation_denied is not None:
            return delegation_denied
        return JSONResponse(
            status_code=403,
            content={
                "error": "forbidden",
                "error_description": "user is not allowed to delegate grants for this resource",
                "resource": req.resource or "",
            },
        )
    render_req = _request_with_scopes(req, visible_scopes)

    delegation_denied = _delegation_denial(_delegation_grants(cfg, visible_scopes), inventory, resource=req.resource)
    if delegation_denied is not None:
        return delegation_denied

    # trusted = a statically pre-registered client (not a dynamically-registered one),
    # so the consent screen can flag unknown clients for anti-phishing.
    trusted = bool(
        req.client is not None
        and req.client.registration_kind == CLIENT_REGISTRATION_PRE_REGISTERED
    )
    # The view model is built before a renderer is chosen: both renderers see
    # the same facts, and a custom one changes presentation only.
    connected_accounts = await _connected_accounts_for_consent(subject)
    seed_client_metadata = req.client.snapshot() if req.client is not None else {}
    seeded_account_scope = await _seed_account_scope_for_consent(
        request,
        subject=subject,
        client_id=req.client_id,
        resource=req.resource,
        client_metadata=seed_client_metadata,
        cfg=cfg,
    )
    seeded_named_service_operations = await _seed_named_service_operations_for_consent(
        request,
        subject=subject,
        client_id=req.client_id,
        resource=req.resource,
        client_metadata=seed_client_metadata,
    )
    seeded_resource_operations = await _seed_resource_operations_for_consent(
        request,
        subject=subject,
        client_id=req.client_id,
        resource=req.resource,
        client_metadata=seed_client_metadata,
    )
    catalog_version = await _active_catalog_version_for_consent(request)
    widget_base = _connection_hub_widget_base(request)
    if cfg.consent_ui.mode == "connection_hub" and widget_base:
        draft = await get_grant_store(request).create_consent_draft(
            subject,
            context={
                "schema": _CONSENT_DRAFT_SCHEMA,
                "authorize_params": {
                    "client_id": render_req.client_id,
                    "redirect_uri": render_req.redirect_uri,
                    "response_type": render_req.response_type,
                    "scope": " ".join(render_req.scopes),
                    "resource": render_req.resource or "",
                    "state": render_req.state or "",
                    "code_challenge": render_req.code_challenge,
                    "code_challenge_method": render_req.code_challenge_method,
                },
                "client_id": render_req.client_id,
                "client_metadata_digest": (
                    render_req.client.snapshot_digest()
                    if render_req.client is not None
                    else ""
                ),
                "catalog_version": catalog_version,
            },
        )
        separator = "&" if "?" in widget_base else "?"
        location = (
            f"{widget_base}{separator}"
            + urlencode({"tab": "delegatedAccess", "oauth_consent": draft})
        )
        LOGGER.info(
            "[connection-hub.oauth] authorize card_editor_handoff "
            "subject=%s client_id=%s resource=%s",
            subject,
            render_req.client_id,
            render_req.resource or "",
        )
        return RedirectResponse(
            location,
            status_code=302,
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Referrer-Policy": "no-referrer",
            },
        )

    compact_seed = await get_automation_access(request).oauth_consent_card_seed(
        grantor_subject=subject,
        client_id=req.client_id,
        resource=req.resource,
        client_metadata=seed_client_metadata,
    )
    if (
        compact_seed.get("ok") is not True
        and compact_seed.get("error") != "oauth_consent_identity_incomplete"
    ):
        return JSONResponse(
            status_code=int(compact_seed.get("status") or 503),
            content=compact_seed,
        )
    # A first classic OAuth consent may omit RFC 8707 ``resource`` without
    # carrying the full-Card client hint. It has no Card identity to seed yet.
    # Every storage failure and identity conflict above remains fail-closed;
    # an existing intentionally-empty Card still returns ``access={...}``.
    existing_card = isinstance(compact_seed.get("access"), Mapping)

    # Non-hub and headless hosts keep the compact renderer. Its synchronizer
    # token is single-use and bound to this user and client snapshot.
    csrf_context = {
        "client_id": req.client_id,
        "client_metadata_digest": (
            req.client.snapshot_digest() if req.client is not None else ""
        ),
    }
    csrf = await get_grant_store(request).create_csrf_token(
        subject,
        context=csrf_context,
    )
    LOGGER.info(
        "[connection-hub.oauth] authorize csrf_minted subject=%s client_id=%s resource=%s",
        subject,
        req.client_id,
        req.resource or "",
    )
    custom = await _render_custom_consent_if_configured(
        request,
        req=render_req,
        issuer=issuer,
        csrf_token=csrf,
        trusted=trusted,
        cfg=cfg,
        grantor_subject=_user_subject(user or {}),
        grantor_label=_user_label(user or {}),
        catalog_version=catalog_version,
        connected_accounts=connected_accounts,
        seeded_account_scope=seeded_account_scope,
        seeded_named_service_operations=seeded_named_service_operations,
        seeded_resource_operations=seeded_resource_operations,
        existing_card=existing_card,
    )
    if custom is not None:
        return custom
    accounts_needed = _accounts_needed_for_consent(request, render_req.scopes, connected_accounts, cfg=cfg)
    return HTMLResponse(
        render_consent_html(
            render_req,
            issuer,
            connection_hub_url=_connection_hub_widget_base(request),
            csrf_token=csrf,
            trusted=trusted,
            brand=cfg.brand,
            form_action=_consent_action(request),
            config=cfg,
            grantor_subject=_user_subject(user or {}),
            grantor_label=_user_label(user or {}),
            signout_action=_logout_action(request),
            return_to=_return_to(request),
            connected_accounts=connected_accounts,
            seeded_account_scope=seeded_account_scope,
            seeded_named_service_operations=seeded_named_service_operations,
            seeded_resource_operations=seeded_resource_operations,
            accounts_needed=accounts_needed,
            catalog_version=catalog_version,
            existing_card=existing_card,
        )
    )


def _connection_hub_widget_base(request: Any) -> str:
    """Absolute base URL of the Connection Hub widget (no query), so the
    consent page can deep-link the hub (connect more accounts; this client's
    card after approval) instead of dead-ending. Empty when the deployment's
    public base is unknown - the page then simply renders no hub links."""
    try:
        from urllib.parse import quote

        from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.connection_edges import (
            DEFAULT_CONNECTION_HUB_BUNDLE_ID,
        )
        from connection_hub.delegated_to_kdcube.public_base import (
            connection_hub_public_base_url,
        )

        base = connection_hub_public_base_url()
        tenant, project = oauth_tenant_project(request)
        if not base or not tenant or not project:
            return ""
        return (
            f"{base}/api/integrations/bundles/"
            f"{quote(str(tenant), safe='')}/{quote(str(project), safe='')}/"
            f"{quote(DEFAULT_CONNECTION_HUB_BUNDLE_ID, safe='')}/widgets/connections_settings"
        )
    except Exception:
        return ""


async def _connected_accounts_for_consent(subject: str) -> list[dict]:
    """The consenting user's connected provider accounts, for the consent
    screen's per-account picker. Best-effort: an empty list renders the
    text fallback, never an error."""
    try:
        from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_to_kdcube.store import (
            DelegatedToKdcubeStore,
        )

        accounts = await DelegatedToKdcubeStore(user_id=subject).list_accounts()
    except Exception:
        LOGGER.exception("[connection-hub.oauth] consent account listing failed")
        return []
    out: list[dict] = []
    for item in accounts or []:
        try:
            out.append(
                {
                    "provider_id": str(item.provider_id or ""),
                    "account_id": str(item.account_id or ""),
                    "label": str(
                        getattr(item, "display_name", "")
                        or getattr(item, "external_subject", "")
                        or item.account_id
                    ),
                    "workspace": str(getattr(item, "workspace", "") or ""),
                    "claims": [str(c) for c in (item.claims or ()) if str(c or "").strip()],
                }
            )
        except Exception:
            continue
    return [entry for entry in out if entry["provider_id"] and entry["account_id"]]


def _accounts_needed_for_consent(request, scopes, connected_accounts, *, cfg=None):
    """Which provider accounts the requested scope needs, for the consent
    page's 'Accounts this connection needs' panel. Best-effort: returns None on
    any failure or when the delegated-to config is unavailable (the panel then
    simply does not render), never an error."""
    try:
        from connection_hub.delegated_credentials.oauth.account_requirements import (
            accounts_needed_for_scopes,
        )
        from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_to_kdcube.preflight import (
            _connection_hub_widget_url,
        )

        config = getattr(getattr(request, "state", None), "connections_delegated_config", None)
        if config is None:
            return None
        tenant, project = oauth_tenant_project(request)

        def _connect_url(provider_id: str, connector_app_id: str, claims) -> str:
            return _connection_hub_widget_url(
                tenant=tenant,
                project=project,
                provider_id=provider_id,
                connector_app_id=connector_app_id,
                claims=claims,
            )

        # Door claims (mail:read) whose backing provider claim differs from the
        # requested token, from the OAuth capability declarations.
        door_claim_providers: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        if cfg is not None:
            for grant, requirements in (cfg.connected_account_requirements() or {}).items():
                door_claim_providers[grant] = [
                    (req.provider_id, tuple(req.claims)) for req in requirements
                ]

        return accounts_needed_for_scopes(
            scopes,
            config=config,
            connected_accounts=connected_accounts,
            connect_url_builder=_connect_url,
            door_claim_providers=door_claim_providers or None,
        )
    except Exception:
        LOGGER.exception("[connection-hub.oauth] accounts-needed resolution failed")
        return None


async def _seed_account_scope_for_consent(
    request,
    *,
    subject: str,
    client_id: str,
    resource: str,
    client_metadata: Mapping[str, Any],
    cfg,
) -> dict:
    """Pre-check the picker from this exact client's existing Card."""
    try:
        service = get_automation_access(request)
        return await service.oauth_seed_account_scope(
            grantor_subject=subject,
            client_id=client_id,
            resource=str(resource or ""),
            client_metadata=client_metadata,
        )
    except Exception:
        LOGGER.exception("[connection-hub.oauth] consent account-scope seed failed")
        return {}


async def _seed_named_service_operations_for_consent(
    request,
    *,
    subject: str,
    client_id: str,
    resource: str,
    client_metadata: Mapping[str, Any],
) -> dict:
    """Pre-check the operations picker from the client's existing card.

    Without it a re-consent renders an empty picker while a submission
    replaces, so leaving the section alone silently drops every operation the
    card holds.
    """
    try:
        service = get_automation_access(request)
        return await service.oauth_seed_named_service_operations(
            grantor_subject=subject,
            client_id=client_id,
            resource=str(resource or ""),
            client_metadata=client_metadata,
        )
    except Exception:
        LOGGER.exception("[connection-hub.oauth] consent named-service seed failed")
        return {}


async def _seed_resource_operations_for_consent(
    request,
    *,
    subject: str,
    client_id: str,
    resource: str,
    client_metadata: Mapping[str, Any],
) -> dict[str, list[str]]:
    """Pre-check owner-resource operations held by this OAuth client."""
    try:
        service = get_automation_access(request)
        return await service.oauth_seed_resource_operations(
            grantor_subject=subject,
            client_id=client_id,
            resource=str(resource or ""),
            client_metadata=client_metadata,
        )
    except Exception:
        LOGGER.exception("[connection-hub.oauth] consent resource-operation seed failed")
        return {}


async def _request_from_consent_draft(
    request: Request,
    *,
    subject: str,
    context: Mapping[str, Any],
) -> tuple[AuthorizeRequest | None, OAuthDelegatedClientConfig | None, Response | None]:
    if str(context.get("schema") or "") != _CONSENT_DRAFT_SCHEMA:
        return None, None, JSONResponse(
            status_code=400,
            content={"error": "oauth_consent_draft_invalid"},
        )
    params = context.get("authorize_params")
    if not isinstance(params, Mapping):
        return None, None, JSONResponse(
            status_code=400,
            content={"error": "oauth_consent_draft_invalid"},
        )
    cfg = await _consent_config(request, owner_subject=subject)
    if cfg is None:
        return None, None, _catalog_unavailable_response()
    try:
        resolver = await _dynamic_client_resolver(
            request,
            str(params.get("client_id") or ""),
        )
        req = parse_authorize_request(
            dict(params),
            client_resolver=resolver,
            public_client_resolver=lambda cid: get_client(cid, request),
            supported_scopes=cfg.supported_scopes(str(params.get("resource") or "")),
        )
    except ClientMetadataError as error:
        return None, None, _client_metadata_error_response(error)
    except AuthorizeError as error:
        return None, None, _error_response(error, resolve_issuer(request))

    expected_client = str(context.get("client_id") or "")
    expected_digest = str(context.get("client_metadata_digest") or "")
    current_digest = req.client.snapshot_digest() if req.client is not None else ""
    if (
        req.client_id != expected_client
        or not expected_digest
        or current_digest != expected_digest
    ):
        return None, None, _client_metadata_error_response(ClientMetadataError(
            "invalid_client_metadata",
            "client metadata changed after authorization began; restart authorization",
        ))
    active_catalog_version = await _active_catalog_version_for_consent(request)
    expected_catalog_version = str(context.get("catalog_version") or "")
    if (
        not active_catalog_version
        or active_catalog_version != expected_catalog_version
    ):
        return None, None, JSONResponse(
            status_code=409,
            content={
                "error": "consent_catalog_changed",
                "error_description": "the service catalog changed; restart authorization",
                "expected_catalog_version": expected_catalog_version,
                "active_catalog_version": active_catalog_version,
            },
        )
    return req, cfg, None


def _account_requirements_payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {"providers": [], "choices": [], "unresolved_claims": [], "has_gap": False}
    return {
        "providers": [
            {
                "provider_id": item.provider_id,
                "provider_label": item.provider_label,
                "connector_app_id": item.connector_app_id,
                "needed_claims": list(item.needed_claims),
                "satisfied_claims": list(item.satisfied_claims),
                "missing_claims": list(item.missing_claims),
                "status": item.status(),
                "connect_url": item.connect_url,
                "accounts": [
                    {
                        "account_id": account.account_id,
                        "label": account.label,
                        "held_claims": list(account.held_claims),
                    }
                    for account in item.accounts
                ],
            }
            for item in value.providers
        ],
        "choices": [
            {
                "label": choice.label,
                "options": [
                    {
                        "provider_id": option.provider_id,
                        "provider_label": option.provider_label,
                        "connector_app_id": option.connector_app_id,
                        "claims": list(option.claims),
                        "connected": bool(option.connected),
                        "connect_url": option.connect_url,
                    }
                    for option in choice.options
                ],
            }
            for choice in value.choices
        ],
        "unresolved_claims": list(value.unresolved_claims),
        "has_gap": bool(value.has_gap),
    }


def _consent_draft_error(reason: str) -> JSONResponse:
    status = 410 if reason in {"missing", "not_found"} else 403
    return JSONResponse(
        status_code=status,
        content={
            "error": "oauth_consent_draft_unavailable",
            "reason": reason,
            "error_description": "This authorization review expired or is not available to this account.",
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get("/oauth/authorize/consent/draft", include_in_schema=False)
@_normalize_grant_store_unavailable
async def authorize_consent_draft(request: Request) -> Response:
    user, denied = await _require_user(request)
    if denied is not None:
        return denied
    subject = _user_subject(user or {})
    draft_id = str(request.query_params.get("draft_id") or "").strip()
    ok, reason, context = await get_grant_store(request).read_consent_draft_context(
        draft_id,
        subject,
    )
    if not ok:
        return _consent_draft_error(reason)
    device_binding = device_consent_binding(context)
    if context.get("device_authorization") is not None and device_binding is None:
        return _consent_draft_error("invalid")
    req, cfg, invalid = await _request_from_consent_draft(
        request,
        subject=subject,
        context=context,
    )
    if invalid is not None:
        return invalid
    if req is None or cfg is None:
        return _consent_draft_error("invalid")

    delegation_grants = _delegation_grants(cfg, req.scopes)
    inventory = await _platform_grant_inventory(
        user or {},
        delegation_grants,
        cfg=cfg,
        resource=req.resource,
    )
    denied_grants = _delegation_denial(delegation_grants, inventory, resource=req.resource)
    if denied_grants is not None:
        return denied_grants

    service = get_automation_access(request)
    seed = await service.oauth_consent_card_seed(
        grantor_subject=subject,
        client_id=req.client_id,
        resource=req.resource,
        client_metadata=req.client.snapshot() if req.client is not None else {},
    )
    if seed.get("ok") is not True:
        return JSONResponse(
            status_code=int(seed.get("status") or 503),
            content=seed,
        )
    if device_binding is not None:
        seeded_access_id = str(seed.get("access_id") or "").strip()
        seeded_revision = int(seed.get("card_revision") or 0)
        requested_access_id = str(
            device_binding.get("requested_access_id") or ""
        ).strip()
        expected_revision = device_binding.get("expected_card_revision")
        terminal_error = ""
        if requested_access_id and requested_access_id != seeded_access_id:
            terminal_error = "device_card_mismatch"
        elif expected_revision is not None and int(expected_revision) != seeded_revision:
            terminal_error = "device_card_revision_conflict"
        if terminal_error:
            await get_device_grant_store(request).deny(
                device_digest=str(device_binding["device_digest"]),
                user_digest=str(device_binding["user_digest"]),
                approving_subject=subject,
                error=terminal_error,
            )
            await get_grant_store(request).consume_consent_draft_context(
                draft_id,
                subject,
            )
            return JSONResponse(
                status_code=409,
                content={
                    "error": terminal_error,
                    "error_description": "The requested Card changed; restart device authorization.",
                },
                headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
            )
    has_existing_card = isinstance(seed.get("access"), Mapping)
    existing = dict(seed.get("access") or {}) if has_existing_card else {}
    if has_existing_card:
        raw_resource_grants = {
            str(resource): [str(grant) for grant in grants or ()]
            for resource, grants in dict(existing.get("resource_grants") or {}).items()
        }
        resource_grants, _rewritten_grants = resolve_declared_resource_keys(
            cfg,
            raw_resource_grants,
        )
        raw_resource_operations = {
            str(resource): [str(operation) for operation in operations or ()]
            for resource, operations in dict(existing.get("resource_operations") or {}).items()
        }
        resource_operations, _rewritten_operations = resolve_declared_resource_keys(
            cfg,
            raw_resource_operations,
        )
        named_service_operations = _declared_named_service_operations(
            cfg,
            existing.get("effective_named_service_operations")
            or existing.get("named_service_operations")
            or {},
        )
    else:
        proposal = requested_card_selection(
            req.scopes,
            config=cfg,
            resource=req.resource,
            full_catalog=(seed.get("catalog_scope") or {}).get("mode") == "full",
        )
        resource_grants = dict(proposal["resource_grants"])
        resource_operations = dict(proposal["resource_operations"])
        named_service_operations = dict(proposal["named_service_operations"])
    invocation_policies = {
        resource: {operation: "always" for operation in operations}
        for resource, operations in resource_operations.items()
    }
    existing_policy_choices: dict[str, dict[str, str]] = {}
    for policy in existing.get("invocation_policies") or ():
        authority = policy.get("authority") if isinstance(policy, Mapping) else {}
        if not isinstance(authority, Mapping) or authority.get("surface") != "outer":
            continue
        resource, _literal = resolve_declared_resource(
            cfg,
            authority.get("resource"),
        )
        operation = str(authority.get("operation") or "")
        mode = str(policy.get("mode") or "")
        if operation in resource_operations.get(resource, ()) and mode in {"always", "once"}:
            existing_policy_choices.setdefault(resource, {})[operation] = mode
    for resource, operations in existing_policy_choices.items():
        invocation_policies.setdefault(resource, {}).update(operations)

    connected_accounts = await _connected_accounts_for_consent(subject)
    requirements = _accounts_needed_for_consent(
        request,
        req.scopes,
        connected_accounts,
        cfg=cfg,
    )
    resource_cfg = cfg.resource_config(req.resource)
    client = req.client.snapshot() if req.client is not None else {"client_id": req.client_id}
    derived_label = _oauth_card_label(client, resource=req.resource)
    payload = {
        "ok": True,
        "draft_id": draft_id,
        "catalog_version": str(context.get("catalog_version") or ""),
        "card_revision": int(seed.get("card_revision") or 0),
        "grantor": {
            "subject": subject,
            "label": _user_label(user or {}),
        },
        "client": client,
        "trusted": bool(
            req.client is not None
            and req.client.registration_kind == CLIENT_REGISTRATION_PRE_REGISTERED
        ),
        "entry_door": {
            "resource": req.resource,
            "label": (
                str(getattr(resource_cfg, "label", "") or "")
                if resource_cfg is not None
                else req.resource
            ),
            "requested_grants": list(req.scopes),
            "operations": [
                {
                    "name": tool.name,
                    "label": tool.label,
                    "description": tool.description,
                    "grants": list(tool.grants),
                }
                for tool in cfg.tools_for_scopes(req.scopes, resource=req.resource)
            ],
        },
        "oauth": {
            "redirect_uri": req.redirect_uri,
            "redirect_host": urlsplit(req.redirect_uri).netloc,
            "requested_scopes": list(req.scopes),
        },
        **(
            {
                "device_authorization": {
                    "user_code": str(device_binding["user_code"]),
                }
            }
            if device_binding is not None
            else {}
        ),
        "account_requirements": _account_requirements_payload(requirements),
        "catalog_scope": seed.get("catalog_scope") or {
            "mode": "entry",
            "resources": [req.resource],
        },
        "selection_source": "existing_card" if has_existing_card else "request",
        "selection": {
            "label": _consent_label(
                str(existing.get("label") or ""), derived_label, client, resource=req.resource
            ),
            "resource_grants": resource_grants,
            "resource_operations": resource_operations,
            "invocation_policies": invocation_policies,
            "properties": dict(existing.get("properties") or {}),
            "named_service_operations": named_service_operations,
            "account_scope": existing.get("account_scope") or {},
            "catalog_row_by_resource": _declared_catalog_rows(
                cfg,
                existing.get("catalog_row_by_resource")
                or seed.get("catalog_row_by_resource")
                or {},
            ),
        },
    }
    return JSONResponse(
        payload,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


async def _json_data(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except Exception:
        return {}
    if not isinstance(value, Mapping):
        return {}
    nested = value.get("data")
    return dict(nested) if isinstance(nested, Mapping) else dict(value)


@router.post("/oauth/authorize/consent/decision", include_in_schema=False)
@_normalize_grant_store_unavailable
async def authorize_consent_decision(request: Request) -> Response:
    issuer = resolve_issuer(request)
    user, denied = await _require_user(request)
    if denied is not None:
        return denied
    subject = _user_subject(user or {})
    payload = await _json_data(request)
    draft_id = str(payload.get("draft_id") or "").strip()
    store = get_grant_store(request)
    ok, reason, context = await store.read_consent_draft_context(draft_id, subject)
    if not ok:
        return _consent_draft_error(reason)
    device_binding = device_consent_binding(context)
    if context.get("device_authorization") is not None and device_binding is None:
        return _consent_draft_error("invalid")
    req, cfg, invalid = await _request_from_consent_draft(
        request,
        subject=subject,
        context=context,
    )
    if invalid is not None:
        return invalid
    if req is None or cfg is None:
        return _consent_draft_error("invalid")

    decision = str(payload.get("decision") or "").strip().lower()
    if decision not in {"approve", "deny"}:
        return JSONResponse(
            status_code=400,
            content={"error": "oauth_consent_decision_invalid"},
        )
    if decision == "deny":
        consumed, consume_reason, _ = await store.consume_consent_draft_context(
            draft_id,
            subject,
        )
        if not consumed:
            return _consent_draft_error(consume_reason)
        if device_binding is not None:
            await get_device_grant_store(request).deny(
                device_digest=str(device_binding["device_digest"]),
                user_digest=str(device_binding["user_digest"]),
                approving_subject=subject,
            )
            return JSONResponse({
                "ok": True,
                "redirect_url": (
                    f"{verification_uri(issuer)}/complete?result=denied"
                ),
            })
        return JSONResponse({
            "ok": True,
            "redirect_url": build_redirect(
                req.redirect_uri,
                {"error": "access_denied", "state": req.state, "iss": issuer},
            ),
        })

    resource_grants = payload.get("resource_grants")
    resource_operations = payload.get("resource_operations")
    invocation_policies = payload.get("invocation_policies")
    account_scope = payload.get("account_scope")
    properties = payload.get("properties")
    if not all(isinstance(value, Mapping) for value in (
        resource_grants,
        resource_operations,
        invocation_policies,
        account_scope,
    )):
        return JSONResponse(
            status_code=400,
            content={"error": "oauth_consent_selection_invalid"},
        )
    if properties is not None and not isinstance(properties, Mapping):
        return JSONResponse(
            status_code=400,
            content={"error": "oauth_consent_selection_invalid"},
        )
    named_service_operations = payload.get("named_service_operations", {})
    if not isinstance(named_service_operations, (Mapping, str)):
        return JSONResponse(
            status_code=400,
            content={"error": "oauth_consent_selection_invalid"},
        )

    service = get_automation_access(request)
    resolved = await service.resolve_oauth_consent_authority(
        user or {},
        client_id=req.client_id,
        entry_resource=req.resource,
        requested_grants=req.scopes,
        client_metadata=req.client.snapshot() if req.client is not None else {},
        resource_grants=resource_grants,
        resource_operations=resource_operations,
        named_service_operations=named_service_operations,
        account_scope=account_scope,
        expected_card_revision=int(payload.get("expected_card_revision") or 0),
        expected_catalog_version=str(payload.get("expected_catalog_version") or ""),
        properties=properties,
    )
    if resolved.get("ok") is not True:
        if (
            device_binding is not None
            and str(resolved.get("error") or "") == "delegated_card_save_conflict"
        ):
            await get_device_grant_store(request).deny(
                device_digest=str(device_binding["device_digest"]),
                user_digest=str(device_binding["user_digest"]),
                approving_subject=subject,
                error="device_card_revision_conflict",
            )
            await store.consume_consent_draft_context(draft_id, subject)
        return JSONResponse(
            status_code=int(resolved.get("status") or 400),
            content=resolved,
        )
    if device_binding is not None:
        requested_access_id = str(
            device_binding.get("requested_access_id") or ""
        ).strip()
        if requested_access_id and requested_access_id != str(
            resolved.get("access_id") or ""
        ).strip():
            await get_device_grant_store(request).deny(
                device_digest=str(device_binding["device_digest"]),
                user_digest=str(device_binding["user_digest"]),
                approving_subject=subject,
                error="device_card_mismatch",
            )
            await store.consume_consent_draft_context(draft_id, subject)
            return JSONResponse(
                status_code=409,
                content={
                    "error": "device_card_mismatch",
                    "error_description": "The approved Card does not match the requested Card.",
                },
            )
    selected_resource_operations = dict(resolved.get("resource_operations") or {})
    selected_policy_keys = {
        (str(resource), str(operation))
        for resource, operations in selected_resource_operations.items()
        for operation in operations or ()
    }
    canonical_invocation_policies = _declared_invocation_policies(
        cfg,
        invocation_policies,
    )
    submitted_policies = {
        (str(resource), str(operation)): str(mode or "").strip().lower()
        for resource, operations in canonical_invocation_policies.items()
        if isinstance(operations, Mapping)
        for operation, mode in operations.items()
    }
    if set(submitted_policies) != selected_policy_keys or any(
        mode not in {"always", "once"} for mode in submitted_policies.values()
    ):
        return JSONResponse(
            status_code=400,
            content={
                "error": "oauth_consent_invocation_policy_invalid",
                "error_description": "every selected outer operation requires Once or Always",
            },
        )

    selected_grants = sorted({
        str(grant)
        for grants in dict(resolved.get("resource_grants") or {}).values()
        for grant in grants or ()
        if str(grant).strip()
    })
    inventory = await _platform_grant_inventory(
        user or {},
        selected_grants,
        cfg=cfg,
    )
    delegation_denied = _delegation_denial(selected_grants, inventory)
    if delegation_denied is not None:
        return delegation_denied
    grantor_authority = _grantor_authority(
        user or {},
        scopes=selected_grants,
        inventory=inventory,
    )
    # Consume only after every submitted authority dimension has passed. A
    # correctable validation error leaves the same editor usable; a successful
    # decision is still single-use and cannot mint two authorization codes.
    consumed, consume_reason, consumed_context = await store.consume_consent_draft_context(
        draft_id,
        subject,
    )
    if not consumed:
        return _consent_draft_error(consume_reason)
    if consumed_context != context:
        return _consent_draft_error("context_changed")

    entry_key = ""
    if req.resource:
        entry_key, _entry_is_literal = resolve_declared_resource(
            cfg,
            req.resource,
        )
    resolved_resource_grants = dict(resolved.get("resource_grants") or {})
    code_scopes = (
        list(resolved_resource_grants.get(entry_key, ()))
        if entry_key
        else selected_grants
    )
    authorization = {
        "client_id": req.client_id,
        "redirect_uri": req.redirect_uri,
        "code_challenge": req.code_challenge,
        "sub": subject,
        "scopes": code_scopes,
        "operations": list(resolved.get("operations") or ()),
        "resource_grants": resolved_resource_grants,
        "resource_operations": selected_resource_operations,
        "resource": req.resource,
        "registry_access_id": str(resolved.get("access_id") or ""),
        "card_kind": str(resolved.get("card_kind") or ""),
        "identity_scope": str(resolved.get("identity_scope") or ""),
        "grantor_authority": grantor_authority,
        "delegation_edges": list(grantor_authority.get("delegation_edges") or []),
        "named_services": dict(resolved.get("named_services") or {}),
        "named_service_operations": resolved.get("named_service_operations") or {},
        "catalog_version": str(resolved.get("catalog_version") or ""),
        "account_scope": dict(resolved.get("account_scope") or {}),
        "client_metadata": req.client.snapshot() if req.client is not None else {},
        "properties": dict(resolved.get("properties") or {}),
        "card_label": str(payload.get("label") or "").strip(),
        "invocation_policies": canonical_invocation_policies,
        "expected_card_revision": int(resolved.get("card_revision") or 0),
    }
    if device_binding is not None:
        decision_state = await get_device_grant_store(request).approve(
            device_digest=str(device_binding["device_digest"]),
            user_digest=str(device_binding["user_digest"]),
            approving_subject=subject,
            authorization=authorization,
        )
        if decision_state != "approved":
            return JSONResponse(
                status_code=409 if decision_state == "already_decided" else 410,
                content={
                    "error": "oauth_device_decision_unavailable",
                    "reason": decision_state,
                },
            )
        return JSONResponse({
            "ok": True,
            "redirect_url": f"{verification_uri(issuer)}/complete?result=approved",
        })

    code = await store.create_auth_code(**authorization)
    return JSONResponse({
        "ok": True,
        "redirect_url": build_redirect(
            req.redirect_uri,
            {"code": code, "state": req.state, "iss": issuer},
        ),
    })


@router.post("/oauth/authorize/consent", include_in_schema=False)
@_normalize_grant_store_unavailable
async def authorize_consent(request: Request) -> Response:
    issuer = resolve_issuer(request)
    form = await request.form()
    params = _consent_authorize_params(request, form)
    cfg = await _consent_config(request)
    if cfg is None:
        return _catalog_unavailable_response()
    try:
        resolver = await _dynamic_client_resolver(request, params.get("client_id"))
    except ClientMetadataError as error:
        LOGGER.warning(
            "[connection-hub.oauth] consent client_metadata_rejected client_id=%s error=%s",
            str(params.get("client_id") or ""),
            error.code,
        )
        return _client_metadata_error_response(error)
    try:
        req = parse_authorize_request(
            params,
            client_resolver=resolver,
            public_client_resolver=lambda cid: get_client(cid, request),
            supported_scopes=cfg.supported_scopes(params.get("resource")),
        )
    except AuthorizeError as err:
        tenant, project = oauth_tenant_project(request)
        LOGGER.warning(
            "[connection-hub.oauth] authorize_consent rejected tenant=%s project=%s "
            "error=%s description=%s client_id=%s form_keys=%s",
            tenant,
            project,
            err.error,
            err.error_description,
            str(params.get("client_id") or ""),
            sorted(str(key) for key in form.keys()),
        )
        return _error_response(err, issuer)

    user, denied = await _require_user(request)
    if denied is not None:
        return denied

    store = get_grant_store(request)

    subject = _user_subject(user or {})
    if not subject:
        return JSONResponse(status_code=401, content={"error": "login_required"})
    owner_cfg = await _consent_config(request, owner_subject=subject)
    if owner_cfg is None:
        return _catalog_unavailable_response()
    try:
        req = parse_authorize_request(
            params,
            client_resolver=resolver,
            public_client_resolver=lambda cid: get_client(cid, request),
            supported_scopes=owner_cfg.supported_scopes(params.get("resource")),
        )
    except AuthorizeError as err:
        return _error_response(err, issuer)
    cfg = owner_cfg

    # CSRF: the consent POST must carry the single-use token minted for THIS user
    # at GET /oauth/authorize. Blocks a forged cross-site POST riding the session
    # cookie. Checked before the decision branch so deny is protected too.
    csrf_value = form.get("csrf_token")
    csrf_context: dict[str, Any] = {}
    if hasattr(store, "consume_csrf_token_context"):
        csrf_ok, csrf_reason, csrf_context = await store.consume_csrf_token_context(
            csrf_value,
            subject,
        )
    elif hasattr(store, "consume_csrf_token_with_reason"):
        csrf_ok, csrf_reason = await store.consume_csrf_token_with_reason(csrf_value, subject)
    else:
        csrf_ok = await store.consume_csrf_token(csrf_value, subject)
        csrf_reason = "ok" if csrf_ok else "invalid"
    if not csrf_ok:
        LOGGER.warning(
            "[connection-hub.oauth] invalid_csrf reason=%s subject=%s client_id=%s resource=%s "
            "csrf_present=%s form_keys=%s content_type=%s path=%s",
            csrf_reason,
            subject,
            params.get("client_id") or "",
            params.get("resource") or "",
            bool(csrf_value),
            sorted(str(key) for key in form.keys()),
            request.headers.get("content-type") or "",
            request.url.path,
        )
        return JSONResponse(
            status_code=403,
            content={"error": "invalid_csrf", "error_description": "CSRF token missing, expired, or invalid"},
        )

    expected_client_id = str(csrf_context.get("client_id") or "")
    expected_digest = str(csrf_context.get("client_metadata_digest") or "")
    current_digest = req.client.snapshot_digest() if req.client is not None else ""
    if (
        expected_client_id
        and (
            expected_client_id != req.client_id
            or not expected_digest
            or expected_digest != current_digest
        )
    ):
        LOGGER.warning(
            "[connection-hub.oauth] consent client metadata changed "
            "client_id=%s",
            req.client_id,
        )
        return _client_metadata_error_response(ClientMetadataError(
            "invalid_client_metadata",
            "client metadata changed after the consent page was shown; restart authorization",
        ))

    if (form.get("decision") or "").strip() != "approve":
        url = build_redirect(
            req.redirect_uri, {"error": "access_denied", "state": req.state, "iss": issuer}
        )
        return RedirectResponse(url, status_code=302)

    declared_contract = str(form.get("consent_contract_version") or "").strip()
    if declared_contract != CONSENT_CONTRACT_VERSION:
        LOGGER.error(
            "[connection-hub.oauth] consent submission contract mismatch client_id=%s "
            "declared=%s expected=%s",
            req.client_id, declared_contract or "<absent>", CONSENT_CONTRACT_VERSION,
        )
        return JSONResponse(
            status_code=400,
            content={
                "error": "consent_ui_contract_mismatch",
                "error_description": (
                    "the consent page did not submit the current consent contract; "
                    "no authorization code is issued"
                ),
            },
        )
    expected_catalog_version = str(form.get("expected_catalog_version") or "").strip()
    active_catalog_version = await _active_catalog_version_for_consent(request)
    if (
        expected_catalog_version
        and active_catalog_version
        and expected_catalog_version != active_catalog_version
    ):
        return JSONResponse(
            status_code=409,
            content={
                "error": "consent_catalog_changed",
                "error_description": (
                    "the service catalog changed after this page was shown; "
                    "restart authorization"
                ),
                "expected_catalog_version": expected_catalog_version,
                "active_catalog_version": active_catalog_version,
            },
        )

    requested_scope_set = _as_set(req.scopes)
    selected_scope_set = _as_set(form.getlist("platform_grants"))
    selected_scopes = [scope for scope in req.scopes if scope in selected_scope_set]
    if not selected_scopes:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_request",
                "error_description": "at least one platform delegation grant must be selected",
            },
        )
    unknown_selected_scopes = sorted(selected_scope_set - requested_scope_set)
    if unknown_selected_scopes:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_scope",
                "error_description": "selected platform delegation grant was not requested",
                "grants": unknown_selected_scopes,
            },
        )

    inventory = await _platform_grant_inventory(user or {}, _delegation_grants(cfg, req.scopes), cfg=cfg, resource=req.resource)
    _log_inventory(stage="authorize.consent", user=user or {}, scopes=req.scopes, inventory=inventory, resource=req.resource)
    delegation_denied = _delegation_denial(_delegation_grants(cfg, selected_scopes), inventory, resource=req.resource)
    if delegation_denied is not None:
        return delegation_denied

    valid_tools = {name for name, _, _ in tools_for_scopes(selected_scopes, config=cfg, resource=req.resource)}
    direct_operations = [t for t in form.getlist("tools") if t in valid_tools]
    try:
        child_resource_grants, child_resource_operations = _selected_resource_authority(
            form,
            scopes=selected_scopes,
            cfg=cfg,
            resource=req.resource,
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_request",
                "error_description": str(exc),
            },
        )
    if req.resource:
        entry_key, _entry_is_literal = resolve_declared_resource(
            cfg,
            req.resource,
        )
        resource_grants = {
            entry_key: list(selected_scopes),
            **child_resource_grants,
        }
        resource_operations = {
            entry_key: direct_operations,
            **child_resource_operations,
        }
    else:
        # Resource-free OAuth represents a whole Card. Its authority is the
        # exact catalog rows submitted by the consent surface, never a
        # synthetic catch-all derived from the missing entry door.
        try:
            direct_resource_grants, direct_resource_operations = (
                _direct_operation_authority(
                    direct_operations,
                    scopes=selected_scopes,
                    cfg=cfg,
                )
            )
        except ValueError as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "error": "invalid_request",
                    "error_description": str(exc),
                },
            )
        resource_grants = dict(child_resource_grants)
        resource_operations = {
            resource: list(operations)
            for resource, operations in child_resource_operations.items()
        }
        for resource, grants in direct_resource_grants.items():
            resource_grants.setdefault(resource, grants)
        for resource, operations in direct_resource_operations.items():
            selected = resource_operations.setdefault(resource, [])
            for operation in operations:
                if operation not in selected:
                    selected.append(operation)
    if req.resource:
        resource_grants, _rewritten_grants = resolve_declared_resource_keys(
            cfg,
            resource_grants,
        )
        resource_operations, _rewritten_operations = resolve_declared_resource_keys(
            cfg,
            resource_operations,
        )
    selected_operations = list(operation_union(resource_operations))
    resource_cfg = cfg.resource_config(req.resource)
    named_services = dict(resource_cfg.named_services or {}) if resource_cfg is not None else {}
    named_service_operations = _selected_named_service_operations(
        form, scopes=selected_scopes, cfg=cfg, resource=req.resource,
    )
    named_service_operations = _declared_named_service_operations(
        cfg,
        named_service_operations,
    )
    grantor_authority = _grantor_authority(user or {}, scopes=selected_scopes, inventory=inventory)
    delegation_edges = list(grantor_authority.get("delegation_edges") or [])
    # Per-account claim picks from the consent screen: checkbox values are
    # "provider|account_id|claim". They become the grant card's account_scope
    # at token exchange — default-closed, so no pick = no account access.
    account_scope: dict[str, dict[str, list[str]]] = {}
    for entry in form.getlist("account_scope"):
        parts = str(entry or "").split("|", 2)
        if len(parts) != 3:
            continue
        provider, account_id, claim = (part.strip() for part in parts)
        if not provider or not account_id or not claim:
            continue
        held = account_scope.setdefault(provider, {}).setdefault(account_id, [])
        if claim not in held:
            held.append(claim)
    LOGGER.info(
        "[connection-hub.oauth] consent approved client_id=%s scopes=%d tools=%d "
        "resources=%d account_scope_providers=%s",
        req.client_id, len(selected_scopes), len(selected_operations),
        len(child_resource_operations),
        sorted(account_scope.keys()) or "-",
    )
    service = get_automation_access(request)
    identity = await service.resolve_oauth_card_identity(
        grantor_subject=subject,
        client_id=req.client_id,
        entry_resource=req.resource,
        client_metadata=req.client.snapshot() if req.client is not None else {},
    )
    if identity.get("ok") is not True:
        return JSONResponse(
            status_code=int(identity.get("status") or 400),
            content=identity,
        )
    code = await store.create_auth_code(
        client_id=req.client_id,
        redirect_uri=req.redirect_uri,
        code_challenge=req.code_challenge,
        sub=subject,
        scopes=selected_scopes,
        operations=selected_operations,
        resource_grants=resource_grants,
        resource_operations=resource_operations,
        resource=req.resource,
        registry_access_id=str(identity.get("access_id") or ""),
        card_kind=str(identity.get("card_kind") or ""),
        identity_scope=resource_cfg.identity_scope if resource_cfg is not None else "",
        grantor_authority=grantor_authority,
        delegation_edges=delegation_edges,
        named_services=named_services,
        named_service_operations=named_service_operations,
        catalog_version=active_catalog_version,
        account_scope=account_scope,
        client_metadata=req.client.snapshot() if req.client is not None else {},
    )
    url = build_redirect(req.redirect_uri, {"code": code, "state": req.state, "iss": issuer})
    return RedirectResponse(url, status_code=302)


def _token_error(
    error: str,
    description: str = "",
    status: int = 400,
    *,
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(max(1, int(retry_after_seconds)))
    return JSONResponse(
        status_code=status,
        content={"error": error, "error_description": description},
        headers=headers,
    )


# Live-grant states that end on their own: the Card sweep completes, an
# in-flight Card mutation commits, or a store read succeeds again. The token is
# still valid, so the client is told to retry after the wait instead of being
# told its grant is invalid.
_TRANSIENT_LIVE_GRANT_REASONS = {
    "lookup_unavailable": 5,
    "cache_unavailable": 5,
    "card_projection_reconciling": 60,
    "card_updating": 5,
    "card_projection_missing": 5,
    "control_card_lookup_unavailable": 5,
    "control_card_updating": 5,
    "durable_card_unreadable": 30,
    "durable_card_history_unreadable": 30,
}


def _refresh_refused(reason: str, client_id: str, description: str) -> JSONResponse:
    """A refresh refusal with its reason in the log.

    Every refusal is logged with a secret-free reason and the client id, so a
    refused refresh can be traced to its cause (2026-09-21: four silent
    invalid_grant returns hid that migrated Cards had no projection).
    """

    LOGGER.warning(
        "[connection-hub.oauth] refresh denied reason=%s client_id=%s",
        reason,
        client_id,
    )
    return _token_error("invalid_grant", description)


async def _restore_failed_refresh_rotation(
    store,
    *,
    refresh_token: str,
    replacement_token: str,
    refresh_state,
    client_id: str,
) -> bool:
    """Keep a client-held token usable when replacement delivery fails."""

    try:
        restored = await store.rollback_refresh_token_rotation(
            refresh_token,
            replacement_token,
            state=refresh_state,
        )
    except Exception:
        LOGGER.exception(
            "[connection-hub.oauth] failed to restore refresh rotation after "
            "token issuance failure client_id=%s",
            client_id,
        )
        return False
    if restored:
        LOGGER.warning(
            "[connection-hub.oauth] refresh rotation restored after token "
            "issuance failure client_id=%s",
            client_id,
        )
        return True
    LOGGER.error(
        "[connection-hub.oauth] refresh rotation was not restorable after "
        "token issuance failure client_id=%s",
        client_id,
    )
    return False


def _refresh_issuance_unavailable(
    *,
    restored: bool,
    cause: Response | None = None,
    cause_error: str = "",
    client_id: str = "",
) -> JSONResponse:
    """The named 503 of a refresh whose issuance failed after rotation.

    It says whether the presented refresh token was restored, so the client
    knows to retry with it or to authorize again, and it keeps what the
    failed issuance said (its ``error``, its description and any
    ``Retry-After``), so the reason a Card could not be issued, for example
    ``delegated_cards_unavailable``, reaches the client instead of being
    replaced by a generic line.
    """

    outcome = (
        "token issuance failed; the presented refresh token remains valid, "
        "retry with it"
        if restored
        else "token issuance failed; the refresh token could not be restored, "
        "authorize again"
    )
    original: dict[str, Any] = {}
    retry_after: int | None = None
    if cause is not None:
        try:
            parsed = json.loads(bytes(getattr(cause, "body", b"") or b"").decode("utf-8"))
        except Exception:
            parsed = {}
        original = parsed if isinstance(parsed, dict) else {}
        header = str(getattr(cause, "headers", {}).get("retry-after") or "").strip()
        if header.isdigit():
            retry_after = int(header)
    original_error = str(original.get("error") or cause_error or "").strip()
    original_description = str(original.get("error_description") or "").strip()
    reason = original_error
    if original_error and original_description:
        reason = f"{original_error} ({original_description})"
    elif original_description:
        reason = original_description
    description = f"{reason}: {outcome}" if reason else outcome
    LOGGER.warning(
        "[connection-hub.oauth] refresh issuance failed after rotation "
        "reason=%s status=%s restored=%s client_id=%s",
        original_error or "-",
        int(getattr(cause, "status_code", 0) or 0) if cause is not None else 0,
        bool(restored),
        client_id or "-",
    )
    content: dict[str, Any] = {
        "error": "temporarily_unavailable",
        "error_description": description,
        "refresh_token_restored": bool(restored),
    }
    if original_error or cause is not None:
        content["cause"] = {
            "error": original_error,
            "status": int(getattr(cause, "status_code", 0) or 0) if cause is not None else 0,
        }
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    if retry_after is not None:
        headers["Retry-After"] = str(max(1, retry_after))
    return JSONResponse(status_code=503, content=content, headers=headers)


def _minter_accepts_authority_kwargs(minter) -> bool:
    try:
        signature = inspect.signature(minter)
    except Exception:
        return False
    params = signature.parameters
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return True
    return any(name in params for name in ("client_id", "operations", "credential"))


@router.post("/oauth/logout", include_in_schema=False)
async def oauth_logout(request: Request) -> Response:
    try:
        form = await request.form()
    except Exception:
        form = {}
    next_url = str(form.get("next") or "/").strip()
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"

    response = RedirectResponse(f"{sign_in_bounce_path()}?next={quote(next_url, safe='')}", status_code=302)
    auth_cfg = get_settings().AUTH
    cookie_names = {
        oauth_delegated_config(request).auth_cookie_name,
        getattr(auth_cfg, "AUTH_TOKEN_COOKIE_NAME", ""),
        getattr(auth_cfg, "ID_TOKEN_COOKIE_NAME", ""),
        getattr(auth_cfg, "MASQUERADED_TOKEN_COOKIE_NAME", ""),
    }
    for name in sorted(item for item in cookie_names if item):
        response.delete_cookie(name, path="/")
    LOGGER.info("[connection-hub.oauth] logout cleared platform cookies next=%s", next_url)
    return response


async def _issue_tokens(
    request,
    store,
    *,
    sub,
    scopes,
    client_id,
    operations,
    resource_grants=None,
    resource_operations=None,
    resource=None,
    registry_access_id="",
    card_kind="",
    identity_scope="",
    grantor_authority=None,
    delegation_edges=None,
    named_services=None,
    named_service_operations=None,
    catalog_version="",
    refresh_token=None,
    account_scope=None,
    client_metadata=None,
    properties=None,
    card_label="",
    invocation_policies=None,
    replace_authority=False,
    expected_card_revision=None,
    card_conflict_error="invalid_grant",
) -> JSONResponse:
    tenant, project = oauth_tenant_project(request)
    resolved_access_id = str(registry_access_id or "").strip()
    resolved_card_kind = str(card_kind or "").strip()
    if not resolved_access_id or not resolved_card_kind:
        service = get_automation_access(request)
        identity = await service.resolve_oauth_card_identity(
            grantor_subject=str(sub or ""),
            client_id=str(client_id or ""),
            entry_resource=str(resource or ""),
            client_metadata=dict(client_metadata or {}),
        )
        if identity.get("ok") is not True:
            return _token_error(
                "invalid_grant",
                "The delegated Card identity is ambiguous or unavailable.",
                status=int(identity.get("status") or 400),
            )
        resolved_access_id = str(identity.get("access_id") or "").strip()
        resolved_card_kind = str(identity.get("card_kind") or "").strip()
    grant_map = normalize_resource_grants(resource_grants)
    if not grant_map and str(resource or "").strip():
        grant_map = normalize_resource_grants({str(resource): list(scopes or [])})
    operation_map = (
        normalize_resource_operations(resource_operations)
        if resource_operations is not None
        else project_legacy_operations(
            {str(resource): list(scopes or [])} if str(resource or "").strip() else {},
            operations or (),
        )
    )
    whole_card = resolved_card_kind in {CARD_KIND_AGENT, CARD_KIND_AUTOMATION}
    token_resource = None if whole_card else resource
    if whole_card and grant_map:
        scopes = sorted(
            {
                str(grant).strip()
                for grants in grant_map.values()
                for grant in grants
                if str(grant).strip()
            }
        )
    operations = list(operation_union(operation_map))
    # This is the common credential envelope understood by the Connection Hub
    # authority SDK. The access token remains a real kst1 session token; the
    # envelope is the routing/verification hint carried inside that token and in
    # the grant store.
    credential = build_delegated_client_credential(
        grantor_subject=sub,
        client_id=client_id,
        scopes=scopes,
        operations=operations,
        resource_operations=operation_map,
        resources=list(grant_map),
        resource_grants=grant_map,
        account_scope=account_scope,
        tenant=tenant,
        project=project,
        resource=token_resource,
        identity_scope=identity_scope,
        expires_in=3600,
    )
    minter = get_access_token_minter(request)
    if _minter_accepts_authority_kwargs(minter):
        minted = await minter(
            sub,
            scopes,
            client_id=client_id,
            operations=operations,
            credential=credential.to_dict(),
        )
    else:
        # Test overrides and older injected minters only accept (sub, scopes).
        minted = await minter(sub, scopes)
    access_token = minted["access_token"]
    expires_in = minted.get("expires_in", 3600)
    if expires_in != 3600:
        credential = build_delegated_client_credential(
            grantor_subject=sub,
            client_id=client_id,
            scopes=scopes,
            operations=operations,
            resource_operations=operation_map,
            resources=list(grant_map),
            resource_grants=grant_map,
            account_scope=account_scope,
            tenant=tenant,
            project=project,
            resource=token_resource,
            identity_scope=identity_scope,
            expires_in=expires_in,
        )
    # Bind the consented operation allowlist to THIS access token so managed
    # guards can enforce it. The binding carries the registry-card POINTER: the
    # guard resolves the card live, so hub-side extend/narrow/revoke apply to
    # this bearer immediately.
    await store.bind_access_grant(
        access_token,
        operations,
        expires_in,
        credential=credential.to_dict(),
        grantor_authority=dict(grantor_authority or {}),
        delegation_edges=list(delegation_edges or []),
        named_services=dict(named_services or {}),
        resource_grants=grant_map,
        resource_operations=operation_map,
        registry_access_id=resolved_access_id,
    )
    if refresh_token is None:
        refresh_token = await store.create_refresh_token(
            client_id=client_id, sub=sub, scopes=scopes, operations=operations,
            resource_grants=grant_map,
            resource_operations=operation_map,
            resource=token_resource,
            identity_scope=identity_scope,
            credential=credential.to_dict(),
            grantor_authority=dict(grantor_authority or {}),
            delegation_edges=list(delegation_edges or []),
            named_services=dict(named_services or {}),
            registry_access_id=resolved_access_id,
            card_kind=resolved_card_kind,
            client_metadata=dict(client_metadata or {}),
        )
    # Register the grant in the user's Connection Hub registry (Delegated by
    # KDCube tab) so the connection is visible and revocable. Registry write
    # failures must never fail token issuance.
    try:
        service = get_automation_access(request)
        metadata_snapshot = dict(client_metadata or {})
        if not metadata_snapshot:
            client_record = await store.get_client_record(client_id) or {}
            metadata_snapshot = dict(client_record.get("metadata") or {})
        client_label = _oauth_card_label(
            metadata_snapshot,
            resource=str(token_resource or ""),
            explicit=str(card_label or ""),
        )
        door_path = str(token_resource or "").split("?", 1)[0].rstrip("*").rstrip("/")
        door_alias = door_path.rsplit("/mcp/", 1)[-1].strip("/") if "/mcp/" in door_path else ""
        registered_name = str(metadata_snapshot.get("client_name") or "")
        asserted_metadata = (
            metadata_snapshot.get("client_metadata")
            if isinstance(metadata_snapshot.get("client_metadata"), Mapping)
            else {}
        )
        # Card naming is derived, not received: log every input so a wrong card
        # title is diagnosable from the proc log instead of by inspecting the
        # rendered UI (grep: connection_hub.oauth card_label).
        LOGGER.info(
            "[connection_hub.oauth] card_label client_id=%s registered_name=%r "
            "metadata_keys=%s resource=%r door_alias=%r -> label=%r",
            client_id, registered_name, sorted(metadata_snapshot.keys()),
            str(token_resource or ""), door_alias, client_label,
        )
        recorded = await service.record_oauth_grant(
            grantor_subject=sub,
            client_id=client_id,
            client_label=client_label,
            scopes=scopes,
            # A refresh token is issued from the live effective authority,
            # which may be narrowed by a project-control Card. That projection
            # governs the new tokens but must never replace the caller Card's
            # durable authority. Omitting these fields activates the registry's
            # documented carry-forward path for refresh rotations.
            operations=operations if replace_authority else None,
            resource_grants=grant_map if replace_authority else None,
            resource_operations=operation_map if replace_authority else None,
            resource=str(token_resource or ""),
            access_id=resolved_access_id,
            card_kind=resolved_card_kind,
            identity_scope=identity_scope,
            access_token=access_token,
            refresh_token=str(refresh_token or ""),
            account_scope=account_scope,
            named_service_operations=named_service_operations,
            catalog_version=catalog_version,
            client_metadata=asserted_metadata,
            properties=(properties if replace_authority else None),
            replace_authority=bool(replace_authority),
            expected_card_revision=expected_card_revision,
        )
        if invocation_policies is not None and recorded is not None:
            await service.apply_oauth_invocation_policies(
                grantor_subject=sub,
                access_id=recorded.access_id,
                resource_operations=operation_map,
                invocation_policies=invocation_policies,
            )
    except CardConflict as exc:
        if replace_authority and expected_card_revision is not None:
            try:
                await store.revoke_access_grant(access_token)
                if refresh_token:
                    await store.revoke_refresh_token(str(refresh_token))
            except Exception:
                LOGGER.exception(
                    "[connection-hub.oauth] failed to remove tokens after card conflict "
                    "client=%s",
                    client_id,
                )
            LOGGER.warning(
                "[connection-hub.oauth] token withheld: card changed after consent "
                "client=%s current_revision=%s",
                client_id,
                getattr(exc, "current_revision", 0),
            )
            return _token_error(
                str(card_conflict_error or "invalid_grant"),
                "The delegated access card changed after approval; restart authorization.",
            )
        LOGGER.error(
            "[connection-hub.oauth] token withheld: delegated card conflict "
            "client=%s reason=%s",
            client_id,
            getattr(exc, "reason", type(exc).__name__),
        )
        return _token_error(
            "temporarily_unavailable",
            "The delegated access card could not be recorded; retry the request.",
            status=503,
        )
    except (AutomationAccessUnavailable, CardUnavailable, CardCommitFailed) as exc:
        # The card is the authority a governed call resolves; a token whose card
        # was never committed would be denied as revoked on every use. Delegated
        # authority is therefore handed over only after the card commits.
        LOGGER.error(
            "[connection-hub.oauth] token withheld: delegated card not committed "
            "client=%s reason=%s",
            client_id,
            getattr(exc, "reason", type(exc).__name__),
        )
        return _token_error(
            "temporarily_unavailable",
            "The delegated access card could not be recorded; retry the request.",
            status=503,
        )
    except Exception:
        LOGGER.exception(
            "[connection-hub.oauth] token withheld: delegated grant not recorded client=%s",
            client_id,
        )
        return _token_error(
            "temporarily_unavailable",
            "The delegated access card could not be recorded; retry the request.",
            status=503,
        )
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "refresh_token": refresh_token,
            "scope": " ".join(scopes),
            "access_id": resolved_access_id,
            "card_kind": resolved_card_kind,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/oauth/token", include_in_schema=False)
@_normalize_grant_store_unavailable
async def token(request: Request) -> Response:
    form = await request.form()
    grant_type = (form.get("grant_type") or "").strip()
    store = get_grant_store(request)

    if grant_type == DEVICE_GRANT_TYPE:
        device_code = str(form.get("device_code") or "").strip()
        client_id = str(form.get("client_id") or "").strip()
        if not device_code or not client_id:
            return _token_error(
                "invalid_request",
                "missing device_code parameters",
            )
        polled = await get_device_grant_store(request).poll(
            device_code=device_code,
            client_id=client_id,
        )
        if polled.status != DEVICE_POLL_APPROVED:
            descriptions = {
                DEVICE_POLL_AUTHORIZATION_PENDING: "The user has not completed authorization.",
                DEVICE_POLL_SLOW_DOWN: "The client is polling too quickly.",
                "access_denied": "The user denied the authorization request.",
                "expired_token": "The device authorization request expired.",
                "device_code_replayed": "The device code was already consumed.",
                "device_client_mismatch": "The device code belongs to another client.",
                "device_card_mismatch": "The requested Card does not match the approved Card.",
                "device_card_revision_conflict": "The requested Card changed during authorization.",
            }
            return _token_error(
                polled.status,
                descriptions.get(
                    polled.status,
                    "The device authorization request is unavailable.",
                ),
                retry_after_seconds=(
                    polled.interval
                    if polled.status == DEVICE_POLL_SLOW_DOWN
                    else None
                ),
            )
        authorization = polled.authorization
        if not isinstance(authorization, Mapping):
            return _token_error(
                "invalid_grant",
                "The approved device authorization is invalid.",
            )
        if str(authorization.get("client_id") or "") != client_id:
            return _token_error("device_client_mismatch", "client mismatch")
        try:
            issued = await _issue_tokens(
                request,
                store,
                sub=authorization.get("sub"),
                scopes=authorization.get("scopes") or [],
                client_id=client_id,
                operations=authorization.get("operations") or [],
                resource_grants=authorization.get("resource_grants"),
                resource_operations=authorization.get("resource_operations"),
                resource=authorization.get("resource"),
                registry_access_id=authorization.get("registry_access_id") or "",
                card_kind=authorization.get("card_kind") or "",
                identity_scope=authorization.get("identity_scope") or "",
                grantor_authority=authorization.get("grantor_authority") or {},
                delegation_edges=authorization.get("delegation_edges") or [],
                named_services=authorization.get("named_services") or {},
                named_service_operations=authorization.get("named_service_operations"),
                catalog_version=authorization.get("catalog_version") or "",
                account_scope=authorization.get("account_scope") or None,
                client_metadata=authorization.get("client_metadata") or {},
                properties=authorization.get("properties") or {},
                card_label=authorization.get("card_label") or "",
                invocation_policies=authorization.get("invocation_policies"),
                replace_authority=True,
                expected_card_revision=authorization.get("expected_card_revision"),
                card_conflict_error="device_card_revision_conflict",
            )
        except GrantStoreUnavailable:
            LOGGER.exception(
                "[connection-hub.oauth] device token issuance unavailable after "
                "device-code consumption client=%s",
                client_id,
            )
            return _token_error(
                "device_authorization_restart_required",
                "Token issuance failed after approval; restart device authorization.",
                status=503,
            )
        if int(getattr(issued, "status_code", 500)) >= 400:
            try:
                issued_payload = json.loads(bytes(issued.body).decode("utf-8"))
            except (AttributeError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
                issued_payload = {}
            if (
                isinstance(issued_payload, Mapping)
                and issued_payload.get("error") == "device_card_revision_conflict"
            ):
                return issued
            return _token_error(
                "device_authorization_restart_required",
                "Token issuance failed after approval; restart device authorization.",
                status=int(getattr(issued, "status_code", 503)),
            )
        return issued

    if grant_type == "authorization_code":
        code = form.get("code")
        client_id = form.get("client_id")
        redirect_uri = form.get("redirect_uri")
        verifier = form.get("code_verifier")
        if not (code and client_id and redirect_uri and verifier):
            return _token_error("invalid_request", "missing authorization_code parameters")

        payload = await store.consume_auth_code(code)
        if payload is None:
            return _token_error("invalid_grant", "authorization code invalid or expired")
        if payload["client_id"] != client_id:
            return _token_error("invalid_grant", "client mismatch")
        if payload["redirect_uri"] != redirect_uri:
            return _token_error("invalid_grant", "redirect_uri mismatch")
        if not verify_s256(verifier, payload["code_challenge"]):
            return _token_error("invalid_grant", "PKCE verification failed")

        return await _issue_tokens(
            request, store,
            sub=payload["sub"], scopes=payload["scopes"], client_id=client_id,
            operations=payload.get("operations") or [],
            resource_grants=payload.get("resource_grants"),
            resource_operations=payload.get("resource_operations"),
            resource=payload.get("resource"),
            registry_access_id=payload.get("registry_access_id") or "",
            card_kind=payload.get("card_kind") or "",
            identity_scope=payload.get("identity_scope") or "",
            grantor_authority=payload.get("grantor_authority") or {},
            delegation_edges=payload.get("delegation_edges") or [],
            named_services=payload.get("named_services") or {},
            named_service_operations=payload.get("named_service_operations"),
            catalog_version=payload.get("catalog_version") or "",
            account_scope=payload.get("account_scope") or None,
            client_metadata=payload.get("client_metadata") or {},
            properties=payload.get("properties") or {},
            card_label=payload.get("card_label") or "",
            invocation_policies=payload.get("invocation_policies"),
            replace_authority=True,
            expected_card_revision=payload.get("expected_card_revision"),
        )

    if grant_type == "refresh_token":
        rt = form.get("refresh_token")
        client_id = form.get("client_id")
        if not rt:
            return _token_error("invalid_request", "missing refresh_token")
        try:
            refresh_state = await store.get_refresh_token_state(rt)
        except RefreshTokenReuseDetected:
            return _refresh_refused(
                "refresh_token_reuse_detected",
                str(client_id or ""),
                "refresh token reuse detected; the credential family was revoked",
            )
        if refresh_state is None:
            return _refresh_refused(
                "refresh_token_unknown",
                str(client_id or ""),
                "refresh token invalid or expired",
            )
        rec = refresh_state.record
        if client_id and rec["client_id"] != client_id:
            return _refresh_refused(
                "client_mismatch",
                str(client_id or ""),
                "client mismatch",
            )
        # The registry card is the authority: a pointer-carrying refresh record
        # re-derives its scopes from the card AS IT IS NOW — a hub-side
        # extension rides the next refresh, and a revoked card (gone) ends the
        # session. Records without a pointer keep their frozen scopes.
        scopes = rec["scopes"]
        operations = list(rec.get("operations") or [])
        resource_grants = rec.get("resource_grants")
        resource_operations = rec.get("resource_operations")
        account_scope: Mapping[str, Mapping[str, list[str] | tuple[str, ...]]] | None = None
        card_pointer = str(rec.get("registry_access_id") or "").strip()
        refresh_card_kind = str(rec.get("card_kind") or "").strip()
        if card_pointer:
            tenant, project = oauth_tenant_project(request)
            credential = rec.get("credential")
            credential = credential if isinstance(credential, Mapping) else {}
            # A projection lost to eviction or removed by a migration is not a
            # revoked Card: with the durable store the lookup reads through to
            # the committed revision, as the MCP surface guard does.
            resolvers = delegated_serving_resolvers(request)
            card_store = getattr(resolvers, "cards", None) or delegated_card_store(
                tenant=tenant,
                project=project,
            )
            try:
                card = await resolve_live_grant_card(
                    store.redis,
                    tenant=tenant,
                    project=project,
                    access_id=card_pointer,
                    expected_client_id=str(rec.get("client_id") or ""),
                    expected_grantor_subject=str(rec.get("sub") or ""),
                    expected_delegate_subject=str(credential.get("subject") or ""),
                    card_store=card_store,
                )
            except LiveGrantCardError as exc:
                LOGGER.warning(
                    "[connection-hub.oauth] refresh denied reason=live_grant_%s client_id=%s",
                    exc.reason,
                    str(rec.get("client_id") or ""),
                )
                retry_after = _TRANSIENT_LIVE_GRANT_REASONS.get(exc.reason)
                if retry_after is not None:
                    return _token_error(
                        "temporarily_unavailable",
                        "current delegated authorization state is unavailable",
                        status=503,
                        retry_after_seconds=retry_after,
                    )
                return _token_error(
                    "invalid_grant",
                    "current delegated authorization state is unavailable",
                )
            if card is None:
                return _refresh_refused(
                    "card_revoked_or_expired",
                    str(rec.get("client_id") or ""),
                    "delegated consent was revoked",
                )
            refresh_card_kind = card.card_kind
            whole_card = refresh_card_kind in {
                CARD_KIND_AGENT,
                CARD_KIND_AUTOMATION,
            }
            live_scopes = (
                whole_card_grants(card)
                if whole_card
                else live_grants_for_resource(
                    card,
                    str(rec.get("resource") or "") or "*",
                )
            )
            if live_scopes is None:
                return _refresh_refused(
                    "card_resource_not_covered",
                    str(rec.get("client_id") or ""),
                    "delegated consent no longer covers this resource",
                )
            scopes = list(live_scopes)
            operations = list(card.operations)
            resource_grants = dict(card.resource_grants)
            resource_operations = dict(card.resource_operations)
            account_scope = card.account_scope
        try:
            new_rt = await store.rotate_refresh_token(
                rt,
                scopes=list(scopes),
                operations=list(operations),
                resource_grants=resource_grants,
                resource_operations=resource_operations,
                resource=(
                    ""
                    if refresh_card_kind in {CARD_KIND_AGENT, CARD_KIND_AUTOMATION}
                    else None
                ),
                card_kind=refresh_card_kind or None,
                state=refresh_state,
            )
        except RefreshTokenReuseDetected:
            return _refresh_refused(
                "refresh_token_reuse_detected",
                str(rec.get("client_id") or ""),
                "refresh token reuse detected; the credential family was revoked",
            )
        if not new_rt:
            return _refresh_refused(
                "refresh_token_rotation_lost",
                str(rec.get("client_id") or ""),
                "refresh token invalid or expired",
            )
        try:
            issued = await _issue_tokens(
                request, store,
                sub=rec["sub"], scopes=scopes, client_id=rec["client_id"],
                operations=operations,
                resource_grants=resource_grants,
                resource_operations=resource_operations,
                resource=rec.get("resource"),
                registry_access_id=card_pointer,
                card_kind=refresh_card_kind,
                identity_scope=rec.get("identity_scope") or "",
                grantor_authority=rec.get("grantor_authority") or {},
                delegation_edges=rec.get("delegation_edges") or [],
                named_services=rec.get("named_services") or {},
                refresh_token=new_rt,
                account_scope=account_scope,
                client_metadata=rec.get("client_metadata") or {},
            )
        except Exception:
            LOGGER.exception(
                "[connection-hub.oauth] token issuance raised after refresh "
                "rotation client_id=%s",
                str(rec.get("client_id") or ""),
            )
            restored = await _restore_failed_refresh_rotation(
                store,
                refresh_token=str(rt),
                replacement_token=new_rt,
                refresh_state=refresh_state,
                client_id=str(rec.get("client_id") or ""),
            )
            return _refresh_issuance_unavailable(
                restored=restored,
                cause_error="token_issuance_raised",
                client_id=str(rec.get("client_id") or ""),
            )
        issuance_status = int(getattr(issued, "status_code", 500))
        if issuance_status >= 500:
            restored = await _restore_failed_refresh_rotation(
                store,
                refresh_token=str(rt),
                replacement_token=new_rt,
                refresh_state=refresh_state,
                client_id=str(rec.get("client_id") or ""),
            )
            return _refresh_issuance_unavailable(
                restored=restored,
                cause=issued,
                client_id=str(rec.get("client_id") or ""),
            )
        if issuance_status >= 400:
            LOGGER.warning(
                "[connection-hub.oauth] refresh rotation remains consumed after "
                "authority refusal status=%s client_id=%s",
                issuance_status,
                str(rec.get("client_id") or ""),
            )
        return issued

    return _token_error("unsupported_grant_type", f"unsupported grant_type: {grant_type}")


@router.post("/oauth/revoke", include_in_schema=False)
@_normalize_grant_store_unavailable
async def revoke(request: Request) -> Response:
    """RFC 7009 token revocation. A client that disconnects can revoke its
    refresh or access token here; the matching Connection Hub card is retired
    with it (its other live token material dies too), so a clean disconnect
    leaves no orphan card. Per the RFC, unknown tokens still return 200 —
    revocation must be idempotent and non-probing."""
    form = await request.form()
    token = str(form.get("token") or "").strip()
    # Arrival log FIRST: while verifying client disconnect behavior we need
    # evidence of whether the client calls revocation at all.
    LOGGER.info(
        "[connection-hub.oauth] rfc7009 revoke called hint=%s client_id=%s token_present=%s",
        str(form.get("token_type_hint") or "-"),
        str(form.get("client_id") or "-"),
        bool(token),
    )
    if not token:
        return _token_error("invalid_request", "missing token")
    store = get_grant_store(request)

    card_pointer = ""
    grantor_subject = ""
    refresh_rec = await store.validate_refresh_token(token)
    grant_rec = None
    if refresh_rec is not None:
        card_pointer = str(refresh_rec.get("registry_access_id") or "").strip()
        grantor_subject = str(refresh_rec.get("sub") or "").strip()
    else:
        grant_rec = await store.get_access_grant_record(token)
        if grant_rec is not None:
            card_pointer = str(grant_rec.get("registry_access_id") or "").strip()
            credential = grant_rec.get("credential") or {}
            grantor_subject = str(credential.get("grantor_subject") or "").strip()
    LOGGER.info(
        "[connection-hub.oauth] rfc7009 revoke resolved kind=%s card=%s subject_present=%s",
        "refresh" if refresh_rec is not None else ("access" if card_pointer or grantor_subject else "unknown"),
        card_pointer or "-",
        bool(grantor_subject),
    )

    if card_pointer and grantor_subject:
        try:
            service = get_automation_access(request)
            card_result = await service.revoke_access(
                {"user_id": grantor_subject},
                access_id=card_pointer,
            )
        except Exception:
            LOGGER.exception(
                "[connection-hub.oauth] rfc7009 card retirement failed card=%s", card_pointer
            )
            return _token_error(
                "temporarily_unavailable",
                "Delegated access revocation is temporarily unavailable.",
                status=503,
            )
        if not isinstance(card_result, Mapping) or card_result.get("ok") is not True:
            LOGGER.warning(
                "[connection-hub.oauth] rfc7009 card retirement refused card=%s code=%s",
                card_pointer,
                str(
                    card_result.get("error")
                    if isinstance(card_result, Mapping)
                    else "invalid_result"
                ),
            )
            return _token_error(
                "temporarily_unavailable",
                "Delegated access revocation is temporarily unavailable.",
                status=503,
            )
        LOGGER.info(
            "[connection-hub.oauth] rfc7009 revocation retired card=%s", card_pointer
        )

    # Keep the submitted record until durable card retirement succeeds. This
    # preserves the card pointer for a retry when its owning store is down.
    if refresh_rec is not None:
        await store.revoke_refresh_token(token)
    elif grant_rec is not None:
        await store.revoke_access_grant(token)
    # 200 with an empty JSON body whether or not the token was known.
    return JSONResponse({}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
