# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube host composition for durable Connection Hub Card handles."""

from __future__ import annotations

from typing import Any

from connection_hub.delegated_credentials.cards.credential_handles import (
    PostgresCardCredentialHandleStore,
)
from connection_hub.delegated_credentials.cards.handle_authority import (
    PostgresCardHandleMetadataStore,
)
from connection_hub.delegated_credentials.cards.resident_secrets import (
    ResidentCardSecretService,
)
from connection_hub.delegated_credentials.cards.resident_secrets.model import (
    ResidentSecretStore,
)
from kdcube_ai_app.infra.secrets.ephemeral import (
    KDCubeEphemeralSecretStore,
    ephemeral_secret_store,
)

RESIDENT_CARD_SECRET_NAMESPACE = "resident-card-credentials"


def resident_card_secret_store(
    settings: Any | None = None,
) -> KDCubeEphemeralSecretStore:
    """Compose the deployment-selected resident Card secret custody."""

    return ephemeral_secret_store(
        namespace=RESIDENT_CARD_SECRET_NAMESPACE,
        settings=settings,
    )


def postgres_card_credential_handle_store(
    *,
    pg_pool: Any,
    tenant: str,
    project: str,
    settings: Any | None = None,
    secret_store: ResidentSecretStore | None = None,
) -> PostgresCardCredentialHandleStore:
    """Bind package-owned metadata and lifecycle to KDCube host custody."""

    metadata = PostgresCardHandleMetadataStore(
        pg_pool=pg_pool,
        tenant=tenant,
        project=project,
    )
    resident_secrets = ResidentCardSecretService(
        metadata_store=metadata,
        secret_store=(
            secret_store
            if secret_store is not None
            else resident_card_secret_store(settings)
        ),
    )
    return PostgresCardCredentialHandleStore(
        metadata_store=metadata,
        resident_secrets=resident_secrets,
    )


__all__ = [
    "RESIDENT_CARD_SECRET_NAMESPACE",
    "postgres_card_credential_handle_store",
    "resident_card_secret_store",
]
