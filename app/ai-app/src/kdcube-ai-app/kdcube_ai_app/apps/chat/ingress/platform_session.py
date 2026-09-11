# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""The platform's browser sign-in routes: the only place FastAPI meets the
server-held browser session.

    GET /api/platform/session/login?next=<same-origin path>
        starts a one-time login attempt, sets the attempt cookie, redirects
        the browser to the authenticator (Cognito's hosted UI, any OIDC issuer).
    GET /api/platform/session/callback?code=&state=
        completes the attempt: code exchange, ID-token verification, the
        platform user record, the session cookie; redirects to the validated
        ``next``. A failed sign-in answers a small page with the reason and
        a link to try again, never a stack.
    GET /api/platform/session/signed-out
        the identity provider's post-logout target, one fixed URL per
        origin; continues to the destination the logout stored in the
        return cookie.
    GET /api/platform/session/status
        whether the lane is configured on this deployment, and its routes.

The flow is built per request from the deployment's descriptors
(``kdcube_ai_app.auth.bundle.login_lane``); a test injects its own
through ``flow_provider``.
"""

from __future__ import annotations

import html
import logging
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from connection_hub.server_side_login.flow import BrowserSessionFlow, LoginAttemptRejected, LoginRejected
from connection_hub.server_side_login.model import CookieSpec
from connection_hub.server_side_login.next_url import safe_next_target

from kdcube_ai_app.auth.AuthManager import AuthenticationError
from kdcube_ai_app.auth.bundle.login_lane import (
    CALLBACK_ROUTE,
    LOGIN_ROUTE,
    LOGOUT_ROUTE,
    PROFILE_ROUTE,
    SIGNED_OUT_ROUTE,
    platform_login_flow,
    public_origin,
)

logger = logging.getLogger(__name__)

FlowProvider = Callable[[Request], Awaitable[BrowserSessionFlow | None]]

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def apply_cookie(response: Response, spec: CookieSpec) -> None:
    """Write one ``CookieSpec`` as the framework's Set-Cookie."""
    if spec.clears:
        response.delete_cookie(spec.name, path=spec.path, domain=spec.domain or None, secure=spec.secure, httponly=spec.http_only, samesite=spec.same_site)
        return
    response.set_cookie(
        spec.name,
        spec.value,
        max_age=spec.max_age,
        path=spec.path,
        domain=spec.domain or None,
        secure=spec.secure,
        httponly=spec.http_only,
        samesite=spec.same_site,
    )


def _failure_page(
    reason: str,
    detail: str,
    *,
    retry_url: str,
    status_code: int = 400,
) -> HTMLResponse:
    text = html.escape(detail or reason.replace("_", " "))
    body = (
        "<!doctype html><meta charset='utf-8'><title>Sign-in did not complete</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem;color:#0D1E2C}"
        "a{color:#009C92}code{background:#E6F1F0;padding:.1em .3em;border-radius:3px}</style>"
        f"<h1>Sign-in did not complete</h1><p>{text}</p>"
        f"<p>Reason code: <code>{html.escape(reason)}</code></p>"
        f"<p><a href='{html.escape(retry_url)}'>Try again</a></p>"
    )
    return HTMLResponse(body, status_code=status_code, headers=NO_STORE)


async def _default_flow_provider(request: Request) -> BrowserSessionFlow | None:
    return await platform_login_flow(origin=public_origin(request))


def create_platform_session_router(*, flow_provider: FlowProvider | None = None) -> APIRouter:
    provider = flow_provider or _default_flow_provider
    router = APIRouter(tags=["platform-session"])

    @router.get(LOGIN_ROUTE)
    async def session_login(request: Request) -> Response:
        flow = await provider(request)
        if flow is None:
            return JSONResponse({"detail": "platform session sign-in is not configured"}, status_code=404, headers=NO_STORE)
        start = await flow.begin_login(request.query_params.get("next"))
        if not start.redirect_url:
            return JSONResponse({"detail": "the configured authenticator does not redirect"}, status_code=500, headers=NO_STORE)
        response = RedirectResponse(start.redirect_url, status_code=302, headers=NO_STORE)
        apply_cookie(response, start.attempt_cookie)
        return response

    @router.get(CALLBACK_ROUTE)
    async def session_callback(request: Request) -> Response:
        flow = await provider(request)
        if flow is None:
            return JSONResponse({"detail": "platform session sign-in is not configured"}, status_code=404, headers=NO_STORE)
        binding = request.cookies.get(flow.cookies.clear_attempt_cookie().name)
        try:
            done = await flow.complete_login(dict(request.query_params), attempt_binding=binding)
        except LoginAttemptRejected as exc:
            logger.info("platform session sign-in refused: %s", exc.reason)
            return _failure_page(exc.reason, "The sign-in attempt is unknown, expired, or was started in another browser.", retry_url=LOGIN_ROUTE)
        except LoginRejected as exc:
            logger.warning("platform session sign-in rejected by the authenticator: %s %s", exc.reason, exc.detail)
            return _failure_page(exc.reason, "The identity provider did not confirm the sign-in.", retry_url=LOGIN_ROUTE)
        except AuthenticationError:
            logger.error("platform session sign-in could not resolve its platform identity", exc_info=True)
            return _failure_page(
                "platform_identity_unavailable",
                "The platform could not resolve this sign-in identity. Try again after the identity service recovers.",
                retry_url=LOGIN_ROUTE,
                status_code=503,
            )
        response = RedirectResponse(done.redirect_to, status_code=302, headers=NO_STORE)
        apply_cookie(response, done.session_cookie)
        apply_cookie(response, done.clear_attempt_cookie)
        logger.info(
            "platform session issued subject=%s session=%s provider=%s",
            done.session.subject, done.session.session_id, done.identity.provider,
        )
        return response

    @router.get(SIGNED_OUT_ROUTE)
    async def session_signed_out(request: Request) -> Response:
        """Where the identity provider sends the browser after its sign-out.
        One fixed URL per origin is registered there; the destination the
        logout carried in the return cookie decides where to go next."""
        flow = await provider(request)
        cookie_name = flow.cookies.clear_return_cookie().name if flow is not None else ""
        destination = safe_next_target(
            request.cookies.get(cookie_name) if cookie_name else None,
            allowed_origins=flow.policy.return_origins if flow is not None else (),
        )
        response = RedirectResponse(destination, status_code=302, headers=NO_STORE)
        if flow is not None:
            apply_cookie(response, flow.cookies.clear_return_cookie())
        return response

    @router.get("/api/platform/session/status")
    async def session_status(request: Request) -> JSONResponse:
        flow = await provider(request)
        payload: dict[str, Any] = {
            "configured": flow is not None,
            "loginUrl": LOGIN_ROUTE,
            "callbackUrl": CALLBACK_ROUTE,
            "signedOutUrl": SIGNED_OUT_ROUTE,
            "logoutUrl": LOGOUT_ROUTE,
            "profileUrl": PROFILE_ROUTE,
        }
        if flow is not None:
            payload["authenticator"] = flow.upstream.name
            payload["idleTtlSeconds"] = flow.policy.idle_ttl_seconds
            payload["maxTtlSeconds"] = flow.policy.max_ttl_seconds
        return JSONResponse(payload, headers=NO_STORE)

    return router


__all__ = ["apply_cookie", "create_platform_session_router"]
