# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""KDCube host adapter for short-lived Connection Hub secret records."""

from __future__ import annotations

import json
import time
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


# W670 (operator, 2026-10-09: "simply start to store the secrets with secrets manager" / "and read them with
# secreys manager" / "so this is change in connection hub, not in secret manager"): records are ordinary
# secrets of their owner, read and written through the configured manager's normal calls, on any backend.
RUNTIME_RECORDS_KEY = "__runtime_records__"


def _record_part(value: str, name: str) -> str:
    text = str(value or "").strip()
    if not text or "." in text or len(text) > 256 or any(ord(c) < 33 for c in text):
        raise SecretsManagerError(f"runtime_record_{name}_invalid")
    return text


class KDCubeEphemeralSecretStore:
    """Expiring Connection Hub secret records, kept as normal secrets of their owner bundle.

    Key: ``bundles.<owner>.secrets.__runtime_records__.<namespace>.<ref>`` (a bundle-less, platform-owned
    caller uses ``platform.__runtime_records__.<namespace>.<ref>``); value: ``{"value", "expires_at"}`` JSON.
    Only ``get_secret`` / ``set_secret`` / ``delete_secret`` of the configured manager are used. Values are
    never logged.
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
        self._bundle_id = str(bundle_id).strip() if bundle_id else None

    @property
    def provider_type(self) -> str:
        return self._manager.provider_type

    @property
    def namespace(self) -> str:
        return self._namespace.lower()

    def _owner_prefix(self) -> str:
        owner = self._bundle_id
        if owner is None:
            try:
                from kdcube_ai_app.apps.chat.sdk.config import _resolve_current_bundle_id

                owner = str(_resolve_current_bundle_id() or "").strip() or None
            except Exception:
                owner = None
        namespace = _record_part(self.namespace, "namespace")
        if owner:
            return f"bundles.{_record_part(owner, 'owner')}.secrets.{RUNTIME_RECORDS_KEY}.{namespace}"
        return f"platform.{RUNTIME_RECORDS_KEY}.{namespace}"

    def _key(self, secret_ref: str) -> str:
        return f"{self._owner_prefix()}.{_record_part(secret_ref, 'ref')}"

    @staticmethod
    def _decode(raw: str | None) -> tuple[str, int] | None:
        try:
            record = json.loads(raw) if isinstance(raw, str) else None
        except ValueError:
            return None
        if (not isinstance(record, dict) or type(record.get("value")) is not str
                or type(record.get("expires_at")) is not int):
            return None
        return record["value"], record["expires_at"]

    async def qualify_durable_backend(self) -> bool:
        """A probe round-trip through the configured manager's normal calls (no provider-specific check)."""
        probe = self._key(f"probe-{uuid.uuid4().hex}")
        try:
            await self._manager.set_secret(probe, "probe")
            ok = await self._manager.get_secret(probe) == "probe"
            await self._manager.delete_secret(probe)
            return ok and await self._manager.get_secret(probe) is None
        except Exception:
            return False

    async def set(self, *, secret_ref: str, value: str, expires_at: int) -> None:
        # Records are immutable, including the compatibility set operation.
        if not await self.create(secret_ref=secret_ref, value=value, expires_at=expires_at):
            raise SecretsManagerError("runtime_secret_create_conflict")

    async def create(self, *, secret_ref: str, value: str, expires_at: int) -> bool:
        """Create-only: absent, then set, then read back; anything else is a conflict, never success."""
        if type(value) is not str or type(expires_at) is not int or expires_at <= 0:
            raise SecretsManagerError("runtime_record_invalid")
        key = self._key(secret_ref)
        if await self._manager.get_secret(key) is not None:
            return False
        record = json.dumps({"value": value, "expires_at": expires_at}, sort_keys=True, separators=(",", ":"))
        await self._manager.set_secret(key, record)
        return await self._manager.get_secret(key) == record

    async def get(self, *, secret_ref: str) -> str | None:
        decoded = self._decode(await self._manager.get_secret(self._key(secret_ref)))
        if decoded is None or decoded[1] <= int(time.time()):
            return None
        return decoded[0]

    async def delete(self, *, secret_ref: str) -> None:
        await self._manager.delete_secret(self._key(secret_ref))

    async def purge_expired(self, *, now: int, limit: int) -> int:
        """Delete at most ``limit`` expired records of this namespace, found through the owner's existing
        ``__keys`` inventory (Main's choice A; no new index or listing)."""
        if type(now) is not int or type(limit) is not int or not 1 <= limit <= 1000:
            raise SecretsManagerError("runtime_record_purge_invalid")
        prefix = self._owner_prefix() + "."
        inventory = prefix.split(f".{RUNTIME_RECORDS_KEY}.")[0] + ".__keys"
        try:
            listed = json.loads(await self._manager.get_secret(inventory) or "[]")
        except ValueError:
            listed = []
        purged = 0
        for key in sorted(item for item in listed if isinstance(item, str) and item.startswith(prefix)):
            if purged >= limit:
                break
            decoded = self._decode(await self._manager.get_secret(key))
            if decoded is None or decoded[1] <= now:
                await self._manager.delete_secret(key)
                purged += 1
        return purged

    async def probe_writable(self) -> None:
        """Exercise the mutation lane without creating a stored value."""

        await self._manager.delete_secret(self._key(uuid.uuid4().hex))


def ephemeral_secret_store(
    *,
    namespace: str,
    settings: Any | None = None,
    manager: ISecretsManager | None = None,
    durability_required: bool = False,
    bundle_id: str | None = None,
) -> KDCubeEphemeralSecretStore:
    """Build the deployment-selected, mode-neutral runtime adapter for the owner bundle."""

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
