# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Request authentication resolver.

The gateway has two request-auth boundaries:

* platform auth, implemented by the configured platform ``AuthManager``;
* Connection Hub auth, implemented by one Connection Hub authentication
  surface.

Connection Hub owns the selector for Telegram/Slack/OIDC/API-key/custom
authority authenticators. This module only asks that surface for a complete
``UserSession`` when platform auth did not already prove the request.
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
    REGISTERED_ROLE,
)
from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType
from kdcube_ai_app.apps.chat.sdk.solutions.connections.authority_projection import (
    authority_has_platform_privilege,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[RequestContext, UserType, Optional[dict[str, Any]]], Awaitable[UserSession]]
RequestAuthenticationSurface = Callable[[Request, RequestContext, SessionFactory], Awaitable[Optional[UserSession]]]


def _auth_debug_enabled() -> bool:
    return os.getenv("AUTH_DEBUG", "").lower() in {"1", "true", "yes", "on"}


def _roles_user_type(roles: list[str] | None) -> UserType:
    role_set = set(roles or [])
    if authority_has_platform_privilege(role_set):
        return UserType.PRIVILEGED
    if PAID_ROLES & role_set:
        return UserType.PAID
    return UserType.REGISTERED


class RequestAuthResolver:
    """Boundary-level request-auth resolver.

    This resolver deliberately does not keep a provider-authenticator registry.
    External proof selection belongs to Connection Hub, so the
    gateway installs exactly one Connection Hub authentication surface.
    """

    def __init__(
        self,
        *,
        auth_manager: AuthManager | None,
        session_factory: SessionFactory,
    ) -> None:
        self.session_factory = session_factory
        self._platform_authenticator: PlatformTokenAuthenticator | None = None
        self._connection_hub_surface: RequestAuthenticationSurface | None = None
        if auth_manager is not None:
            self._platform_authenticator = PlatformTokenAuthenticator(auth_manager=auth_manager)

    def install_connection_hub_surface(self, surface: RequestAuthenticationSurface) -> None:
        self._connection_hub_surface = surface

    async def resolve_session(
        self,
        request: Request,
        context: RequestContext,
        *,
        allow_connection_hub: bool = True,
    ) -> UserSession:
        if context.authorization_header:
            session = await self._try_surface(
                self._platform_authenticator,
                request,
                context,
                label="platform",
            )
            if session is not None:
                return session

        if allow_connection_hub and self._connection_hub_surface is not None:
            session = await self._try_surface(
                self._connection_hub_surface,
                request,
                context,
                label="connection_hub",
            )
            if session is not None:
                return session

        return await self.session_factory(context, UserType.ANONYMOUS, None)

    async def _try_surface(
        self,
        surface: RequestAuthenticationSurface | None,
        request: Request,
        context: RequestContext,
        *,
        label: str,
    ) -> Optional[UserSession]:
        if surface is None:
            return None
        try:
            session = await surface(request, context, self.session_factory)
        except Exception:
            logger.warning(
                "Request-auth surface failed; continuing auth stack surface=%s",
                label,
                exc_info=_auth_debug_enabled(),
            )
            return None
        if session is not None and _auth_debug_enabled():
            logger.info(
                "Request auth resolver accepted session surface=%s user=%s type=%s",
                label,
                session.user_id,
                session.user_type.value if hasattr(session.user_type, "value") else session.user_type,
            )
        return session


class PlatformTokenAuthenticator:
    """Descriptor-registered platform token/cookie authenticator.

    This preserves the existing AuthManager implementations while exposing
    them through the same session-returning surface contract.
    """

    def __init__(self, *, auth_manager: AuthManager) -> None:
        self.auth_manager = auth_manager
        self.authenticator_id = getattr(auth_manager, "authenticator_id", "") or "kdcube.platform.token"
        self.authority_id = getattr(auth_manager, "authority_id", "") or "kdcube.platform"

    async def __call__(
        self,
        _request: Request,
        context: RequestContext,
        session_factory: SessionFactory,
    ) -> Optional[UserSession]:
        if not context.authorization_header or not self.auth_manager:
            if _auth_debug_enabled():
                logger.info(
                    "Request auth resolver: no token/auth manager auth_header=%s manager=%s",
                    bool(context.authorization_header),
                    bool(self.auth_manager),
                )
            return None

        try:
            parts = context.authorization_header.split(" ", 1)
            if len(parts) != 2 or parts[0].lower() != "bearer":
                if _auth_debug_enabled():
                    logger.info("Request auth resolver: malformed authorization header")
                return None

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
                "identity_authority": {
                    "authority_id": self.authority_id,
                    "authenticator_id": self.authenticator_id,
                    "actor_user_id": getattr(user, "sub", None) or user.username,
                    "platform_user_id": getattr(user, "sub", None) or user.username,
                    "platform_roles": roles,
                    "platform_permissions": permissions,
                    "source": "platform_token_authenticator",
                },
            }
            return await session_factory(context, user_type, user_data)
        except AuthenticationError as exc:
            if _auth_debug_enabled():
                logger.info("Request auth resolver: token rejected: %s", exc)
            return None
        except Exception as exc:
            logger.warning(
                "Request auth resolver: unexpected platform auth failure: %s: %s",
                type(exc).__name__,
                str(exc),
                exc_info=_auth_debug_enabled(),
            )
            return None


__all__ = [
    "PlatformTokenAuthenticator",
    "RequestAuthenticationSurface",
    "RequestAuthResolver",
    "SessionFactory",
]
