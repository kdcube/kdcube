from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path
from types import ModuleType

import pytest
from fastapi import HTTPException
from kdcube_ai_app.infra.secrets.host_vault.broker import (
    BrokerListResult,
    BrokerReadResult,
)
from kdcube_ai_app.infra.secrets.host_vault.protocol import ErrorCode

_DEPLOYMENT_SECRETS = (
    Path(__file__).resolve().parents[6]
    / "deployment"
    / "docker"
    / "all_in_one_kdcube"
    / "secrets"
)


def _load_script(path: Path, prefix: str) -> ModuleType:
    name = f"{prefix}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_ephemeral_service_inventory_is_derived_from_live_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRETS_STORE_PATH", str(tmp_path / "store.json"))
    module = _load_script(_DEPLOYMENT_SECRETS / "secrets_server.py", "secrets_server")
    prefix = "bundles.fixture@1-0.secrets."
    first = f"{prefix}provider.token"
    second = f"{prefix}provider.refresh"
    metadata = f"{prefix}__keys"

    module.set_secret(module.SecretItem(key=first, value="one"), None)
    module.set_secret(module.SecretItem(key=second, value="two"), None)
    module.set_secret(
        module.SecretItem(key=metadata, value=json.dumps([first, "stale"])),
        None,
    )

    persisted = json.loads((tmp_path / "store.json").read_text(encoding="utf-8"))
    assert metadata not in persisted
    if os.name == "posix":
        assert (tmp_path / "store.json").stat().st_mode & 0o777 == 0o600
    assert json.loads(module.get_secret(metadata, None)["value"]) == sorted(
        [first, second]
    )
    module.delete_secret(second, None)
    assert json.loads(module.get_secret(metadata, None)["value"]) == [first]


def test_ephemeral_service_fails_closed_on_corrupt_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "store.json"
    store.write_text('{"duplicate":"first","duplicate":"second"}', encoding="utf-8")
    monkeypatch.setenv("SECRETS_STORE_PATH", str(store))
    module = _load_script(_DEPLOYMENT_SECRETS / "secrets_server.py", "secrets_server")

    with pytest.raises(HTTPException) as read_failure:
        module.get_secret("platform.services.fixture.token", None)
    assert read_failure.value.status_code == 503

    with pytest.raises(HTTPException) as write_failure:
        module.set_secret(
            module.SecretItem(
                key="platform.services.fixture.token",
                value="must-not-land",
            ),
            None,
        )
    assert write_failure.value.status_code == 503
    assert store.read_text(encoding="utf-8") == (
        '{"duplicate":"first","duplicate":"second"}'
    )


def test_ephemeral_service_create_only_preserves_existing_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "store.json"
    monkeypatch.setenv("SECRETS_STORE_PATH", str(store))
    module = _load_script(_DEPLOYMENT_SECRETS / "secrets_server.py", "secrets_server")
    key = "platform.runtime.resident-secrets." + ("a" * 32)

    assert module.set_secret(
        module.SecretItem(key=key, value="original", expected_generation=0),
        None,
    ) == {"status": "ok", "generation": 1}
    with pytest.raises(HTTPException) as collision:
        module.set_secret(
            module.SecretItem(
                key=key,
                value="replacement",
                expected_generation=0,
            ),
            None,
        )

    assert collision.value.status_code == 409
    assert json.loads(store.read_text(encoding="utf-8"))[key] == "original"
    assert module.set_secret(
        module.SecretItem(
            key="platform.services.fixture.token",
            value="ordinary",
        ),
        None,
    ) == {"status": "ok"}


class _FixtureBroker:
    def __init__(self, *, prefix: str) -> None:
        self.prefix = prefix
        self.live = {f"{prefix}new", f"{prefix}legacy"}
        self.mutations: list[tuple[str, str]] = []

    def list_names(self, *, application: str, metadata_key: str) -> BrokerListResult:
        del application, metadata_key
        return BrokerListResult(
            ok=True,
            code=ErrorCode.OK,
            names=(f"{self.prefix}new",),
        )

    def read(self, *, application: str, key: str) -> BrokerReadResult:
        del application
        if key == f"{self.prefix}__keys":
            return BrokerReadResult(
                ok=True,
                code=ErrorCode.OK,
                value=json.dumps(
                    [f"{self.prefix}legacy", f"{self.prefix}deleted"]
                ),
            )
        if key in self.live:
            return BrokerReadResult(
                ok=True,
                code=ErrorCode.OK,
                value="value",
                generation=1,
            )
        return BrokerReadResult(ok=False, code=ErrorCode.NOT_FOUND)

    def set(self, *, application: str, key: str, value: str):
        del application, value
        self.mutations.append(("set", key))
        raise AssertionError("derived inventory must not be stored")

    def delete(self, *, application: str, key: str):
        del application
        self.mutations.append(("delete", key))
        raise AssertionError("derived inventory must not be deleted")


