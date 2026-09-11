# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from urllib.parse import quote

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from kdcube_ai_app.auth.AuthManager import HTTP_401_UNAUTHORIZED, RequireUser
from kdcube_ai_app.auth.sessions import UserSession, UserType


def _is_top_level_html_navigation(request: Request) -> bool:
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


def _sign_in_redirect(request: Request) -> RedirectResponse:
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


def enforce_protected_widget_user(
    request: Request,
    session: UserSession,
) -> RedirectResponse | None:
    """Require a platform user, with login continuation for browser documents."""

    user_type = getattr(session, "user_type", UserType.ANONYMOUS)
    user_type_value = str(getattr(user_type, "value", user_type))
    if session is None or user_type_value.lower() == UserType.ANONYMOUS.value:
        if _is_top_level_html_navigation(request):
            return _sign_in_redirect(request)
        raise HTTPException(status_code=403, detail="User is required.")

    validation_error = RequireUser().validate_requirement(session)
    if validation_error is None:
        return None

    if (
        validation_error.code == HTTP_401_UNAUTHORIZED
        and _is_top_level_html_navigation(request)
    ):
        return _sign_in_redirect(request)

    # Preserve the protected widget route's existing JSON denial status. The
    # gateway currently presents requirement failures as authorization errors.
    raise HTTPException(status_code=403, detail=validation_error.message)
