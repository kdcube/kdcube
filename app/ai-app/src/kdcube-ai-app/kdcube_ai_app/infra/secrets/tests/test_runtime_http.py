# SPDX-License-Identifier: MIT
"""Real file custody over the ASGI boundary; only volume qualification is stubbed."""
from __future__ import annotations

import hashlib
import asyncio
import json
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from kdcube_ai_app.infra.secrets import manager, runtime_contract, runtime_file, runtime_http
from kdcube_ai_app.infra.secrets.tests.test_runtime_pg_metadata import metadata

REF = "a" * 32
CANARY = "synthetic-http-secret-canary"
READ = {"X-KDCUBE-SECRET-TOKEN": "synthetic-reader"}
WRITE = {"X-KDCUBE-ADMIN-TOKEN": "synthetic-writer"}
BASE = "/runtime-secrets/custody"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    clock = [int(time.time())]
    calls = []
    monkeypatch.setattr(runtime_file, "time", SimpleNamespace(time=lambda: clock[0]))
    # This suite proves route authorization and primitive behavior, not an
    # installed volume's lifecycle. Physical qualification has its own suite.
    monkeypatch.setattr(runtime_contract, "persistent_filesystem", lambda root: True)
    policy = runtime_contract.RuntimeScopePolicy(json.dumps({
        "schema": runtime_contract.POLICY_SCHEMA,
        "read": {hashlib.sha256(b"synthetic-reader").hexdigest(): ["custody"]},
        "write": {hashlib.sha256(b"synthetic-writer").hexdigest(): ["custody"]},
    }))

    def factory(namespace):
        calls.append(namespace)
        return runtime_file.RuntimeFileStore(root=root, namespace=namespace,
                                             authorized_namespaces=("custody",))

    app = FastAPI()
    runtime_http.install_runtime_routes(app, policy=policy, store_factory=factory)
    return TestClient(app), app, clock, root, calls


def create(client, expiry, *, value=CANARY, ref=REF, headers=WRITE, namespace="custody"):
    return client.post(f"/runtime-secrets/{namespace}/create", headers=headers,
                       json={"secret_ref": ref, "value": value, "expires_at": expiry})


def test_authorized_create_conflict_restart_expiry_and_tombstone(rig):
    client, app, clock, root, _ = rig
    assert create(client, clock[0] + 10).json() == {"status": "ok", "created": True}
    conflict = create(client, clock[0] + 60, value="synthetic-rival")
    assert conflict.status_code == 409
    restarted = TestClient(app)
    response = restarted.get(f"{BASE}/secret/{REF}", headers=READ)
    assert response.json() == {"value": CANARY}
    assert response.headers["cache-control"] == "no-store"
    clock[0] += 10
    assert restarted.get(f"{BASE}/secret/{REF}", headers=READ).status_code == 404
    assert restarted.post(f"{BASE}/purge", headers=WRITE,
                          json={"now": clock[0], "limit": 1}).json() == {"status": "ok", "removed": 1}
    assert create(restarted, clock[0] + 60).status_code == 409
    assert CANARY not in (root / "platform" / "custody" / f"{REF}.json").read_text()


@pytest.mark.parametrize("headers", [{}, READ, {"X-KDCUBE-ADMIN-TOKEN": "unknown"},
    {"X-KDCUBE-ADMIN-TOKEN": "synthetic-reader"},
    [("X-KDCUBE-ADMIN-TOKEN", "synthetic-writer"), ("X-KDCUBE-ADMIN-TOKEN", "synthetic-writer")]])
def test_create_denies_before_storage_or_body_decoding(rig, headers):
    client, _, clock, root, calls = rig
    response = client.post(f"{BASE}/create", headers=headers, content=CANARY)
    assert response.status_code == 403
    assert CANARY not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert calls == [] and not root.exists()


@pytest.mark.parametrize("method, suffix, headers", [
    ("get", f"secret/{REF}", WRITE), ("delete", f"secret/{REF}", READ),
    ("post", "purge", READ), ("get", "qualification", READ), ("get", "qualification", WRITE),
])
def test_read_and_write_grants_are_independent(rig, method, suffix, headers):
    client, _, _, root, calls = rig
    response = getattr(client, method)(f"{BASE}/{suffix}", headers=headers)
    assert response.status_code == 403
    assert calls == [] and not root.exists()


