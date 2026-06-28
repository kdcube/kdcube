# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""
/oauth/authorize + /oauth/authorize/consent routes.

GET renders the consent screen for an authenticated admin; POST issues an
authorization code (on approve) or bounces ``access_denied`` (on deny). The
consent POST re-validates client/redirect/PKCE — it never trusts the rendered
hidden fields blindly — and restricts granted tools to those valid for the scope.
"""
from __future__ import annotations

import inspect
from typing import Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from kdcube_ai_app.apps.chat.ingress.oauth_mcp.clients import (
    client_from_record,
    dcr_redirect_allowed,
    get_client,
)
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.config import oauth_mcp_config
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.consent import render_consent_html, tools_for_scopes
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.authority import build_oauth_mcp_credential
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.deps import (
    extract_bearer,
    get_access_token_minter,
    get_authenticate,
    get_grant_store,
    is_admin,
    oauth_tenant_project,
)
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.discovery import resolve_issuer
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.flow import (
    AuthorizeError,
    build_redirect,
    parse_authorize_request,
)
from kdcube_ai_app.apps.chat.ingress.oauth_mcp.pkce import verify_s256

router = APIRouter()

_AUTHORIZE_FORM_KEYS = (
    "client_id", "redirect_uri", "response_type", "scope",
    "state", "code_challenge", "code_challenge_method",
)


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


async def _require_admin(request: Request) -> Tuple[Optional[dict], Optional[Response]]:
    token = extract_bearer(request)
    if not token:
        return None, JSONResponse(status_code=401, content={"error": "login_required"})
    user = await get_authenticate(request)(token)
    if not user:
        return None, JSONResponse(status_code=401, content={"error": "login_required"})
    if not is_admin(user.get("roles")):
        return None, JSONResponse(
            status_code=403,
            content={"error": "forbidden", "error_description": "admin role required"},
        )
    return user, None


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
    try:
        req = parse_authorize_request(
            params,
            client_resolver=resolver,
            public_client_resolver=lambda cid: get_client(cid, request),
        )
    except AuthorizeError as err:
        return _error_response(err, issuer)

    user, denied = await _require_admin(request)
    if denied is not None:
        # Browser entry point: on a missing session, send the user to the platform
        # login with a return-to (url-encoded so the multi-param authorize URL
        # survives) instead of a dead-end JSON 401. 403 (authenticated non-admin)
        # still returns its JSON.
        if getattr(denied, "status_code", None) == 401:
            from urllib.parse import quote
            return_to = request.url.path
            if request.url.query:
                return_to += "?" + request.url.query
            return RedirectResponse(f"/signin?next={quote(return_to, safe='')}", status_code=302)
        return denied

    # Synchronizer CSRF token bound to the consenting admin, embedded in the form.
    csrf = await get_grant_store(request).create_csrf_token(user["sub"])
    # trusted = a statically pre-registered client (not a dynamically-registered one),
    # so the consent screen can flag unknown clients for anti-phishing.
    trusted = get_client(req.client_id, request) is not None
    return HTMLResponse(
        render_consent_html(
            req, issuer, csrf_token=csrf, trusted=trusted, brand=oauth_mcp_config(request).brand
        )
    )


@router.post("/oauth/authorize/consent", include_in_schema=False)
async def authorize_consent(request: Request) -> Response:
    issuer = resolve_issuer(request)
    form = await request.form()
    params = {k: form.get(k) for k in _AUTHORIZE_FORM_KEYS}
    resolver = await _dyn_client_resolver(request, params.get("client_id"))
    try:
        req = parse_authorize_request(
            params,
            client_resolver=resolver,
            public_client_resolver=lambda cid: get_client(cid, request),
        )
    except AuthorizeError as err:
        return _error_response(err, issuer)

    user, denied = await _require_admin(request)
    if denied is not None:
        return denied

    store = get_grant_store(request)

    # CSRF: the consent POST must carry the single-use token minted for THIS admin
    # at GET /oauth/authorize. Blocks a forged cross-site POST riding the session
    # cookie. Checked before the decision branch so deny is protected too.
    if not await store.consume_csrf_token(form.get("csrf_token"), user["sub"]):
        return JSONResponse(
            status_code=403,
            content={"error": "invalid_csrf", "error_description": "CSRF token missing, expired, or invalid"},
        )

    if (form.get("decision") or "").strip() != "approve":
        url = build_redirect(
            req.redirect_uri, {"error": "access_denied", "state": req.state, "iss": issuer}
        )
        return RedirectResponse(url, status_code=302)

    valid_tools = {name for name, _ in tools_for_scopes(req.scopes)}
    selected = [t for t in form.getlist("tools") if t in valid_tools]
    code = await store.create_auth_code(
        client_id=req.client_id,
        redirect_uri=req.redirect_uri,
        code_challenge=req.code_challenge,
        sub=user["sub"],
        scopes=req.scopes,
        tools=selected,
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


async def _issue_tokens(request, store, *, sub, scopes, client_id, tools, refresh_token=None) -> JSONResponse:
    tenant, project = oauth_tenant_project(request)
    # This is the common credential envelope understood by the Connection Hub
    # authority SDK. The access token remains a real kst1 session token; the
    # envelope is the routing/verification hint carried inside that token and in
    # the grant store.
    credential = build_oauth_mcp_credential(
        grantor_subject=sub,
        client_id=client_id,
        scopes=scopes,
        tools=tools,
        tenant=tenant,
        project=project,
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
        credential = build_oauth_mcp_credential(
            grantor_subject=sub,
            client_id=client_id,
            scopes=scopes,
            tools=tools,
            tenant=tenant,
            project=project,
            expires_in=expires_in,
        )
    # Bind the consented tool allowlist to THIS access token so /mcp tools/call can
    # enforce it (the consent screen's per-tool selection is meaningless otherwise).
    await store.bind_access_grant(access_token, tools, expires_in, authority=credential.to_dict())
    if refresh_token is None:
        refresh_token = await store.create_refresh_token(
            client_id=client_id, sub=sub, scopes=scopes, tools=tools,
            authority=credential.to_dict(),
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
            refresh_token=new_rt,
        )

    return _token_error("unsupported_grant_type", f"unsupported grant_type: {grant_type}")
