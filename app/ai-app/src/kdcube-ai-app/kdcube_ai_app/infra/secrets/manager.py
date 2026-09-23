# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import logging
import os
import re
import asyncio
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import quote, urlparse

logger = logging.getLogger("kdcube.secrets.manager")

_SECRET_INVENTORY_SUFFIX = ".__keys"
PLATFORM_SECRET_PREFIX = "platform."
PLATFORM_SECRET_INVENTORY_KEY = "platform.__keys"
BUNDLE_SECRET_INVENTORY_KEY = "bundles.__keys"
USER_SECRET_INVENTORY_KEY = "users.__keys"
DEPLOYMENT_SECRET_INVENTORY_KEYS = (
    PLATFORM_SECRET_INVENTORY_KEY,
    BUNDLE_SECRET_INVENTORY_KEY,
    USER_SECRET_INVENTORY_KEY,
)
_MAX_SECRET_INVENTORY_KEYS = 4096
_MAX_SECRET_INVENTORY_BYTES = 4 * 1024 * 1024
_MAX_SECRET_PROVIDER_KEY_CHARS = 1024
_AWS_SECRET_INVENTORY_SCHEMA = "kdcube.aws_secret_inventory.v1"
_AWS_SECRET_STRING_MAX_BYTES = 64 * 1024
_EPHEMERAL_SECRET_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_EPHEMERAL_SECRET_REF = re.compile(r"^[a-f0-9]{32}$")
_EPHEMERAL_EXPIRES_AT_TAG = "kdcube:expires-at"
_EPHEMERAL_PURGE_INTERVAL_SECONDS = 300.0
_AWS_EPHEMERAL_PURGE_MAX_PAGES = 3
_EPHEMERAL_PURGE_LOCK = threading.Lock()


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _ephemeral_secret_parts(namespace: str, secret_ref: str) -> tuple[str, str]:
    clean_namespace = str(namespace or "").strip().lower()
    clean_ref = str(secret_ref or "").strip().lower()
    if not _EPHEMERAL_SECRET_NAMESPACE.fullmatch(clean_namespace):
        raise SecretsManagerError("Ephemeral secret namespace is invalid")
    if not _EPHEMERAL_SECRET_REF.fullmatch(clean_ref):
        raise SecretsManagerError("Ephemeral secret reference is invalid")
    return clean_namespace, clean_ref


def _ephemeral_provider_key(namespace: str, secret_ref: str) -> str:
    clean_namespace, clean_ref = _ephemeral_secret_parts(namespace, secret_ref)
    return f"platform.runtime.{clean_namespace}.{clean_ref}"


def _ephemeral_inventory_key(namespace: str) -> str:
    clean_namespace = str(namespace or "").strip().lower()
    if not _EPHEMERAL_SECRET_NAMESPACE.fullmatch(clean_namespace):
        raise SecretsManagerError("Ephemeral secret namespace is invalid")
    return f"platform.runtime.{clean_namespace}.__keys"


def _ephemeral_expires_at(value: str | None) -> int:
    try:
        payload = json.loads(value or "")
        expires_at = int(payload.get("expires_at") or 0) if isinstance(payload, dict) else 0
    except (TypeError, ValueError):
        expires_at = 0
    if expires_at <= 0:
        raise SecretsManagerError("Ephemeral secret envelope is invalid")
    return expires_at


async def _run_blocking_critical_section(fn) -> Any:
    task = asyncio.create_task(asyncio.to_thread(fn))
    cancelled = False
    while not task.done():
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        task.result()
        raise asyncio.CancelledError
    return task.result()


def _normalize_component_name(component: Optional[str]) -> str:
    raw = (component or "").strip().lower()
    if raw in {"proc", "processor", "worker", "chat-proc", "chat_proc"}:
        return "proc"
    if raw in {"ingress", "rest", "chat-rest", "chat_rest"}:
        return "ingress"
    return raw or "ingress"


def _normalize_provider_name(provider: Optional[str], *, url: Optional[str] = None) -> str:
    raw = (provider or "").strip().lower().replace("_", "-")
    if raw in {"local", "service", "sidecar", "secrets-service"}:
        return "secrets-service"
    if raw in {"aws", "aws-sm", "awssm"}:
        return "aws-sm"
    if raw in {"file", "yaml", "yaml-file", "secrets-file"}:
        return "secrets-file"
    if raw in {"memory", "in-memory", "inmemory", "none", "env", "disabled"}:
        return "in-memory"
    if raw:
        return raw
    if _first_non_empty(url):
        return "secrets-service"
    return "in-memory"


def _default_aws_sm_prefix(
    *,
    explicit: Optional[str] = None,
    tenant: Optional[str] = None,
    project: Optional[str] = None,
) -> str:
    if explicit:
        return explicit
    if tenant and project:
        return f"kdcube/{tenant}/{project}"
    return "kdcube"


def _get_httpx():
    import httpx

    return httpx


def _secret_inventory_prefix(key: str) -> str | None:
    text = str(key or "").strip()
    if not text.endswith(_SECRET_INVENTORY_SUFFIX):
        return None
    prefix = text[: -len("__keys")]
    return prefix if prefix else None


def _reject_inventory_constant(_value: str) -> None:
    raise ValueError("nonstandard inventory value")


def _parse_secret_inventory(
    raw: str | None,
    *,
    metadata_key: str,
) -> list[str]:
    prefix = _secret_inventory_prefix(metadata_key)
    if prefix is None:
        raise SecretsManagerError("Secret inventory key is invalid")
    if raw is None:
        return []
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > (
        _MAX_SECRET_INVENTORY_BYTES
    ):
        raise SecretsManagerError("Secret inventory response is invalid")
    try:
        values = json.loads(raw, parse_constant=_reject_inventory_constant)
    except (TypeError, ValueError) as exc:
        raise SecretsManagerError("Secret inventory response is invalid") from exc
    if not isinstance(values, list) or len(values) > _MAX_SECRET_INVENTORY_KEYS:
        raise SecretsManagerError("Secret inventory response is invalid")
    keys: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise SecretsManagerError("Secret inventory response is invalid")
        item = value.strip()
        if (
            not item
            or len(item) > _MAX_SECRET_PROVIDER_KEY_CHARS
            or not item.startswith(prefix)
            or _secret_inventory_prefix(item) is not None
        ):
            raise SecretsManagerError("Secret inventory response is invalid")
        keys.add(item)
    return sorted(keys)


def _parse_aws_deployment_inventory(raw: str | None) -> list[str]:
    if raw is None:
        raise SecretsManagerError("AWS secret inventory is not initialized")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > (
        _AWS_SECRET_STRING_MAX_BYTES
    ):
        raise SecretsManagerError("AWS secret inventory response is invalid")
    try:
        payload = json.loads(raw, parse_constant=_reject_inventory_constant)
    except (TypeError, ValueError) as exc:
        raise SecretsManagerError("AWS secret inventory response is invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema") != (
        _AWS_SECRET_INVENTORY_SCHEMA
    ):
        raise SecretsManagerError("AWS secret inventory response is invalid")
    values = payload.get("keys")
    if not isinstance(values, list) or len(values) > _MAX_SECRET_INVENTORY_KEYS:
        raise SecretsManagerError("AWS secret inventory response is invalid")
    keys: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise SecretsManagerError("AWS secret inventory response is invalid")
        try:
            key = validate_secret_provider_key(value)
        except SecretsManagerError as exc:
            raise SecretsManagerError(
                "AWS secret inventory response is invalid"
            ) from exc
        if _secret_inventory_prefix(key) is not None:
            raise SecretsManagerError("AWS secret inventory response is invalid")
        keys.add(key)
    if len(keys) != len(values):
        raise SecretsManagerError("AWS secret inventory response is invalid")
    return sorted(keys)


