# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Service-owned AWS/PG resource lifecycle for the common custody boundary.

Trusted descriptor composition supplies references, never secret values.
Bootstrap reads pin complete AWS ARNs and VersionIds. No SDK caller opens
this pool; import, construction and route installation perform no I/O.
The deployment entrypoint/descriptor projection is a separate integration
step and the underlying AWS qualifier remains closed.
"""
from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from kdcube_ai_app.infra.secrets.runtime_aws import RuntimeAwsClientFactory, RuntimeAwsStore, RuntimeCloudError
from kdcube_ai_app.infra.secrets.runtime_contract import RuntimeScopePolicy, valid_namespace
from kdcube_ai_app.infra.secrets.runtime_http import install_runtime_routes
from kdcube_ai_app.infra.secrets.runtime_pg_access import check_runtime_metadata_access
from kdcube_ai_app.infra.secrets.runtime_pg_metadata import PostgresRuntimeCustodyMetadata
from kdcube_ai_app.infra.secrets.runtime_pg_schema import metadata_tables


@dataclass(frozen=True)
class RuntimeBootstrapSecretRef:
    arn: str
    version_id: str


@dataclass(frozen=True)
class RuntimeAwsServiceConfig:
    database_dsn_ref: RuntimeBootstrapSecretRef
    commitment_key_ref: RuntimeBootstrapSecretRef
    schema: str
    service_role: str
    namespaces: tuple[str, ...]
    cloud_prefix: str
    account_id: str
    region: str
    partition: str = "aws"
    max_pool_size: int = 4
    max_unresolved: int = 1000

    def __post_init__(self):
        try:
            metadata_tables(self.schema)
            if (self.schema == "public" or self.schema.startswith("pg_")
                    or type(self.service_role) is not str
                    or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", self.service_role) is None
                    or type(self.namespaces) is not tuple or not 1 <= len(self.namespaces) <= 64
                    or any(not valid_namespace(n) for n in self.namespaces)
                    or len(set(self.namespaces)) != len(self.namespaces)
                    or type(self.cloud_prefix) is not str
                    or re.fullmatch(r"[A-Za-z0-9/_+=.@-]{1,300}", self.cloud_prefix) is None
                    or self.cloud_prefix.startswith("/") or self.cloud_prefix.endswith("/")
                    or type(self.account_id) is not str or re.fullmatch(r"[0-9]{12}", self.account_id) is None
                    or type(self.region) is not str or re.fullmatch(r"[a-z0-9-]{1,64}", self.region) is None
                    or type(self.partition) is not str or re.fullmatch(r"[a-z][a-z0-9-]{1,31}", self.partition) is None
                    or type(self.max_pool_size) is not int or not 1 <= self.max_pool_size <= 16
                    or type(self.max_unresolved) is not int or not 1 <= self.max_unresolved <= 10000):
                raise ValueError
            prefix = f"arn:{self.partition}:secretsmanager:{self.region}:{self.account_id}:secret:"
            for ref in (self.database_dsn_ref, self.commitment_key_ref):
                if (type(ref) is not RuntimeBootstrapSecretRef or type(ref.arn) is not str
                        or re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9/_+=.@-]{1,300}-[A-Za-z0-9]{6}", ref.arn) is None
                        or type(ref.version_id) is not str or re.fullmatch(r"[A-Za-z0-9_-]{32,64}", ref.version_id) is None
                        or ref.arn[len(prefix):].startswith(self.cloud_prefix + "/")):
                    raise ValueError
            if self.database_dsn_ref.arn == self.commitment_key_ref.arn:
                raise ValueError
        except Exception:
            raise RuntimeCloudError("runtime_secret_storage_unavailable") from None


def _database_dsn(value, *, service_role: str) -> str:
    try:
        if type(value) is not str or not 1 <= len(value.encode("utf-8")) <= 8192:
            raise ValueError
        url = urlsplit(value)
        if (url.scheme not in {"postgresql", "postgres"} or not url.hostname
                or url.port is None or not 1 <= url.port <= 65535
                or unquote(url.username or "") != service_role or not url.password
                or re.fullmatch(r"/[a-z_][a-z0-9_]{0,62}", url.path) is None
                or url.query or url.fragment or any(char in value for char in "\r\n\0")):
            raise ValueError
        return value
    except Exception:
        raise RuntimeCloudError("runtime_secret_storage_unavailable") from None


def _session():
    import aioboto3
    return aioboto3.Session()


async def _pool(**kwargs):
    import asyncpg
    return await asyncpg.create_pool(**kwargs)


class RuntimeAwsService:
    """Own one dedicated pool and key for a trusted ASGI service lifecycle.

    Factories are composition dependencies for tests/hosts, never descriptor
    labels or HTTP request fields. The default pool requires verified TLS;
    the actual logged-in DB principal must pass a fresh acquisition check.
    """

    def __init__(self, config: RuntimeAwsServiceConfig, *, session_factory=_session, pool_factory=_pool):
        if (type(config) is not RuntimeAwsServiceConfig
                or not callable(session_factory) or not callable(pool_factory)):
            raise RuntimeCloudError("runtime_secret_storage_unavailable")
        self._config = config
        self._session_factory = session_factory
        self._pool_factory = pool_factory
        self._lock = asyncio.Lock()
        self._pool = None
        self._client_factory = None
        self._commitment_key = None

    async def _bootstrap_secret(self, client_factory, ref, *, binary: bool):
        async with client_factory() as client:
            value = await client.get_secret_value(SecretId=ref.arn, VersionId=ref.version_id)
        if (type(value) is not dict or value.get("ARN") != ref.arn
                or value.get("VersionId") != ref.version_id):
            raise RuntimeCloudError("runtime_secret_storage_unavailable")
        if binary:
            secret = value.get("SecretBinary")
            if "SecretString" in value or type(secret) is not bytes or not 32 <= len(secret) <= 4096:
                raise RuntimeCloudError("runtime_secret_storage_unavailable")
            return secret
        if "SecretBinary" in value:
            raise RuntimeCloudError("runtime_secret_storage_unavailable")
        return _database_dsn(value.get("SecretString"), service_role=self._config.service_role)

    @staticmethod
    async def _close_pool(pool):
        if pool is None:
            return
        try:
            await asyncio.wait_for(pool.close(), timeout=10)
        except BaseException:
            pool.terminate()
            raise

    async def start(self):
        async with self._lock:
            if self._pool is not None:
                return
            pool = None
            try:
                config = self._config
                client_factory = RuntimeAwsClientFactory(self._session_factory(), region=config.region)
                dsn = await asyncio.wait_for(
                    self._bootstrap_secret(client_factory, config.database_dsn_ref, binary=False), timeout=10)
                key = await asyncio.wait_for(
                    self._bootstrap_secret(client_factory, config.commitment_key_ref, binary=True), timeout=10)

                async def setup(connection):
                    await check_runtime_metadata_access(connection, schema=config.schema,
                                                        service_role=config.service_role)

                pool = await self._pool_factory(dsn=dsn, min_size=1, max_size=config.max_pool_size,
                                               ssl=True, timeout=10, command_timeout=5, setup=setup)
                # asyncpg setup is run at acquire, not at pool creation. Refuse
                # startup before publishing any service/store capability.
                async with pool.acquire():
                    pass
                self._client_factory, self._commitment_key, self._pool = client_factory, key, pool
            except BaseException as exc:
                try:
                    await self._close_pool(pool)
                except BaseException:
                    pass
                if isinstance(exc, Exception):
                    raise RuntimeCloudError("runtime_secret_storage_unavailable") from None
                raise

    async def close(self):
        async with self._lock:
            pool, self._pool = self._pool, None
            self._client_factory = self._commitment_key = None
            try:
                await self._close_pool(pool)
            except Exception:
                raise RuntimeCloudError("runtime_secret_storage_unavailable") from None

    def store(self, namespace: str):
        config = self._config
        if not valid_namespace(namespace) or namespace not in config.namespaces:
            raise RuntimeCloudError("runtime_secret_scope_forbidden")
        if self._pool is None or self._client_factory is None or self._commitment_key is None:
            raise RuntimeCloudError("runtime_secret_storage_unavailable")
        metadata = PostgresRuntimeCustodyMetadata(
            self._pool, schema=config.schema, namespace=namespace,
            authorized_namespaces=config.namespaces, cloud_prefix=config.cloud_prefix,
            max_unresolved=config.max_unresolved,
        )
        return RuntimeAwsStore(metadata=metadata, client_factory=self._client_factory,
                               account_id=config.account_id, region=config.region,
                               partition=config.partition, commitment_key=self._commitment_key)

    def install_routes(self, app, *, policy: RuntimeScopePolicy):
        # Async factory retains service-loop ownership; sync factories are run
        # in a threadpool by the common HTTP boundary.
        async def factory(namespace):
            return self.store(namespace)
        install_runtime_routes(app, policy=policy, store_factory=factory)

    async def admission_status(self, namespace: str):
        """Value-free input for the trusted operator/metrics collector.

        This service capability does not install a public status route or
        claim a collector is deployed. No secret/reference identity is emitted.
        """
        return await self.store(namespace)._metadata.admission_status()

    @asynccontextmanager
    async def lifespan(self, app):
        await self.start()
        try:
            yield
        finally:
            await self.close()