@pytest.mark.parametrize("namespace", ["other", "custody-extra", "Custody", "custody.*"])
def test_namespace_spelling_cannot_expand_a_grant(rig, namespace):
    client, _, clock, root, calls = rig
    assert create(client, clock[0] + 10, namespace=namespace).status_code == 403
    assert calls == [] and not root.exists()


def test_unconfigured_policy_refuses_even_valid_legacy_credentials():
    calls = []
    app = FastAPI()
    runtime_http.install_runtime_routes(app, policy=runtime_contract.RuntimeScopePolicy(None),
        store_factory=lambda namespace: calls.append(namespace))
    response = TestClient(app).get(f"{BASE}/qualification", headers={**READ, **WRITE})
    assert response.status_code == 403 and calls == []


def test_qualification_requires_store_proof_as_well_as_both_grants(rig, monkeypatch):
    client, _, _, _, _ = rig
    response = client.get(f"{BASE}/qualification", headers={**READ, **WRITE})
    assert runtime_contract.qualified(response.json(), namespace="custody")
    monkeypatch.setattr(runtime_contract, "persistent_filesystem", lambda root: False)
    refused = client.get(f"{BASE}/qualification", headers={**READ, **WRITE})
    assert refused.status_code == 503
    assert refused.json() == {"detail": "runtime_secret_storage_unavailable"}


