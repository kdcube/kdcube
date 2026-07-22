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
from typing import Any, Iterable, Mapping, Optional, Tuple
from urllib.parse import parse_qs, quote, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.clients import (
    client_from_record,
    dcr_redirect_allowed,
    get_client,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.config import (
    OAuthDelegatedClientConfig,
    oauth_delegated_config,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.consent import (
    platform_edge_grants_for_scopes,
    render_consent_html,
    tools_for_scopes,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.authority import build_delegated_client_credential
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.http.deps import (
    extract_bearer,
    get_access_token_minter,
    get_authenticate,
    get_grant_store,
    is_admin,
    oauth_tenant_project,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.http.discovery import resolve_issuer
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.flow import (
    AuthorizeError,
    AuthorizeRequest,
    build_redirect,
    parse_authorize_request,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.pkce import verify_s256
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_registry_config import (
    resolve_authority_provider_instance,
)
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import call_bundle_operation
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory import (
    AuthorityGrantInventory,
    PlatformAuthorityInventoryProvider,
    platform_identity_from_user,
    selected_delegation_edge,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.mcp_metadata import (
    kdcube_icon_url,
    kdcube_website_url,
)

router = APIRouter()
LOGGER = logging.getLogger("kdcube.connection_hub.oauth")

_AUTHORIZE_FORM_KEYS = (
    "client_id", "redirect_uri", "response_type", "scope",
    "resource", "state", "code_challenge", "code_challenge_method",
)


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
    }
    return {
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
    return HTMLResponse(html)


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
    )


async def _dyn_client_resolver(request: Request, client_id: Optional[str]):
    """A sync resolver for the one client_id in play, backed by the DCR store."""
    if not client_id:
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


@router.post("/oauth/register", include_in_schema=False)
async def register_client(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_redirect_uri", "error_description": "redirect_uris is required"},
        )
    # DCR is open (pre-auth), so restrict registrable redirects to the trusted set
    # (claude.ai callback + loopback) — an attacker cannot register a client that
    # delivers a stolen code to their own server.
    if not all(dcr_redirect_allowed(u, request) for u in redirect_uris):
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
    }
    record = await get_grant_store(request).register_client(
        redirect_uris=redirect_uris, metadata=metadata
    )
    content = {
        "client_id": record["client_id"],
        "redirect_uris": record["redirect_uris"],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "client_name": body.get("client_name"),
    }
    if logo_uri:
        content["logo_uri"] = logo_uri
    if client_uri:
        content["client_uri"] = client_uri
    return JSONResponse(status_code=201, content=content)


@router.get("/oauth/authorize", include_in_schema=False)
async def authorize(request: Request) -> Response:
    issuer = resolve_issuer(request)
    params = dict(request.query_params)
    resolver = await _dyn_client_resolver(request, params.get("client_id"))
    cfg = oauth_delegated_config(request)
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
            return RedirectResponse(f"/signin/?next={quote(return_to, safe='')}", status_code=302)
        return denied

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

    # Synchronizer CSRF token bound to the consenting user, embedded in the form.
    subject = _user_subject(user or {})
    if not subject:
        return JSONResponse(status_code=401, content={"error": "login_required"})
    csrf = await get_grant_store(request).create_csrf_token(subject)
    LOGGER.info(
        "[connection-hub.oauth] authorize csrf_minted subject=%s client_id=%s resource=%s",
        subject,
        req.client_id,
        req.resource or "",
    )
    # trusted = a statically pre-registered client (not a dynamically-registered one),
    # so the consent screen can flag unknown clients for anti-phishing.
    trusted = get_client(req.client_id, request) is not None
    custom = await _render_custom_consent_if_configured(
        request,
        req=render_req,
        issuer=issuer,
        csrf_token=csrf,
        trusted=trusted,
        cfg=cfg,
        grantor_subject=_user_subject(user or {}),
        grantor_label=_user_label(user or {}),
    )
    if custom is not None:
        return custom
    connected_accounts = await _connected_accounts_for_consent(subject)
    seeded_account_scope = await _seed_account_scope_for_consent(
        request, subject=subject, client_id=req.client_id, resource=req.resource, cfg=cfg,
    )
    return HTMLResponse(
        render_consent_html(
            render_req,
            issuer,
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
        )
    )


async def _connected_accounts_for_consent(subject: str) -> list[dict]:
    """The consenting user's connected provider accounts, for the consent
    screen's per-account picker. Best-effort: an empty list renders the
    text fallback, never an error."""
    try:
        from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.store import (
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


async def _seed_account_scope_for_consent(
    request, *, subject: str, client_id: str, resource: str, cfg
) -> dict:
    """Pre-check the picker from the client's existing card (re-consent) and
    from a DCR sibling this consent will supersede."""
    try:
        from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
            AutomationAccessService,
        )

        store = get_grant_store(request)
        tenant, project = oauth_tenant_project(request)
        service = AutomationAccessService(
            redis=store.redis, tenant=tenant, project=project, config=cfg, grant_store=store,
        )
        return await service.oauth_seed_account_scope(
            grantor_subject=subject, client_id=client_id, resource=str(resource or ""),
        )
    except Exception:
        LOGGER.exception("[connection-hub.oauth] consent account-scope seed failed")
        return {}


@router.post("/oauth/authorize/consent", include_in_schema=False)
async def authorize_consent(request: Request) -> Response:
    issuer = resolve_issuer(request)
    form = await request.form()
    params = _consent_authorize_params(request, form)
    resolver = await _dyn_client_resolver(request, params.get("client_id"))
    cfg = oauth_delegated_config(request)
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

    # CSRF: the consent POST must carry the single-use token minted for THIS user
    # at GET /oauth/authorize. Blocks a forged cross-site POST riding the session
    # cookie. Checked before the decision branch so deny is protected too.
    csrf_value = form.get("csrf_token")
    if hasattr(store, "consume_csrf_token_with_reason"):
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

    if (form.get("decision") or "").strip() != "approve":
        url = build_redirect(
            req.redirect_uri, {"error": "access_denied", "state": req.state, "iss": issuer}
        )
        return RedirectResponse(url, status_code=302)

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
    selected_operations = [t for t in form.getlist("tools") if t in valid_tools]
    resource_cfg = cfg.resource_config(req.resource)
    named_services = dict(resource_cfg.named_services or {}) if resource_cfg is not None else {}
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
        "account_scope_providers=%s",
        req.client_id, len(selected_scopes), len(selected_operations),
        sorted(account_scope.keys()) or "-",
    )
    code = await store.create_auth_code(
        client_id=req.client_id,
        redirect_uri=req.redirect_uri,
        code_challenge=req.code_challenge,
        sub=subject,
        scopes=selected_scopes,
        operations=selected_operations,
        resource=req.resource,
        identity_scope=resource_cfg.identity_scope if resource_cfg is not None else "",
        grantor_authority=grantor_authority,
        delegation_edges=delegation_edges,
        named_services=named_services,
        account_scope=account_scope,
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

    response = RedirectResponse(f"/signin/?next={quote(next_url, safe='')}", status_code=302)
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
    resource=None,
    identity_scope="",
    grantor_authority=None,
    delegation_edges=None,
    named_services=None,
    refresh_token=None,
    account_scope=None,
) -> JSONResponse:
    tenant, project = oauth_tenant_project(request)
    # This is the common credential envelope understood by the Connection Hub
    # authority SDK. The access token remains a real kst1 session token; the
    # envelope is the routing/verification hint carried inside that token and in
    # the grant store.
    credential = build_delegated_client_credential(
        grantor_subject=sub,
        client_id=client_id,
        scopes=scopes,
        operations=operations,
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
    from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
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
        registry_access_id=registry_access_id,
    )
    if refresh_token is None:
        refresh_token = await store.create_refresh_token(
            client_id=client_id, sub=sub, scopes=scopes, operations=operations,
            resource=resource,
            identity_scope=identity_scope,
            credential=credential.to_dict(),
            grantor_authority=dict(grantor_authority or {}),
            delegation_edges=list(delegation_edges or []),
            named_services=dict(named_services or {}),
            registry_access_id=registry_access_id,
        )
    # Register the grant in the user's Connection Hub registry (Delegated by
    # KDCube tab) so the connection is visible and revocable. Registry write
    # failures must never fail token issuance.
    try:
        from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
            AutomationAccessService,
        )

        client_record = await store.get_client_record(client_id) or {}
        client_metadata = dict(client_record.get("metadata") or {})
        client_label = str(
            client_metadata.get("client_name")
            or client_metadata.get("name")
            or client_metadata.get("client_uri")
            or ""
        ).strip()
        service = AutomationAccessService(
            redis=store.redis,
            tenant=tenant,
            project=project,
            config=oauth_delegated_config(request),
            grant_store=store,
        )
        await service.record_oauth_grant(
            grantor_subject=sub,
            client_id=client_id,
            client_label=client_label,
            scopes=scopes,
            operations=operations,
            resource=str(resource or ""),
            identity_scope=identity_scope,
            access_token=access_token,
            refresh_token=str(refresh_token or ""),
            account_scope=account_scope,
        )
    except Exception:
        LOGGER.exception("[connection-hub.oauth] failed to record delegated grant client=%s", client_id)
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "refresh_token": refresh_token,
            "scope": " ".join(scopes),
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/oauth/token", include_in_schema=False)
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
            resource=payload.get("resource"),
            identity_scope=payload.get("identity_scope") or "",
            grantor_authority=payload.get("grantor_authority") or {},
            delegation_edges=payload.get("delegation_edges") or [],
            named_services=payload.get("named_services") or {},
            account_scope=payload.get("account_scope") or None,
        )

    if grant_type == "refresh_token":
        rt = form.get("refresh_token")
        client_id = form.get("client_id")
        if not rt:
            return _token_error("invalid_request", "missing refresh_token")
        rec = await store.validate_refresh_token(rt)
        if rec is None:
            return _token_error("invalid_grant", "refresh token invalid or expired")
        if client_id and rec["client_id"] != client_id:
            return _token_error("invalid_grant", "client mismatch")
        new_rt = await store.rotate_refresh_token(rt)
        # The registry card is the authority: a pointer-carrying refresh record
        # re-derives its scopes from the card AS IT IS NOW — a hub-side
        # extension rides the next refresh, and a revoked card (gone) ends the
        # session. Records without a pointer keep their frozen scopes.
        scopes = rec["scopes"]
        card_pointer = str(rec.get("registry_access_id") or "").strip()
        if card_pointer:
            from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
                automation_record_key,
            )

            tenant, project = oauth_tenant_project(request)
            raw_card = await store.redis.get(automation_record_key(tenant, project, card_pointer))
            if raw_card is None:
                return _token_error("invalid_grant", "delegated consent was revoked")
            try:
                card = json.loads(raw_card)
                card_map = card.get("resource_grants") or {}
                entry = card_map.get(str(rec.get("resource") or "") or "*")
                if isinstance(entry, list) and entry:
                    scopes = [str(scope) for scope in entry]
            except Exception:
                scopes = rec["scopes"]
        return await _issue_tokens(
            request, store,
            sub=rec["sub"], scopes=scopes, client_id=rec["client_id"],
            operations=rec.get("operations") or [],
            resource=rec.get("resource"),
            identity_scope=rec.get("identity_scope") or "",
            grantor_authority=rec.get("grantor_authority") or {},
            delegation_edges=rec.get("delegation_edges") or [],
            named_services=rec.get("named_services") or {},
            refresh_token=new_rt,
        )

    return _token_error("unsupported_grant_type", f"unsupported grant_type: {grant_type}")


