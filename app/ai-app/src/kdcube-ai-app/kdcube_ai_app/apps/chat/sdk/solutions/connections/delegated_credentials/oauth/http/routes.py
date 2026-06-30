# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
/oauth/authorize + /oauth/authorize/consent routes.

GET renders the consent screen for an authenticated user; POST issues an
authorization code (on approve) or bounces ``access_denied`` (on deny). The
consent POST re-validates client/redirect/PKCE — it never trusts the rendered
hidden fields blindly — and restricts granted tools to those valid for the scope.
"""
from __future__ import annotations

import inspect
import logging
from typing import Iterable, Mapping, Optional, Tuple
from urllib.parse import quote

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
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_credentials.oauth.consent import render_consent_html, tools_for_scopes
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
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_inventory import (
    AuthorityGrantInventory,
    PlatformAuthorityInventoryProvider,
    platform_identity_from_user,
    selected_delegation_edge,
)

router = APIRouter()
LOGGER = logging.getLogger("kdcube.connection_hub.oauth")

_AUTHORIZE_FORM_KEYS = (
    "client_id", "redirect_uri", "response_type", "scope",
    "resource", "state", "code_challenge", "code_challenge_method",
)


def _consent_action(request: Request) -> str:
    path = str(request.url.path or "").rstrip("/")
    if path.endswith("/authorize"):
        return f"{path}/consent"
    return "/oauth/authorize/consent"


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
    record = await get_grant_store(request).register_client(
        redirect_uris=redirect_uris, metadata={"client_name": body.get("client_name")}
    )
    return JSONResponse(
        status_code=201,
        content={
            "client_id": record["client_id"],
            "redirect_uris": record["redirect_uris"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": body.get("client_name"),
        },
    )


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
            return RedirectResponse(f"/signin?next={quote(return_to, safe='')}", status_code=302)
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
        )
    )


@router.post("/oauth/authorize/consent", include_in_schema=False)
async def authorize_consent(request: Request) -> Response:
    issuer = resolve_issuer(request)
    form = await request.form()
    params = {k: form.get(k) for k in _AUTHORIZE_FORM_KEYS}
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
    selected = [t for t in form.getlist("tools") if t in valid_tools]
    resource_cfg = cfg.resource_config(req.resource)
    grantor_authority = _grantor_authority(user or {}, scopes=selected_scopes, inventory=inventory)
    delegation_edges = list(grantor_authority.get("delegation_edges") or [])
    code = await store.create_auth_code(
        client_id=req.client_id,
        redirect_uri=req.redirect_uri,
        code_challenge=req.code_challenge,
        sub=subject,
        scopes=selected_scopes,
        tools=selected,
        resource=req.resource,
        identity_scope=resource_cfg.identity_scope if resource_cfg is not None else "",
        grantor_authority=grantor_authority,
        delegation_edges=delegation_edges,
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
    return any(name in params for name in ("client_id", "tools", "credential"))


@router.post("/oauth/logout", include_in_schema=False)
async def oauth_logout(request: Request) -> Response:
    try:
        form = await request.form()
    except Exception:
        form = {}
    next_url = str(form.get("next") or "/").strip()
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"

    response = RedirectResponse(f"/signin?next={quote(next_url, safe='')}", status_code=302)
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
    tools,
    resource=None,
    identity_scope="",
    grantor_authority=None,
    delegation_edges=None,
    refresh_token=None,
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
        tools=tools,
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
            tools=tools,
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
            tools=tools,
            tenant=tenant,
            project=project,
            resource=resource,
            identity_scope=identity_scope,
            expires_in=expires_in,
        )
    # Bind the consented tool allowlist to THIS access token so /mcp tools/call can
    # enforce it (the consent screen's per-tool selection is meaningless otherwise).
    await store.bind_access_grant(
        access_token,
        tools,
        expires_in,
        credential=credential.to_dict(),
        grantor_authority=dict(grantor_authority or {}),
        delegation_edges=list(delegation_edges or []),
    )
    if refresh_token is None:
        refresh_token = await store.create_refresh_token(
            client_id=client_id, sub=sub, scopes=scopes, tools=tools,
            resource=resource,
            identity_scope=identity_scope,
            credential=credential.to_dict(),
            grantor_authority=dict(grantor_authority or {}),
            delegation_edges=list(delegation_edges or []),
        )
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
            tools=payload.get("tools") or [],
            resource=payload.get("resource"),
            identity_scope=payload.get("identity_scope") or "",
            grantor_authority=payload.get("grantor_authority") or {},
            delegation_edges=payload.get("delegation_edges") or [],
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
        return await _issue_tokens(
            request, store,
            sub=rec["sub"], scopes=rec["scopes"], client_id=rec["client_id"],
            tools=rec.get("tools") or [],
            resource=rec.get("resource"),
            identity_scope=rec.get("identity_scope") or "",
            grantor_authority=rec.get("grantor_authority") or {},
            delegation_edges=rec.get("delegation_edges") or [],
            refresh_token=new_rt,
        )

    return _token_error("unsupported_grant_type", f"unsupported grant_type: {grant_type}")