@pytest.mark.parametrize("answer", [False, True, 0, 1, {}, CANARY])
@pytest.mark.parametrize("operation", ["qualify", "delete"])
def test_void_store_operations_require_the_exact_success_contract(rig, monkeypatch, operation, answer):
    client, _, _, _, _ = rig
    monkeypatch.setattr(runtime_file.RuntimeFileStore, operation, lambda self, **kwargs: answer)
    if operation == "qualify":
        response = client.get(f"{BASE}/qualification", headers={**READ, **WRITE})
    else:
        response = client.delete(f"{BASE}/secret/{REF}", headers=WRITE)
    assert response.status_code == 503
    assert response.json() == {"detail": "runtime_secret_storage_unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert CANARY not in response.text


@pytest.mark.parametrize("patch", [{"value": True}, {"value": [CANARY]}, {"value": "\ud800"},
    {"expires_at": True}, {"expires_at": "2000"}, {"secret_ref": "invalid"},
    {"extra": CANARY}])
def test_invalid_input_never_echoes_secret_material(rig, patch):
    client, _, clock, root, calls = rig
    payload = {"secret_ref": REF, "value": CANARY, "expires_at": clock[0] + 10, **patch}
    response = client.post(f"{BASE}/create", headers=WRITE, content=json.dumps(payload))
    assert response.status_code == 400
    assert CANARY not in response.text and "\\ud800" not in response.text
    assert calls == [] and not root.exists()


@pytest.mark.parametrize("raw", ["not-json", "[1]", "null",
    '{"secret_ref":"' + REF + '","value":"' + CANARY + '","value":"duplicate","expires_at":2000}',
    '{"secret_ref":"' + REF + '","value":"' + CANARY + '","expires_at":NaN}'])
def test_malformed_body_is_a_finite_value_free_failure(rig, raw):
    client, _, _, root, calls = rig
    response = client.post(f"{BASE}/create", headers=WRITE, content=raw)
    assert response.status_code == 400 and CANARY not in response.text
    assert calls == [] and not root.exists()


def test_request_size_is_bounded_before_storage(rig):
    client, _, _, root, calls = rig
    response = client.post(f"{BASE}/create", headers=WRITE, content="x" * (512 * 1024 + 1))
    assert response.status_code == 413
    assert calls == [] and not root.exists()


@pytest.mark.parametrize("limit", [True, 0, 1001, "1", 1.0])
def test_purge_limit_is_strict_before_storage(rig, limit):
    client, _, clock, root, calls = rig
    response = client.post(f"{BASE}/purge", headers=WRITE, json={"now": clock[0], "limit": limit})
    assert response.status_code == 400
    assert calls == [] and not root.exists()


def test_expired_create_and_future_purge_cannot_return_success(rig):
    client, _, clock, _, _ = rig
    assert create(client, clock[0]).status_code == 410
    assert client.post(f"{BASE}/purge", headers=WRITE,
                       json={"now": clock[0] + 1, "limit": 1}).status_code == 400


def test_delete_is_idempotent_and_cannot_recreate_a_used_ref(rig):
    client, _, clock, _, _ = rig
    assert create(client, clock[0] + 10).status_code == 200
    for _ in range(2):
        assert client.delete(f"{BASE}/secret/{REF}", headers=WRITE).json() == {"status": "ok"}
    assert create(client, clock[0] + 60).status_code == 409


@pytest.mark.parametrize("key", ["platform.runtime", "platform.runtime.custody." + REF,
    "platform.runtime.custody.__keys", "platform.runtime..ref", "platform.runtime.custody.ref.extra"])
def test_legacy_runtime_key_guard_covers_malformed_and_inventory_routes(key):
    with pytest.raises(HTTPException) as refused:
        runtime_http.reject_legacy_runtime_key(key)
    assert refused.value.status_code == 403
    runtime_http.reject_legacy_runtime_key("platform.ordinary-secret")


def test_backend_exception_text_never_crosses_http_boundary(rig, monkeypatch):
    client, _, _, _, _ = rig

    def unavailable(self, **kwargs):
        raise RuntimeError(CANARY)

    monkeypatch.setattr(runtime_file.RuntimeFileStore, "get", unavailable)
    response = client.get(f"{BASE}/secret/{REF}", headers=READ)
    assert response.status_code == 503 and CANARY not in response.text


def service_adapter(app, monkeypatch):
    real_client = httpx.AsyncClient

    def client(**kwargs):
        return real_client(transport=httpx.ASGITransport(app=app), **kwargs)

    monkeypatch.setattr(manager, "_get_httpx", lambda: SimpleNamespace(AsyncClient=client))
    return manager.SecretsServiceSecretsManager(manager.SecretsManagerConfig(
        provider="secrets-service", component="proc", url="http://synthetic-service",
        token="synthetic-reader", admin_token="synthetic-writer"))


@pytest.mark.asyncio
async def test_sdk_service_manager_uses_scoped_expiry_api_end_to_end(rig, monkeypatch):
    _, app, clock, root, _ = rig
    adapter = service_adapter(app, monkeypatch)
    assert await adapter.qualify_runtime_custody(namespace="custody")
    assert await adapter.create_ephemeral_secret(namespace="custody", secret_ref=REF,
                                                value=CANARY, expires_at=clock[0] + 10)
    assert not await adapter.create_ephemeral_secret(namespace="custody", secret_ref=REF,
                                                     value="synthetic-rival", expires_at=clock[0] + 60)
    assert await adapter.get_ephemeral_secret(namespace="custody", secret_ref=REF) == CANARY
    clock[0] += 10
    assert await adapter.get_ephemeral_secret(namespace="custody", secret_ref=REF) is None
    assert await adapter.purge_expired_ephemeral_secrets(namespace="custody", now=clock[0], limit=1) == 1
    assert not await adapter.create_ephemeral_secret(namespace="custody", secret_ref=REF,
                                                     value="synthetic-rival", expires_at=clock[0] + 60)
    assert CANARY not in (root / "platform" / "custody" / f"{REF}.json").read_text()


@pytest.mark.asyncio
async def test_sdk_never_falls_back_to_generic_overwrite_or_delete(rig, monkeypatch):
    _, app, clock, _, _ = rig
    adapter = service_adapter(app, monkeypatch)
    assert await adapter.create_ephemeral_secret(namespace="custody", secret_ref=REF,
                                                value=CANARY, expires_at=clock[0] + 10)
    with pytest.raises(manager.SecretsManagerWriteError, match="^runtime_secret_conflict$"):
        await adapter.set_ephemeral_secret(namespace="custody", secret_ref=REF,
                                           value="synthetic-rival", expires_at=clock[0] + 60)
    await adapter.delete_ephemeral_secret(namespace="custody", secret_ref=REF)
    assert await adapter.get_ephemeral_secret(namespace="custody", secret_ref=REF) is None


@pytest.mark.asyncio
async def test_sdk_wrong_scope_is_failure_not_absence(rig, monkeypatch):
    _, app, clock, root, _ = rig
    adapter = service_adapter(app, monkeypatch)
    assert not await adapter.qualify_runtime_custody(namespace="other")
    with pytest.raises(manager.SecretsManagerWriteError, match="^runtime_secret_storage_unavailable$"):
        await adapter.get_ephemeral_secret(namespace="other", secret_ref=REF)
    assert not root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [False, True, 0, 1, {}, CANARY])