@router.post("/oauth/revoke", include_in_schema=False)
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
    if refresh_rec is not None:
        card_pointer = str(refresh_rec.get("registry_access_id") or "").strip()
        grantor_subject = str(refresh_rec.get("sub") or "").strip()
        await store.revoke_refresh_token(token)
    else:
        grant_rec = await store.get_access_grant_record(token)
        if grant_rec is not None:
            card_pointer = str(grant_rec.get("registry_access_id") or "").strip()
            credential = grant_rec.get("credential") or {}
            grantor_subject = str(credential.get("grantor_subject") or "").strip()
            await store.revoke_access_grant(token)
    LOGGER.info(
        "[connection-hub.oauth] rfc7009 revoke resolved kind=%s card=%s subject_present=%s",
        "refresh" if refresh_rec is not None else ("access" if card_pointer or grantor_subject else "unknown"),
        card_pointer or "-",
        bool(grantor_subject),
    )

    if card_pointer and grantor_subject:
        try:
            from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.automation_access import (
                AutomationAccessService,
            )

            tenant, project = oauth_tenant_project(request)
            service = AutomationAccessService(
                redis=store.redis,
                tenant=tenant,
                project=project,
                config=oauth_delegated_config(request),
                grant_store=store,
            )
            await service.revoke_access({"user_id": grantor_subject}, access_id=card_pointer)
            LOGGER.info(
                "[connection-hub.oauth] rfc7009 revocation retired card=%s", card_pointer
            )
        except Exception:
            LOGGER.exception(
                "[connection-hub.oauth] rfc7009 card retirement failed card=%s", card_pointer
            )
    # 200 with an empty JSON body whether or not the token was known.
    return JSONResponse({}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
