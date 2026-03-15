# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import quote

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
    if raw in {"memory", "in-memory", "inmemory", "none", "env", "disabled"}:
        return "in-memory"
    if raw:
        return raw
    if _first_non_empty(url):
        return "secrets-service"
    return "in-memory"


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
    url: Optional[str] = None
    token: Optional[str] = None
    admin_token: Optional[str] = None
    aws_region: Optional[str] = None
    aws_profile: Optional[str] = None
    aws_sm_prefix: str = "kdcube"
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
        parts = key.split(".")
        if len(parts) >= 4 and parts[0] == "bundles" and parts[2] == "secrets":
            bundle_id = parts[1]
            tail = "/".join(parts[3:])
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
    provider = _normalize_provider_name(
        _first_non_empty(
            getattr(settings, "SECRETS_PROVIDER", None),
            os.getenv("SECRETS_PROVIDER"),
        ),
        url=url,
    )
    return SecretsManagerConfig(
        provider=provider,
        component=component,
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
        aws_sm_prefix=_first_non_empty(
            getattr(settings, "SECRETS_AWS_SM_PREFIX", None),
            os.getenv("SECRETS_AWS_SM_PREFIX"),
            os.getenv("SECRETS_SM_PREFIX"),
            "kdcube",
        )
        or "kdcube",
    )


def create_secrets_manager(config: SecretsManagerConfig) -> ISecretsManager:
    if config.provider == "secrets-service":
        return SecretsServiceSecretsManager(config)
    if config.provider == "aws-sm":
        return AwsSecretsManagerSecretsManager(config)
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
