# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

import html
from urllib.parse import quote, urlencode

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from kdcube_ai_app.auth.sessions import UserSession, UserType


def is_top_level_html_navigation(request: Request) -> bool:
    accept = request.headers.get("accept", "").lower()
    if "text/html" not in accept or "application/json" in accept:
        return False

    fetch_destination = request.headers.get("sec-fetch-dest", "").strip().lower()
    if fetch_destination and fetch_destination != "document":
        return False

    fetch_mode = request.headers.get("sec-fetch-mode", "").strip().lower()
    if fetch_mode and fetch_mode != "navigate":
        return False

    return True


_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def platform_sign_in_redirect(request: Request) -> Response:
    from kdcube_ai_app.auth.bundle.login_lane import sign_in_bounce_path

    # The person has just signed out (the signed-out route adds the marker):
    # sending them to sign-in now would let an identity provider that is
    # still signed in upstream sign them straight back in (W260). Show that
    # they are signed out, with a Sign in link that carries no marker.
    signed_out = request.query_params.get("signed_out") == "1"
    return_to = request.url.path
    if signed_out:
        params = [(key, value) for key, value in request.query_params.multi_items() if key != "signed_out"]
        if params:
            return_to = f"{return_to}?{urlencode(params)}"
    elif request.url.query:
        return_to = f"{return_to}?{request.url.query}"
    sign_in = f"{sign_in_bounce_path()}?next={quote(return_to, safe='')}"
    if signed_out:
        return HTMLResponse(
            "<!doctype html><html><head><meta charset=\"utf-8\"><title>Signed out</title>"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"></head>"
            "<body style=\"font-family: system-ui, sans-serif; margin: 3rem auto; max-width: 28rem; padding: 0 1rem\">"
            "<h1 style=\"font-size: 1.25rem\">You are signed out.</h1>"
            f"<p><a href=\"{html.escape(sign_in, quote=True)}\">Sign in</a></p></body></html>",
            status_code=200,
            headers=_NO_STORE,
        )
    return RedirectResponse(url=sign_in, status_code=302, headers=_NO_STORE)


def enforce_platform_session_user(
    request: Request,
    session: UserSession | None,
) -> Response | None:
    """Require a verified platform user and continue browser documents to login."""

    user_type = getattr(session, "user_type", UserType.ANONYMOUS)
    user_type_value = str(getattr(user_type, "value", user_type))
    if session is not None and user_type_value.lower() != UserType.ANONYMOUS.value:
        return None
    if is_top_level_html_navigation(request):
        return platform_sign_in_redirect(request)
    raise HTTPException(status_code=401, detail="User is required.")


__all__ = [
    "enforce_platform_session_user",
    "is_top_level_html_navigation",
    "platform_sign_in_redirect",
]
