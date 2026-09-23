# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube host adapter for short-lived Connection Hub secret records."""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import Any

from kdcube_ai_app.infra.secrets.manager import (
    ISecretsManager,
    SecretsManagerError,
    build_secrets_manager_config,
    create_secrets_manager,
    get_secrets_manager,
)


SUPPORTED_EPHEMERAL_SECRET_PROVIDERS = frozenset(
    {"aws-sm", "secrets-service", "in-memory"}
)
LOCAL_SECRETS_SERVICE_URL = "http://kdcube-secrets:7777"


def _runtime_secret_manager(settings: Any | None) -> ISecretsManager:
    """Select custody independently from provider-value reads.

    During local Host Vault shadow staging, provider values intentionally stay
    file-backed while runtime-owned secrets already require durable custody.
    ``secrets.service.backend`` owns that second decision.
    """

    manager = get_secrets_manager(settings)
    if manager.provider_type in SUPPORTED_EPHEMERAL_SECRET_PROVIDERS:
        return manager

    backend = str(
        getattr(settings, "SECRETS_SERVICE_BACKEND", None) or ""
    ).strip().lower().replace("_", "-")
    if manager.provider_type != "secrets-file" or backend != "host-vault":
        return manager

    config = build_secrets_manager_config(settings)
    return create_secrets_manager(
        replace(
            config,
            provider="secrets-service",
            url=config.url or LOCAL_SECRETS_SERVICE_URL,
        )
    )


class KDCubeEphemeralSecretStore:
    """Bind portable expiring-secret contracts to one deployment namespace."""

    def __init__(self, manager: ISecretsManager, *, namespace: str) -> None:
        if manager.provider_type not in SUPPORTED_EPHEMERAL_SECRET_PROVIDERS:
            raise SecretsManagerError(
                "Short-lived runtime secrets require the host vault locally or "
                "AWS Secrets Manager in a hosted deployment"
            )
        if not manager.can_write():
            raise SecretsManagerError(
                "The selected secrets provider is not configured for runtime writes"
            )
        self._manager = manager
        self._namespace = str(namespace or "").strip()

    async def set(
        self,
        *,
        secret_ref: str,
        value: str,
        expires_at: int,
    ) -> None:
        await self._manager.set_ephemeral_secret(
            namespace=self._namespace,
            secret_ref=secret_ref,
            value=value,
            expires_at=expires_at,
        )

    async def create(
        self,
        *,
        secret_ref: str,
        value: str,
        expires_at: int,
    ) -> bool:
        return await self._manager.create_ephemeral_secret(
            namespace=self._namespace,
            secret_ref=secret_ref,
            value=value,
            expires_at=expires_at,
        )

    async def get(self, *, secret_ref: str) -> str | None:
        return await self._manager.get_ephemeral_secret(
            namespace=self._namespace,
            secret_ref=secret_ref,
        )

    async def delete(self, *, secret_ref: str) -> None:
        await self._manager.delete_ephemeral_secret(
            namespace=self._namespace,
            secret_ref=secret_ref,
        )

    async def purge_expired(self, *, now: int, limit: int) -> int:
        return await self._manager.purge_expired_ephemeral_secrets(
            namespace=self._namespace,
            now=now,
            limit=limit,
        )

    async def probe_writable(self) -> None:
        """Exercise the mutation lane without creating a stored value."""

        await self._manager.delete_ephemeral_secret(
            namespace=self._namespace,
            secret_ref=uuid.uuid4().hex,
        )


def ephemeral_secret_store(
    *,
    namespace: str,
    settings: Any | None = None,
    manager: ISecretsManager | None = None,
) -> KDCubeEphemeralSecretStore:
    """Build the deployment-selected host-vault or AWS adapter."""

    return KDCubeEphemeralSecretStore(
        manager or _runtime_secret_manager(settings),
        namespace=namespace,
    )


__all__ = [
    "KDCubeEphemeralSecretStore",
    "LOCAL_SECRETS_SERVICE_URL",
    "SUPPORTED_EPHEMERAL_SECRET_PROVIDERS",
    "ephemeral_secret_store",
]
