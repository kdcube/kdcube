# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Expiry-enveloped bearer custody over a host-qualified secret backend.

The host owns namespace reader authorization and production-backend durability.
The envelope is secret provider data, never a public issuance receipt.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.infra.secrets.ephemeral import KDCubeEphemeralSecretStore, ephemeral_secret_store
from kdcube_ai_app.infra.secrets.manager import ISecretsManager, SecretsManagerError

_SCHEMA = "kdcube.issuance_custody.v1"
_REF = re.compile(r"[0-9a-f]{32}")
_MAX_BEARER_BYTES = 32768
_MAX_ENVELOPE_BYTES = 65536


def _valid_bearer(value: object) -> bool:
    try:
        return (type(value) is str and bool(value) and value == value.strip()
                and len(value.encode("utf-8")) <= _MAX_BEARER_BYTES
                and not any(ord(char) < 32 or ord(char) == 127 for char in value))
    except UnicodeError:
        return False


def _validate_ref(secret_ref: object) -> None:
    if type(secret_ref) is not str or _REF.fullmatch(secret_ref) is None:
        raise SessionIssuanceRefused("issuance_custody_invalid")


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


class KDCubeIssuanceSecretCustody:
    """Create-only original bearer with a deadline-enforcing read contract.

    Build through ``issuance_secret_custody`` for production selection checks.
    A directly injected store remains the trusted host's qualified dependency;
    this class cannot infer backend ACLs or restart durability from its name.
    """

    def __init__(self, store: KDCubeEphemeralSecretStore, *, settings: Any | None = None) -> None:
        if type(store) is not KDCubeEphemeralSecretStore:
            raise SessionIssuanceRefused("issuance_custody_not_durable")
        if store.provider_type == "in-memory":
            raise SessionIssuanceRefused("issuance_custody_not_durable")
        if store.provider_type == "secrets-service":
            backend = str(getattr(settings, "SECRETS_SERVICE_BACKEND", None) or "").strip().lower().replace("_", "-")
            if backend != "host-vault":
                raise SecretsManagerError("Durable secrets-service custody requires the host-vault backend")
            self._effective_backend = "host-vault"
        elif store.provider_type == "aws-sm":
            self._effective_backend = "aws-sm"
        else:
            raise SessionIssuanceRefused("issuance_custody_not_durable")
        self._store = store

    @property
    def namespace(self) -> str:
        return self._store.namespace

    @property
    def effective_backend(self) -> str:
        """Configured selection only; call qualify for the running service."""
        return self._effective_backend

    @property
    def declared_backend(self) -> str:
        """The configured provider, never evidence of the running backend."""
        return self._effective_backend

    async def qualify(self) -> None:
        """Refuse a mismatched/unavailable running backend before secret I/O.

        Never cache this check: a later sidecar restart may change its backend.
        It does not establish namespace ACLs or durability through restart.
        """
        try:
            durable = await self._store.qualify_durable_backend()
        except Exception:
            raise SessionIssuanceRefused("issuance_custody_unavailable") from None
        if durable is not True:
            raise SessionIssuanceRefused("issuance_custody_not_durable")

    async def create(self, *, secret_ref: str, value: str, expires_at: int) -> bool:
        _validate_ref(secret_ref)
        if not _valid_bearer(value) or type(expires_at) is not int or expires_at <= 0:
            raise SessionIssuanceRefused("issuance_custody_invalid")
        if expires_at <= int(time.time()):
            raise SessionIssuanceRefused("issuance_custody_expired")
        await self.qualify()
        envelope = json.dumps({
            "schema": _SCHEMA, "secret_ref": secret_ref,
            "expires_at": expires_at, "bearer": value,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if len(envelope.encode("utf-8")) > _MAX_ENVELOPE_BYTES:
            raise SessionIssuanceRefused("issuance_custody_invalid")
        try:
            return await self._store.create(
                secret_ref=secret_ref, value=envelope, expires_at=expires_at,
            )
        except Exception:
            raise SessionIssuanceRefused("issuance_custody_unavailable") from None

    async def get(self, *, secret_ref: str) -> str | None:
        _validate_ref(secret_ref)
        await self.qualify()
        try:
            raw = await self._store.get(secret_ref=secret_ref)
        except Exception:
            raise SessionIssuanceRefused("issuance_custody_unavailable") from None
        if raw is None:
            return None
        try:
            if type(raw) is not str or len(raw.encode("utf-8")) > _MAX_ENVELOPE_BYTES:
                raise ValueError
            envelope = json.loads(raw, object_pairs_hook=_unique_fields)
            valid = (
                type(envelope) is dict
                and set(envelope) == {"schema", "secret_ref", "expires_at", "bearer"}
                and envelope["schema"] == _SCHEMA
                and envelope["secret_ref"] == secret_ref
                and type(envelope["expires_at"]) is int
                and envelope["expires_at"] > 0
                and _valid_bearer(envelope["bearer"])
            )
        except (TypeError, ValueError, UnicodeError, RecursionError):
            valid = False
        if not valid:
            raise SessionIssuanceRefused("issuance_custody_invalid")
        if envelope["expires_at"] <= int(time.time()):
            raise SessionIssuanceRefused("issuance_custody_expired")
        return envelope["bearer"]

    async def purge_expired(self, *, now: int, limit: int) -> int:
        if (type(now) is not int or not 1 <= now <= int(time.time())
                or type(limit) is not int or not 1 <= limit <= 1000):
            raise SessionIssuanceRefused("issuance_custody_invalid")
        await self.qualify()
        try:
            return await self._store.purge_expired(now=now, limit=limit)
        except Exception:
            raise SessionIssuanceRefused("issuance_custody_unavailable") from None


def issuance_secret_custody(
    *, namespace: str, settings: Any | None = None, manager: ISecretsManager | None = None,
) -> KDCubeIssuanceSecretCustody:
    """Select durable-provider custody; host authorization still needs proof."""

    return KDCubeIssuanceSecretCustody(
        ephemeral_secret_store(
            namespace=namespace, settings=settings, manager=manager, durability_required=True,
        ), settings=settings,
    )


__all__ = ["KDCubeIssuanceSecretCustody", "issuance_secret_custody"]
