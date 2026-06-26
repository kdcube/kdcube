# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Request-authentication selector.

Every request-auth candidate receives the same request context and must return
a complete ``UserSession`` or decline. Provider-specific authenticators such as
Telegram/Slack/API-key verifiers live inside Connection Hub; the gateway sees
only one Connection Hub bridge candidate.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable, Optional

from fastapi import Request

from kdcube_ai_app.auth.AuthManager import (
    AuthManager,
    AuthenticationError,
    PAID_ROLES,
    PRIVILEGED_ROLES,
    REGISTERED_ROLE,
)
from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType

logger = logging.getLogger(__name__)

SessionFactory = Callable[[RequestContext, UserType, Optional[dict[str, Any]]], Awaitable[UserSession]]
RequestAuthCandidate = Callable[[Request, RequestContext, SessionFactory], Awaitable[Optional[UserSession]]]


def _auth_debug_enabled() -> bool:
    return os.getenv("AUTH_DEBUG", "").lower() in {"1", "true", "yes", "on"}


def _roles_user_type(roles: list[str] | None) -> UserType:
    role_set = set(roles or [])
    if PRIVILEGED_ROLES & role_set:
        return UserType.PRIVILEGED
    if PAID_ROLES & role_set:
        return UserType.PAID
    return UserType.REGISTERED


class RequestAuthSelector:
    """Central request-auth stack.

    The current token/cookie ``AuthManager`` is one selector candidate.
    Connection Hub can be registered as another candidate; its provider modules
    do the Telegram/Slack/API-key/OIDC proof work internally. The public
    contract is intentionally one value: ``UserSession``.
    """

    def __init__(
        self,
        *,
        auth_manager: AuthManager | None,
        session_factory: SessionFactory,
    ) -> None:
        self.auth_manager = auth_manager
        self.session_factory = session_factory
        self._request_auth_candidates: list[RequestAuthCandidate] = []

    def register_request_auth_candidate(self, candidate: RequestAuthCandidate) -> None:
        self._request_auth_candidates.append(candidate)

    async def resolve_session(
        self,
        request: Request,
        context: RequestContext,
        *,
        allow_request_auth_candidates: bool = True,
    ) -> UserSession:
        standard_first = bool(context.authorization_header and self.auth_manager)
        if standard_first:
            standard_session = await self._resolve_standard_auth(context)
            if standard_session.user_type != UserType.ANONYMOUS:
                return standard_session

        if allow_request_auth_candidates:
            for candidate in self._request_auth_candidates:
                try:
                    session = await candidate(request, context, self.session_factory)
                except Exception:
                    logger.warning(
                        "Request-auth candidate failed; continuing auth stack",
                        exc_info=_auth_debug_enabled(),
                    )
                    continue
                if session is not None:
                    if _auth_debug_enabled():
                        logger.info(
                            "Request auth selector accepted request-auth session user=%s type=%s",
                            session.user_id,
                            session.user_type.value if hasattr(session.user_type, "value") else session.user_type,
                        )
                    return session

        if standard_first:
            return standard_session
        return await self._resolve_standard_auth(context)

    async def _resolve_standard_auth(self, context: RequestContext) -> UserSession:
        if not context.authorization_header or not self.auth_manager:
            if _auth_debug_enabled():
                logger.info(
                    "Request auth selector: no token/auth manager, creating anonymous session auth_header=%s manager=%s",
                    bool(context.authorization_header),
                    bool(self.auth_manager),
                )
            return await self.session_factory(context, UserType.ANONYMOUS, None)

        try:
            parts = context.authorization_header.split(" ", 1)
            if len(parts) != 2 or parts[0].lower() != "bearer":
                if _auth_debug_enabled():
                    logger.info("Request auth selector: malformed authorization header")
                return await self.session_factory(context, UserType.ANONYMOUS, None)

            token = parts[1]
            user = await self.auth_manager.authenticate_with_both(token, context.id_token)
            if user and not user.roles:
                user.roles = [REGISTERED_ROLE]
            roles = list(getattr(user, "roles", None) or [])
            permissions = list(getattr(user, "permissions", None) or [])
            user_type = _roles_user_type(roles)
            user_data = {
                "user_id": getattr(user, "sub", None) or user.username,
                "username": user.username,
                "email": user.email,
                "roles": roles,
                "permissions": permissions,
            }
            return await self.session_factory(context, user_type, user_data)
        except AuthenticationError as exc:
            if _auth_debug_enabled():
                logger.info("Request auth selector: token rejected, anonymous fallback: %s", exc)
            return await self.session_factory(context, UserType.ANONYMOUS, None)
        except Exception as exc:
            logger.warning(
                "Request auth selector: unexpected standard auth failure; anonymous fallback: %s: %s",
                type(exc).__name__,
                str(exc),
                exc_info=_auth_debug_enabled(),
            )
            return await self.session_factory(context, UserType.ANONYMOUS, None)


__all__ = ["RequestAuthCandidate", "RequestAuthSelector", "SessionFactory"]
