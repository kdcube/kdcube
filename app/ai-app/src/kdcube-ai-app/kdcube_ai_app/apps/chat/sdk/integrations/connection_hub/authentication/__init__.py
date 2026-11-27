# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Request authentication surfaces and gateway-facing Connection Hub auth."""

from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.authentication_surface import (
    ConnectionHubAuthenticationSurface,
    connection_hub_auth_enabled,
    maybe_install_connection_hub_authentication_surface,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.request_auth import (
    PlatformTokenAuthenticator,
    RequestAuthenticationSurface,
    RequestAuthResolver,
    SessionFactory,
)

__all__ = [
    "ConnectionHubAuthenticationSurface",
    "PlatformTokenAuthenticator",
    "RequestAuthenticationSurface",
    "RequestAuthResolver",
    "SessionFactory",
    "connection_hub_auth_enabled",
    "maybe_install_connection_hub_authentication_surface",
]