async def test_sdk_never_qualifies_a_malformed_store_acknowledgement(rig, monkeypatch, answer):
    _, app, _, _, _ = rig
    monkeypatch.setattr(runtime_file.RuntimeFileStore, "qualify", lambda self: answer)
    adapter = service_adapter(app, monkeypatch)
    with pytest.raises(manager.SecretsManagerError, match="^Runtime secret qualification is unavailable$"):
        await adapter.qualify_runtime_custody(namespace="custody")


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [False, True, 0, 1, {}, CANARY])
async def test_sdk_never_accepts_a_malformed_delete_acknowledgement(rig, monkeypatch, answer):
    _, app, _, _, _ = rig
    monkeypatch.setattr(runtime_file.RuntimeFileStore, "delete", lambda self, **kwargs: answer)
    adapter = service_adapter(app, monkeypatch)
    with pytest.raises(manager.SecretsManagerWriteError, match="^runtime_secret_storage_unavailable$"):
        await adapter.delete_ephemeral_secret(namespace="custody", secret_ref=REF)


class AsyncBoundaryStore:
    """Explicit HTTP-shape fixture, not provider durability qualification."""

    def __init__(self):
        self.records = {}
        self.calls = []
        self.failure = None
        self.answers = {}

    async def answer(self, operation, default):
        await asyncio.sleep(0)
        self.calls.append((operation, threading.get_ident(), asyncio.get_running_loop()))
        if self.failure == operation:
            raise RuntimeError(CANARY)
        return self.answers.get(operation, default)

    async def qualify(self):
        return await self.answer("qualify", None)

    async def create(self, *, secret_ref, value, expires_at):
        fresh = secret_ref not in self.records
        result = await self.answer("create", fresh)
        if result is True:
            self.records[secret_ref] = (value, expires_at)
        return result

    async def get(self, *, secret_ref):
        row = self.records.get(secret_ref)
        value = row[0] if row is not None and row[1] > int(time.time()) else None
        return await self.answer("get", value)

    async def delete(self, *, secret_ref):
        result = await self.answer("delete", None)
        if result is None:
            self.records[secret_ref] = ("", 1)
        return result

    async def purge_expired(self, *, now, limit):
        rows = [ref for ref, (_, expires) in self.records.items() if 1 < expires <= now][:limit]
        result = await self.answer("purge", len(rows))
        for ref in rows:
            self.records[ref] = ("", 1)
        return result


def async_boundary(store, *, asynchronous_factory=True, fail_factory=False):
    calls = []
    policy = runtime_contract.RuntimeScopePolicy(json.dumps({
        "schema": runtime_contract.POLICY_SCHEMA,
        "read": {hashlib.sha256(b"synthetic-reader").hexdigest(): ["custody"]},
        "write": {hashlib.sha256(b"synthetic-writer").hexdigest(): ["custody"]},
    }))

    def factory(namespace):
        calls.append(namespace)
        if fail_factory:
            raise RuntimeError(CANARY)
        return store

    async def async_factory(namespace):
        await asyncio.sleep(0)
        return factory(namespace)

    app = FastAPI()
    runtime_http.install_runtime_routes(app, policy=policy,
        store_factory=async_factory if asynchronous_factory else factory)
    return app, calls


