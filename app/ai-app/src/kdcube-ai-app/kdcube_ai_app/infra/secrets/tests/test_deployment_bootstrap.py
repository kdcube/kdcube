import asyncio
import copy
import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

import kdcube_ai_app.infra.secrets.manager as providers
from kdcube_ai_app.infra.secrets import deployment_bootstrap as bootstrap
from kdcube_ai_app.infra.secrets.manager import (
    AwsSecretsManagerSecretsManager, InMemorySecretsManager, ISecretsManager,
    SecretsFileSecretsManager, SecretsManagerConfig, SecretsManagerError,
    SecretsManagerWriteError, SecretsServiceSecretsManager,
)


def descriptor(refresh="oauth_original.refresh_signing_secret", management=bootstrap.MANAGEMENT_SIGNER):
    return {"bundles": {"items": [{"id": bootstrap.HUB_ID, "config": {"connections": {
        "delegated_credentials": {
            "oauth": {"original_exchange": {"refresh_signing_secret_ref": refresh}},
            "admission": {"services": {"kdcube-management": {"secret_ref": management}}},
        },
    }}}]}}


class AwsError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class Aws:
    def __init__(self):
        self.data = {}
        self.error = None
        self.writes = []

    async def get_secret_value(self, *, SecretId):
        await asyncio.sleep(0)
        if self.error:
            raise AwsError(self.error)
        if SecretId not in self.data:
            raise AwsError("ResourceNotFoundException")
        return {"SecretString": self.data[SecretId]}

    async def put_secret_value(self, *, SecretId, SecretString):
        if SecretId not in self.data:
            raise AwsError("ResourceNotFoundException")
        self.data[SecretId] = SecretString
        self.writes.append(SecretId)

    async def create_secret(self, *, Name, SecretString):
        if Name in self.data:
            raise AwsError("ResourceExistsException")
        self.data[Name] = SecretString
        self.writes.append(Name)


class Response:
    def __init__(self, status, body):
        self.status_code, self.body = status, body

    def json(self):
        return self.body


class Http:
    def __init__(self):
        self.data, self.writes = {}, []
        self.error = None
        self.durable = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def get(self, url, **_):
        await asyncio.sleep(0)
        if url.endswith("/health"):
            return Response(200, {"status": "ok", "vault": {"ok": True, "code": "ok"}}) if self.durable else Response(200, {"status": "ok"})
        if self.error:
            return Response(self.error, {})
        key = unquote(url.rsplit("/", 1)[-1])
        return Response(200, {"value": self.data[key]}) if key in self.data else Response(404, {})

    async def post(self, url, *, json, **_):
        assert json["expected_generation"] == 0
        key = json["key"]
        if key in self.data:
            return Response(409, {})
        self.data[key] = json["value"]
        self.writes.append(key)
        return Response(200, {"status": "ok", "generation": 1})


@pytest.fixture(params=["memory", "file", "service", "aws"])
def backend(request, tmp_path, monkeypatch):
    kind = request.param
    io = None
    if kind == "memory":
        manager = InMemorySecretsManager()
    elif kind == "file":
        monkeypatch.setenv("KDCUBE_SECRETS_OWNER_UID", str(os.getuid()))
        manager = SecretsFileSecretsManager(SecretsManagerConfig(
            provider="secrets-file", component="test", runtime_secrets_root=str(tmp_path / "secrets"),
            global_secrets_yaml=(tmp_path / "secrets.yaml").as_uri(),
        ))
    elif kind == "service":
        io = Http()
        monkeypatch.setattr(providers, "_get_httpx", lambda: SimpleNamespace(AsyncClient=lambda **_: io))
        manager = SecretsServiceSecretsManager(SecretsManagerConfig(
            provider="secrets-service", component="test", url="http://fixture", token="reader", admin_token="writer",
        ))
    else:
        io = Aws()
        manager = AwsSecretsManagerSecretsManager(SecretsManagerConfig(
            provider="aws-sm", component="test", aws_sm_prefix="fixture", tenant="t", project="p",
        ))

        @asynccontextmanager
        async def client():
            yield io

        monkeypatch.setattr(manager, "_client_cm", client)
        lock = asyncio.Lock()

        async def acquire(_):
            await lock.acquire()
            return lock, "held"

        async def release(*_):
            lock.release()

        monkeypatch.setattr(manager, "_acquire_distributed_lock", acquire)
        monkeypatch.setattr(manager, "_release_distributed_lock", release)
    return kind, manager, io


