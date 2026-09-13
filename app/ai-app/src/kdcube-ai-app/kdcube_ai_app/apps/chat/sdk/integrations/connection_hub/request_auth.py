# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube session and platform-auth bindings for Connection Hub request auth."""

from __future__ import annotations

import os
from importlib import import_module
from typing import Any

from connection_hub.authority_projection import (
    authority_has_platform_privilege,
)
from kdcube_ai_app.auth.AuthManager import (
    AuthenticationError,
    AuthorizationError,
    PAID_ROLES,
    ensure_platform_registered_role,
)
from kdcube_ai_app.auth.sessions import UserType


_core = import_module("connection_hub.request_auth")

CONNECTION_HUB_DELEGATED_BEARER_ONLY = (
    _core.CONNECTION_HUB_DELEGATED_BEARER_ONLY
)
SessionFactory = _core.SessionFactory
RequestAuthenticationSurface = _core.RequestAuthenticationSurface


def _auth_debug_enabled() -> bool:
    return os.getenv("AUTH_DEBUG", "").lower() in {"1", "true", "yes", "on"}


def _roles_user_type(roles: list[str] | None) -> UserType:
    role_set = set(roles or [])
    if authority_has_platform_privilege(role_set):
        return UserType.PRIVILEGED
    if PAID_ROLES & role_set:
        return UserType.PAID
    if not role_set:
        return UserType.EXTERNAL
    return UserType.REGISTERED


class PlatformTokenAuthenticator(_core.PlatformTokenAuthenticator):
    def __init__(self, *, auth_manager: Any) -> None:
        super().__init__(
            auth_manager=auth_manager,
            role_normalizer=ensure_platform_registered_role,
            user_type_resolver=_roles_user_type,
            authentication_errors=(AuthenticationError,),
            debug_enabled=_auth_debug_enabled,
        )


class RequestAuthResolver(_core.RequestAuthResolver):
    def __init__(
        self,
        *,
        auth_manager: Any | None,
        session_factory: Any,
    ) -> None:
        platform_authenticator = (
            PlatformTokenAuthenticator(auth_manager=auth_manager)
            if auth_manager is not None
            else None
        )
        super().__init__(
            session_factory=session_factory,
            platform_authenticator=platform_authenticator,
            anonymous_user_type=UserType.ANONYMOUS,
            propagated_errors=(AuthenticationError, AuthorizationError),
            debug_enabled=_auth_debug_enabled,
        )

    async def resolve_delegated_resource_session(
        self,
        *,
        app: Any,
        resource: str,
        bearer_token: str,
        context: Any,
    ) -> Any | None:
        """Resolve a Card bearer for a trusted non-HTTP resource adapter."""

        surface = getattr(self, "_connection_hub_surface", None)
        authenticate = getattr(surface, "authenticate_delegated_resource_bearer", None)
        if not callable(authenticate):
            return None
        return await authenticate(
            app=app,
            resource=resource,
            bearer_token=bearer_token,
            context=context,
            session_factory=self.session_factory,
        )


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


__all__ = [
    "CONNECTION_HUB_DELEGATED_BEARER_ONLY",
    "PlatformTokenAuthenticator",
    "RequestAuthenticationSurface",
    "RequestAuthResolver",
    "SessionFactory",
]
