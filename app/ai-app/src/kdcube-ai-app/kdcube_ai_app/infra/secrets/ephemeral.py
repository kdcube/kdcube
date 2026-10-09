# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube host adapter for short-lived Connection Hub secret records."""

from __future__ import annotations

import uuid
from typing import Any

from kdcube_ai_app.infra.secrets.manager import (
    ISecretsManager,
    SecretsManagerError,
    get_secrets_manager,
)


SUPPORTED_EPHEMERAL_SECRET_PROVIDERS = frozenset(
    {"aws-sm", "secrets-service", "secrets-file", "in-memory"}
)
LOCAL_SECRETS_SERVICE_URL = "http://kdcube-secrets:7777"


def _runtime_secret_manager(settings: Any | None) -> ISecretsManager:
    """Use the configured secrets abstraction; never switch modes for custody."""
    return get_secrets_manager(settings)


class KDCubeEphemeralSecretStore:
    """Bind portable expiring-secret contracts to one owner bundle and one deployment namespace.

    ``bundle_id`` names the bundle that owns the records (W670, operator: "these secrets belong to bunlde");
    ``None`` is a bundle-less, platform-owned caller.
    """

    def __init__(
        self, manager: ISecretsManager, *, namespace: str,
        durability_required: bool = False, bundle_id: str | None = None,
    ) -> None:
        # Eligibility is the common asynchronous qualification, not a mode
        # allowlist. This argument is retained for constructor compatibility.
        del durability_required
        if not manager.can_write():
            raise SecretsManagerError(
                "The selected secrets provider is not configured for runtime writes"
            )
        self._manager = manager
        self._namespace = str(namespace or "").strip()
        self._bundle_id = bundle_id
        # Only an owned store passes the keyword, so a platform caller reaches any manager unchanged.
        self._owner = {} if bundle_id is None else {"bundle_id": bundle_id}

    @property
    def provider_type(self) -> str:
        return self._manager.provider_type

    @property
    def namespace(self) -> str:
        return self._namespace.lower()

    @property
    def bundle_id(self) -> str | None:
        return self._bundle_id

    async def qualify_durable_backend(self) -> bool:
        """Ask the selected secrets layer for this exact namespace's guarantees."""
        qualify = getattr(self._manager, "qualify_runtime_custody", None)
        return (await qualify(namespace=self._namespace, **self._owner)) is True if callable(qualify) else False

    async def set(
        self,
        *,
        secret_ref: str,
        value: str,
        expires_at: int,
    ) -> None:
        await self._manager.set_ephemeral_secret(
            namespace=self._namespace,
            **self._owner,
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
            **self._owner,
            secret_ref=secret_ref,
            value=value,
            expires_at=expires_at,
        )

    async def get(self, *, secret_ref: str) -> str | None:
        return await self._manager.get_ephemeral_secret(
            namespace=self._namespace,
            **self._owner,
            secret_ref=secret_ref,
        )

    async def delete(self, *, secret_ref: str) -> None:
        await self._manager.delete_ephemeral_secret(
            namespace=self._namespace,
            **self._owner,
            secret_ref=secret_ref,
        )

    async def purge_expired(self, *, now: int, limit: int) -> int:
        return await self._manager.purge_expired_ephemeral_secrets(
            namespace=self._namespace,
            **self._owner,
            now=now,
            limit=limit,
        )

    async def probe_writable(self) -> None:
        """Exercise the mutation lane without creating a stored value."""

        await self._manager.delete_ephemeral_secret(
            namespace=self._namespace,
            **self._owner,
            secret_ref=uuid.uuid4().hex,
        )


def ephemeral_secret_store(
    *,
    namespace: str,
    settings: Any | None = None,
    manager: ISecretsManager | None = None,
    durability_required: bool = False,
    bundle_id: str | None = None,
) -> KDCubeEphemeralSecretStore:
    """Build the deployment-selected, mode-neutral runtime adapter for one owner bundle."""

    selected = manager or _runtime_secret_manager(settings)
    return KDCubeEphemeralSecretStore(
        selected, namespace=namespace, durability_required=durability_required, bundle_id=bundle_id,
    )


__all__ = [
    "KDCubeEphemeralSecretStore",
    "LOCAL_SECRETS_SERVICE_URL",
    "SUPPORTED_EPHEMERAL_SECRET_PROVIDERS",
    "ephemeral_secret_store",
]
