"""Actual deployment imports and generated policy; not installed mount proof."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kdcube_ai_app.infra.secrets import runtime_contract, runtime_file, runtime_vault
from kdcube_ai_app.infra.secrets.host_vault import audit, broker, identity, keys, protocol, service, storage

SERVERS = Path(__file__).resolve().parents[6] / "deployment/docker/all_in_one_kdcube/secrets"
NS = "custody"
REF = "a" * 32
CANARY = "synthetic-bootstrap-secret"
READ = {"X-KDCUBE-SECRET-TOKEN": "synthetic-runtime-reader"}
WRITE = {"X-KDCUBE-ADMIN-TOKEN": "synthetic-runtime-writer"}
BASE = f"/runtime-secrets/{NS}"


def _policy():
    return json.dumps({
        "schema": runtime_contract.POLICY_SCHEMA,
        "read": {hashlib.sha256(b"synthetic-runtime-reader").hexdigest(): [NS]},
        "write": {hashlib.sha256(b"synthetic-runtime-writer").hexdigest(): [NS]},
    })


def _load(backend, monkeypatch, tmp_path, *, configured=True):
    for name in ("KDCUBE_SECRETS_RUNTIME_ROOT", "KDCUBE_SECRETS_RUNTIME_NAMESPACES",
                 "KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "runtime"
    monkeypatch.setenv("SECRETS_STORE_PATH", str(tmp_path / "ordinary.json"))
    monkeypatch.setenv("SECRETS_READ_TOKENS", "synthetic-legacy-reader")
    monkeypatch.setenv("SECRETS_ADMIN_TOKEN", "synthetic-legacy-admin")
    if configured:
        monkeypatch.setenv("KDCUBE_SECRETS_RUNTIME_ROOT", str(root))
        monkeypatch.setenv("KDCUBE_SECRETS_RUNTIME_NAMESPACES", json.dumps([NS]))
        monkeypatch.setenv("KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY", _policy())
    path = SERVERS / "secrets_server.py"
    if backend == "host-vault":
        identity = tmp_path / "identity"
        identity.mkdir(exist_ok=True)
        for name in ("host-vault-client.crt", "host-vault-client.key", "host-vault-ca.crt"):
            (identity / name).write_text("synthetic identity fixture", encoding="utf-8")
        monkeypatch.setenv("KDCUBE_HOST_VAULT_ADDR", "127.0.0.1:9443")
        monkeypatch.setenv("KDCUBE_HOST_VAULT_IDENTITY_DIR", str(identity))
        monkeypatch.setenv("KDCUBE_SECRETS_TENANT", "synthetic-tenant")
        monkeypatch.setenv("KDCUBE_SECRETS_PROJECT", "synthetic-project")
        path = SERVERS / "host_vault/broker_server.py"
    spec = importlib.util.spec_from_file_location(f"runtime_bootstrap_{uuid.uuid4().hex}", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module, TestClient(module.app), root


@pytest.mark.parametrize("backend", ["file", "host-vault"])
def test_actual_entrypoints_install_default_closed_common_routes(backend, monkeypatch, tmp_path):
    _, client, root = _load(backend, monkeypatch, tmp_path, configured=False)
    response = client.get(f"{BASE}/qualification", headers={**READ, **WRITE})
    assert response.status_code == 403
    assert response.json() == {"detail": "runtime_secret_scope_forbidden"}
    assert response.headers["cache-control"] == "no-store"
    assert not root.exists()


def test_file_bootstrap_create_restart_conflict_expiry_and_purge(monkeypatch, tmp_path):
    # Only the physical filesystem classifier is stubbed. Real file store,
    # startup composition and HTTP routes are used; no mounted-runtime claim.
    monkeypatch.setattr(runtime_contract, "persistent_filesystem", lambda root: True)
    clock = [int(time.time())]
    monkeypatch.setattr(runtime_file, "time", SimpleNamespace(time=lambda: clock[0]))
    _, client, root = _load("file", monkeypatch, tmp_path)
    assert runtime_contract.qualified(
        client.get(f"{BASE}/qualification", headers={**READ, **WRITE}).json(), namespace=NS)
    payload = {"secret_ref": REF, "value": CANARY, "expires_at": clock[0] + 10}
    assert client.post(f"{BASE}/create", headers=WRITE, json=payload).status_code == 200
    _, restarted, _ = _load("file", monkeypatch, tmp_path)
    assert restarted.get(f"{BASE}/secret/{REF}", headers=READ).json() == {"value": CANARY}
    assert restarted.post(f"{BASE}/create", headers=WRITE,
                          json={**payload, "value": "synthetic-rival"}).status_code == 409
    clock[0] += 10
    assert restarted.get(f"{BASE}/secret/{REF}", headers=READ).status_code == 404
    assert restarted.post(f"{BASE}/purge", headers=WRITE,
                          json={"now": clock[0], "limit": 1}).json() == {"status": "ok", "removed": 1}
    assert CANARY not in (root / f"{NS}.json").read_text()
    assert restarted.post(f"{BASE}/create", headers=WRITE,
                          json={**payload, "expires_at": clock[0] + 30}).status_code == 409


@pytest.mark.parametrize("operation", ["qualification", "create", "read", "delete", "purge"])
def test_file_bootstrap_refuses_transient_storage_before_record_io(operation, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_contract, "persistent_filesystem", lambda root: False)
    calls = []
    for name in ("_load", "_save"):
        monkeypatch.setattr(runtime_file.RuntimeFileStore, name,
                            lambda *args: calls.append("record-io"))
    _, client, _ = _load("file", monkeypatch, tmp_path)
    if operation == "qualification":
        response = client.get(f"{BASE}/qualification", headers={**READ, **WRITE})
    elif operation == "create":
        response = client.post(f"{BASE}/create", headers=WRITE,
                              json={"secret_ref": REF, "value": CANARY, "expires_at": int(time.time()) + 10})
    elif operation == "read":
        response = client.get(f"{BASE}/secret/{REF}", headers=READ)
    elif operation == "delete":
        response = client.delete(f"{BASE}/secret/{REF}", headers=WRITE)
    else:
        response = client.post(f"{BASE}/purge", headers=WRITE, json={"now": int(time.time()), "limit": 1})
    assert response.status_code == 503
    assert response.json() == {"detail": "runtime_secret_storage_unavailable"}
    assert calls == [] and CANARY not in response.text


@pytest.mark.parametrize("headers", [
    {"X-KDCUBE-SECRET-TOKEN": "synthetic-legacy-reader", "X-KDCUBE-ADMIN-TOKEN": "synthetic-legacy-admin"},
    {}, {**READ, "X-KDCUBE-ADMIN-TOKEN": "synthetic-runtime-reader"},
])
@pytest.mark.parametrize("backend", ["file", "host-vault"])
def test_legacy_credentials_never_acquire_runtime_grants(backend, headers, monkeypatch, tmp_path):
    _, client, root = _load(backend, monkeypatch, tmp_path)
    response = client.get(f"{BASE}/qualification", headers=headers)
    assert response.status_code == 403 and not root.exists()


def test_secrets_image_and_compose_project_common_bootstrap_without_tmpfs_custody():
    dockerfile = (SERVERS.parent / "Dockerfile_Secrets").read_text()
    for name in ("runtime_bootstrap.py", "runtime_file.py", "runtime_vault.py"):
        assert f"infra/secrets/{name}" in dockerfile
    import yaml
    service = yaml.safe_load((SERVERS.parent / "docker-compose.yaml").read_text())["services"]["kdcube-secrets"]
    for name in ("ROOT", "NAMESPACES", "SCOPE_POLICY"):
        assert any(value.startswith(f"KDCUBE_SECRETS_RUNTIME_{name}=") for value in service["environment"])
    mount = next(value for value in service["volumes"] if "HOST_KDCUBE_RUNTIME_SECRETS_ROOT" in value.get("source", ""))
    assert mount["type"] == "bind" and mount.get("read_only", False) is False
    assert mount["bind"]["create_host_path"] is False
    assert mount["target"] not in service["tmpfs"]


def test_actual_native_bootstrap_uses_enrolled_broker_and_requalifies_each_operation(monkeypatch, tmp_path):
    # Native protocol, enrollment and encrypted disk store are real. Transport
    # is in-process and disk classification synthetic: not deployed mTLS proof.
    monkeypatch.setattr(storage, "persistent_filesystem", lambda root: True)
    monkeypatch.setattr(keys, "persistent_filesystem", lambda root: True)
    clock = [int(time.time())]
    monkeypatch.setattr(runtime_vault, "time", SimpleNamespace(time=lambda: clock[0]))
    ca = identity.HostIssuingCA.generate()
    registry = identity.TrustRegistry(tmp_path / "trust.json", ca=ca)
    namespace = protocol.SecretNamespace("synthetic-tenant", "synthetic-project", "kdcube-runtime")
    ticket = registry.mint_ticket(deployment_id="synthetic-deployment", namespaces=[namespace.path])
    deployment = identity.DeploymentKey.generate()
    cert, _ = registry.enroll(ticket_id=ticket.ticket_id, csr_pem=deployment.csr())
    provider = keys.FileRootKeyProvider(tmp_path / "root-keys")
    provider.rotate()
    disk = storage.FileDurableSecretStore(tmp_path / "native-store", provider)
    vault = service.HostVaultService(store=disk, registry=registry, audit=audit.MemoryAuditSink())
    requests = []

    class Transport:
        def call(self, request):
            requests.append(request)
            return vault.handle(request.to_wire(), peer_cert_pem=cert)

    module, client, file_root = _load("host-vault", monkeypatch, tmp_path)
    monkeypatch.setattr(module, "BROKER", broker.SecretsBroker(
        transport=Transport(), tenant=namespace.tenant, project=namespace.project))
    assert runtime_contract.qualified(
        client.get(f"{BASE}/qualification", headers={**READ, **WRITE}).json(), namespace=NS)
    payload = {"secret_ref": REF, "value": CANARY, "expires_at": clock[0] + 10}
    assert client.post(f"{BASE}/create", headers=WRITE, json=payload).status_code == 200
    # Reconstruct the actual encrypted store/root-key provider, not only a client.
    vault._store = storage.FileDurableSecretStore(disk._root, keys.FileRootKeyProvider(provider._dir))
    assert client.get(f"{BASE}/secret/{REF}", headers=READ).json() == {"value": CANARY}
    assert client.post(f"{BASE}/create", headers=WRITE,
                       json={**payload, "value": "synthetic-rival"}).status_code == 409
    clock[0] += 10
    assert client.get(f"{BASE}/secret/{REF}", headers=READ).status_code == 404
    assert client.post(f"{BASE}/purge", headers=WRITE,
                       json={"now": clock[0], "limit": 1}).json() == {"status": "ok", "removed": 1}
    assert client.post(f"{BASE}/create", headers=WRITE,
                       json={**payload, "expires_at": clock[0] + 30}).status_code == 409
    assert sum(request.operation is protocol.Operation.QUALIFY for request in requests) == 8
    assert all(request.operation is not protocol.Operation.HEALTH for request in requests)
    assert not file_root.exists(), "native bootstrap must not silently select a file fallback"
    assert CANARY not in "\n".join(path.read_text() for path in disk._root.rglob("*.json"))
    monkeypatch.setattr(storage, "persistent_filesystem", lambda root: False)
    before = len(requests)
    refused = client.get(f"{BASE}/secret/{REF}", headers=READ)
    assert refused.status_code == 503
    assert [request.operation for request in requests[before:]] == [protocol.Operation.QUALIFY]


@pytest.mark.parametrize("raw", ["custody", '"custody"', '["custody","custody"]',
                                  '["Custody"]', '[true]', '["custody",null]'])
def test_malformed_generated_namespace_policy_cannot_open_storage(raw, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from kdcube_ai_app.infra.secrets.runtime_bootstrap import install_configured_runtime_routes
    app = FastAPI()
    root = tmp_path / "never-opened"
    install_configured_runtime_routes(app, environ={
        "KDCUBE_SECRETS_RUNTIME_ROOT": str(root),
        "KDCUBE_SECRETS_RUNTIME_NAMESPACES": raw,
        "KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY": _policy(),
    })
    response = TestClient(app).get(f"{BASE}/qualification", headers={**READ, **WRITE})
    assert response.status_code == 403 and not root.exists()


def test_configuration_snapshot_cannot_be_replaced_by_ambient_values_after_start(monkeypatch, tmp_path):
    _, client, root = _load("file", monkeypatch, tmp_path, configured=False)
    monkeypatch.setenv("KDCUBE_SECRETS_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("KDCUBE_SECRETS_RUNTIME_NAMESPACES", json.dumps([NS]))
    monkeypatch.setenv("KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY", _policy())
    assert client.get(f"{BASE}/qualification", headers={**READ, **WRITE}).status_code == 403
    assert not root.exists()
