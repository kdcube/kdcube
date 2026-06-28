# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Connection Hub request-authenticator SDK."""

from .client import (
    ConnectionHubAuthenticatorsClient,
    DEFAULT_CONNECTION_HUB_BUNDLE_ID,
    REQUEST_AUTHENTICATE_OPERATION,
)
from .authority import (
    AuthRequestHints,
    AuthorityIdentity,
    SurfaceGuardRequirement,
    select_authenticator_candidates,
)
from .models import AuthenticatedRequest, AuthenticatorRegistration, RequestEnvelope

__all__ = [
    "AuthRequestHints",
    "AuthenticatedRequest",
    "AuthenticatorRegistration",
    "AuthorityIdentity",
    "ConnectionHubAuthenticatorsClient",
    "DEFAULT_CONNECTION_HUB_BUNDLE_ID",
    "REQUEST_AUTHENTICATE_OPERATION",
    "RequestEnvelope",
    "SurfaceGuardRequirement",
    "select_authenticator_candidates",
]
