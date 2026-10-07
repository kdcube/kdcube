"""Pure descriptor projection; never generates credentials or activates stores."""
from __future__ import annotations

import hashlib
import json

import pytest

from kdcube_cli.host_vault import HostVaultConfigurationError, config_from_assembly, compose_environment
from kdcube_ai_app.infra.secrets.runtime_contract import POLICY_SCHEMA, RuntimeScopePolicy


def _assembly(tmp_path):
    return {"secrets": {"provider": "secrets-service", "runtime": {
        "root": str(tmp_path / "private-custody"), "namespaces": ["custody"],
        "scope_policy": {"schema": POLICY_SCHEMA,
            "read": {hashlib.sha256(b"synthetic-reader").hexdigest(): ["custody"]},
            "write": {hashlib.sha256(b"synthetic-writer").hexdigest(): ["custody"]}},
    }}}


def test_descriptor_projects_static_scope_and_root_not_ambient_authority(tmp_path, monkeypatch):
    assembly = _assembly(tmp_path)
    monkeypatch.setenv("KDCUBE_SECRETS_RUNTIME_ROOT", "/ambient-rival")
    monkeypatch.setenv("KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY", "ambient-rival")
    values = compose_environment(config_from_assembly(assembly))
    assert values["KDCUBE_SECRETS_RUNTIME_ROOT"] == str(tmp_path / "private-custody")
    assert values["HOST_KDCUBE_RUNTIME_SECRETS_ROOT"] == values["KDCUBE_SECRETS_RUNTIME_ROOT"]
    assert json.loads(values["KDCUBE_SECRETS_RUNTIME_NAMESPACES"]) == ["custody"]
    policy = RuntimeScopePolicy(values["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"])
    assert policy.authorized(namespace="custody", token="synthetic-reader", kind="read")
    assert not policy.authorized(namespace="custody", token="synthetic-reader", kind="write")
    assert "synthetic-reader" not in str(values) and "synthetic-writer" not in str(values)
    assert "ambient-rival" not in str(values)
    assert not (tmp_path / "private-custody").exists()


@pytest.mark.parametrize("field,value", [
    ("root", "/"), ("root", "relative"), ("root", "/private/../broad"),
    ("root", "/private/${RIVAL}"), ("root", "/private\nPUBLIC=value"), ("root", True),
    ("root", "/private/\ud800"), ("root", "/" + "x" * 4096),
    ("namespaces", "custody"), ("namespaces", ["custody", "custody"]),
    ("namespaces", ["Custody"]), ("namespaces", ["custody.*"]),
    ("namespaces", [False]), ("scope_policy", {}), ("scope_policy", "synthetic-secret-canary"),
])
def test_malformed_runtime_policy_refuses_without_echoing_values(tmp_path, field, value):
    assembly = _assembly(tmp_path)
    assembly["secrets"]["runtime"][field] = value
    with pytest.raises(HostVaultConfigurationError) as failure:
        config_from_assembly(assembly)
    assert "synthetic-secret-canary" not in str(failure.value)
    assert not (tmp_path / "private-custody").exists()


def test_foreign_scope_cannot_be_granted_by_descriptor(tmp_path):
    assembly = _assembly(tmp_path)
    assembly["secrets"]["runtime"]["scope_policy"]["read"] = {"a" * 64: ["foreign"]}
    with pytest.raises(HostVaultConfigurationError, match="only declared namespaces"):
        config_from_assembly(assembly)


def test_absent_scope_does_not_infer_grants_from_legacy_credentials(tmp_path):
    assembly = _assembly(tmp_path)
    assembly["secrets"]["runtime"].pop("scope_policy")
    assembly["secrets"]["token"] = "synthetic-reader"
    assembly["secrets"]["admin_token"] = "synthetic-writer"
    values = compose_environment(config_from_assembly(assembly))
    assert values["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"] == ""
    assert not RuntimeScopePolicy(values["KDCUBE_SECRETS_RUNTIME_SCOPE_POLICY"]).authorized(
        namespace="custody", token="synthetic-reader", kind="read")


def test_projected_descriptor_drives_real_common_http_storage(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kdcube_ai_app.infra.secrets import runtime_contract
    from kdcube_ai_app.infra.secrets.runtime_bootstrap import install_configured_runtime_routes
    import time

    monkeypatch.setattr(runtime_contract, "persistent_filesystem", lambda root: True)
    assembly = _assembly(tmp_path)
    values = compose_environment(config_from_assembly(assembly))
    app = FastAPI()
    install_configured_runtime_routes(app, environ=values)
    client = TestClient(app)
    read = {"X-KDCUBE-SECRET-TOKEN": "synthetic-reader"}
    write = {"X-KDCUBE-ADMIN-TOKEN": "synthetic-writer"}
    base = "/runtime-secrets/custody"
    assert runtime_contract.qualified(client.get(base + "/qualification", headers={**read, **write}).json(),
                                      namespace="custody")
    response = client.post(base + "/create", headers=write, json={
        "secret_ref": "a" * 32, "value": "synthetic-roundtrip-value", "expires_at": int(time.time()) + 30,
    })
    assert response.json() == {"status": "ok", "created": True}
    assert client.get(base + "/secret/" + "a" * 32, headers=read).json() == {"value": "synthetic-roundtrip-value"}
    assert client.get(base + "/secret/" + "a" * 32, headers=write).status_code == 403
    assert (tmp_path / "private-custody" / "custody.json").stat().st_mode & 0o777 == 0o600
