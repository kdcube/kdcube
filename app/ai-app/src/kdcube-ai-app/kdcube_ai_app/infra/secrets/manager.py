# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import quote, urlparse

logger = logging.getLogger("kdcube.secrets.manager")


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


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
    def get_secret(self, key: str) -> Optional[str]:
        raise NotImplementedError

    def can_write(self) -> bool:
        return False

    def set_secret(self, key: str, value: str) -> None:
        raise SecretsManagerWriteError(f"{self.provider_type} provider does not support writes")

    def delete_secret(self, key: str) -> None:
        raise SecretsManagerWriteError(f"{self.provider_type} provider does not support deletes")

    def set_many(self, values: Mapping[str, str]) -> None:
        for key, value in values.items():
            self.set_secret(key, value)

    def delete_many(self, keys: Iterable[str]) -> None:
        for key in keys:
            self.delete_secret(key)


class InMemorySecretsManager(ISecretsManager):
    provider_type = "in-memory"

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._lock = threading.RLock()

    def get_secret(self, key: str) -> Optional[str]:
        with self._lock:
            return self._data.get(key)

    def can_write(self) -> bool:
        return True

    def set_secret(self, key: str, value: str) -> None:
        with self._lock:
            self._data[key] = value

    def delete_secret(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)


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
    text = str(node).strip()
    if not text:
        return
    out[prefix] = text


def _flatten_global_secrets_descriptor(data: Mapping[str, Any]) -> dict[str, str]:
    root = data.get("secrets") if isinstance(data.get("secrets"), dict) else data
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


def _bundle_secret_metadata(flattened: Mapping[str, str]) -> dict[str, str]:
    keys_by_bundle: dict[str, list[str]] = {}
    for key in flattened.keys():
        parsed = _split_bundle_secret_key(key)
        if not parsed:
            continue
        bundle_id, tail = parsed
        if tail == "__keys":
            continue
        keys_by_bundle.setdefault(bundle_id, []).append(key)
    return {
        f"bundles.{bundle_id}.secrets.__keys": json.dumps(sorted(keys), ensure_ascii=False)
        for bundle_id, keys in keys_by_bundle.items()
    }


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
        payload = yaml.safe_load(raw) or {}
    except Exception as exc:
        raise SecretsManagerError(f"Failed to parse secrets YAML: {storage_uri}") from exc
    if not isinstance(payload, dict):
        raise SecretsManagerError(f"Secrets YAML must contain a mapping at top level: {storage_uri}")
    return payload