@pytest.mark.asyncio
async def test_fresh_refresh_and_one_missing_preserve_provider_values(backend, monkeypatch, capsys):
    _, manager, _ = backend
    bundles = descriptor()
    created = await bootstrap.ensure_hub_signers(manager, bundles=bundles)
    assert len(created) == 3
    original = {key: await manager.get_secret_strict(key) for key in created}
    assert len(set(original.values())) == 3
    assert all(len(value) == 64 and bytes.fromhex(value) for value in original.values())
    with monkeypatch.context() as patch:
        patch.setattr(bootstrap.secrets, "token_hex", lambda *_: pytest.fail("all-present generated a value"))
        assert await bootstrap.ensure_hub_signers(manager, bundles=bundles) == ()
    missing = created[1]
    if backend[0] in {"memory", "file"}:
        await manager.delete_secret(missing)
    if backend[0] == "service":
        backend[2].data.pop(missing)
    elif backend[0] == "aws":
        payload = json.loads(backend[2].data[manager._secret_id(missing)])
        providers._delete_nested_value(payload, manager._tail_for_key(missing))
        backend[2].data[manager._secret_id(missing)] = json.dumps(payload)
    assert await bootstrap.ensure_hub_signers(manager, bundles=bundles) == (missing,)
    assert all([await manager.get_secret_strict(key) == value for key, value in original.items() if key != missing])
    output = capsys.readouterr()
    assert not output.out and not output.err


@pytest.mark.asyncio
async def test_concurrent_creation_preserves_one_winner_and_verifies_it(backend):
    _, manager, _ = backend
    other = copy.copy(manager)
    if backend[0] == "aws":
        other._write_lock = asyncio.Lock()
    first, second = await asyncio.gather(
        bootstrap.ensure_hub_signers(manager, bundles=descriptor()),
        bootstrap.ensure_hub_signers(other, bundles=descriptor()),
    )
    assert len(first) + len(second) == 3
    assert not set(first) & set(second)
    assert all([await manager.get_secret_strict(key) is not None for key in first + second])


@pytest.mark.asyncio
async def test_configured_references_and_existing_seed_are_respected(backend):
    _, manager, _ = backend
    bundles = descriptor("custom.refresh", "custom.management")
    seed = {"bundles": {"items": [{"id": bootstrap.HUB_ID, "secrets": {
        "custom": {"refresh": "known-seed-" + "x" * 40},
    }}]}}
    keys = await bootstrap.ensure_hub_signers(manager, bundles=bundles, seed_bundles=seed)
    assert f"bundles.{bootstrap.HUB_ID}.secrets.custom.refresh" in keys
    assert await manager.get_secret_strict(keys[0]) == "known-seed-" + "x" * 40
    assert await manager.get_secret_strict(f"bundles.{bootstrap.HUB_ID}.secrets.{bootstrap.REFRESH_SIGNER}") is None


@pytest.mark.asyncio
async def test_retry_after_partial_creation_preserves_first_key(backend, monkeypatch):
    _, manager, _ = backend
    original_create = manager.create_bundle_secret
    calls = []

    async def fail_second(key, value):
        calls.append(key)
        if len(calls) == 2:
            raise SecretsManagerWriteError("provider unavailable")
        return await original_create(key, value)

    with monkeypatch.context() as patch:
        patch.setattr(manager, "create_bundle_secret", fail_second)
        with pytest.raises(SecretsManagerWriteError, match="unavailable"):
            await bootstrap.ensure_hub_signers(manager, bundles=descriptor())
    first = await manager.get_secret_strict(calls[0])
    assert len(await bootstrap.ensure_hub_signers(manager, bundles=descriptor())) == 2
    assert await manager.get_secret_strict(calls[0]) == first


@pytest.mark.asyncio
async def test_unsupported_provider_with_present_keys_is_a_noop():
    class ReadOnly(ISecretsManager):
        provider_type = "read-only"

        async def get_secret(self, key):
            return "existing-" + "x" * 40

    assert await bootstrap.ensure_hub_signers(ReadOnly(), bundles=descriptor()) == ()


@pytest.mark.asyncio
async def test_unsupported_provider_with_missing_key_refuses():
    class ReadOnly(ISecretsManager):
        provider_type = "read-only"

        async def get_secret(self, key):
            return None

    with pytest.raises(SecretsManagerWriteError, match="create-only"):
        await bootstrap.ensure_hub_signers(ReadOnly(), bundles=descriptor())


