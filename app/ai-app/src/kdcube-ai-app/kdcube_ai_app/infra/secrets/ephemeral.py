# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube host adapter for short-lived Connection Hub secret records."""

from __future__ import annotations

from typing import Any

from kdcube_ai_app.infra.secrets.manager import (
    ISecretsManager,
    SecretsManagerError,
    get_secrets_manager,
)


SUPPORTED_EPHEMERAL_SECRET_PROVIDERS = frozenset(
    {"aws-sm", "secrets-service", "in-memory"}
)


class KDCubeEphemeralSecretStore:
    """Bind a portable one-time state store to one deployment namespace."""

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


def ephemeral_secret_store(
    *,
    namespace: str,
    settings: Any | None = None,
    manager: ISecretsManager | None = None,
) -> KDCubeEphemeralSecretStore:
    """Build the deployment-selected host-vault or AWS adapter."""

    return KDCubeEphemeralSecretStore(
        manager or get_secrets_manager(settings),
        namespace=namespace,
    )


__all__ = [
    "KDCubeEphemeralSecretStore",
    "SUPPORTED_EPHEMERAL_SECRET_PROVIDERS",
    "ephemeral_secret_store",
]
