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
)
from connection_hub.delegated_credentials.cards.resolver import (
    CardUnavailable,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.serving import (
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
from connection_hub.delegated_credentials.oauth.flow import (
    AuthorizeError,
    AuthorizeRequest,
    build_redirect,
    parse_authorize_request,
)
from connection_hub.delegated_credentials.oauth.pkce import verify_s256
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


def _oauth_card_label(
    client_metadata: Mapping[str, Any] | None,
    *,
    resource: str,
    explicit: str = "",
) -> str:
    """The owner-visible card name, including the entry door and agent id."""

    if str(explicit or "").strip():
        return str(explicit).strip()
    metadata = dict(client_metadata or {})
    label = str(
        metadata.get("client_name")
        or metadata.get("name")
        or metadata.get("client_uri")
        or ""
    ).strip()
    door_path = str(resource or "").split("?", 1)[0].rstrip("*").rstrip("/")
    door_alias = door_path.rsplit("/mcp/", 1)[-1].strip("/") if "/mcp/" in door_path else ""
    if door_alias and door_alias.lower() not in label.lower():
        label = f"{label} · {door_alias}" if label else door_alias
    asserted = metadata.get("client_metadata")
    asserted = asserted if isinstance(asserted, Mapping) else {}
    agent_id = str(
        asserted.get("kdcube_agent_id")
        or asserted.get("kdcube_worker_id")
        or ""
    ).strip()
    if agent_id and agent_id.lower() not in label.lower():
        label = f"{label} · {agent_id}" if label else agent_id
    return label or str(metadata.get("client_id") or "Connected client")


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
        application_type=application_type,
        metadata=metadata,
    )
    content = dict(asserted_metadata)
    content.update({
        "client_id": record["client_id"],
        "redirect_uris": record["redirect_uris"],
        "token_endpoint_auth_method": "none",
        "application_type": application_type,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": body.get("client_name"),
    })
    if logo_uri:
        content["logo_uri"] = logo_uri
    if client_uri:
        content["client_uri"] = client_uri
    return JSONResponse(status_code=201, content=content)


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

    inventory = await _platform_grant_inventory(user or {}, req.scopes, cfg=cfg, resource=req.resource)
    _log_inventory(stage="authorize", user=user or {}, scopes=req.scopes, inventory=inventory, resource=req.resource)
    visible_scopes = [scope for scope in req.scopes if scope in set(inventory.grant_names())]
    if not visible_scopes:
        delegation_denied = _delegation_denial(req.scopes, inventory, resource=req.resource)
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

    delegation_denied = _delegation_denial(visible_scopes, inventory, resource=req.resource)
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
    seeded_account_scope = await _seed_account_scope_for_consent(
        request, subject=subject, client_id=req.client_id, resource=req.resource, cfg=cfg,
    )
    seeded_named_service_operations = await _seed_named_service_operations_for_consent(
        request, subject=subject, client_id=req.client_id, resource=req.resource,
    )
    seeded_resource_operations = await _seed_resource_operations_for_consent(
        request, subject=subject, client_id=req.client_id, resource=req.resource,
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
    request, *, subject: str, client_id: str, resource: str, cfg
) -> dict:
    """Pre-check the picker from this exact client's existing Card."""
    try:
        service = get_automation_access(request)
        return await service.oauth_seed_account_scope(
            grantor_subject=subject, client_id=client_id, resource=str(resource or ""),
        )
    except Exception:
        LOGGER.exception("[connection-hub.oauth] consent account-scope seed failed")
        return {}


async def _seed_named_service_operations_for_consent(
    request, *, subject: str, client_id: str, resource: str
) -> dict:
    """Pre-check the operations picker from the client's existing card.

    Without it a re-consent renders an empty picker while a submission
    replaces, so leaving the section alone silently drops every operation the
    card holds.
    """
    try:
        service = get_automation_access(request)
        return await service.oauth_seed_named_service_operations(
            grantor_subject=subject, client_id=client_id, resource=str(resource or ""),
        )
    except Exception:
        LOGGER.exception("[connection-hub.oauth] consent named-service seed failed")
        return {}


async def _seed_resource_operations_for_consent(
    request, *, subject: str, client_id: str, resource: str
) -> dict[str, list[str]]:
    """Pre-check owner-resource operations held by this OAuth client."""
    try:
        service = get_automation_access(request)
        return await service.oauth_seed_resource_operations(
            grantor_subject=subject,
            client_id=client_id,
            resource=str(resource or ""),
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
    req, cfg, invalid = await _request_from_consent_draft(
        request,
        subject=subject,
        context=context,
    )
    if invalid is not None:
        return invalid
    if req is None or cfg is None:
        return _consent_draft_error("invalid")

    inventory = await _platform_grant_inventory(
        user or {},
        req.scopes,
        cfg=cfg,
        resource=req.resource,
    )
    denied_grants = _delegation_denial(req.scopes, inventory, resource=req.resource)
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
    existing = seed.get("access") if isinstance(seed.get("access"), Mapping) else {}
    resource_grants = {
        str(resource): [str(grant) for grant in grants or ()]
        for resource, grants in dict(existing.get("resource_grants") or {}).items()
    }
    entry_grants = resource_grants.setdefault(req.resource, [])
    for grant in req.scopes:
        if grant not in entry_grants:
            entry_grants.append(grant)
    requested_operations = [
        tool.name
        for tool in cfg.tools_for_scopes(req.scopes, resource=req.resource)
    ]
    if existing:
        resource_operations = {
            str(resource): [str(operation) for operation in operations or ()]
            for resource, operations in dict(existing.get("resource_operations") or {}).items()
        }
        entry_operations = resource_operations.setdefault(req.resource, [])
        for operation in requested_operations:
            if operation not in entry_operations:
                entry_operations.append(operation)
    else:
        resource_operations = {
            req.resource: requested_operations
        }
    invocation_policies = {
        resource: {operation: "always" for operation in operations}
        for resource, operations in resource_operations.items()
    }
    for policy in existing.get("invocation_policies") or ():
        authority = policy.get("authority") if isinstance(policy, Mapping) else {}
        if not isinstance(authority, Mapping) or authority.get("surface") != "outer":
            continue
        resource = str(authority.get("resource") or "")
        operation = str(authority.get("operation") or "")
        mode = str(policy.get("mode") or "")
        if operation in resource_operations.get(resource, ()) and mode in {"always", "once"}:
            invocation_policies.setdefault(resource, {})[operation] = mode

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
        "account_requirements": _account_requirements_payload(requirements),
        "catalog_scope": seed.get("catalog_scope") or {
            "mode": "entry",
            "resources": [req.resource],
        },
        "selection": {
            "label": str(existing.get("label") or derived_label),
            "resource_grants": resource_grants,
            "resource_operations": resource_operations,
            "invocation_policies": invocation_policies,
            "named_service_operations": (
                existing.get("effective_named_service_operations")
                or existing.get("named_service_operations")
                or {}
            ),
            "account_scope": existing.get("account_scope") or {},
            "catalog_row_by_resource": (
                existing.get("catalog_row_by_resource")
                or seed.get("catalog_row_by_resource")
                or {}
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
    )
    if resolved.get("ok") is not True:
        return JSONResponse(
            status_code=int(resolved.get("status") or 400),
            content=resolved,
        )
    selected_resource_operations = dict(resolved.get("resource_operations") or {})
    selected_policy_keys = {
        (str(resource), str(operation))
        for resource, operations in selected_resource_operations.items()
        for operation in operations or ()
    }
    submitted_policies = {
        (str(resource), str(operation)): str(mode or "").strip().lower()
        for resource, operations in dict(invocation_policies).items()
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

    code = await store.create_auth_code(
        client_id=req.client_id,
        redirect_uri=req.redirect_uri,
        code_challenge=req.code_challenge,
        sub=subject,
        scopes=list(
            dict(resolved.get("resource_grants") or {}).get(req.resource, ())
        ),
        operations=list(resolved.get("operations") or ()),
        resource_grants=dict(resolved.get("resource_grants") or {}),
        resource_operations=selected_resource_operations,
        resource=req.resource,
        identity_scope=str(resolved.get("identity_scope") or ""),
        grantor_authority=grantor_authority,
        delegation_edges=list(grantor_authority.get("delegation_edges") or []),
        named_services=dict(resolved.get("named_services") or {}),
        named_service_operations=resolved.get("named_service_operations") or {},
        catalog_version=str(resolved.get("catalog_version") or ""),
        account_scope=dict(resolved.get("account_scope") or {}),
        client_metadata=req.client.snapshot() if req.client is not None else {},
        card_label=str(payload.get("label") or "").strip(),
        invocation_policies=dict(invocation_policies),
        expected_card_revision=int(resolved.get("card_revision") or 0),
    )
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

    inventory = await _platform_grant_inventory(user or {}, req.scopes, cfg=cfg, resource=req.resource)
    _log_inventory(stage="authorize.consent", user=user or {}, scopes=req.scopes, inventory=inventory, resource=req.resource)
    delegation_denied = _delegation_denial(selected_scopes, inventory, resource=req.resource)
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
    resource_grants = {
        req.resource: list(selected_scopes),
        **child_resource_grants,
    }
    resource_operations = {
        req.resource: direct_operations,
        **child_resource_operations,
    }
    selected_operations = list(operation_union(resource_operations))
    resource_cfg = cfg.resource_config(req.resource)
    named_services = dict(resource_cfg.named_services or {}) if resource_cfg is not None else {}
    named_service_operations = _selected_named_service_operations(
        form, scopes=selected_scopes, cfg=cfg, resource=req.resource,
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


def _token_error(error: str, description: str = "", status: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": error, "error_description": description},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


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
    identity_scope="",
    grantor_authority=None,
    delegation_edges=None,
    named_services=None,
    named_service_operations=None,
    catalog_version="",
    refresh_token=None,
    account_scope=None,
    client_metadata=None,
    card_label="",
    invocation_policies=None,
    replace_authority=False,
    expected_card_revision=None,
) -> JSONResponse:
    tenant, project = oauth_tenant_project(request)
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
        resource=resource,
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
            resource=resource,
            identity_scope=identity_scope,
            expires_in=expires_in,
        )
    # Bind the consented operation allowlist to THIS access token so managed
    # guards can enforce it. The binding carries the registry-card POINTER: the
    # guard resolves the card live, so hub-side extend/narrow/revoke apply to
    # this bearer immediately.
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.automation_access import (
        oauth_access_id,
    )

    registry_access_id = oauth_access_id(sub, client_id, str(resource or ""))
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
        registry_access_id=registry_access_id,
    )
    if refresh_token is None:
        refresh_token = await store.create_refresh_token(
            client_id=client_id, sub=sub, scopes=scopes, operations=operations,
            resource_grants=grant_map,
            resource_operations=operation_map,
            resource=resource,
            identity_scope=identity_scope,
            credential=credential.to_dict(),
            grantor_authority=dict(grantor_authority or {}),
            delegation_edges=list(delegation_edges or []),
            named_services=dict(named_services or {}),
            registry_access_id=registry_access_id,
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
            resource=str(resource or ""),
            explicit=str(card_label or ""),
        )
        door_path = str(resource or "").split("?", 1)[0].rstrip("*").rstrip("/")
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
            str(resource or ""), door_alias, client_label,
        )
        recorded = await service.record_oauth_grant(
            grantor_subject=sub,
            client_id=client_id,
            client_label=client_label,
            scopes=scopes,
            operations=operations,
            resource_grants=grant_map,
            resource_operations=operation_map,
            resource=str(resource or ""),
            identity_scope=identity_scope,
            access_token=access_token,
            refresh_token=str(refresh_token or ""),
            account_scope=account_scope,
            named_service_operations=named_service_operations,
            catalog_version=catalog_version,
            client_metadata=asserted_metadata,
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
                "invalid_grant",
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
            "access_id": registry_access_id,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/oauth/token", include_in_schema=False)
@_normalize_grant_store_unavailable
async def token(request: Request) -> Response:
    form = await request.form()
    grant_type = (form.get("grant_type") or "").strip()
    store = get_grant_store(request)

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
            identity_scope=payload.get("identity_scope") or "",
            grantor_authority=payload.get("grantor_authority") or {},
            delegation_edges=payload.get("delegation_edges") or [],
            named_services=payload.get("named_services") or {},
            named_service_operations=payload.get("named_service_operations"),
            catalog_version=payload.get("catalog_version") or "",
            account_scope=payload.get("account_scope") or None,
            client_metadata=payload.get("client_metadata") or {},
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
        refresh_state = await store.get_refresh_token_state(rt)
        if refresh_state is None:
            return _token_error("invalid_grant", "refresh token invalid or expired")
        rec = refresh_state.record
        if client_id and rec["client_id"] != client_id:
            return _token_error("invalid_grant", "client mismatch")
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
        if card_pointer:
            tenant, project = oauth_tenant_project(request)
            credential = rec.get("credential")
            credential = credential if isinstance(credential, Mapping) else {}
            try:
                card = await resolve_live_grant_card(
                    store.redis,
                    tenant=tenant,
                    project=project,
                    access_id=card_pointer,
                    expected_client_id=str(rec.get("client_id") or ""),
                    expected_grantor_subject=str(rec.get("sub") or ""),
                    expected_delegate_subject=str(credential.get("subject") or ""),
                )
            except LiveGrantCardError as exc:
                LOGGER.warning(
                    "[connection-hub.oauth] refresh denied reason=live_grant_%s client_id=%s",
                    exc.reason,
                    str(rec.get("client_id") or ""),
                )
                status = 503 if exc.reason == "lookup_unavailable" else 400
                return _token_error(
                    "temporarily_unavailable" if status == 503 else "invalid_grant",
                    "current delegated authorization state is unavailable",
                    status=status,
                )
            if card is None:
                return _token_error("invalid_grant", "delegated consent was revoked")
            live_scopes = live_grants_for_resource(
                card,
                str(rec.get("resource") or "") or "*",
            )
            if live_scopes is None:
                return _token_error("invalid_grant", "delegated consent no longer covers this resource")
            scopes = list(live_scopes)
            operations = list(card.operations)
            resource_grants = dict(card.resource_grants)
            resource_operations = dict(card.resource_operations)
            account_scope = card.account_scope
        new_rt = await store.rotate_refresh_token(
            rt,
            scopes=list(scopes),
            operations=list(operations),
            resource_grants=resource_grants,
            resource_operations=resource_operations,
            state=refresh_state,
        )
        if not new_rt:
            return _token_error("invalid_grant", "refresh token invalid or expired")
        return await _issue_tokens(
            request, store,
            sub=rec["sub"], scopes=scopes, client_id=rec["client_id"],
            operations=operations,
            resource_grants=resource_grants,
            resource_operations=resource_operations,
            resource=rec.get("resource"),
            identity_scope=rec.get("identity_scope") or "",
            grantor_authority=rec.get("grantor_authority") or {},
            delegation_edges=rec.get("delegation_edges") or [],
            named_services=rec.get("named_services") or {},
            refresh_token=new_rt,
            account_scope=account_scope,
            client_metadata=rec.get("client_metadata") or {},
        )

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
