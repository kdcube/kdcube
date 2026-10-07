# SPDX-License-Identifier: MIT
"""Real file custody over the ASGI boundary; only volume qualification is stubbed."""
from __future__ import annotations

import hashlib
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from kdcube_ai_app.infra.secrets import manager, runtime_contract, runtime_file, runtime_http

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
    assert CANARY not in (root / "custody.json").read_text()


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
    assert CANARY not in (root / "custody.json").read_text()


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