async def boundary_request(client, operation, *, headers=None):
    if operation == "qualify":
        return await client.get(f"{BASE}/qualification", headers=headers or {**READ, **WRITE})
    if operation == "create":
        return await client.post(f"{BASE}/create", headers=headers or WRITE,
            json={"secret_ref": REF, "value": CANARY, "expires_at": int(time.time()) + 600})
    if operation == "get":
        return await client.get(f"{BASE}/secret/{REF}", headers=headers or READ)
    if operation == "delete":
        return await client.delete(f"{BASE}/secret/{REF}", headers=headers or WRITE)
    return await client.post(f"{BASE}/purge", headers=headers or WRITE,
                            json={"now": int(time.time()), "limit": 1})


@pytest.mark.parametrize("asynchronous_factory", [False, True], ids=["sync-factory", "async-factory"])
@pytest.mark.asyncio
async def test_async_store_protocol_is_awaited_on_service_loop_without_provider_branch(asynchronous_factory):
    store = AsyncBoundaryStore()
    app, factories = async_boundary(store, asynchronous_factory=asynchronous_factory)
    loop, thread = asyncio.get_running_loop(), threading.get_ident()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic-service") as client:
        assert (await boundary_request(client, "qualify")).status_code == 200
        assert (await boundary_request(client, "create")).json() == {"status": "ok", "created": True}
        assert (await boundary_request(client, "create")).status_code == 409
        assert (await boundary_request(client, "get")).json() == {"value": CANARY}
        assert (await boundary_request(client, "purge")).json() == {"status": "ok", "removed": 0}
        assert (await boundary_request(client, "delete")).json() == {"status": "ok"}
        assert (await boundary_request(client, "get")).status_code == 404
    assert factories == ["custody"] * 7
    assert all(call_thread == thread and call_loop is loop for _, call_thread, call_loop in store.calls)
    assert [operation for operation, _, _ in store.calls].count("qualify") == 7


