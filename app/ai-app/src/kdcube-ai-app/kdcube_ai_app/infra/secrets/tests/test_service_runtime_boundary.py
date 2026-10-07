"""Deployment HTTP entrypoints keep runtime custody on its scoped protocol."""
from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kdcube_ai_app.infra.secrets.host_vault.broker import BrokerListResult, BrokerReadResult, BrokerResult
from kdcube_ai_app.infra.secrets.host_vault.protocol import ErrorCode

_SERVERS = Path(__file__).resolve().parents[6] / "deployment/docker/all_in_one_kdcube/secrets"
_REF = "a" * 32
_KEYS = (
    "platform.runtime",
    f"platform.runtime.custody.{_REF}",
    "platform.runtime.custody.__keys",
    "platform.runtime.custody",
    "platform.runtime..reference",
    "platform.runtime.Custody.reference",
    "platform.runtime.custody.reference.extra",
)
_OPERATIONS = (
    ("ephemeral", "get"), ("ephemeral", "set"), ("ephemeral", "delete"),
    ("host-vault", "get"), ("host-vault", "set"), ("host-vault", "delete"),
    ("host-vault", "verify"),
)


def _server(backend, monkeypatch, tmp_path):
    for key in ("SECRETS_ADMIN_TOKEN", "SECRETS_READ_TOKENS"):
        monkeypatch.delenv(key, raising=False)
    path = _SERVERS / "secrets_server.py"
    if backend == "host-vault":
        identity = tmp_path / "identity"
        identity.mkdir()
        for name in ("host-vault-client.crt", "host-vault-client.key", "host-vault-ca.crt"):
            (identity / name).write_text("synthetic fixture", encoding="utf-8")
        monkeypatch.setenv("KDCUBE_HOST_VAULT_ADDR", "127.0.0.1:9443")
        monkeypatch.setenv("KDCUBE_HOST_VAULT_IDENTITY_DIR", str(identity))
        monkeypatch.setenv("KDCUBE_SECRETS_TENANT", "tenant-fixture")
        monkeypatch.setenv("KDCUBE_SECRETS_PROJECT", "project-fixture")
        path = _SERVERS / "host_vault/broker_server.py"
    spec = importlib.util.spec_from_file_location(f"runtime_boundary_{uuid.uuid4().hex}", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    calls = []
    if backend == "ephemeral":
        def load():
            calls.append("load")
            return {key: "synthetic fixture value" for key in _KEYS}
        monkeypatch.setattr(module, "_load_store", load)
        monkeypatch.setattr(module, "_save_store", lambda data: calls.append("save"))
    else:
        class Broker:
            def read(self, **kwargs):
                calls.append("read")
                return BrokerReadResult(ok=True, code=ErrorCode.OK,
                                        value="[]" if kwargs["key"].endswith(".__keys")
                                        else "synthetic fixture value", generation=1)
            def list_names(self, **kwargs):
                calls.append("list")
                return BrokerListResult(ok=True, code=ErrorCode.OK, names=())
            def set(self, **kwargs):
                calls.append("set")
                return BrokerResult(ok=True, code=ErrorCode.OK, generation=1)
            def delete(self, **kwargs):
                calls.append("delete")
                return BrokerResult(ok=True, code=ErrorCode.OK, generation=1)
        monkeypatch.setattr(module, "BROKER", Broker())
    return module, TestClient(module.app), calls


@pytest.mark.parametrize("backend,operation", _OPERATIONS)
@pytest.mark.parametrize("key", _KEYS)
@pytest.mark.parametrize("headers", [{}, {"X-KDCUBE-SECRET-TOKEN": "unknown-fixture",
                                           "X-KDCUBE-ADMIN-TOKEN": "unknown-fixture"}])
def test_legacy_endpoints_refuse_runtime_keys_before_storage_or_backend(
    backend, operation, key, headers, monkeypatch, tmp_path,
):
    _, client, calls = _server(backend, monkeypatch, tmp_path)
    if operation == "get":
        response = client.get(f"/secret/{key}", headers=headers)
    elif operation == "delete":
        response = client.delete(f"/secret/{key}", headers=headers)
    elif operation == "set":
        response = client.post("/set", json={"key": key, "value": "synthetic replacement"}, headers=headers)
    else:
        response = client.post("/verify", json={"key": key, "sha256": "b" * 64}, headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "runtime_secret_scoped_api_required"}
    assert response.headers["Cache-Control"] == "no-store"
    assert calls == []


def test_secrets_image_includes_the_shared_runtime_boundary():
    dockerfile = (_SERVERS.parent / "Dockerfile_Secrets").read_text(encoding="utf-8")
    for name in ("runtime_contract.py", "runtime_http.py"):
        assert f"infra/secrets/{name}" in dockerfile


@pytest.mark.parametrize("backend", ["ephemeral", "host-vault"])
def test_parent_inventory_neither_exposes_nor_reads_runtime_custody(
    backend, monkeypatch, tmp_path,
):
    module, client, _ = _server(backend, monkeypatch, tmp_path)
    runtime_key = f"platform.runtime.custody.{_REF}"
    ordinary_key = "platform.services.fixture.token"
    if backend == "ephemeral":
        monkeypatch.setattr(module, "_load_store", lambda: {
            runtime_key: "synthetic custody value", ordinary_key: "ordinary fixture",
        })
    else:
        class Broker:
            def list_names(self, **kwargs):
                return BrokerListResult(ok=True, code=ErrorCode.OK,
                                        names=(runtime_key, ordinary_key))
            def read(self, *, key, **kwargs):
                assert key != runtime_key, "legacy inventory read runtime custody"
                return BrokerReadResult(ok=True, code=ErrorCode.OK, generation=1,
                    value=json.dumps([runtime_key, ordinary_key]) if key.endswith(".__keys")
                    else "ordinary fixture")
        monkeypatch.setattr(module, "BROKER", Broker())
    response = client.get("/secret/platform.__keys")
    assert response.status_code == 200
    assert json.loads(response.json()["value"]) == [ordinary_key]