def _write_yaml_mapping_to_storage(storage_uri: str, payload: Mapping[str, Any]) -> None:
    yaml = _yaml_module()

    from kdcube_ai_app.storage.storage import create_storage_backend

    backend_uri, key = _storage_backend_and_key_from_uri(storage_uri)
    backend = create_storage_backend(backend_uri)
    try:
        rendered = yaml.safe_dump(dict(payload), allow_unicode=True, sort_keys=False)
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
            merged.update(
                _flatten_global_secrets_descriptor(
                    _load_yaml_mapping_from_storage(self._global_uri, missing_ok=True)
                )
            )
        if self._bundle_uri:
            bundle_flat = _flatten_bundle_secrets_descriptor(
                _load_yaml_mapping_from_storage(self._bundle_uri, missing_ok=True)
            )
            merged.update(bundle_flat)
            merged.update(_bundle_secret_metadata(bundle_flat))
        return merged

    def _get_sync_redis(self):
        if not self._redis_url:
            return None
        if self._redis is not None:
            return self._redis
        try:
            from kdcube_ai_app.infra.redis.client import get_sync_redis_client

            self._redis = get_sync_redis_client(self._redis_url, decode_responses=True)
        except Exception:
            logger.debug("Failed to initialize sync Redis client for secrets-file provider", exc_info=True)
            self._redis = None
        return self._redis

    def _acquire_distributed_lock(self) -> tuple[Any, str] | tuple[None, None]:
        redis = self._get_sync_redis()
        if redis is None:
            return None, None
        token = uuid.uuid4().hex
        start = time.time()
        while (time.time() - start) < self._LOCK_WAIT_SECONDS:
            try:
                acquired = bool(redis.set(self._lock_key, token, nx=True, ex=self._LOCK_TTL_SECONDS))
            except Exception:
                acquired = False
            if acquired:
                return redis, token
            time.sleep(0.25)
        raise SecretsManagerWriteError("Failed to acquire distributed secrets-file write lock")

    def _release_distributed_lock(self, redis, token: str | None) -> None:
        if redis is None or not token:
            return
        try:
            redis.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                1,
                self._lock_key,
                token,
            )
        except Exception:
            logger.debug("Failed to release distributed secrets-file write lock", exc_info=True)

    def get_secret(self, key: str) -> Optional[str]:
        with self._lock:
            return self._load_current_data().get(key)

    def can_write(self) -> bool:
        return True

    def _set_global_secret(self, key: str, value: str) -> None:
        if not self._global_uri:
            raise SecretsManagerWriteError(
                "GLOBAL_SECRETS_YAML is not configured for the secrets-file provider"
            )
        data = _load_yaml_mapping_from_storage(self._global_uri, missing_ok=True)
        _set_nested_value(_global_descriptor_root(data), key, value)
        _write_yaml_mapping_to_storage(self._global_uri, data)

    def _delete_global_secret(self, key: str) -> None:
        if not self._global_uri:
            raise SecretsManagerWriteError(
                "GLOBAL_SECRETS_YAML is not configured for the secrets-file provider"
            )
        data = _load_yaml_mapping_from_storage(self._global_uri, missing_ok=True)
        _delete_nested_value(_global_descriptor_root(data), key)
        _write_yaml_mapping_to_storage(self._global_uri, data)

    def _set_bundle_secret(self, bundle_id: str, tail: str, value: str) -> None:
        if tail == "__keys":
            return
        if not self._bundle_uri:
            raise SecretsManagerWriteError(
                "BUNDLE_SECRETS_YAML is not configured for the secrets-file provider"
            )
        data = _load_yaml_mapping_from_storage(self._bundle_uri, missing_ok=True)
        items = _bundle_descriptor_items(data)
        item = _find_bundle_item(items, bundle_id)
        if item is None:
            item = {"id": bundle_id, "secrets": {}}
            items.append(item)
        secrets = item.get("secrets")
        if not isinstance(secrets, dict):
            secrets = {}
            item["secrets"] = secrets
        _set_nested_value(secrets, tail, value)
        _write_yaml_mapping_to_storage(self._bundle_uri, data)

    def _delete_bundle_secret(self, bundle_id: str, tail: str) -> None:
        if tail == "__keys":
            return
        if not self._bundle_uri:
            raise SecretsManagerWriteError(
                "BUNDLE_SECRETS_YAML is not configured for the secrets-file provider"
            )
        data = _load_yaml_mapping_from_storage(self._bundle_uri, missing_ok=True)
        items = _bundle_descriptor_items(data)
        item = _find_bundle_item(items, bundle_id)
        if item is None:
            return
        secrets = item.get("secrets")
        if not isinstance(secrets, dict):
            return
        _delete_nested_value(secrets, tail)
        if not secrets:
            item.pop("secrets", None)
        _write_yaml_mapping_to_storage(self._bundle_uri, data)

    def set_secret(self, key: str, value: str) -> None:
        with self._lock:
            redis, token = self._acquire_distributed_lock()
            try:
                bundle_match = _split_bundle_secret_key(key)
                if bundle_match:
                    bundle_id, tail = bundle_match
                    self._set_bundle_secret(bundle_id, tail, value)
                else:
                    self._set_global_secret(key, value)
            finally:
                self._release_distributed_lock(redis, token)

    def delete_secret(self, key: str) -> None:
        with self._lock:
            redis, token = self._acquire_distributed_lock()
            try:
                bundle_match = _split_bundle_secret_key(key)
                if bundle_match:
                    bundle_id, tail = bundle_match
                    self._delete_bundle_secret(bundle_id, tail)
                else:
                    self._delete_global_secret(key)
            finally:
                self._release_distributed_lock(redis, token)

    def set_many(self, values: Mapping[str, str]) -> None:
        with self._lock:
            redis, token = self._acquire_distributed_lock()
            try:
                global_data = (
                    _load_yaml_mapping_from_storage(self._global_uri, missing_ok=True) if self._global_uri else None
                )
                bundle_data = (
                    _load_yaml_mapping_from_storage(self._bundle_uri, missing_ok=True) if self._bundle_uri else None
                )
                global_dirty = False
                bundle_dirty = False
                for key, value in values.items():
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
            finally:
                self._release_distributed_lock(redis, token)

    def delete_many(self, keys: Iterable[str]) -> None:
        with self._lock:
            redis, token = self._acquire_distributed_lock()
            try:
                global_data = (
                    _load_yaml_mapping_from_storage(self._global_uri, missing_ok=True) if self._global_uri else None
                )
                bundle_data = (
                    _load_yaml_mapping_from_storage(self._bundle_uri, missing_ok=True) if self._bundle_uri else None
                )
                global_dirty = False
                bundle_dirty = False
                for key in keys:
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
            finally:
                self._release_distributed_lock(redis, token)


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

    def get_secret(self, key: str) -> Optional[str]:
        if not self._url:
            return None
        httpx = _get_httpx()
        headers: dict[str, str] = {}
        if self._token:
            headers["X-KDCUBE-SECRET-TOKEN"] = self._token
        try:
            response = httpx.get(self._key_url(key), timeout=self._read_timeout, headers=headers)
            if response.status_code == 200:
                payload = response.json() or {}
                value = payload.get("value")
                return str(value) if value is not None else None
            if response.status_code in {403, 404}:
                return None
            logger.warning("Secrets service GET %s failed with status %s", key, response.status_code)
        except Exception:
            logger.debug("Secrets service GET %s failed", key, exc_info=True)
        return None

    def can_write(self) -> bool:
        return bool(self._url and self._admin_token)

    def set_secret(self, key: str, value: str) -> None:
        if not self.can_write():
            raise SecretsManagerWriteError("secrets-service provider is not configured for writes")
        httpx = _get_httpx()
        response = httpx.post(
            f"{self._url}/set",
            json={"key": key, "value": value},
            headers={"X-KDCUBE-ADMIN-TOKEN": self._admin_token},
            timeout=self._write_timeout,
        )
        if response.status_code != 200:
            raise SecretsManagerWriteError(f"secrets-service set failed for {key}: {response.status_code}")

    def delete_secret(self, key: str) -> None:
        if not self.can_write():
            raise SecretsManagerWriteError("secrets-service provider is not configured for writes")
        httpx = _get_httpx()
        response = httpx.delete(
            self._key_url(key),
            headers={"X-KDCUBE-ADMIN-TOKEN": self._admin_token},
            timeout=self._write_timeout,
        )
        if response.status_code == 405:
            # Backward-compatible fallback for older local sidecars.
            self.set_secret(key, "")
            return
        if response.status_code not in {200, 204, 404}:
            raise SecretsManagerWriteError(f"secrets-service delete failed for {key}: {response.status_code}")


