# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Service lifecycle: real disposable PG roles, synthetic AWS transport.

The loopback fixture deliberately substitutes SSL=False for the production
pool's verified-TLS setting. It proves access/lifecycle behavior, not deployed
TLS, persistence, IAM isolation, image/descriptor wiring, or AWS qualification.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from urllib.parse import urlsplit

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from kdcube_ai_app.infra.secrets.runtime_aws import RuntimeCloudError
from kdcube_ai_app.infra.secrets.runtime_aws_service import (
    RuntimeAwsService, RuntimeAwsServiceConfig, RuntimeBootstrapSecretRef, _database_dsn,
)
from kdcube_ai_app.infra.secrets.runtime_contract import POLICY_SCHEMA, RuntimeScopePolicy
from kdcube_ai_app.infra.secrets.runtime_pg_access import check_runtime_metadata_access
from kdcube_ai_app.infra.secrets.runtime_pg_schema import RuntimeMetadataError, create_runtime_metadata_schema
from kdcube_ai_app.infra.secrets.tests.test_runtime_aws import SyntheticAws

NS = "custody"
PREFIX = "arn:aws:secretsmanager:eu-central-1:123456789012:secret:bootstrap/"
KEY = hashlib.sha256(b"synthetic-service-key-no-live-material").digest()
DSN_CANARY = "synthetic-database-password-no-live-material"


def configuration(**changes):
    values = dict(
        database_dsn_ref=RuntimeBootstrapSecretRef(PREFIX + "database-Ab1234", "a" * 32),
        commitment_key_ref=RuntimeBootstrapSecretRef(PREFIX + "commitment-Cd5678", "b" * 32),
        schema="runtime_custody_test", service_role="runtime_custody_service",
        namespaces=(NS,), cloud_prefix="test/custody", account_id="123456789012", region="eu-central-1",
    )
    values.update(changes)
    return RuntimeAwsServiceConfig(**values)


class Session:
    """Explicit transport-shape fixture; no actual AWS client/network."""

    def __init__(self, cloud):
        self.cloud, self.configurations = cloud, []

    def client(self, name, *, region_name, config):
        assert name == "secretsmanager" and region_name == "eu-central-1"
        assert config.retries == {"total_max_attempts": 1, "mode": "standard"}
        self.configurations.append(config)
        return self.cloud.client()


def cloud_for(config, dsn):
    cloud = SyntheticAws()
    for ref, value in ((config.database_dsn_ref, dsn), (config.commitment_key_ref, KEY)):
        name = ref.arn.split(":secret:")[1][:-7]
        cloud.resources[name] = dict(Name=name, ARN=ref.arn,
                                    versions={ref.version_id: value}, current=ref.version_id)
    return cloud