@pytest.mark.asyncio
@pytest.mark.parametrize("backend,error", [
    ("service", 403), ("service", 503), ("aws", "AccessDeniedException"), ("aws", "InternalServiceError"),
], indirect=["backend"])
async def test_remote_read_failure_is_not_absence(backend, error):
    kind, manager, io = backend
    io.error = error
    with pytest.raises(SecretsManagerError):
        await bootstrap.ensure_hub_signers(manager, bundles=descriptor())
    assert io.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["service"], indirect=True)
async def test_service_missing_key_refuses_ephemeral_backend(backend):
    _, manager, io = backend
    io.durable = False
    with pytest.raises(SecretsManagerWriteError, match="durable host-vault"):
        await bootstrap.ensure_hub_signers(manager, bundles=descriptor())
    assert io.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["file", "service", "aws"], indirect=True)
async def test_unparseable_provider_record_refuses_without_writing(backend):
    kind, manager, io = backend
    if kind == "file":
        store = manager._user_store()
        store.set_app(bundle_id=bootstrap.HUB_ID, key=bootstrap.REFRESH_SIGNER, value="existing")
        path = store.app_path(bundle_id=bootstrap.HUB_ID, key=bootstrap.REFRESH_SIGNER)
        path.write_text("invalid-json")
        before = path.read_bytes()
    elif kind == "aws":
        io.data[f"fixture/bundles/{bootstrap.HUB_ID}/secrets"] = "invalid-json"
    elif kind == "service":
        async def malformed(*_, **__):
            return Response(200, {"unexpected": "payload"})
        io.get = malformed
    with pytest.raises(SecretsManagerError):
        await bootstrap.ensure_hub_signers(manager, bundles=descriptor())
    if kind == "file":
        assert path.read_bytes() == before
    else:
        assert io.writes == []


@pytest.mark.parametrize("refresh,management", [(None, bootstrap.MANAGEMENT_SIGNER), ("../escape", "mgmt"), ("same", "same")])
def test_invalid_or_colliding_references_refuse(refresh, management):
    with pytest.raises(SecretsManagerError):
        bootstrap.signing_paths(descriptor(refresh, management)["bundles"]["items"][0]["config"])


def test_management_consumer_reference_must_match_admission():
    with pytest.raises(SecretsManagerError, match="must match"):
        bootstrap.signing_paths(descriptor()["bundles"]["items"][0]["config"], management_secret_ref="different")


def test_module_error_does_not_expose_provider_details(monkeypatch, capsys):
    async def fail():
        raise RuntimeError("sensitive-provider-value")
    monkeypatch.setattr(bootstrap, "_run", fail)
    assert bootstrap.main() == 1
    assert "sensitive-provider-value" not in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["file"], indirect=True)
async def test_unsafe_file_permissions_are_not_a_missing_secret(backend):
    _, manager, _ = backend
    store = manager._user_store()
    store.set_app(bundle_id=bootstrap.HUB_ID, key=bootstrap.REFRESH_SIGNER, value="existing")
    path = store.app_path(bundle_id=bootstrap.HUB_ID, key=bootstrap.REFRESH_SIGNER)
    path.chmod(0o644)
    with pytest.raises(SecretsManagerError):
        await bootstrap.ensure_hub_signers(manager, bundles=descriptor())
    assert json.loads(path.read_text())["value"] == "existing"


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["aws"], indirect=True)
async def test_aws_missing_key_requires_the_existing_deployment_lock(backend, monkeypatch):
    _, manager, io = backend

    async def no_lock(_):
        return None, None

    monkeypatch.setattr(manager, "_acquire_distributed_lock", no_lock)
    with pytest.raises(SecretsManagerWriteError, match="deployment write lock"):
        await bootstrap.ensure_hub_signers(manager, bundles=descriptor())
    assert io.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["aws"], indirect=True)
async def test_aws_add_absent_preserves_unrelated_keys_and_refuses_parent_collision(backend):
    _, manager, io = backend
    doc = f"fixture/bundles/{bootstrap.HUB_ID}/secrets"
    io.data[doc] = json.dumps({"unrelated": "existing", "oauth_original": "parent-value"})
    before = io.data[doc]
    with pytest.raises(SecretsManagerWriteError, match="conflicts"):
        await bootstrap.ensure_hub_signers(manager, bundles=descriptor())
    assert io.data[doc] == before
    assert io.writes == []
    io.data[doc] = json.dumps({"unrelated": "existing"})
    assert len(await bootstrap.ensure_hub_signers(manager, bundles=descriptor())) == 3
    assert json.loads(io.data[doc])["unrelated"] == "existing"