def test_host_vault_broker_inventory_unions_verified_legacy_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = tmp_path / "identity"
    identity.mkdir()
    for filename in (
        "host-vault-client.crt",
        "host-vault-client.key",
        "host-vault-ca.crt",
    ):
        (identity / filename).write_text("fixture", encoding="utf-8")
    monkeypatch.setenv("KDCUBE_HOST_VAULT_ADDR", "127.0.0.1:9443")
    monkeypatch.setenv("KDCUBE_HOST_VAULT_IDENTITY_DIR", str(identity))
    monkeypatch.setenv("KDCUBE_SECRETS_TENANT", "tenant-a")
    monkeypatch.setenv("KDCUBE_SECRETS_PROJECT", "project-a")
    module = _load_script(
        _DEPLOYMENT_SECRETS / "host_vault" / "broker_server.py",
        "host_vault_broker_server",
    )
    prefix = "bundles.fixture@1-0.secrets."
    module.BROKER = _FixtureBroker(prefix=prefix)

    response = module.get_secret(f"{prefix}__keys", None)

    assert json.loads(response["value"]) == [
        f"{prefix}legacy",
        f"{prefix}new",
    ]


def test_host_vault_broker_inventory_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = tmp_path / "identity"
    identity.mkdir()
    for filename in (
        "host-vault-client.crt",
        "host-vault-client.key",
        "host-vault-ca.crt",
    ):
        (identity / filename).write_text("fixture", encoding="utf-8")
    monkeypatch.setenv("KDCUBE_HOST_VAULT_ADDR", "127.0.0.1:9443")
    monkeypatch.setenv("KDCUBE_HOST_VAULT_IDENTITY_DIR", str(identity))
    monkeypatch.setenv("KDCUBE_SECRETS_TENANT", "tenant-a")
    monkeypatch.setenv("KDCUBE_SECRETS_PROJECT", "project-a")
    module = _load_script(
        _DEPLOYMENT_SECRETS / "host_vault" / "broker_server.py",
        "host_vault_broker_server",
    )
    prefix = "bundles.fixture@1-0.secrets."
    broker = _FixtureBroker(prefix=prefix)
    module.BROKER = broker
    metadata_key = f"{prefix}__keys"

    assert module.set_secret(
        module.SecretItem(key=metadata_key, value='["must-not-land"]'),
        None,
    ) == {"status": "ok", "inventory": "derived"}
    assert module.delete_secret(metadata_key, None) == {
        "status": "ok",
        "deleted": False,
        "inventory": "derived",
    }
    assert broker.mutations == []


def test_host_vault_broker_does_not_turn_backend_denial_into_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = tmp_path / "identity"
    identity.mkdir()
    for filename in (
        "host-vault-client.crt",
        "host-vault-client.key",
        "host-vault-ca.crt",
    ):
        (identity / filename).write_text("fixture", encoding="utf-8")
    monkeypatch.setenv("KDCUBE_HOST_VAULT_ADDR", "127.0.0.1:9443")
    monkeypatch.setenv("KDCUBE_HOST_VAULT_IDENTITY_DIR", str(identity))
    monkeypatch.setenv("KDCUBE_SECRETS_TENANT", "tenant-a")
    monkeypatch.setenv("KDCUBE_SECRETS_PROJECT", "project-a")
    module = _load_script(
        _DEPLOYMENT_SECRETS / "host_vault" / "broker_server.py",
        "host_vault_broker_server",
    )

    class _DeniedBroker:
        def read(self, *, application: str, key: str) -> BrokerReadResult:
            del application, key
            return BrokerReadResult(ok=False, code=ErrorCode.FORBIDDEN)

    module.BROKER = _DeniedBroker()

    with pytest.raises(HTTPException) as captured:
        module.get_secret("platform.services.fixture.token", None)

    assert captured.value.status_code == 403
    assert captured.value.detail == "forbidden"
