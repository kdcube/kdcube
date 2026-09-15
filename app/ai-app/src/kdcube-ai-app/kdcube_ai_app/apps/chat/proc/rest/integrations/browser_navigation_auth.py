# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

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


def platform_sign_in_redirect(request: Request) -> RedirectResponse:
    from kdcube_ai_app.auth.bundle.login_lane import sign_in_bounce_path

    return_to = request.url.path
    if request.url.query:
        return_to = f"{return_to}?{request.url.query}"
    return RedirectResponse(
        url=f"{sign_in_bounce_path()}?next={quote(return_to, safe='')}",
        status_code=302,
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )


def enforce_platform_session_user(
    request: Request,
    session: UserSession | None,
) -> RedirectResponse | None:
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