@pytest.mark.parametrize("operation", ["qualify", "create", "get", "delete", "purge"])
@pytest.mark.asyncio
async def test_async_scope_denial_precedes_factory_and_storage(operation):
    store = AsyncBoundaryStore()
    app, factories = async_boundary(store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic-service") as client:
        response = await boundary_request(client, operation, headers={"X-KDCUBE-SECRET-TOKEN": "unknown"})
    assert response.status_code == 403 and response.headers["cache-control"] == "no-store"
    assert CANARY not in response.text and factories == [] and store.calls == []


@pytest.mark.parametrize("operation", ["qualify", "create", "get", "delete", "purge", "factory"])
@pytest.mark.asyncio
async def test_async_backend_and_factory_failures_remain_finite_value_free(operation):
    store = AsyncBoundaryStore()
    store.failure = operation
    app, _ = async_boundary(store, fail_factory=operation == "factory")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic-service") as client:
        response = await boundary_request(client, "get" if operation == "factory" else operation)
    assert response.status_code == 503
    assert response.json() == {"detail": "runtime_secret_storage_unavailable"}
    assert response.headers["cache-control"] == "no-store" and CANARY not in response.text


@pytest.mark.parametrize("operation", ["qualify", "create", "get", "delete", "purge"])
@pytest.mark.parametrize("answer", [True, False, 0, {}, CANARY],
                         ids=["true", "false", "integer", "dict", "string"])
@pytest.mark.asyncio
async def test_async_unqualified_store_never_runs_any_record_operation(operation, answer):
    store = AsyncBoundaryStore()
    store.answers["qualify"] = answer
    app, _ = async_boundary(store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic-service") as client:
        response = await boundary_request(client, operation)
    assert response.status_code == 503
    assert response.json() == {"detail": "runtime_secret_storage_unavailable"}
    assert CANARY not in response.text
    assert [operation for operation, _, _ in store.calls] == ["qualify"]
    assert store.records == {}


@pytest.mark.parametrize("operation,answer", [
    ("create", 1), ("create", None), ("create", CANARY),
    ("get", True), ("get", {"value": CANARY}),
    ("delete", False), ("delete", CANARY),
    ("purge", True), ("purge", -1), ("purge", 2),
], ids=["create-int", "create-none", "create-string", "get-bool", "get-dict",
        "delete-bool", "delete-string", "purge-bool", "purge-negative", "purge-over-limit"])
@pytest.mark.asyncio
async def test_async_operation_results_keep_exact_types_after_awaiting(operation, answer):
    store = AsyncBoundaryStore()
    store.answers[operation] = answer
    app, _ = async_boundary(store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic-service") as client:
        response = await boundary_request(client, operation)
    assert response.status_code == 503
    assert response.json() == {"detail": "runtime_secret_storage_unavailable"}
    assert CANARY not in response.text


@pytest.mark.parametrize("bad_store", [None, 1, {}])
@pytest.mark.asyncio
async def test_async_factory_invalid_capability_is_finite_not_an_attribute_error(bad_store):
    app, _ = async_boundary(bad_store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic-service") as client:
        response = await boundary_request(client, "get")
    assert response.status_code == 503 and response.json() == {"detail": "runtime_secret_storage_unavailable"}


@pytest.mark.parametrize("operation", ["qualify", "create", "get", "delete", "purge"])
@pytest.mark.asyncio
async def test_actual_unqualified_async_aws_component_cannot_bypass_common_http_gate(metadata, operation):
    from kdcube_ai_app.infra.secrets.runtime_aws import RuntimeAwsStore
    from kdcube_ai_app.infra.secrets.runtime_pg_metadata import PostgresRuntimeCustodyMetadata
    from kdcube_ai_app.infra.secrets.tests.test_runtime_aws import SyntheticAws, COMMITMENT_KEY

    cloud = SyntheticAws()
    coordinates = PostgresRuntimeCustodyMetadata(
        metadata._pool, schema=metadata._records.split('"')[1], namespace="custody",
        authorized_namespaces=("custody",), cloud_prefix="test/runtime",
    )
    store = RuntimeAwsStore(metadata=coordinates, client_factory=cloud.client,
                           account_id="123456789012", region="eu-central-1", commitment_key=COMMITMENT_KEY)
    app, _ = async_boundary(store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic-service") as client:
        response = await boundary_request(client, operation)
    assert response.status_code == 503 and response.json() == {"detail": "runtime_secret_storage_unavailable"}
    assert cloud.calls == []
    async with metadata._pool.acquire() as connection:
        assert await connection.fetchval(f"SELECT count(*) FROM {metadata._records}") == 0


@pytest.mark.asyncio
async def test_synchronous_boundary_operations_retain_threadpool_isolation():
    thread = threading.get_ident()
    calls = []

    class SyncStore:
        def qualify(self):
            calls.append(("qualify", threading.get_ident()))

        def get(self, **kwargs):
            calls.append(("get", threading.get_ident()))
            return None

        def create(self, **kwargs):
            return True

        def delete(self, **kwargs):
            return None

        def purge_expired(self, **kwargs):
            return 0

    app, _ = async_boundary(SyncStore(), asynchronous_factory=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic-service") as client:
        assert (await boundary_request(client, "get")).status_code == 404
    assert [operation for operation, _ in calls] == ["qualify", "get"]
    assert all(call_thread != thread for _, call_thread in calls)


@pytest.mark.asyncio
async def test_async_factory_capability_property_failure_never_exposes_backend_exception():
    class BrokenCapability:
        @property
        def qualify(self):
            raise RuntimeError(CANARY)

    app, _ = async_boundary(BrokenCapability())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic-service") as client:
        response = await boundary_request(client, "get")
    assert response.status_code == 503 and response.json() == {"detail": "runtime_secret_storage_unavailable"}
    assert CANARY not in response.text


@pytest.mark.asyncio
async def test_cancelled_async_operation_does_not_become_a_success_acknowledgement():
    store = AsyncBoundaryStore()

    async def cancelled_delete(**kwargs):
        raise asyncio.CancelledError

    store.delete = cancelled_delete
    app, _ = async_boundary(store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://synthetic-service") as client:
        with pytest.raises(asyncio.CancelledError):
            await boundary_request(client, "delete")
    assert [operation for operation, _, _ in store.calls] == ["qualify"]
    assert store.records == {}
