# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube application-operation adapter for Connection Hub connection edges."""

from __future__ import annotations

from typing import Any

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import call_bundle_operation
from connection_hub.connection_edges import (
    DEFAULT_CONNECTION_HUB_BUNDLE_ID,
    ConnectionEdgesClient as PortableConnectionEdgesClient,
    connection_hub_bundle_id,
    connection_hub_bundle_id_from_entrypoint,
    request_origin,
)


class ConnectionEdgesClient(PortableConnectionEdgesClient):
    """Connection-edge client bound to KDCube's app-operation bridge."""

    def __init__(
        self,
        entrypoint: Any,
        *,
        connection_hub_bundle_id: str | None = None,
        tenant: str | None = None,
        project: str | None = None,
        **kwargs: Any,
    ) -> None:
        # W303: keywords this wrapper does not handle go to the portable class unchanged.
        kwargs.setdefault("operation_caller", call_bundle_operation)
        super().__init__(
            entrypoint,
            connection_hub_bundle_id=connection_hub_bundle_id,
            tenant=tenant,
            project=project,
            **kwargs,
        )


__all__ = [
    "DEFAULT_CONNECTION_HUB_BUNDLE_ID",
    "ConnectionEdgesClient",
    "connection_hub_bundle_id",
    "connection_hub_bundle_id_from_entrypoint",
    "request_origin",
]
