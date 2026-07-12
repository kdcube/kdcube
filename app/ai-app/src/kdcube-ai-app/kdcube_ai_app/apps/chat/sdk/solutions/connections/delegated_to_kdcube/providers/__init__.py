# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Built-in delegated to KDCube adapters."""

from __future__ import annotations

# Imports register adapters through decorators.
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.providers.email import EmailAppPasswordAdapter
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.providers.generic_oauth import (
    GenericOAuthAdapter,
    GenericOIDCAdapter,
)
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.providers.google import GoogleOAuthAdapter
from kdcube_ai_app.apps.chat.sdk.solutions.connections.delegated_to_kdcube.providers.slack import SlackUserTokenAdapter

__all__ = [
    "EmailAppPasswordAdapter",
    "GenericOAuthAdapter",
    "GenericOIDCAdapter",
    "GoogleOAuthAdapter",
    "SlackUserTokenAdapter",
]