def _serialize_aws_deployment_inventory(keys: Iterable[str]) -> str:
    normalized = sorted(
        {
            validate_secret_provider_key(key)
            for key in keys
            if _secret_inventory_prefix(str(key or "").strip()) is None
        }
    )
    if len(normalized) > _MAX_SECRET_INVENTORY_KEYS:
        raise SecretsManagerWriteError("AWS secret inventory capacity exceeded")
    rendered = json.dumps(
        {"schema": _AWS_SECRET_INVENTORY_SCHEMA, "keys": normalized},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(rendered.encode("utf-8")) > _AWS_SECRET_STRING_MAX_BYTES:
        raise SecretsManagerWriteError("AWS secret inventory capacity exceeded")
    return rendered


class SecretsManagerError(RuntimeError):
    pass


class SecretsManagerWriteError(SecretsManagerError):
    pass


@dataclass(frozen=True)
class SecretsManagerConfig:
    provider: str
    component: str
    tenant: Optional[str] = None
    project: Optional[str] = None
    url: Optional[str] = None
    token: Optional[str] = None
    admin_token: Optional[str] = None
    aws_region: Optional[str] = None
    aws_profile: Optional[str] = None
    aws_sm_prefix: str = "kdcube"
    redis_url: Optional[str] = None
    global_secrets_yaml: Optional[str] = None
    bundle_secrets_yaml: Optional[str] = None
    read_timeout_seconds: float = 2.0
    write_timeout_seconds: float = 5.0


class ISecretsManager(ABC):
    provider_type: str

    @abstractmethod
    async def get_secret(self, key: str) -> Optional[str]:
        raise NotImplementedError

    async def get_secret_strict(self, key: str) -> Optional[str]:
        """Read a key while distinguishing absence from provider failure.

        Providers whose normal reads already raise on failure inherit this
        default. A provider with compatibility-oriented tolerant reads must
        override this method.
        """

        return await self.get_secret(key)

    async def list_secret_keys(self, metadata_key: str) -> list[str]:
        """Return one provider-derived inventory without exposing values."""

        metadata_key = validate_secret_provider_key(
            metadata_key,
            require_inventory=True,
        )
        raw = await self.get_secret_strict(metadata_key)
        return _parse_secret_inventory(raw, metadata_key=metadata_key)

    async def list_all_secret_keys(self) -> list[str]:
        """Return the explicit union of platform, bundle, and user keys."""

        keys: set[str] = set()
        for metadata_key in DEPLOYMENT_SECRET_INVENTORY_KEYS:
            keys.update(await self.list_secret_keys(metadata_key))
        return sorted(keys)

    def can_write(self) -> bool:
        return False

    async def set_secret(self, key: str, value: str) -> None:
        raise SecretsManagerWriteError(f"{self.provider_type} provider does not support writes")

    async def delete_secret(self, key: str) -> None:
        raise SecretsManagerWriteError(f"{self.provider_type} provider does not support deletes")

    async def set_many(self, values: Mapping[str, str]) -> None:
        for key, value in values.items():
            await self.set_secret(key, value)

    async def delete_many(self, keys: Iterable[str]) -> None:
        for key in keys:
            await self.delete_secret(key)

    def _claim_ephemeral_purge(self, namespace: str) -> bool:
        """Throttle cleanup per manager and namespace in this host process."""

        clean_namespace, _ = _ephemeral_secret_parts(namespace, "0" * 32)
        moment = time.monotonic()
        with _EPHEMERAL_PURGE_LOCK:
            due_by_namespace = getattr(self, "_ephemeral_purge_after", None)
            if not isinstance(due_by_namespace, dict):
                due_by_namespace = {}
                self._ephemeral_purge_after = due_by_namespace
            if moment < float(due_by_namespace.get(clean_namespace) or 0.0):
                return False
            # Claim before I/O so concurrent login starts do not all scan the
            # provider. A failed best-effort purge is retried after the same
            # bounded interval, outside the login-start critical path.
            due_by_namespace[clean_namespace] = (
                moment + _EPHEMERAL_PURGE_INTERVAL_SECONDS
            )
            return True

    async def set_ephemeral_secret(
        self,
        *,
        namespace: str,
        secret_ref: str,
        value: str,
        expires_at: int,
    ) -> None:
        """Create one short-lived runtime secret outside tracked descriptors."""

        del expires_at
        await self.set_secret(_ephemeral_provider_key(namespace, secret_ref), value)

    async def create_ephemeral_secret(
        self,
        *,
        namespace: str,
        secret_ref: str,
        value: str,
        expires_at: int,
    ) -> bool:
        """Atomically create one runtime secret without replacing a record."""

        del namespace, secret_ref, value, expires_at
        raise SecretsManagerWriteError(
            f"{self.provider_type} provider does not support create-only runtime secrets"
        )

    async def get_ephemeral_secret(
        self,
        *,
        namespace: str,
        secret_ref: str,
    ) -> Optional[str]:
        return await self.get_secret_strict(
            _ephemeral_provider_key(namespace, secret_ref)
        )

    async def delete_ephemeral_secret(
        self,
        *,
        namespace: str,
        secret_ref: str,
    ) -> None:
        await self.delete_secret(_ephemeral_provider_key(namespace, secret_ref))

    async def purge_expired_ephemeral_secrets(
        self,
        *,
        namespace: str,
        now: int,
        limit: int = 100,
    ) -> int:
        """Delete expired host-vault records without exposing their values."""

        clean_namespace, _ = _ephemeral_secret_parts(namespace, "0" * 32)
        if not self._claim_ephemeral_purge(clean_namespace):
            return 0
        prefix = f"platform.runtime.{clean_namespace}."
        keys = await self.list_secret_keys(_ephemeral_inventory_key(namespace))
        removed = 0
        for key in keys:
            if removed >= max(1, int(limit)):
                break
            if not key.startswith(prefix):
                continue
            raw = await self.get_secret_strict(key)
            if raw is None or _ephemeral_expires_at(raw) > int(now):
                continue
            await self.delete_secret(key)
            removed += 1
        return removed

    async def get_user_secret(self, *, user_id: str, key: str, bundle_id: str | None = None) -> Optional[str]:
        return await self.get_secret(build_user_secret_key(user_id=user_id, key=key, bundle_id=bundle_id))

    async def set_user_secret(self, *, user_id: str, key: str, value: str, bundle_id: str | None = None) -> None:
        await self.set_secret(build_user_secret_key(user_id=user_id, key=key, bundle_id=bundle_id), value)

    async def delete_user_secret(self, *, user_id: str, key: str, bundle_id: str | None = None) -> None:
        await self.delete_secret(build_user_secret_key(user_id=user_id, key=key, bundle_id=bundle_id))

    async def list_user_secret_keys(self, *, user_id: str, bundle_id: str | None = None) -> list[str]:
        return await self.list_secret_keys(
            build_user_secret_metadata_key(user_id=user_id, bundle_id=bundle_id)
        )


class InMemorySecretsManager(ISecretsManager):
    provider_type = "in-memory"

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = threading.RLock()

    async def get_secret(self, key: str) -> Optional[str]:
        key = validate_secret_provider_key(key)
        with self._lock:
            prefix = _secret_inventory_prefix(key)
            if prefix is not None:
                keys = sorted(
                    item
                    for item in self._data
                    if item.startswith(prefix)
                    and _secret_inventory_prefix(item) is None
                )
                return json.dumps(keys, ensure_ascii=False) if keys else None
            return self._data.get(key)

    def can_write(self) -> bool:
        return True

    async def set_secret(self, key: str, value: str) -> None:
        key = validate_secret_provider_key(key)
        with self._lock:
            if _secret_inventory_prefix(key) is not None:
                return
            self._data[key] = value

    async def delete_secret(self, key: str) -> None:
        key = validate_secret_provider_key(key)
        with self._lock:
            if _secret_inventory_prefix(key) is not None:
                return
            self._data.pop(key, None)

    async def create_ephemeral_secret(
        self,
        *,
        namespace: str,
        secret_ref: str,
        value: str,
        expires_at: int,
    ) -> bool:
        del expires_at
        key = _ephemeral_provider_key(namespace, secret_ref)
        with self._lock:
            if key in self._data:
                return False
            self._data[key] = value
            return True


def _split_bundle_secret_key(key: str) -> tuple[str, str] | None:
    prefix = "bundles."
    marker = ".secrets."
    if not isinstance(key, str) or not key.startswith(prefix):
        return None
    rest = key[len(prefix):]
    idx = rest.find(marker)
    if idx < 0:
        return None
    bundle_id = rest[:idx].strip()
    tail = rest[idx + len(marker):].strip()
    if not bundle_id or not tail:
        return None
    return bundle_id, tail


def _split_platform_secret_key(key: str) -> str | None:
    if not isinstance(key, str) or not key.startswith(PLATFORM_SECRET_PREFIX):
        return None
    tail = key[len(PLATFORM_SECRET_PREFIX) :].strip()
    return tail or None


def build_user_secret_key(*, user_id: str, key: str, bundle_id: str | None = None) -> str:
    resolved_user_id = str(user_id or "").strip()
    resolved_key = str(key or "").strip().strip(".")
    if not resolved_user_id or not resolved_key:
        raise SecretsManagerError("User secret key requires non-empty user_id and key")
    if bundle_id is not None:
        resolved_bundle_id = str(bundle_id or "").strip()
        if not resolved_bundle_id:
            raise SecretsManagerError("User bundle secret key requires non-empty bundle_id")
        return f"users.{resolved_user_id}.bundles.{resolved_bundle_id}.secrets.{resolved_key}"
    return f"users.{resolved_user_id}.secrets.{resolved_key}"


def build_user_secret_metadata_key(*, user_id: str, bundle_id: str | None = None) -> str:
    return build_user_secret_key(user_id=user_id, key="__keys", bundle_id=bundle_id)


def _split_user_secret_key(key: str) -> tuple[str, str | None, str] | None:
    prefix = "users."
    marker_bundle = ".bundles."
    marker_secrets = ".secrets."
    if not isinstance(key, str) or not key.startswith(prefix):
        return None
    rest = key[len(prefix):]
    bundle_idx = rest.find(marker_bundle)
    secrets_idx = rest.find(marker_secrets)
    if secrets_idx < 0:
        return None
    if bundle_idx >= 0 and bundle_idx < secrets_idx:
        user_id = rest[:bundle_idx].strip()
        remainder = rest[bundle_idx + len(marker_bundle):]
        inner_secrets_idx = remainder.find(marker_secrets)
        if inner_secrets_idx < 0:
            return None
        bundle_id = remainder[:inner_secrets_idx].strip()
        tail = remainder[inner_secrets_idx + len(marker_secrets):].strip()
        if not user_id or not bundle_id or not tail:
            return None
        return user_id, bundle_id, tail
    user_id = rest[:secrets_idx].strip()
    tail = rest[secrets_idx + len(marker_secrets):].strip()
    if not user_id or not tail:
        return None
    return user_id, None, tail


def validate_secret_provider_key(
    key: str,
    *,
    require_inventory: bool = False,
) -> str:
    """Validate one canonical provider key without inferring its scope."""

    if not isinstance(key, str):
        raise SecretsManagerError("Secret provider key must be text")
    text = key.strip()
    if (
        not text
        or text != key
        or len(text) > _MAX_SECRET_PROVIDER_KEY_CHARS
        or ".." in text
    ):
        raise SecretsManagerError("Secret provider key is invalid")
    inventory = _secret_inventory_prefix(text) is not None
    if require_inventory and not inventory:
        raise SecretsManagerError("Secret inventory key is invalid")

    if text in {BUNDLE_SECRET_INVENTORY_KEY, USER_SECRET_INVENTORY_KEY}:
        return text
    if _split_platform_secret_key(text) is not None:
        return text
    if _split_bundle_secret_key(text) is not None:
        return text
    if _split_user_secret_key(text) is not None:
        return text
    raise SecretsManagerError(
        "Secret provider key must use platform.*, bundles.*.secrets.*, or users.*"
    )


def _flatten_mapping(prefix: str, node: Any, out: dict[str, str]) -> None:
    if node is None:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if key is None:
                continue
            child = str(key).strip()
            if not child:
                continue
            _flatten_mapping(f"{prefix}.{child}" if prefix else child, value, out)
        return
    if isinstance(node, list):
        for idx, value in enumerate(node):
            _flatten_mapping(f"{prefix}.{idx}" if prefix else str(idx), value, out)
        return
    if isinstance(node, str):
        if node == "":
            return
        out[prefix] = node
        return
    out[prefix] = str(node)


def _flatten_global_secrets_descriptor(data: Mapping[str, Any]) -> dict[str, str]:
    root = data.get("secrets") if isinstance(data.get("secrets"), dict) else data
    invalid_roots = sorted(
        str(key)
        for key in root
        if str(key) not in {"platform", "users"}
    )
    if invalid_roots:
        raise SecretsManagerError(
            "secrets.yaml contains unqualified or misplaced roots; run the "
            "platform namespace migration"
        )
    flattened: dict[str, str] = {}
    _flatten_mapping("", root, flattened)
    return flattened


def _flatten_bundle_secrets_descriptor(data: Mapping[str, Any]) -> dict[str, str]:
    flattened: dict[str, str] = {}
    root = data.get("bundles") if isinstance(data.get("bundles"), dict) else data
    items = root.get("items") if isinstance(root, dict) else None
    if not isinstance(items, list):
        return flattened
    for item in items:
        if not isinstance(item, dict):
            continue
        bundle_id = str(item.get("id") or "").strip()
        if not bundle_id:
            continue
        secrets_block = item.get("secrets")
        if secrets_block is None:
            continue
        _flatten_mapping(f"bundles.{bundle_id}.secrets", secrets_block, flattened)
    return flattened


def _secret_inventory_metadata(flattened: Mapping[str, str]) -> dict[str, str]:
    keys_by_inventory: dict[str, set[str]] = {}
    for key in flattened:
        if _secret_inventory_prefix(key) is not None:
            continue
        bundle = _split_bundle_secret_key(key)
        if bundle is not None:
            bundle_id, _tail = bundle
            keys_by_inventory.setdefault(BUNDLE_SECRET_INVENTORY_KEY, set()).add(
                key
            )
            metadata_key = f"bundles.{bundle_id}.secrets.__keys"
        else:
            user = _split_user_secret_key(key)
            if user is None:
                if _split_platform_secret_key(key) is None:
                    continue
                keys_by_inventory.setdefault(
                    PLATFORM_SECRET_INVENTORY_KEY,
                    set(),
                ).add(key)
                continue
            user_id, bundle_id, _tail = user
            keys_by_inventory.setdefault(USER_SECRET_INVENTORY_KEY, set()).add(key)
            metadata_key = build_user_secret_metadata_key(
                user_id=user_id,
                bundle_id=bundle_id,
            )
        keys_by_inventory.setdefault(metadata_key, set()).add(key)
    return {
        metadata_key: json.dumps(sorted(keys), ensure_ascii=False)
        for metadata_key, keys in keys_by_inventory.items()
    }


def _parse_secret_mapping_payload(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        try:
            yaml = _yaml_module()
            parsed = yaml.safe_load(text)
        except Exception:
            return None
    return parsed if isinstance(parsed, dict) else None


def _resolve_nested_value(root: Any, path: str) -> Any:
    parts = [part.strip() for part in str(path or "").split(".") if part.strip()]
    if not parts:
        return root
    cursor: Any = root
    for part in parts:
        if isinstance(cursor, dict):
            cursor = cursor.get(part)
            if cursor is None:
                return None
            continue
        if isinstance(cursor, list) and part.isdigit():
            idx = int(part)
            if idx < 0 or idx >= len(cursor):
                return None
            cursor = cursor[idx]
            continue
        return None
    return cursor


def _storage_backend_and_key_from_uri(storage_uri: str) -> tuple[str, str]:
    raw = str(storage_uri or "").strip()
    if not raw:
        raise SecretsManagerError("Secrets file URI is empty")

    parsed = urlparse(raw)
    if parsed.scheme == "s3":
        bucket = (parsed.netloc or "").strip()
        key = (parsed.path or "").lstrip("/")
        if not bucket or not key:
            raise SecretsManagerError(f"Invalid S3 secrets file URI: {storage_uri}")
        prefix, _, leaf = key.rpartition("/")
        backend_uri = f"s3://{bucket}/{prefix}" if prefix else f"s3://{bucket}"
        return backend_uri, leaf

    file_path = parsed.path if parsed.scheme == "file" else raw
    resolved = Path(file_path).expanduser().resolve()
    if not resolved.name:
        raise SecretsManagerError(f"Secrets file URI must point to a file: {storage_uri}")
    backend_uri = f"file://{resolved.parent}"
    return backend_uri, resolved.name


def _yaml_module():
    try:
        import yaml  # type: ignore
    except Exception as exc:
        raise SecretsManagerError("PyYAML is required for the secrets-file provider") from exc
    return yaml


def _load_yaml_mapping_from_storage(storage_uri: str, *, missing_ok: bool = False) -> dict[str, Any]:
    yaml = _yaml_module()

    from kdcube_ai_app.storage.storage import create_storage_backend

    backend_uri, key = _storage_backend_and_key_from_uri(storage_uri)
    backend = create_storage_backend(backend_uri)
    if missing_ok and not backend.exists(key):
        return {}
    try:
        raw = backend.read_text(key)
    except Exception as exc:
        raise SecretsManagerError(f"Failed to read secrets descriptor: {storage_uri}") from exc

    try:
        # Prefer the libyaml-backed loader when available; it is semantically
        # equivalent to SafeLoader and substantially faster on large documents.
        c_safe_loader = getattr(yaml, "CSafeLoader", None)
        if c_safe_loader is not None:
            payload = yaml.load(raw, Loader=c_safe_loader) or {}
        else:
            payload = yaml.safe_load(raw) or {}
    except Exception as exc:
        raise SecretsManagerError(f"Failed to parse secrets YAML: {storage_uri}") from exc
    if not isinstance(payload, dict):
        raise SecretsManagerError(f"Secrets YAML must contain a mapping at top level: {storage_uri}")
    return payload


def _write_yaml_mapping_to_storage(storage_uri: str, payload: Mapping[str, Any]) -> None:
    yaml = _yaml_module()

    from kdcube_ai_app.storage.storage import create_storage_backend

    try:
        rendered = yaml.safe_dump(dict(payload), allow_unicode=True, sort_keys=False)
        parsed = urlparse(str(storage_uri or "").strip())
        if parsed.scheme in {"", "file"}:
            file_path = parsed.path if parsed.scheme == "file" else str(storage_uri or "").strip()
            resolved = Path(file_path).expanduser().resolve()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = resolved.with_name(f".{resolved.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
            fd = -1
            try:
                fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                if os.name == "posix":
                    os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    fd = -1
                    handle.write(rendered)
                    handle.flush()
                    os.fsync(handle.fileno())
                tmp_path.replace(resolved)
                if os.name == "posix":
                    resolved.chmod(0o600)
            finally:
                if fd >= 0:
                    os.close(fd)
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
            return

        backend_uri, key = _storage_backend_and_key_from_uri(storage_uri)
        backend = create_storage_backend(backend_uri)
        backend.write_text(key, rendered)
    except Exception as exc:
        raise SecretsManagerWriteError(f"Failed to write secrets descriptor: {storage_uri}") from exc


def _set_nested_value(root: dict[str, Any], path: str, value: str) -> None:
    parts = [part.strip() for part in str(path or "").split(".") if part.strip()]
    if not parts:
        raise SecretsManagerWriteError("Secret key path is empty")
    cursor = root
    for part in parts[:-1]:
        existing = cursor.get(part)
        if not isinstance(existing, dict):
            existing = {}
            cursor[part] = existing
        cursor = existing
    cursor[parts[-1]] = value


def _delete_nested_value(root: dict[str, Any], path: str) -> None:
    parts = [part.strip() for part in str(path or "").split(".") if part.strip()]
    if not parts:
        return

    def _walk(node: dict[str, Any], idx: int) -> bool:
        key = parts[idx]
        if key not in node:
            return not node
        if idx == len(parts) - 1:
            node.pop(key, None)
            return not node
        child = node.get(key)
        if not isinstance(child, dict):
            return not node
        should_prune_child = _walk(child, idx + 1)
        if should_prune_child:
            node.pop(key, None)
        return not node

    _walk(root, 0)


def _global_descriptor_root(data: dict[str, Any]) -> dict[str, Any]:
    root = data.get("secrets")
    if isinstance(root, dict):
        return root
    return data


def _bundle_descriptor_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    bundles_root = data.get("bundles")
    if not isinstance(bundles_root, dict):
        bundles_root = {}
        data["bundles"] = bundles_root
    bundles_root.setdefault("version", "1")
    items = bundles_root.get("items")
    if not isinstance(items, list):
        items = []
        bundles_root["items"] = items
    if any(not isinstance(item, dict) for item in items):
        items = [item for item in items if isinstance(item, dict)]
        bundles_root["items"] = items
    return items


def _find_bundle_item(items: list[dict[str, Any]], bundle_id: str) -> dict[str, Any] | None:
    for item in items:
        if str(item.get("id") or "").strip() == bundle_id:
            return item
    return None


class SecretsFileSecretsManager(ISecretsManager):
    provider_type = "secrets-file"
    _LOCK_TTL_SECONDS = 30
    _LOCK_WAIT_SECONDS = 10.0

    def __init__(self, config: SecretsManagerConfig) -> None:
        import kdcube_ai_app.infra.namespaces as namespaces

        self._global_uri = _first_non_empty(config.global_secrets_yaml)
        self._bundle_uri = _first_non_empty(config.bundle_secrets_yaml)
        if not self._global_uri and not self._bundle_uri:
            raise SecretsManagerError(
                "secrets-file provider requires GLOBAL_SECRETS_YAML and/or BUNDLE_SECRETS_YAML"
            )
        self._tenant = _first_non_empty(config.tenant) or "home"
        self._project = _first_non_empty(config.project) or "default-project"
        self._redis_url = _first_non_empty(config.redis_url)
        self._lock_key = namespaces.CONFIG.BUNDLES.SECRETS_FILE_LOCK_FMT.format(
            tenant=self._tenant,
            project=self._project,
        )
        self._lock = threading.RLock()
        self._redis = None

    def _load_current_data(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        if self._global_uri:
            global_flat = _flatten_global_secrets_descriptor(
                _load_yaml_mapping_from_storage(self._global_uri, missing_ok=True)
            )
            global_flat = {
                key: value
                for key, value in global_flat.items()
                if _secret_inventory_prefix(key) is None
            }
            merged.update(global_flat)
            merged.update(_secret_inventory_metadata(global_flat))
        if self._bundle_uri:
            bundle_flat = _flatten_bundle_secrets_descriptor(
                _load_yaml_mapping_from_storage(self._bundle_uri, missing_ok=True)
            )
            bundle_flat = {
                key: value
                for key, value in bundle_flat.items()
                if _secret_inventory_prefix(key) is None
            }
            merged.update(bundle_flat)
            merged.update(_secret_inventory_metadata(bundle_flat))
        return merged

    def _get_redis(self):
        if not self._redis_url:
            return None
        if self._redis is not None:
            return self._redis
        try:
            from kdcube_ai_app.infra.redis.client import get_async_redis_client

            self._redis = get_async_redis_client(self._redis_url, decode_responses=True)
        except Exception:
            logger.debug("Failed to initialize Redis client for secrets-file provider", exc_info=True)
            self._redis = None
        return self._redis

    async def _acquire_distributed_lock(self) -> tuple[Any, str] | tuple[None, None]:
        redis = self._get_redis()
        if redis is None:
            return None, None
        token = uuid.uuid4().hex
        start = time.time()
        while (time.time() - start) < self._LOCK_WAIT_SECONDS:
            try:
                acquired = bool(await redis.set(self._lock_key, token, nx=True, ex=self._LOCK_TTL_SECONDS))
            except Exception:
                acquired = False
            if acquired:
                return redis, token
            await asyncio.sleep(0.25)
        raise SecretsManagerWriteError("Failed to acquire distributed secrets-file write lock")

    async def _release_distributed_lock(self, redis, token: str | None) -> None:
        if redis is None or not token:
            return
        try:
            await redis.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1,
                self._lock_key,
                token,
            )
        except Exception:
            logger.debug("Failed to release distributed secrets-file write lock", exc_info=True)

    async def get_secret(self, key: str) -> Optional[str]:
        key = validate_secret_provider_key(key)

        def read_value() -> Optional[str]:
            with self._lock:
                return self._load_current_data().get(key)

        return await asyncio.to_thread(read_value)

    def can_write(self) -> bool:
        return True

    async def set_secret(self, key: str, value: str) -> None:
        await self.set_many({key: value})

    async def delete_secret(self, key: str) -> None:
        await self.delete_many([key])

    @staticmethod
    def _ephemeral_secrets_are_unsupported() -> SecretsManagerWriteError:
        return SecretsManagerWriteError(
            "secrets-file cannot store short-lived runtime secrets in tracked descriptors"
        )

    async def set_ephemeral_secret(
        self,
        *,
        namespace: str,
        secret_ref: str,
        value: str,
        expires_at: int,
    ) -> None:
        del namespace, secret_ref, value, expires_at
        raise self._ephemeral_secrets_are_unsupported()

    async def create_ephemeral_secret(
        self,
        *,
        namespace: str,
        secret_ref: str,
        value: str,
        expires_at: int,
    ) -> bool:
        del namespace, secret_ref, value, expires_at
        raise self._ephemeral_secrets_are_unsupported()

    async def get_ephemeral_secret(
        self,
        *,
        namespace: str,
        secret_ref: str,
    ) -> Optional[str]:
        del namespace, secret_ref
        raise self._ephemeral_secrets_are_unsupported()

    async def delete_ephemeral_secret(
        self,
        *,
        namespace: str,
        secret_ref: str,
    ) -> None:
        del namespace, secret_ref
        raise self._ephemeral_secrets_are_unsupported()

    async def purge_expired_ephemeral_secrets(
        self,
        *,
        namespace: str,
        now: int,
        limit: int = 100,
    ) -> int:
        del namespace, now, limit
        raise self._ephemeral_secrets_are_unsupported()

    async def set_many(self, values: Mapping[str, str]) -> None:
        normalized_values = {
            validate_secret_provider_key(key): value
            for key, value in values.items()
        }
        redis, token = await self._acquire_distributed_lock()

        def write_values() -> None:
            with self._lock:
                global_data = (
                    _load_yaml_mapping_from_storage(self._global_uri, missing_ok=True) if self._global_uri else None
                )
                bundle_data = (
                    _load_yaml_mapping_from_storage(self._bundle_uri, missing_ok=True) if self._bundle_uri else None
                )
                global_dirty = False
                bundle_dirty = False
                for key, value in normalized_values.items():
                    if _secret_inventory_prefix(key) is not None:
                        continue
                    bundle_match = _split_bundle_secret_key(key)
                    if bundle_match:
                        bundle_id, tail = bundle_match
                        if tail == "__keys":
                            continue
                        if bundle_data is None:
                            raise SecretsManagerWriteError(
                                "BUNDLE_SECRETS_YAML is not configured for the secrets-file provider"
                            )
                        items = _bundle_descriptor_items(bundle_data)
                        item = _find_bundle_item(items, bundle_id)
                        if item is None:
                            item = {"id": bundle_id, "secrets": {}}
                            items.append(item)
                        secrets = item.get("secrets")
                        if not isinstance(secrets, dict):
                            secrets = {}
                            item["secrets"] = secrets
                        _set_nested_value(secrets, tail, value)
                        bundle_dirty = True
                    else:
                        if global_data is None:
                            raise SecretsManagerWriteError(
                                "GLOBAL_SECRETS_YAML is not configured for the secrets-file provider"
                            )
                        _set_nested_value(_global_descriptor_root(global_data), key, value)
                        global_dirty = True
                if global_dirty and global_data is not None:
                    _write_yaml_mapping_to_storage(self._global_uri, global_data)
                if bundle_dirty and bundle_data is not None:
                    _write_yaml_mapping_to_storage(self._bundle_uri, bundle_data)

        try:
            await _run_blocking_critical_section(write_values)
        finally:
            await self._release_distributed_lock(redis, token)

    async def delete_many(self, keys: Iterable[str]) -> None:
        key_list = [validate_secret_provider_key(key) for key in keys]
        redis, token = await self._acquire_distributed_lock()

        def delete_values() -> None:
            with self._lock:
                global_data = (
                    _load_yaml_mapping_from_storage(self._global_uri, missing_ok=True) if self._global_uri else None
                )
                bundle_data = (
                    _load_yaml_mapping_from_storage(self._bundle_uri, missing_ok=True) if self._bundle_uri else None
                )
                global_dirty = False
                bundle_dirty = False
                for key in key_list:
                    if _secret_inventory_prefix(key) is not None:
                        continue
                    bundle_match = _split_bundle_secret_key(key)
                    if bundle_match:
                        bundle_id, tail = bundle_match
                        if tail == "__keys":
                            continue
                        if bundle_data is None:
                            raise SecretsManagerWriteError(
                                "BUNDLE_SECRETS_YAML is not configured for the secrets-file provider"
                            )
                        items = _bundle_descriptor_items(bundle_data)
                        item = _find_bundle_item(items, bundle_id)
                        if item is None:
                            continue
                        secrets = item.get("secrets")
                        if not isinstance(secrets, dict):
                            continue
                        _delete_nested_value(secrets, tail)
                        if not secrets:
                            item.pop("secrets", None)
                        bundle_dirty = True
                    else:
                        if global_data is None:
                            raise SecretsManagerWriteError(
                                "GLOBAL_SECRETS_YAML is not configured for the secrets-file provider"
                            )
                        _delete_nested_value(_global_descriptor_root(global_data), key)
                        global_dirty = True
                if global_dirty and global_data is not None:
                    _write_yaml_mapping_to_storage(self._global_uri, global_data)
                if bundle_dirty and bundle_data is not None:
                    _write_yaml_mapping_to_storage(self._bundle_uri, bundle_data)

        try:
            await _run_blocking_critical_section(delete_values)
        finally:
            await self._release_distributed_lock(redis, token)


class SecretsServiceSecretsManager(ISecretsManager):
    provider_type = "secrets-service"

    def __init__(self, config: SecretsManagerConfig) -> None:
        self._url = (config.url or "").rstrip("/")
        self._token = config.token
        self._admin_token = config.admin_token
        self._read_timeout = float(config.read_timeout_seconds)
        self._write_timeout = float(config.write_timeout_seconds)

    def _key_url(self, key: str) -> str:
        return f"{self._url}/secret/{quote(key, safe='')}"

    def _admin_headers(self) -> dict[str, str]:
        if not self._admin_token:
            return {}
        return {"X-KDCUBE-ADMIN-TOKEN": self._admin_token}

    @staticmethod
    def _validate_write_response(response: Any, *, operation: str) -> int | None:
        if response.status_code == 409:
            raise SecretsManagerWriteError(f"secrets-service {operation} conflict")
        if response.status_code == 503:
            raise SecretsManagerWriteError(f"secrets-service {operation} unavailable")
        if response.status_code != 200:
            raise SecretsManagerWriteError(
                f"secrets-service {operation} failed with status {response.status_code}"
            )
        try:
            payload = response.json() or {}
        except Exception:
            raise SecretsManagerWriteError(
                f"secrets-service {operation} returned an invalid response"
            ) from None
        if payload.get("status") != "ok":
            raise SecretsManagerWriteError(
                f"secrets-service {operation} returned an invalid response"
            )
        generation = payload.get("generation")
        if generation is not None and (
            isinstance(generation, bool) or not isinstance(generation, int) or generation < 1
        ):
            raise SecretsManagerWriteError(
                f"secrets-service {operation} returned an invalid generation"
            )
        return generation

    async def _read_secret(self, key: str, *, strict: bool) -> Optional[str]:
        key = validate_secret_provider_key(key)
        if not self._url:
            if strict:
                raise SecretsManagerError("Secrets service is not configured")
            return None
        httpx = _get_httpx()
        headers: dict[str, str] = {}
        if self._token:
            headers["X-KDCUBE-SECRET-TOKEN"] = self._token
        try:
            async with httpx.AsyncClient(timeout=self._read_timeout) as client:
                response = await client.get(self._key_url(key), headers=headers)
            if response.status_code == 200:
                try:
                    payload = response.json()
                except Exception:
                    if strict:
                        raise SecretsManagerError(
                            "Secrets service returned an invalid read response"
                        ) from None
                    return None
                if not isinstance(payload, dict) or "value" not in payload:
                    if strict:
                        raise SecretsManagerError(
                            "Secrets service returned an invalid read response"
                        )
                    return None
                value = payload.get("value")
                return str(value) if value is not None else None
            if response.status_code == 404:
                return None
            if response.status_code == 403 and not strict:
                return None
            if response.status_code == 503:
                logger.warning("Secrets service GET unavailable")
            else:
                logger.warning("Secrets service GET failed with status %s", response.status_code)
            if strict:
                raise SecretsManagerError("Secrets service read failed")
        except SecretsManagerError:
            raise
        except Exception as exc:
            logger.debug("Secrets service GET failed: %s", type(exc).__name__)
            if strict:
                raise SecretsManagerError("Secrets service read request failed") from None
        return None

    async def get_secret(self, key: str) -> Optional[str]:
        return await self._read_secret(key, strict=False)

    async def get_secret_strict(self, key: str) -> Optional[str]:
        return await self._read_secret(key, strict=True)

    def can_write(self) -> bool:
        # The local Host Vault broker intentionally permits an omitted HTTP
        # gate token: its deployment mTLS identity is the authority boundary.
        # A configured HTTP gate still rejects an absent or mismatched token.
        return bool(self._url)

    async def set_secret(self, key: str, value: str) -> None:
        key = validate_secret_provider_key(key)
        if _secret_inventory_prefix(key) is not None:
            return
        if not self.can_write():
            raise SecretsManagerWriteError("secrets-service provider is not configured for writes")
        httpx = _get_httpx()
        try:
            async with httpx.AsyncClient(timeout=self._write_timeout) as client:
                response = await client.post(
                    f"{self._url}/set",
                    json={"key": key, "value": value},
                    headers=self._admin_headers(),
                )
        except Exception:
            raise SecretsManagerWriteError("secrets-service set request failed") from None
        self._validate_write_response(response, operation="set")

    async def create_ephemeral_secret(
        self,
        *,
        namespace: str,
        secret_ref: str,
        value: str,
        expires_at: int,
    ) -> bool:
        del expires_at
        key = _ephemeral_provider_key(namespace, secret_ref)
        if not self.can_write():
            raise SecretsManagerWriteError(
                "secrets-service provider is not configured for writes"
            )
        httpx = _get_httpx()
        try:
            async with httpx.AsyncClient(timeout=self._write_timeout) as client:
                response = await client.post(
                    f"{self._url}/set",
                    json={"key": key, "value": value, "expected_generation": 0},
                    headers=self._admin_headers(),
                )
        except Exception:
            raise SecretsManagerWriteError(
                "secrets-service create request outcome is unknown"
            ) from None
        if response.status_code == 409:
            return False
        generation = self._validate_write_response(response, operation="create")
        if generation != 1:
            raise SecretsManagerWriteError(
                "secrets-service create response does not prove ownership; "
                "outcome is unknown"
            )
        return True

    async def delete_secret(self, key: str) -> None:
        key = validate_secret_provider_key(key)
        if _secret_inventory_prefix(key) is not None:
            return
        if not self.can_write():
            raise SecretsManagerWriteError("secrets-service provider is not configured for writes")
        httpx = _get_httpx()
        try:
            async with httpx.AsyncClient(timeout=self._write_timeout) as client:
                response = await client.delete(
                    self._key_url(key),
                    headers=self._admin_headers(),
                )
        except Exception:
            raise SecretsManagerWriteError("secrets-service delete request failed") from None
        if response.status_code == 405:
            await self.set_secret(key, "")
            return
        if response.status_code in {204, 404}:
            return
        self._validate_write_response(response, operation="delete")


class AwsSecretsManagerSecretsManager(ISecretsManager):
    provider_type = "aws-sm"
    _LOCK_TTL_SECONDS = 30
    _LOCK_WAIT_SECONDS = 10.0

    def __init__(self, config: SecretsManagerConfig) -> None:
        import kdcube_ai_app.infra.namespaces as namespaces

        self._region = config.aws_region
        self._profile = config.aws_profile
        self._prefix = (config.aws_sm_prefix or "kdcube").strip("/") or "kdcube"
        self._tenant = _first_non_empty(config.tenant) or "home"
        self._project = _first_non_empty(config.project) or "default-project"
        self._redis_url = _first_non_empty(config.redis_url)
        self._lock_key_fmt = namespaces.CONFIG.BUNDLES.SECRETS_AWS_SM_LOCK_FMT
        self._session: Any | None = None
        self._redis = None
        self._lock = threading.RLock()
        self._write_lock = asyncio.Lock()

    def _get_session(self):
        if self._session is not None:
            return self._session
        import aioboto3

        session_kwargs: dict[str, Any] = {}
        if self._profile:
            session_kwargs["profile_name"] = self._profile
        self._session = aioboto3.Session(**session_kwargs)
        return self._session

    def _client_cm(self):
        return self._get_session().client("secretsmanager", region_name=self._region)

    def _secret_id(self, key: str) -> str:
        key = validate_secret_provider_key(key)
        bundle_match = _split_bundle_secret_key(key)
        if bundle_match:
            bundle_id, _tail = bundle_match
            return f"{self._prefix}/bundles/{bundle_id}/secrets"
        user_match = _split_user_secret_key(key)
        if user_match:
            user_id, bundle_id, _tail = user_match
            if bundle_id:
                return f"{self._prefix}/users/{user_id}/bundles/{bundle_id}/secrets"
            return f"{self._prefix}/users/{user_id}/secrets"
        return f"{self._prefix}/platform/secrets"

    def _inventory_secret_id(self) -> str:
        return f"{self._prefix}/inventory"

    def _ephemeral_secret_id(self, namespace: str, secret_ref: str) -> str:
        clean_namespace, clean_ref = _ephemeral_secret_parts(namespace, secret_ref)
        return f"{self._prefix}/runtime/{clean_namespace}/{clean_ref}"

    def _doc_lock_key(self, secret_id: str) -> str:
        safe = str(secret_id or "").replace("/", ":")
        return self._lock_key_fmt.format(
            tenant=self._tenant,
            project=self._project,
            doc=safe,
        )

    def _error_code(self, exc: Exception) -> str:
        response = getattr(exc, "response", None) or {}
        error = response.get("Error") if isinstance(response, dict) else {}
        return str((error or {}).get("Code") or "")

    def _get_redis(self):
        if not self._redis_url:
            return None
        if self._redis is not None:
            return self._redis
        try:
            from kdcube_ai_app.infra.redis.client import get_async_redis_client

            self._redis = get_async_redis_client(self._redis_url, decode_responses=True)
        except Exception:
            logger.debug("Failed to initialize Redis client for aws-sm provider", exc_info=True)
            self._redis = None
        return self._redis

    async def _acquire_distributed_lock(self, secret_id: str) -> tuple[Any, str] | tuple[None, None]:
        redis = self._get_redis()
        if redis is None:
            return None, None
        token = uuid.uuid4().hex
        lock_key = self._doc_lock_key(secret_id)
        start = time.time()
        while (time.time() - start) < self._LOCK_WAIT_SECONDS:
            try:
                acquired = bool(await redis.set(lock_key, token, nx=True, ex=self._LOCK_TTL_SECONDS))
            except Exception:
                acquired = False
            if acquired:
                return redis, token
            await asyncio.sleep(0.25)
        raise SecretsManagerWriteError(f"Failed to acquire distributed aws-sm write lock for {secret_id}")

    async def _release_distributed_lock(self, redis, token: str | None, secret_id: str) -> None:
        if redis is None or not token:
            return
        lock_key = self._doc_lock_key(secret_id)
        try:
            await redis.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1,
                lock_key,
                token,
            )
        except Exception:
            logger.debug("Failed to release distributed aws-sm write lock", exc_info=True)

    async def _get_secret_string_by_id(
        self,
        secret_id: str,
        *,
        strict: bool,
    ) -> str | None:
        try:
            async with self._client_cm() as client:
                response = await client.get_secret_value(SecretId=secret_id)
        except Exception as exc:
            if self._error_code(exc) == "ResourceNotFoundException":
                return None
            logger.warning(
                "AWS Secrets Manager GET failed (%s)",
                type(exc).__name__,
            )
            if strict:
                raise SecretsManagerError("AWS Secrets Manager read failed") from None
            return None
        if "SecretString" in response:
            value = response.get("SecretString")
            return str(value) if value is not None else None
        binary = response.get("SecretBinary")
        if binary is None:
            if strict:
                raise SecretsManagerError(
                    "AWS Secrets Manager returned an invalid secret value"
                )
            return None
        try:
            if isinstance(binary, (bytes, bytearray)):
                return bytes(binary).decode("utf-8")
            return str(binary)
        except Exception:
            if strict:
                raise SecretsManagerError(
                    "AWS Secrets Manager returned an invalid secret value"
                ) from None
            return None

    async def _get_secret_mapping_by_id(
        self,
        secret_id: str,
        *,
        strict: bool,
    ) -> dict[str, Any] | None:
        raw = await self._get_secret_string_by_id(secret_id, strict=strict)
        payload = _parse_secret_mapping_payload(raw)
        if strict and raw is not None and payload is None:
            raise SecretsManagerError(
                "AWS Secrets Manager returned an invalid secret document"
            )
        return payload

    async def _load_grouped_payload_for_key(
        self,
        key: str,
        *,
        strict: bool,
    ) -> dict[str, Any] | None:
        bundle_match = _split_bundle_secret_key(key)
        if bundle_match:
            payload = await self._get_secret_mapping_by_id(
                self._secret_id(key),
                strict=strict,
            )
            return payload if isinstance(payload, dict) else None
        payload = await self._get_secret_mapping_by_id(
            self._secret_id(key),
            strict=strict,
        )
        return payload if isinstance(payload, dict) else None

    async def _put_secret_string_by_id(self, secret_id: str, value: str, *, key: str) -> None:
        try:
            async with self._client_cm() as client:
                try:
                    await client.put_secret_value(SecretId=secret_id, SecretString=value)
                    return
                except Exception as exc:
                    if self._error_code(exc) != "ResourceNotFoundException":
                        raise SecretsManagerWriteError(f"aws-sm put failed for {key}") from exc
                    await client.create_secret(Name=secret_id, SecretString=value)
        except SecretsManagerWriteError:
            raise
        except Exception as exc:
            raise SecretsManagerWriteError(f"aws-sm create failed for {key}") from exc

    async def _delete_secret_by_id(self, secret_id: str, *, key: str) -> None:
        try:
            async with self._client_cm() as client:
                await client.delete_secret(
                    SecretId=secret_id,
                    ForceDeleteWithoutRecovery=True,
                )
        except Exception as exc:
            code = self._error_code(exc)
            if code in {"ResourceNotFoundException", "InvalidRequestException"}:
                return
            raise SecretsManagerWriteError(f"aws-sm delete failed for {key}") from exc

    async def set_ephemeral_secret(
        self,
        *,
        namespace: str,
        secret_ref: str,
        value: str,
        expires_at: int,
    ) -> None:
        secret_id = self._ephemeral_secret_id(namespace, secret_ref)
        try:
            async with self._client_cm() as client:
                await client.create_secret(
                    Name=secret_id,
                    SecretString=value,
                    Tags=[
                        {
                            "Key": _EPHEMERAL_EXPIRES_AT_TAG,
                            "Value": str(int(expires_at)),
                        }
                    ],
                )
        except Exception as exc:
            raise SecretsManagerWriteError("aws-sm ephemeral create failed") from exc

    async def create_ephemeral_secret(
        self,
        *,
        namespace: str,
        secret_ref: str,
        value: str,
        expires_at: int,
    ) -> bool:
        _clean_namespace, clean_ref = _ephemeral_secret_parts(namespace, secret_ref)
        secret_id = self._ephemeral_secret_id(namespace, clean_ref)
        try:
            async with self._client_cm() as client:
                await client.create_secret(
                    Name=secret_id,
                    ClientRequestToken=clean_ref,
                    SecretString=value,
                    Tags=[
                        {
                            "Key": _EPHEMERAL_EXPIRES_AT_TAG,
                            "Value": str(int(expires_at)),
                        }
                    ],
                )
        except Exception as exc:
            if self._error_code(exc) == "ResourceExistsException":
                return False
            raise SecretsManagerWriteError(
                "aws-sm ephemeral create outcome is unknown"
            ) from exc
        return True

    async def get_ephemeral_secret(
        self,
        *,
        namespace: str,
        secret_ref: str,
    ) -> Optional[str]:
        return await self._get_secret_string_by_id(
            self._ephemeral_secret_id(namespace, secret_ref),
            strict=True,
        )

    async def delete_ephemeral_secret(
        self,
        *,
        namespace: str,
        secret_ref: str,
    ) -> None:
        secret_id = self._ephemeral_secret_id(namespace, secret_ref)
        await self._delete_secret_by_id(secret_id, key="ephemeral runtime secret")

    async def purge_expired_ephemeral_secrets(
        self,
        *,
        namespace: str,
        now: int,
        limit: int = 100,
    ) -> int:
        clean_namespace, _ = _ephemeral_secret_parts(namespace, "0" * 32)
        if not self._claim_ephemeral_purge(clean_namespace):
            return 0
        name_prefix = f"{self._prefix}/runtime/{clean_namespace}/"
        removed = 0
        next_token: str | None = None
        pages = 0
        try:
            async with self._client_cm() as client:
                while (
                    removed < max(1, int(limit))
                    and pages < _AWS_EPHEMERAL_PURGE_MAX_PAGES
                ):
                    request: dict[str, Any] = {
                        "Filters": [{"Key": "name", "Values": [name_prefix]}],
                        "MaxResults": 100,
                        "IncludePlannedDeletion": False,
                    }
                    if next_token:
                        request["NextToken"] = next_token
                    response = await client.list_secrets(**request)
                    pages += 1
                    for item in response.get("SecretList") or []:
                        tags = {
                            str(tag.get("Key") or ""): str(tag.get("Value") or "")
                            for tag in item.get("Tags") or []
                            if isinstance(tag, Mapping)
                        }
                        try:
                            expires_at = int(tags.get(_EPHEMERAL_EXPIRES_AT_TAG) or 0)
                        except (TypeError, ValueError):
                            expires_at = 0
                        name = str(item.get("Name") or "")
                        if not name.startswith(name_prefix) or expires_at <= 0 or expires_at > int(now):
                            continue
                        await client.delete_secret(
                            SecretId=name,
                            ForceDeleteWithoutRecovery=True,
                        )
                        removed += 1
                        if removed >= max(1, int(limit)):
                            break
                    next_token = str(response.get("NextToken") or "")
                    if not next_token:
                        break
        except Exception as exc:
            raise SecretsManagerWriteError("aws-sm ephemeral purge failed") from exc
        return removed

    async def _read_secret(self, key: str, *, strict: bool) -> Optional[str]:
        key = validate_secret_provider_key(key)
        if key.endswith(".__keys"):
            try:
                keys = await self.list_secret_keys(key)
            except SecretsManagerError:
                if strict:
                    raise
                return None
            return json.dumps(keys, ensure_ascii=False) if keys else None
        payload = await self._load_grouped_payload_for_key(key, strict=strict)
        if isinstance(payload, dict):
            bundle_match = _split_bundle_secret_key(key)
            if bundle_match:
                _bundle_id, tail = bundle_match
            else:
                user_match = _split_user_secret_key(key)
                if user_match:
                    _user_id, _bundle_id, tail = user_match
                else:
                    tail = _split_platform_secret_key(key)
                    if tail is None:
                        raise SecretsManagerError(
                            "Secret provider key scope is invalid"
                        )
            value = _resolve_nested_value(payload, tail)
            if value is not None:
                return str(value)
        return None

    async def get_secret(self, key: str) -> Optional[str]:
        return await self._read_secret(key, strict=False)

    async def get_secret_strict(self, key: str) -> Optional[str]:
        return await self._read_secret(key, strict=True)

    async def list_secret_keys(self, metadata_key: str) -> list[str]:
        metadata_key = validate_secret_provider_key(
            metadata_key,
            require_inventory=True,
        )
        prefix = _secret_inventory_prefix(metadata_key)
        if prefix is None:
            raise SecretsManagerError("Secret inventory key is invalid")
        keys = _parse_aws_deployment_inventory(
            await self._get_secret_string_by_id(
                self._inventory_secret_id(),
                strict=True,
            )
        )
        return [key for key in keys if key.startswith(prefix)]

    async def list_all_secret_keys(self) -> list[str]:
        return _parse_aws_deployment_inventory(
            await self._get_secret_string_by_id(
                self._inventory_secret_id(),
                strict=True,
            )
        )

    async def _inventory_for_mutation(self) -> set[str]:
        raw = await self._get_secret_string_by_id(
            self._inventory_secret_id(),
            strict=True,
        )
        if raw is None:
            return set()
        return set(_parse_aws_deployment_inventory(raw))

    async def _write_inventory(self, keys: Iterable[str]) -> None:
        await self._put_secret_string_by_id(
            self._inventory_secret_id(),
            _serialize_aws_deployment_inventory(keys),
            key="deployment inventory",
        )

    def can_write(self) -> bool:
        return True

    async def set_secret(self, key: str, value: str) -> None:
        if key.endswith(".__keys"):
            return
        await self.set_many({key: value})

    async def delete_secret(self, key: str) -> None:
        if key.endswith(".__keys"):
            return
        await self.delete_many([key])

    def _tail_for_key(self, key: str) -> str:
        key = validate_secret_provider_key(key)
        bundle_match = _split_bundle_secret_key(key)
        if bundle_match:
            _bundle_id, tail = bundle_match
            return tail
        user_match = _split_user_secret_key(key)
        if user_match:
            _user_id, _bundle_id, tail = user_match
            return tail
        platform_tail = _split_platform_secret_key(key)
        if platform_tail is None:
            raise SecretsManagerError("Secret provider key scope is invalid")
        return platform_tail

    async def set_many(self, values: Mapping[str, str]) -> None:
        grouped: dict[str, dict[str, str]] = {}
        for raw_key, value in values.items():
            key = validate_secret_provider_key(raw_key)
            if str(key).endswith(".__keys"):
                continue
            grouped.setdefault(self._secret_id(key), {})[key] = value
        if not grouped:
            return
        inventory_id = self._inventory_secret_id()
        async with self._write_lock:
            redis, token = await self._acquire_distributed_lock(inventory_id)
            try:
                inventory = await self._inventory_for_mutation()
                inventory.update(
                    key for doc_values in grouped.values() for key in doc_values
                )
                await self._write_inventory(inventory)
                for secret_id, doc_values in grouped.items():
                    first_key = next(iter(doc_values.keys()))
                    payload = await self._load_grouped_payload_for_key(
                        first_key,
                        strict=True,
                    ) or {}
                    for key, value in doc_values.items():
                        _set_nested_value(payload, self._tail_for_key(key), value)
                    await self._put_secret_string_by_id(
                        secret_id,
                        json.dumps(payload, ensure_ascii=False),
                        key=first_key,
                    )
            finally:
                await self._release_distributed_lock(redis, token, inventory_id)

    async def delete_many(self, keys: Iterable[str]) -> None:
        grouped: dict[str, list[str]] = {}
        for raw_key in keys:
            key = validate_secret_provider_key(raw_key)
            if str(key).endswith(".__keys"):
                continue
            grouped.setdefault(self._secret_id(key), []).append(key)
        if not grouped:
            return
        inventory_id = self._inventory_secret_id()
        async with self._write_lock:
            redis, token = await self._acquire_distributed_lock(inventory_id)
            try:
                inventory = await self._inventory_for_mutation()
                for secret_id, doc_keys in grouped.items():
                    payload = await self._load_grouped_payload_for_key(
                        doc_keys[0],
                        strict=True,
                    ) or {}
                    for key in doc_keys:
                        _delete_nested_value(payload, self._tail_for_key(key))
                    if payload:
                        await self._put_secret_string_by_id(
                            secret_id,
                            json.dumps(payload, ensure_ascii=False),
                            key=doc_keys[0],
                        )
                    else:
                        await self._delete_secret_by_id(secret_id, key=doc_keys[0])
                inventory.difference_update(
                    key for doc_keys in grouped.values() for key in doc_keys
                )
                await self._write_inventory(inventory)
            finally:
                await self._release_distributed_lock(redis, token, inventory_id)


def build_secrets_manager_config(settings: Any | None = None) -> SecretsManagerConfig:
    component = _normalize_component_name(
        getattr(settings, "GATEWAY_COMPONENT", None) or os.getenv("GATEWAY_COMPONENT")
    )
    url = _first_non_empty(
        getattr(settings, "SECRETS_URL", None),
        os.getenv("SECRETS_URL"),
    )
    global_secrets_yaml = _first_non_empty(
        getattr(settings, "GLOBAL_SECRETS_YAML", None),
        os.getenv("GLOBAL_SECRETS_YAML"),
    )
    bundle_secrets_yaml = _first_non_empty(
        getattr(settings, "BUNDLE_SECRETS_YAML", None),
        os.getenv("BUNDLE_SECRETS_YAML"),
    )
    provider = _normalize_provider_name(
        _first_non_empty(
            getattr(settings, "SECRETS_PROVIDER", None),
            os.getenv("SECRETS_PROVIDER"),
        ),
        url=url,
    )
    if provider == "in-memory" and (global_secrets_yaml or bundle_secrets_yaml):
        provider = "secrets-file"
    tenant = _first_non_empty(getattr(settings, "TENANT", None))
    project = _first_non_empty(getattr(settings, "PROJECT", None))
    explicit_prefix = _first_non_empty(
        getattr(settings, "SECRETS_AWS_SM_PREFIX", None),
        getattr(settings, "SECRETS_SM_PREFIX", None),
    )
    return SecretsManagerConfig(
        provider=provider,
        component=component,
        tenant=tenant,
        project=project,
        url=url,
        token=_first_non_empty(
            getattr(settings, "SECRETS_TOKEN", None),
            os.getenv("SECRETS_TOKEN"),
        ),
        admin_token=_first_non_empty(
            getattr(settings, "SECRETS_ADMIN_TOKEN", None),
            os.getenv("SECRETS_ADMIN_TOKEN"),
        ),
        aws_region=_first_non_empty(
            getattr(settings, "SECRETS_AWS_REGION", None),
            os.getenv("SECRETS_AWS_REGION"),
            os.getenv("SECRETS_SM_REGION"),
            getattr(settings, "AWS_REGION", None),
            os.getenv("AWS_REGION"),
        ),
        aws_profile=_first_non_empty(
            getattr(settings, "AWS_PROFILE", None),
            os.getenv("AWS_PROFILE"),
        ),
        aws_sm_prefix=_default_aws_sm_prefix(
            explicit=explicit_prefix,
            tenant=tenant,
            project=project,
        ),
        redis_url=_first_non_empty(
            getattr(settings, "REDIS_URL", None),
            os.getenv("REDIS_URL"),
        ),
        global_secrets_yaml=global_secrets_yaml,
        bundle_secrets_yaml=bundle_secrets_yaml,
    )


def create_secrets_manager(config: SecretsManagerConfig) -> ISecretsManager:
    if config.provider == "secrets-service":
        return SecretsServiceSecretsManager(config)
    if config.provider == "aws-sm":
        return AwsSecretsManagerSecretsManager(config)
    if config.provider == "secrets-file":
        return SecretsFileSecretsManager(config)
    if config.provider == "in-memory":
        return InMemorySecretsManager()
    raise SecretsManagerError(f"Unsupported secrets provider: {config.provider}")


_manager_lock = threading.RLock()
_manager_cache_key: tuple[Any, ...] | None = None
_manager_cache: ISecretsManager | None = None


def get_secrets_manager(settings: Any | None = None) -> ISecretsManager:
    global _manager_cache, _manager_cache_key
    config = build_secrets_manager_config(settings)
    key = (
        config.provider,
        config.component,
        config.url,
        config.token,
        config.admin_token,
        config.aws_region,
        config.aws_profile,
        config.aws_sm_prefix,
        config.redis_url,
        config.global_secrets_yaml,
        config.bundle_secrets_yaml,
        config.read_timeout_seconds,
        config.write_timeout_seconds,
    )
    with _manager_lock:
        if _manager_cache is None or _manager_cache_key != key:
            _manager_cache = create_secrets_manager(config)
            _manager_cache_key = key
        return _manager_cache


def reset_secrets_manager_cache() -> None:
    global _manager_cache, _manager_cache_key
    with _manager_lock:
        _manager_cache = None
        _manager_cache_key = None
