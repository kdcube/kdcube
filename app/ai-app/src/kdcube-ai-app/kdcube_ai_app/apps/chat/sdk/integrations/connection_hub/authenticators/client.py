# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube app-operation adapter for Connection Hub request authentication."""

from __future__ import annotations

from typing import Any

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import call_bundle_operation
from connection_hub.authenticators.client import (
    DEFAULT_CONNECTION_HUB_BUNDLE_ID,
    REQUEST_AUTHENTICATE_OPERATION,
    ConnectionHubAuthenticatorsClient as PortableAuthenticatorsClient,
)


class ConnectionHubAuthenticatorsClient(PortableAuthenticatorsClient):
    """Authenticator client bound to KDCube's app-operation bridge."""

    def __init__(
        self,
        *,
        connection_hub_bundle_id: str = DEFAULT_CONNECTION_HUB_BUNDLE_ID,
        tenant: str | None = None,
        project: str | None = None,
        **kwargs: Any,
    ) -> None:
        # W303: keywords this wrapper does not handle go to the portable class unchanged.
        kwargs.setdefault("operation_caller", call_bundle_operation)
        super().__init__(
            connection_hub_bundle_id=connection_hub_bundle_id,
            tenant=tenant,
            project=project,
            **kwargs,
        )


__all__ = [
    "ConnectionHubAuthenticatorsClient",
    "DEFAULT_CONNECTION_HUB_BUNDLE_ID",
    "REQUEST_AUTHENTICATE_OPERATION",
]