class AwsSecretsManagerSecretsManager(ISecretsManager):
    provider_type = "aws-sm"

    def __init__(self, config: SecretsManagerConfig) -> None:
        self._region = config.aws_region
        self._profile = config.aws_profile
        self._prefix = (config.aws_sm_prefix or "kdcube").strip("/") or "kdcube"
        self._client: Any | None = None
        self._lock = threading.RLock()

    def _get_client(self):
        with self._lock:
            if self._client is not None:
                return self._client
            import boto3

            session_kwargs: dict[str, Any] = {}
            if self._profile:
                session_kwargs["profile_name"] = self._profile
            session = boto3.Session(**session_kwargs)
            self._client = session.client("secretsmanager", region_name=self._region)
            return self._client

    def _secret_id(self, key: str) -> str:
        bundle_match = _split_bundle_secret_key(key)
        if bundle_match:
            bundle_id, tail = bundle_match
            tail = tail.replace(".", "/")
            return f"{self._prefix}/bundles/{bundle_id}/secrets/{tail}"
        return f"{self._prefix}/{key.replace('.', '/')}"

    def _error_code(self, exc: Exception) -> str:
        response = getattr(exc, "response", None) or {}
        error = response.get("Error") if isinstance(response, dict) else {}
        return str((error or {}).get("Code") or "")

    def get_secret(self, key: str) -> Optional[str]:
        try:
            response = self._get_client().get_secret_value(SecretId=self._secret_id(key))
        except Exception as exc:
            if self._error_code(exc) == "ResourceNotFoundException":
                return None
            logger.warning("AWS Secrets Manager GET %s failed", key, exc_info=True)
            return None
        if "SecretString" in response:
            value = response.get("SecretString")
            return str(value) if value is not None else None
        binary = response.get("SecretBinary")
        if binary is None:
            return None
        if isinstance(binary, (bytes, bytearray)):
            return bytes(binary).decode("utf-8")
        return str(binary)

    def can_write(self) -> bool:
        return True

    def set_secret(self, key: str, value: str) -> None:
        client = self._get_client()
        secret_id = self._secret_id(key)
        try:
            client.put_secret_value(SecretId=secret_id, SecretString=value)
            return
        except Exception as exc:
            if self._error_code(exc) != "ResourceNotFoundException":
                raise SecretsManagerWriteError(f"aws-sm put failed for {key}") from exc
        try:
            client.create_secret(Name=secret_id, SecretString=value)
        except Exception as exc:
            raise SecretsManagerWriteError(f"aws-sm create failed for {key}") from exc

    def delete_secret(self, key: str) -> None:
        client = self._get_client()
        try:
            client.delete_secret(
                SecretId=self._secret_id(key),
                ForceDeleteWithoutRecovery=True,
            )
        except Exception as exc:
            code = self._error_code(exc)
            if code in {"ResourceNotFoundException", "InvalidRequestException"}:
                return
            raise SecretsManagerWriteError(f"aws-sm delete failed for {key}") from exc


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
