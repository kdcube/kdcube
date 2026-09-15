# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Elena Viter

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from kdcube_ai_app.auth.AuthManager import HTTP_401_UNAUTHORIZED, RequireUser
from kdcube_ai_app.auth.sessions import UserSession
from kdcube_ai_app.apps.chat.proc.rest.integrations.browser_navigation_auth import (
    enforce_platform_session_user,
    is_top_level_html_navigation,
    platform_sign_in_redirect,
)


def enforce_protected_widget_user(
    request: Request,
    session: UserSession,
) -> RedirectResponse | None:
    """Require a platform user, with login continuation for browser documents."""

    try:
        sign_in = enforce_platform_session_user(request, session)
    except HTTPException as exc:
        # Preserve the protected widget route's existing JSON denial status.
        raise HTTPException(status_code=403, detail=exc.detail) from exc
    if sign_in is not None:
        return sign_in

    validation_error = RequireUser().validate_requirement(session)
    if validation_error is None:
        return None

    if (
        validation_error.code == HTTP_401_UNAUTHORIZED
        and is_top_level_html_navigation(request)
    ):
        return platform_sign_in_redirect(request)

    # Preserve the protected widget route's existing JSON denial status. The
    # gateway currently presents requirement failures as authorization errors.
    raise HTTPException(status_code=403, detail=validation_error.message)
