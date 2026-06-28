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
from dataclasses import dataclass
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
        self.session_factory = session_factory
        self._platform_authenticators: list[RegisteredRequestAuthenticator] = []
        self._request_auth_candidates: list[RegisteredRequestAuthenticator] = []
        if auth_manager is not None:
            self.register_platform_authenticator(
                PlatformTokenAuthenticator(auth_manager=auth_manager),
                authenticator_id=getattr(auth_manager, "authenticator_id", "") or "kdcube.platform.token",
                authority_id=getattr(auth_manager, "authority_id", "") or "kdcube.platform",
            )

    def register_request_auth_candidate(self, candidate: RequestAuthCandidate) -> None:
        self._request_auth_candidates.append(
            RegisteredRequestAuthenticator(
                authenticator_id=getattr(candidate, "authenticator_id", "") or candidate.__class__.__name__,
                authority_id=getattr(candidate, "authority_id", ""),
                candidate=candidate,
                header_only_allowed=False,
                role_providing=False,
            )
        )

    def register_platform_authenticator(
        self,
        candidate: RequestAuthCandidate,
        *,
        authenticator_id: str,
        authority_id: str = "kdcube.platform",
    ) -> None:
        self._platform_authenticators.append(
            RegisteredRequestAuthenticator(
                authenticator_id=authenticator_id,
                authority_id=authority_id,
                candidate=candidate,
                header_only_allowed=True,
                role_providing=True,
            )
        )

    async def resolve_session(
        self,
        request: Request,
        context: RequestContext,
        *,
        allow_request_auth_candidates: bool = True,
    ) -> UserSession:
        tried_platform = False
        if context.authorization_header:
            session = await self._try_registered_authenticators(
                self._platform_authenticators,
                request,
                context,
            )
            tried_platform = True
            if session is not None:
                return session

        if allow_request_auth_candidates:
            session = await self._try_registered_authenticators(
                self._request_auth_candidates,
                request,
                context,
            )
            if session is not None:
                return session

        if not tried_platform and context.authorization_header:
            session = await self._try_registered_authenticators(
                self._platform_authenticators,
                request,
                context,
            )
            if session is not None:
                return session

        return await self.session_factory(context, UserType.ANONYMOUS, None)

    async def _try_registered_authenticators(
        self,
        authenticators: list["RegisteredRequestAuthenticator"],
        request: Request,
        context: RequestContext,
    ) -> Optional[UserSession]:
        for registered in authenticators:
            try:
                session = await registered.candidate(request, context, self.session_factory)
            except Exception:
                logger.warning(
                    "Request-auth candidate failed; continuing auth stack authenticator_id=%s authority_id=%s",
                    registered.authenticator_id,
                    registered.authority_id,
                    exc_info=_auth_debug_enabled(),
                )
                continue
            if session is not None:
                if _auth_debug_enabled():
                    logger.info(
                        "Request auth selector accepted session authenticator_id=%s authority_id=%s user=%s type=%s",
                        registered.authenticator_id,
                        registered.authority_id,
                        session.user_id,
                        session.user_type.value if hasattr(session.user_type, "value") else session.user_type,
                    )
                return session
        return None


@dataclass(frozen=True)
class RegisteredRequestAuthenticator:
    authenticator_id: str
    authority_id: str
    candidate: RequestAuthCandidate
    header_only_allowed: bool = False
    role_providing: bool = False


class PlatformTokenAuthenticator:
    """Descriptor-registered platform token/cookie authenticator.

    This preserves the existing AuthManager implementations while moving them
    into the same selector contract as Connection Hub request authenticators.
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
                    "Request auth selector: no token/auth manager auth_header=%s manager=%s",
                    bool(context.authorization_header),
                    bool(self.auth_manager),
                )
            return None

        try:
            parts = context.authorization_header.split(" ", 1)
            if len(parts) != 2 or parts[0].lower() != "bearer":
                if _auth_debug_enabled():
                    logger.info("Request auth selector: malformed authorization header")
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
                logger.info("Request auth selector: token rejected: %s", exc)
            return None
        except Exception as exc:
            logger.warning(
                "Request auth selector: unexpected platform auth failure: %s: %s",
                type(exc).__name__,
                str(exc),
                exc_info=_auth_debug_enabled(),
            )
            return None


__all__ = [
    "PlatformTokenAuthenticator",
    "RegisteredRequestAuthenticator",
    "RequestAuthCandidate",
    "RequestAuthSelector",
    "SessionFactory",
]
