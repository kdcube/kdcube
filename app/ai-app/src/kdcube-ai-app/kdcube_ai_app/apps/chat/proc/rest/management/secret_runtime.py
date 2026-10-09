# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Provider-neutral execution of exact delegated secret operations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import Request
from kdcube_ai_app.apps.chat.proc.rest.management.secret_contracts import (
    BUNDLE_SCOPE,
    MAX_SECRET_VALUE_BYTES,
    SecretTarget,
    USER_SCOPE,
)
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.infra.secrets.manager import (
    ISecretsManager,
    SecretsManagerError,
    get_secrets_manager,
)
from kdcube_ai_app.infra.secrets.projections import (
    invalidate_bundle_secret_inventory,
    publish_bundle_secret_update,
)


class ManagementSecretNotFound(LookupError):
    pass


class ManagementSecretsProviderReadOnly(RuntimeError):
    pass


class ManagementSecretsProviderUnavailable(RuntimeError):
    pass


class KDCubeSecretRuntime:
    """Resolve secret operations through the deployment-selected provider."""

    def __init__(
        self,
        request: Request,
        *,
        tenant: str,
        project: str,
        manager: ISecretsManager | None = None,
    ) -> None:
        self._request = request
        self._tenant = tenant
        self._project = project
        self._manager_override = manager

    def _manager(self) -> ISecretsManager:
        if self._manager_override is not None:
            return self._manager_override
        try:
            return get_secrets_manager(get_settings())
        except SecretsManagerError as exc:
            raise ManagementSecretsProviderUnavailable(
                "The configured secrets provider is unavailable"
            ) from exc

    @staticmethod
    async def _value(
        manager: ISecretsManager,
        target: SecretTarget,
    ) -> str | None:
        try:
            return await manager.get_secret_strict(target.provider_key)
        except Exception as exc:
            raise ManagementSecretsProviderUnavailable(
                "The configured secrets provider could not read the secret"
            ) from exc

    async def metadata(self, target: SecretTarget) -> Mapping[str, Any]:
        manager = self._manager()
        value = await self._value(manager, target)
        return {
            **target.public_dict(),
            "exists": value is not None,
            "provider": manager.provider_type,
            "writable": manager.can_write(),
        }

    async def inventory(self) -> tuple[SecretTarget, ...]:
        """List every canonical key from the selected provider without values."""

        manager = self._manager()
        try:
            keys = await manager.list_all_secret_keys()
            targets = tuple(SecretTarget.from_provider_key(key) for key in keys)
        except (SecretsManagerError, ValueError) as exc:
            raise ManagementSecretsProviderUnavailable(
                "The configured secrets provider could not list its inventory"
            ) from exc
        if len({target.provider_key for target in targets}) != len(targets):
            raise ManagementSecretsProviderUnavailable(
                "The configured secrets provider returned duplicate identities"
            )
        return tuple(sorted(targets, key=lambda target: target.provider_key))

    async def read(self, target: SecretTarget) -> Mapping[str, Any]:
        manager = self._manager()
        value = await self._value(manager, target)
        if value is None:
            raise ManagementSecretNotFound(target.provider_key)
        if not isinstance(value, str) or len(value.encode("utf-8")) > (
            MAX_SECRET_VALUE_BYTES
        ):
            raise ManagementSecretsProviderUnavailable(
                "The configured secrets provider returned an invalid secret value"
            )
        return {**target.public_dict(), "value": value}

    async def write(
        self,
        target: SecretTarget,
        *,
        value: str,
        caller_profile: str,
    ) -> Mapping[str, Any]:
        manager = self._manager()
        if not manager.can_write():
            raise ManagementSecretsProviderReadOnly(manager.provider_type)
        previous = await self._value(manager, target)
        try:
            await manager.set_secret(target.provider_key, value)
            # Every scope is broadcast (platform.* too), so each process's secret cache drops the entry.
            await self._record_bundle_update(
                target=target,
                mode="set",
                caller_profile=caller_profile,
            )
        except Exception as exc:
            raise ManagementSecretsProviderUnavailable(
                "The configured secrets provider could not write the secret"
            ) from exc
        return {
            **target.public_dict(),
            "created": previous is None,
            "provider": manager.provider_type,
            "state": "stored",
        }

    async def delete(
        self,
        target: SecretTarget,
        *,
        caller_profile: str,
    ) -> Mapping[str, Any]:
        manager = self._manager()
        if not manager.can_write():
            raise ManagementSecretsProviderReadOnly(manager.provider_type)
        previous = await self._value(manager, target)
        try:
            await manager.delete_secret(target.provider_key)
            # Every scope is broadcast (platform.* too), so each process's secret cache drops the entry.
            await self._record_bundle_update(
                target=target,
                mode="clear",
                caller_profile=caller_profile,
            )
        except Exception as exc:
            raise ManagementSecretsProviderUnavailable(
                "The configured secrets provider could not delete the secret"
            ) from exc
        return {
            **target.public_dict(),
            "existed": previous is not None,
            "provider": manager.provider_type,
            "state": "deleted",
        }

    async def _record_bundle_update(
        self,
        *,
        target: SecretTarget,
        mode: str,
        caller_profile: str,
    ) -> None:
        redis = getattr(self._request.app.state, "redis_async", None)
        if target.bundle_id:
            # The inventory projection is per bundle; a platform or bundle-less user key has none.
            await invalidate_bundle_secret_inventory(
                redis,
                tenant=self._tenant,
                project=self._project,
                bundle_id=target.bundle_id,
                user_id=target.user_id or None,
            )
        await publish_bundle_secret_update(
            redis,
            tenant=self._tenant,
            project=self._project,
            bundle_id=target.bundle_id or "",
            scope=target.scope,
            mode=mode,
            keys={target.provider_key},
            actor=caller_profile,
            user_id=target.user_id or None,
        )


__all__ = [
    "KDCubeSecretRuntime",
    "ManagementSecretNotFound",
    "ManagementSecretsProviderReadOnly",
    "ManagementSecretsProviderUnavailable",
]