@pytest_asyncio.fixture
async def system():
    dsn = os.environ.get("KDCUBE_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("KDCUBE_TEST_POSTGRES_DSN is not set")
    import asyncpg
    admin = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    schema, role = "w585_service_" + uuid.uuid4().hex, "w585_login_" + uuid.uuid4().hex
    config = configuration(schema=schema, service_role=role)
    await create_runtime_metadata_schema(admin, schema=schema)
    async with admin.acquire() as connection:
        await connection.execute(f'CREATE ROLE "{role}" LOGIN NOINHERIT')
        await connection.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"')
        await connection.execute(f'GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"')
    url = urlsplit(dsn)
    service_dsn = f"postgresql://{role}:{DSN_CANARY}@{url.hostname}:{url.port}{url.path}"
    cloud = cloud_for(config, service_dsn)
    session = Session(cloud)
    pools, arguments = [], []

    async def pool_factory(**kwargs):
        assert kwargs["ssl"] is True and kwargs["dsn"] == service_dsn
        arguments.append({k: v for k, v in kwargs.items() if k not in {"dsn", "setup"}})
        # Explicit disposable trust/loopback input, not a deployed TLS claim.
        result = await asyncpg.create_pool(**{**kwargs, "ssl": False})
        pools.append(result)
        return result

    service = RuntimeAwsService(config, session_factory=lambda: session, pool_factory=pool_factory)
    try:
        yield service, config, admin, cloud, session, pools, arguments
    finally:
        await service.close()
        for pool in pools:
            if not pool.is_closing():
                await pool.close()
        async with admin.acquire() as connection:
            await connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
            await connection.execute(f'DROP OWNED BY "{role}"')
            await connection.execute(f'DROP ROLE "{role}"')
        await admin.close()


@pytest.mark.parametrize("changes", [
    {"schema": "public"}, {"schema": "pg_catalog"}, {"schema": "a; DROP"},
    {"service_role": "postgres;"}, {"namespaces": ()}, {"namespaces": [NS]},
    {"namespaces": (NS, NS)}, {"namespaces": ("Custody",)}, {"account_id": "123"},
    {"region": "bad/region"}, {"partition": None}, {"cloud_prefix": "/test"},
    {"max_pool_size": True}, {"max_pool_size": 17}, {"max_unresolved": 0},
    {"database_dsn_ref": {"arn": "caller-json"}},
    {"database_dsn_ref": RuntimeBootstrapSecretRef(PREFIX + "database", "a" * 32)},
    {"commitment_key_ref": RuntimeBootstrapSecretRef(PREFIX + "commitment-Cd5678", "CURRENT")},
    {"commitment_key_ref": RuntimeBootstrapSecretRef(PREFIX.replace("123456789012", "999999999999") + "key-Cd5678", "b" * 32)},
    {"commitment_key_ref": RuntimeBootstrapSecretRef(PREFIX + "database-Ab1234", "b" * 32)},
    {"commitment_key_ref": RuntimeBootstrapSecretRef(PREFIX.replace("bootstrap/", "test/custody/runtime/") + "key-Cd5678", "b" * 32)},
    {"commitment_key_ref": RuntimeBootstrapSecretRef(PREFIX.replace("bootstrap/", "test/custody/custody/") + "key-Cd5678", "b" * 32)},
])
def test_invalid_trusted_config_is_finite_without_resource_io(changes):
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_storage_unavailable$"):
        configuration(**changes)


@pytest.mark.parametrize("dsn", [
    None, "", "postgresql://runtime_custody_service@host:5432/database",
    "postgresql://runtime_custody_service:synthetic@host/database",
    "postgresql://another_role:synthetic@host:5432/database",
    "postgresql://runtime_custody_service:synthetic@host:5432/database?sslmode=disable",
    "postgresql://runtime_custody_service:synthetic@host:5432/database?options=-c%20role=postgres",
    "postgresql://runtime_custody_service:synthetic@host:5432/", "service=ambient",
    "postgresql://runtime_custody_service:synthetic@host:5432/database\n",
])
def test_dsn_requires_explicit_principal_host_port_database_and_no_ambient_options(dsn):
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_storage_unavailable$"):
        _database_dsn(dsn, service_role="runtime_custody_service")


def test_construction_and_route_installation_have_no_cloud_or_pool_io():
    def forbidden():
        raise AssertionError("construction must not perform I/O")
    service = RuntimeAwsService(configuration(), session_factory=forbidden, pool_factory=forbidden)
    service.install_routes(FastAPI(), policy=RuntimeScopePolicy(None))
    with pytest.raises(RuntimeCloudError, match="storage_unavailable"):
        service.store(NS)
    with pytest.raises(RuntimeCloudError, match="scope_forbidden"):
        service.store("ungranted")


def test_bootstrap_version_reference_preserves_explicit_uuid_identity():
    version = "12345678-1234-1234-1234-123456789abc"
    config = configuration(commitment_key_ref=RuntimeBootstrapSecretRef(PREFIX + "commitment-Cd5678", version))
    assert config.commitment_key_ref.version_id == version


@pytest.mark.parametrize("value", [None, False, 1, "true"])
@pytest.mark.asyncio
async def test_access_check_requires_actual_boolean_true_and_suppresses_driver_text(value):
    class Connection:
        async def fetchval(self, *args):
            return value
        async def execute(self, *args):
            raise AssertionError("must refuse before column checks")
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_unavailable$"):
        await check_runtime_metadata_access(Connection(), schema="runtime_test", service_role="runtime_service")


@pytest.mark.asyncio
async def test_access_check_driver_exception_is_finite():
    class Connection:
        async def fetchval(self, *args):
            raise RuntimeError(DSN_CANARY)
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_unavailable$") as caught:
        await check_runtime_metadata_access(Connection(), schema="runtime_test", service_role="runtime_service")
    assert DSN_CANARY not in str(caught.value)


@pytest.mark.asyncio
async def test_owned_pool_restart_recovers_same_key_original_pin_and_deadline_without_mint(system):
    service, config, admin, cloud, session, pools, arguments = system
    await asyncio.gather(service.start(), service.start(), service.start())
    assert len(pools) == 1 and len(cloud.calls) == 2
    assert arguments[0] == dict(min_size=1, max_size=4, ssl=True, timeout=10, command_timeout=5)
    for (_, args), ref in zip(cloud.calls, (config.database_dsn_ref, config.commitment_key_ref)):
        assert args == dict(SecretId=ref.arn, VersionId=ref.version_id)
    store = service.store(NS)
    ref, deadline = uuid.uuid4().hex, int(time.time()) + 120
    assert await store.create(secret_ref=ref, value="synthetic-bearer", expires_at=deadline)
    original = await store._metadata.read_active(secret_ref=ref)
    await service.close()
    assert pools[0].is_closing() and service._commitment_key is None
    with pytest.raises(RuntimeCloudError, match="storage_unavailable"):
        service.store(NS)
    # Changing arbitrary CURRENT cannot silently rotate the persistent key.
    resource = cloud.find(config.commitment_key_ref.arn)
    resource["versions"]["c" * 32] = b"synthetic-different-key" * 2
    resource["current"] = "c" * 32
    await service.start()
    fresh = service.store(NS)
    assert fresh._metadata._pool is not store._metadata._pool
    assert not await fresh.create(secret_ref=ref, value="synthetic-bearer", expires_at=deadline + 999)
    assert await fresh.get(secret_ref=ref) == "synthetic-bearer"
    assert await fresh._metadata.read_active(secret_ref=ref) == original
    assert len([c for c in cloud.calls if c[0] == "create"]) == 1
    async with admin.acquire() as connection:
        row = await connection.fetchval(f'SELECT row_to_json(r)::text FROM "{config.schema}".runtime_secret_records r')
    assert DSN_CANARY not in row and "synthetic-bearer" not in row and KEY.hex() not in row


@pytest.mark.parametrize("fault", ["delete", "ddl", "superuser", "member", "missing_column", "disabled_trigger", "unlogged"])
@pytest.mark.asyncio
async def test_actual_login_role_or_schema_failure_refuses_startup_and_closes_owned_pool(system, fault):
    service, config, admin, cloud, session, pools, arguments = system
    role, schema = config.service_role, config.schema
    async with admin.acquire() as connection:
        parent_role = await connection.fetchval("SELECT quote_ident(current_user)")
        sql = {
            "delete": f'GRANT DELETE ON ALL TABLES IN SCHEMA "{schema}" TO "{role}"',
            "ddl": f'GRANT CREATE ON SCHEMA "{schema}" TO "{role}"',
            "superuser": f'ALTER ROLE "{role}" SUPERUSER',
            "member": f'GRANT {parent_role} TO "{role}"',
            "missing_column": f'ALTER TABLE "{schema}".runtime_secret_cleanup DROP COLUMN retry_count',
            "disabled_trigger": f'ALTER TABLE "{schema}".runtime_secret_records DISABLE TRIGGER runtime_terminal_guard',
            "unlogged": f'ALTER TABLE "{schema}".runtime_secret_cleanup SET UNLOGGED',
        }[fault]
        await connection.execute(sql)
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_storage_unavailable$") as caught:
        await service.start()
    assert DSN_CANARY not in str(caught.value) and len(pools) == 1 and pools[0].is_closing()
    assert service._pool is None and service._commitment_key is None


@pytest.mark.asyncio
async def test_privilege_drift_is_rechecked_on_each_actual_pool_acquisition(system):
    service, config, admin, cloud, session, pools, arguments = system
    await service.start()
    store = service.store(NS)
    async with admin.acquire() as connection:
        await connection.execute(f'GRANT DELETE ON ALL TABLES IN SCHEMA "{config.schema}" TO "{config.service_role}"')
    with pytest.raises(RuntimeMetadataError, match="^runtime_secret_metadata_unavailable$"):
        await store._metadata.read_original(secret_ref="a" * 32)
    assert len(cloud.calls) == 2, "access refusal must precede custody cloud I/O"


@pytest.mark.asyncio
async def test_trusted_collector_status_is_namespace_bound_value_free_and_not_qualification(system):
    service, config, admin, cloud, session, pools, arguments = system
    await service.start()
    assert await service.admission_status(NS) == dict(
        unknown=0, legacy_unknown=0, unstarted=0, unresolved=0, capacity=1000, saturated=False)
    ref = uuid.uuid4().hex
    await service.store(NS)._metadata.reserve(secret_ref=ref, request_digest="a" * 64,
                                             expires_at=int(time.time()) + 60)
    status = await service.admission_status(NS)
    assert status["unstarted"] == status["unresolved"] == 1
    assert ref not in json.dumps(status) and DSN_CANARY not in json.dumps(status)
    with pytest.raises(RuntimeCloudError, match="scope_forbidden"):
        await service.admission_status("ungranted")
    with pytest.raises(RuntimeCloudError, match="storage_unavailable"):
        await service.store(NS).qualify()
    assert len(cloud.calls) == 2, "collector reads metadata only, not a cloud availability proof"


@pytest.mark.asyncio
async def test_service_principal_with_outside_table_access_cannot_be_substituted_for_dedicated_role(system):
    service, config, admin, cloud, session, pools, arguments = system
    outside = "w585_outside_" + uuid.uuid4().hex
    try:
        async with admin.acquire() as connection:
            await connection.execute(f'CREATE SCHEMA "{outside}"')
            await connection.execute(f'CREATE TABLE "{outside}".unrelated_authority (id integer)')
            await connection.execute(f'GRANT USAGE ON SCHEMA "{outside}" TO "{config.service_role}"')
            await connection.execute(f'GRANT SELECT ON "{outside}".unrelated_authority TO "{config.service_role}"')
        with pytest.raises(RuntimeCloudError, match="^runtime_secret_storage_unavailable$"):
            await service.start()
        assert len(pools) == 1 and pools[0].is_closing()
    finally:
        async with admin.acquire() as connection:
            await connection.execute(f'DROP SCHEMA "{outside}" CASCADE')


@pytest.mark.parametrize("changed", ["wrong_arn", "wrong_version", "missing", "mixed", "bad_key", "bad_dsn"])
@pytest.mark.asyncio
async def test_bootstrap_response_pin_type_and_value_failures_never_open_pool(system, changed):
    service, config, admin, cloud, session, pools, arguments = system
    def change(_operation, response):
        key_response = response["ARN"] == config.commitment_key_ref.arn
        if changed == "wrong_arn": response["ARN"] += "replacement"
        elif changed == "wrong_version": response["VersionId"] = "d" * 32
        elif changed == "missing": response.pop("VersionId")
        elif changed == "mixed": response["SecretBinary"] = KEY
        elif changed == "bad_key" and key_response: response["SecretBinary"] = b"short"
        elif changed == "bad_dsn" and not key_response: response["SecretString"] = DSN_CANARY
        return response
    cloud.response_change = change
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_storage_unavailable$") as caught:
        await service.start()
    assert DSN_CANARY not in str(caught.value) and not pools


@pytest.mark.asyncio
async def test_common_asgi_lifespan_owns_pool_and_closed_aws_routes_have_zero_record_io(system):
    service, config, admin, cloud, session, pools, arguments = system
    reader, writer = "synthetic-reader", "synthetic-writer"
    headers = {"X-KDCUBE-SECRET-TOKEN": reader, "X-KDCUBE-ADMIN-TOKEN": writer}
    policy = RuntimeScopePolicy(json.dumps(dict(schema=POLICY_SCHEMA,
        read={hashlib.sha256(reader.encode()).hexdigest(): [NS]},
        write={hashlib.sha256(writer.encode()).hexdigest(): [NS]})))
    app = FastAPI(lifespan=service.lifespan)
    service.install_routes(app, policy=policy)
    base, ref = f"/runtime-secrets/{NS}", "e" * 32
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
            assert (await client.get(base + "/qualification")).status_code == 403
            for method, suffix, payload in (
                ("GET", "/qualification", None),
                ("POST", "/create", dict(secret_ref=ref, value="synthetic-bearer", expires_at=int(time.time()) + 60)),
                ("GET", "/secret/" + ref, None), ("DELETE", "/secret/" + ref, None),
                ("POST", "/purge", dict(now=int(time.time()), limit=1)),
            ):
                response = await client.request(method, base + suffix, headers=headers, json=payload)
                assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
                assert response.json() == dict(detail="runtime_secret_storage_unavailable")
    assert pools[0].is_closing() and service._pool is None
    assert len(cloud.calls) == 2, "only pinned startup inputs, no record operations"
    async with admin.acquire() as connection:
        assert await connection.fetchval(f'SELECT count(*) FROM "{config.schema}".runtime_secret_records') == 0


@pytest.mark.parametrize("failure", ["exception", "cancelled"])
@pytest.mark.asyncio
async def test_failed_or_cancelled_startup_never_publishes_a_pool_or_leaks_driver_text(failure):
    config = configuration()
    cloud = cloud_for(config, f"postgresql://{config.service_role}:{DSN_CANARY}@host:5432/database")
    async def pool_factory(**kwargs):
        if failure == "cancelled":
            raise asyncio.CancelledError
        raise RuntimeError(DSN_CANARY)
    service = RuntimeAwsService(config, session_factory=lambda: Session(cloud), pool_factory=pool_factory)
    expected = asyncio.CancelledError if failure == "cancelled" else RuntimeCloudError
    with pytest.raises(expected) as caught:
        await service.start()
    assert DSN_CANARY not in str(caught.value) and service._pool is None
    assert service._commitment_key is None


@pytest.mark.asyncio
async def test_close_failure_terminates_pool_and_clears_service_capabilities():
    class Pool:
        terminated = False
        async def close(self):
            raise RuntimeError(DSN_CANARY)
        def terminate(self):
            self.terminated = True
    service, pool = RuntimeAwsService(configuration()), Pool()
    service._pool, service._commitment_key, service._client_factory = pool, KEY, object()
    with pytest.raises(RuntimeCloudError, match="^runtime_secret_storage_unavailable$"):
        await service.close()
    assert pool.terminated and service._pool is None and service._commitment_key is None
