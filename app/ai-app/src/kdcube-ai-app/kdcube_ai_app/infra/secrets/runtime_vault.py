# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Expiry-aware immutable runtime records behind the authenticated vault broker.

The service boundary supplies exact namespace grants. This adapter enforces
record binding, expiry and generation-guarded cleanup, and consumes fresh
native storage/enrollment qualification. Caller-scope policy and deployment
mount persistence still require their own trusted composition and acceptance.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Iterable

from kdcube_ai_app.infra.secrets.host_vault.broker import BrokerResult, SecretsBroker
from kdcube_ai_app.infra.secrets.host_vault.protocol import MAX_VALUE_BYTES, ErrorCode
from kdcube_ai_app.infra.secrets.runtime_contract import valid_namespace

SCHEMA = "kdcube.runtime_vault_record.v1"
_REF = re.compile(r"[0-9a-f]{32}")


class RuntimeVaultError(RuntimeError):
    """A fixed, value-free service failure."""


@dataclass(frozen=True, repr=False)
class _Record:
    value: str
    expires_at: int
    generation: int


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


class RuntimeVaultStore:
    def __init__(self, *, broker: SecretsBroker, application: str, namespace: str,
                 authorized_namespaces: Iterable[str]):
        if not valid_namespace(namespace):
            raise RuntimeVaultError("runtime_secret_scope_invalid")
        if (isinstance(authorized_namespaces, (str, bytes))
                or namespace not in frozenset(authorized_namespaces)):
            raise RuntimeVaultError("runtime_secret_scope_forbidden")
        self._broker = broker
        self._application = application
        self._namespace = namespace
        self._prefix = f"platform.runtime.{namespace}."

    def qualify(self) -> None:
        try:
            result = self._broker.qualify_custody(application=self._application,
                metadata_key=self._prefix + "__keys")
            if type(result) is BrokerResult and result.ok is True and result.code is ErrorCode.OK:
                return
        except Exception:
            pass
        raise RuntimeVaultError("runtime_secret_custody_unqualified") from None

    def _key(self, secret_ref: str) -> str:
        if type(secret_ref) is not str or _REF.fullmatch(secret_ref) is None:
            raise RuntimeVaultError("runtime_secret_reference_invalid")
        return self._prefix + secret_ref

    @staticmethod
    def _call(operation, **kwargs):
        try:
            return operation(**kwargs)
        except Exception:
            raise RuntimeVaultError("runtime_secret_storage_unavailable") from None

    def _read(self, secret_ref: str) -> _Record | None:
        result = self._call(self._broker.read, application=self._application, key=self._key(secret_ref))
        if result.code is ErrorCode.NOT_FOUND:
            return None
        if not result.ok:
            raise RuntimeVaultError("runtime_secret_storage_unavailable")
        try:
            raw = result.value
            if type(raw) is not str or len(raw.encode("utf-8")) > MAX_VALUE_BYTES:
                raise ValueError
            payload = json.loads(raw, object_pairs_hook=_unique_fields)
            if (type(payload) is not dict
                    or set(payload) != {"schema", "namespace", "secret_ref", "value", "expires_at"}
                    or payload["schema"] != SCHEMA
                    or payload["namespace"] != self._namespace
                    or payload["secret_ref"] != secret_ref
                    or type(payload["value"]) is not str
                    or type(payload["expires_at"]) is not int or payload["expires_at"] <= 0
                    or type(result.generation) is not int or result.generation <= 0):
                raise ValueError
            payload["value"].encode("utf-8")
            return _Record(payload["value"], payload["expires_at"], result.generation)
        except (ValueError, UnicodeError, TypeError, RecursionError):
            raise RuntimeVaultError("runtime_secret_record_invalid") from None

    def create(self, *, secret_ref: str, value: str, expires_at: int) -> bool:
        key = self._key(secret_ref)
        try:
            if type(value) is not str or type(expires_at) is not int or expires_at <= 0:
                raise ValueError
            encoded = json.dumps({
                "schema": SCHEMA, "namespace": self._namespace, "secret_ref": secret_ref,
                "value": value, "expires_at": expires_at,
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
                raise ValueError
        except (ValueError, UnicodeError, TypeError):
            raise RuntimeVaultError("runtime_secret_value_invalid") from None
        if expires_at <= int(time.time()):
            raise RuntimeVaultError("runtime_secret_expired")
        result = self._call(self._broker.set, application=self._application, key=key,
                            value=encoded, expected_generation=0)
        if result.code is ErrorCode.CONFLICT:
            return False
        if not result.ok or type(result.generation) is not int or result.generation != 1:
            raise RuntimeVaultError("runtime_secret_storage_unavailable")
        # Transport/lock wait can cross expiry. The already-committed record
        # remains immutable and unreadable; no acknowledgement revives it.
        if expires_at <= int(time.time()):
            raise RuntimeVaultError("runtime_secret_expired")
        return True

    def get(self, *, secret_ref: str) -> str | None:
        record = self._read(secret_ref)
        return record.value if record is not None and record.expires_at > int(time.time()) else None

    def delete(self, *, secret_ref: str) -> None:
        record = self._read(secret_ref)
        if record is None:
            return
        result = self._call(self._broker.delete, application=self._application, key=self._key(secret_ref),
                            expected_generation=record.generation)
        if result.code is ErrorCode.CONFLICT:
            raise RuntimeVaultError("runtime_secret_conflict")
        if not result.ok:
            raise RuntimeVaultError("runtime_secret_storage_unavailable")

    def purge_expired(self, *, now: int, limit: int) -> int:
        if (type(now) is not int or now <= 0 or now > int(time.time())
                or type(limit) is not int or not 1 <= limit <= 1000):
            raise RuntimeVaultError("runtime_secret_purge_invalid")
        inventory = self._call(self._broker.list_names, application=self._application,
                               metadata_key=self._prefix + "__keys")
        if not inventory.ok:
            raise RuntimeVaultError("runtime_secret_storage_unavailable")
        candidates = []
        for name in inventory.names:
            if type(name) is not str or not name.startswith(self._prefix):
                raise RuntimeVaultError("runtime_secret_record_invalid")
            secret_ref = name[len(self._prefix):]
            record = self._read(secret_ref)
            if record is not None and record.expires_at <= now:
                candidates.append((secret_ref, record.generation))
            if len(candidates) == limit:
                break
        removed = 0
        for secret_ref, generation in candidates:
            if now > int(time.time()):
                raise RuntimeVaultError("runtime_secret_purge_invalid")
            result = self._call(self._broker.delete, application=self._application, key=self._key(secret_ref),
                                expected_generation=generation)
            if result.code in {ErrorCode.CONFLICT, ErrorCode.NOT_FOUND}:
                continue
            if not result.ok:
                raise RuntimeVaultError("runtime_secret_storage_unavailable")
            removed += 1
        return removed
