from __future__ import annotations

import asyncio
import hashlib
import json
import traceback
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.bundle.session_bound_issuer import _custody_call
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.infra.secrets import ephemeral as ephemeral_module
from kdcube_ai_app.infra.secrets import issuance as issuance_module
from kdcube_ai_app.infra.secrets import manager as manager_module
from kdcube_ai_app.infra.secrets.ephemeral import KDCubeEphemeralSecretStore, ephemeral_secret_store
from kdcube_ai_app.infra.secrets.issuance import KDCubeIssuanceSecretCustody, issuance_secret_custody
from kdcube_ai_app.infra.secrets.runtime_contract import qualification
from kdcube_ai_app.infra.secrets.manager import (
    AwsSecretsManagerSecretsManager, InMemorySecretsManager, SecretsManagerConfig, SecretsManagerError,
    SecretsServiceSecretsManager,
)
from kdcube_ai_app.infra.secrets.tests.test_manager import (
    _FakeAwsSecretsClient, _FakeAwsSession, _FakeHttpResponse,
    _FakeHttpxModule, _FakeSecretsHttpClient,
)

NAMESPACE = "connection-hub-issuance-custody"
REF = "a" * 32
CANARY = "synthetic-bearer-for-digest-comparison"
HOST_SETTINGS = SimpleNamespace(SECRETS_SERVICE_BACKEND="host-vault")


class _ProviderFixture(InMemorySecretsManager):
    """Test seam only: same manager protocol, NOT a production durability proof."""
    provider_type = "secrets-service"

    async def qualify_runtime_custody(self, *, namespace):
        return namespace == NAMESPACE


@pytest.fixture
def custody(monkeypatch):
    monkeypatch.setattr(issuance_module, "time", SimpleNamespace(time=lambda: 10))
    manager = _ProviderFixture()
    return issuance_secret_custody(namespace=NAMESPACE, manager=manager, settings=HOST_SETTINGS), manager


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.mark.asyncio
async def test_enveloped_custody_purge_preserves_unexpired_value_and_fresh_adapter_recovers(custody, monkeypatch):
    store, manager = custody
    assert await store.create(secret_ref=REF, value=CANARY, expires_at=30)
    monkeypatch.setattr(issuance_module, "time", SimpleNamespace(time=lambda: 20))
    assert await store.purge_expired(now=20, limit=1) == 0
    fresh = issuance_secret_custody(namespace=NAMESPACE, manager=manager, settings=HOST_SETTINGS)
    assert digest(await fresh.get(secret_ref=REF)) == digest(CANARY)
    raw = await manager.get_ephemeral_secret(namespace=NAMESPACE, secret_ref=REF)
    envelope = json.loads(raw)
    assert envelope["schema"] == "kdcube.issuance_custody.v1"
    assert envelope["expires_at"] == 30
    assert envelope["secret_ref"] == REF
    assert digest(envelope["bearer"]) == digest(CANARY)


@pytest.mark.asyncio
async def test_different_value_contenders_preserve_one_original_envelope(custody):
    store, _ = custody
    values = [CANARY, CANARY + "-other"]
    outcomes = await asyncio.gather(*[
        store.create(secret_ref=REF, value=value, expires_at=30) for value in values
    ])
    assert sorted(outcomes) == [False, True]
    original_hash = digest(await store.get(secret_ref=REF))
    assert original_hash == digest(values[outcomes.index(True)])
    assert not await store.create(secret_ref=REF, value="replacement", expires_at=40)
    assert digest(await store.get(secret_ref=REF)) == original_hash


@pytest.mark.asyncio
async def test_expired_entry_read_refuses_without_waiting_for_purge(custody, monkeypatch):
    store, manager = custody
    await store.create(secret_ref=REF, value=CANARY, expires_at=20)
    monkeypatch.setattr(issuance_module, "time", SimpleNamespace(time=lambda: 20))
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_expired$"):
        await store.get(secret_ref=REF)
    assert await manager.get_ephemeral_secret(namespace=NAMESPACE, secret_ref=REF) is not None


@pytest.mark.asyncio
async def test_purge_is_bounded_and_deleted_entry_returns_absence(custody, monkeypatch):
    store, manager = custody
    await store.create(secret_ref=REF, value=CANARY, expires_at=20)
    await store.create(secret_ref="b" * 32, value=CANARY, expires_at=20)
    await store.create(secret_ref="c" * 32, value=CANARY, expires_at=40)
    monkeypatch.setattr(issuance_module, "time", SimpleNamespace(time=lambda: 30))
    assert await store.purge_expired(now=30, limit=1) == 1
    assert await store.get(secret_ref=REF) is None
    assert await manager.get_ephemeral_secret(namespace=NAMESPACE, secret_ref="b" * 32) is not None
    assert digest(await store.get(secret_ref="c" * 32)) == digest(CANARY)


@pytest.mark.asyncio
async def test_delete_retires_only_original_reference_and_is_idempotent(custody):
    store, manager = custody
    await store.create(secret_ref=REF, value=CANARY, expires_at=30)
    other = "b" * 32
    await store.create(secret_ref=other, value=CANARY + "-other", expires_at=30)
    await store.delete(secret_ref=REF)
    assert await manager.get_ephemeral_secret(namespace=NAMESPACE, secret_ref=REF) is None
    fresh = issuance_secret_custody(namespace=NAMESPACE, manager=manager)
    await fresh.delete(secret_ref=REF)
    assert await fresh.get(secret_ref=REF) is None
    assert digest(await fresh.get(secret_ref=other)) == digest(CANARY + "-other")


@pytest.mark.asyncio
async def test_delete_retires_expired_original_without_reading_bearer(custody, monkeypatch):
    store, manager = custody
    await store.create(secret_ref=REF, value=CANARY, expires_at=20)
    monkeypatch.setattr(issuance_module, "time", SimpleNamespace(time=lambda: 30))
    async def forbidden_read(**kwargs):
        raise AssertionError("deletion must not fetch bearer material")
    monkeypatch.setattr(manager, "get_ephemeral_secret", forbidden_read)
    await store.delete(secret_ref=REF)
    assert manager._data == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("secret_ref", [None, True, "", CANARY, "../outside", "A" * 32, "a" * 33])
async def test_delete_invalid_reference_refuses_before_provider_io(custody, monkeypatch, secret_ref):
    store, manager = custody
    calls = []
    async def forbidden_io(**kwargs):
        calls.append(kwargs)
        raise AssertionError("invalid reference reached provider")
    monkeypatch.setattr(manager, "qualify_runtime_custody", forbidden_io)
    monkeypatch.setattr(manager, "delete_ephemeral_secret", forbidden_io)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_invalid$"):
        await store.delete(secret_ref=secret_ref)
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", [RuntimeError, SessionIssuanceRefused])
async def test_delete_provider_failure_is_sanitized(custody, monkeypatch, caplog, failure_type):
    store, manager = custody
    async def unavailable(**kwargs):
        raise failure_type(CANARY)
    monkeypatch.setattr(manager, "delete_ephemeral_secret", unavailable)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_unavailable$") as captured:
        await store.delete(secret_ref=REF)
    assert CANARY not in "".join(traceback.format_exception(captured.value))
    assert CANARY not in caplog.text


@pytest.mark.asyncio
async def test_lost_delete_response_is_unavailable_then_same_reference_retry(custody, monkeypatch):
    store, manager = custody
    await store.create(secret_ref=REF, value=CANARY, expires_at=30)
    delete = manager.delete_ephemeral_secret
    calls = []
    async def deleted_then_lost(**kwargs):
        calls.append(kwargs)
        await delete(**kwargs)
        raise RuntimeError(CANARY)
    monkeypatch.setattr(manager, "delete_ephemeral_secret", deleted_then_lost)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_unavailable$"):
        await store.delete(secret_ref=REF)
    assert await manager.get_ephemeral_secret(namespace=NAMESPACE, secret_ref=REF) is None
    monkeypatch.setattr(manager, "delete_ephemeral_secret", delete)
    fresh = issuance_secret_custody(namespace=NAMESPACE, manager=manager)
    await fresh.delete(secret_ref=REF)
    assert await fresh.get(secret_ref=REF) is None
    assert calls == [{"namespace": NAMESPACE, "secret_ref": REF}]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["raw-bearer", "{}", "null", "[]", "{invalid-json",
                                 '{"expires_at":20,"expires_at":30}', "[" * 2000])
async def test_malformed_or_unenveloped_custody_refuses(custody, raw):
    store, manager = custody
    await manager.set_ephemeral_secret(namespace=NAMESPACE, secret_ref=REF, value=raw, expires_at=30)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_invalid$"):
        await store.get(secret_ref=REF)


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("schema", "unknown"), ("secret_ref", "b" * 32),
    ("expires_at", True), ("expires_at", "30"), ("expires_at", 0),
    ("bearer", ""), ("bearer", "line\nbreak"), ("extra", "forbidden"),
])
async def test_invalid_envelope_fields_refuse_without_echoing_values(custody, field, value):
    store, manager = custody
    await store.create(secret_ref=REF, value=CANARY, expires_at=30)
    raw = await manager.get_ephemeral_secret(namespace=NAMESPACE, secret_ref=REF)
    envelope = {**json.loads(raw), field: value}
    await manager.set_ephemeral_secret(
        namespace=NAMESPACE, secret_ref=REF, value=json.dumps(envelope), expires_at=30,
    )
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_invalid$") as captured:
        await store.get(secret_ref=REF)
    assert CANARY not in str(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_at", [0, -1, True, "30", 10])
async def test_invalid_or_passed_creation_deadline_issues_nothing(custody, expires_at):
    store, manager = custody
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_(invalid|expired)$"):
        await store.create(secret_ref=REF, value=CANARY, expires_at=expires_at)
    assert await manager.get_ephemeral_secret(namespace=NAMESPACE, secret_ref=REF) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("now,limit", [(True, 1), (0, 1), (31, 1), (10, 0), (10, True), (10, 1001)])
async def test_invalid_purge_bounds_refuse_before_deletion(custody, now, limit):
    store, manager = custody
    await store.create(secret_ref=REF, value=CANARY, expires_at=30)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_invalid$"):
        await store.purge_expired(now=now, limit=limit)
    assert await manager.get_ephemeral_secret(namespace=NAMESPACE, secret_ref=REF) is not None


@pytest.mark.asyncio
async def test_in_memory_refuses_qualification_before_secret_io():
    manager = InMemorySecretsManager()
    custody = issuance_secret_custody(namespace=NAMESPACE, manager=manager)
    ordinary = KDCubeEphemeralSecretStore(manager, namespace=NAMESPACE)
    for adapter in (custody, KDCubeIssuanceSecretCustody(ordinary)):
        with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_not_durable$"):
            await adapter.qualify()
    assert manager._data == {}


@pytest.mark.parametrize("backend", [None, "ephemeral", "unknown"])
@pytest.mark.parametrize("injected", [False, True])
@pytest.mark.asyncio
async def test_factory_uses_common_qualification_not_internal_backend_labels(monkeypatch, backend, injected):
    monkeypatch.setattr(ephemeral_module, "_runtime_secret_manager", lambda settings: _ProviderFixture())
    adapter = issuance_secret_custody(
        namespace=NAMESPACE, settings=SimpleNamespace(SECRETS_SERVICE_BACKEND=backend),
        manager=_ProviderFixture() if injected else None,
    )
    await adapter.qualify()
    assert adapter.effective_backend == "secrets-service"


def test_production_factory_allows_explicit_host_vault_selection(monkeypatch):
    monkeypatch.setattr(ephemeral_module, "_runtime_secret_manager", lambda settings: _ProviderFixture())
    assert issuance_secret_custody(
        namespace=NAMESPACE, settings=SimpleNamespace(SECRETS_SERVICE_BACKEND="host-vault"),
    )


def test_wrapper_exposes_exact_namespace_and_validated_effective_backend(custody):
    store, _ = custody
    assert store.namespace == NAMESPACE
    assert store.effective_backend == "secrets-service"
    with pytest.raises(AttributeError):
        store.namespace = "other-namespace"
    with pytest.raises(AttributeError):
        store.effective_backend = "aws-sm"


@pytest.mark.parametrize("backend", [None, "ephemeral", "unknown"])
@pytest.mark.asyncio
async def test_backend_setting_cannot_qualify_an_unqualified_manager(backend):
    store = KDCubeEphemeralSecretStore(InMemorySecretsManager(), namespace=NAMESPACE)
    adapter = KDCubeIssuanceSecretCustody(store, settings=SimpleNamespace(SECRETS_SERVICE_BACKEND=backend))
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_not_durable$"):
        await adapter.qualify()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["issuance_custody_expired", "issuance_custody_invalid",
                                    "issuance_custody_not_durable", CANARY])
async def test_issuer_preserves_only_allowlisted_non_secret_custody_refusals(reason):
    async def failed(**kwargs):
        raise SessionIssuanceRefused(reason)
    expected = reason if reason != CANARY else "issuance_custody_unavailable"
    with pytest.raises(SessionIssuanceRefused, match=f"^{expected}$") as captured:
        await _custody_call(failed, secret_ref=REF)
    formatted = "".join(traceback.format_exception(captured.value))
    assert CANARY not in formatted


@pytest.mark.asyncio
async def test_provider_exception_is_sanitized_including_traceback(custody, monkeypatch, caplog):
    store, manager = custody
    async def unavailable(**kwargs):
        raise RuntimeError(CANARY)
    monkeypatch.setattr(manager, "get_ephemeral_secret", unavailable)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_unavailable$") as captured:
        await store.get(secret_ref=REF)
    assert CANARY not in "".join(traceback.format_exception(captured.value))
    assert CANARY not in caplog.text


@pytest.mark.asyncio
async def test_missing_entry_is_read_only(custody):
    store, manager = custody
    assert await store.get(secret_ref=REF) is None
    assert manager._data == {}


def test_existing_ephemeral_factory_defaults_remain_compatible():
    assert ephemeral_secret_store(namespace="test-fixture", manager=InMemorySecretsManager())


@pytest.mark.asyncio
@pytest.mark.parametrize("enrolled", [False], ids=["default-closed"])
async def test_aws_adapter_repeated_create_refuses_without_storage_mutation(monkeypatch, enrolled):
    """Without namespace enrollment the AWS lane does not qualify and nothing is sent."""
    monkeypatch.setattr(issuance_module, "time", SimpleNamespace(time=lambda: 10))
    manager = AwsSecretsManagerSecretsManager(SecretsManagerConfig(
        provider="aws-sm", component="ingress", aws_sm_prefix="synthetic/issuance",
        runtime_secret_namespaces=(NAMESPACE,) if enrolled else (),
    ))
    client = _FakeAwsSecretsClient()
    manager._session = _FakeAwsSession(client)
    store = issuance_secret_custody(namespace=NAMESPACE, manager=manager)
    assert store.namespace == NAMESPACE and store.effective_backend == "aws-sm"
    for _ in range(2):
        with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_not_durable$"):
            await store.create(secret_ref=REF, value=CANARY, expires_at=30)
    assert len(client.list_calls) == (2 if enrolled else 0)
    assert client.create_calls == [] and client.delete_calls == []
    assert client.data == {} and client.tags == {} and client.version_tokens == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("enrolled", [False], ids=["default-closed"])
async def test_aws_adapter_concurrent_creates_refuse_without_storage_mutation(monkeypatch, enrolled):
    """Without namespace enrollment concurrent creates refuse and nothing is sent."""
    monkeypatch.setattr(issuance_module, "time", SimpleNamespace(time=lambda: 10))
    manager = AwsSecretsManagerSecretsManager(SecretsManagerConfig(
        provider="aws-sm", component="ingress", aws_sm_prefix="synthetic/issuance",
        runtime_secret_namespaces=(NAMESPACE,) if enrolled else (),
    ))
    client = _FakeAwsSecretsClient()
    manager._session = _FakeAwsSession(client)
    store = issuance_secret_custody(namespace=NAMESPACE, manager=manager)
    values = [CANARY, CANARY + "-different"]
    results = await asyncio.gather(*[
        store.create(secret_ref=REF, value=value, expires_at=30) for value in values
    ], return_exceptions=True)
    assert all(isinstance(result, SessionIssuanceRefused) for result in results)
    assert [result.reason for result in results] == ["issuance_custody_not_durable"] * 2
    assert len(client.list_calls) == (2 if enrolled else 0)
    assert client.create_calls == [] and client.delete_calls == []
    assert client.data == {} and client.tags == {} and client.version_tokens == {}


def _stored_bearers(client):
    return [json.loads(value)["bearer"] for value in client.data.values()]


def _enrolled_aws_custody(monkeypatch):
    monkeypatch.setattr(issuance_module, "time", SimpleNamespace(time=lambda: 10))
    manager = AwsSecretsManagerSecretsManager(SecretsManagerConfig(
        provider="aws-sm", component="ingress", aws_sm_prefix="synthetic/issuance",
        runtime_secret_namespaces=(NAMESPACE,),
    ))
    client = _FakeAwsSecretsClient()
    manager._session = _FakeAwsSession(client)
    return issuance_secret_custody(namespace=NAMESPACE, manager=manager), client


@pytest.mark.asyncio
async def test_enrolled_aws_custody_stores_one_record_and_an_identical_replay_reuses_it(monkeypatch):
    """An enrolled, reachable AWS namespace qualifies (enrollment plus availability, not IAM proof).

    The create sends the reference as AWS's ClientRequestToken, so an identical replay is AWS's
    idempotent answer for the same version: it reports created again without writing a second value.
    The issuers' durable reservation and digest comparison decide ownership above this layer.
    """
    store, client = _enrolled_aws_custody(monkeypatch)
    assert await store.create(secret_ref=REF, value=CANARY, expires_at=30) is True
    assert await store.create(secret_ref=REF, value=CANARY, expires_at=30) is True
    assert [call["ClientRequestToken"] for call in client.create_calls] == [REF, REF]
    assert _stored_bearers(client) == [CANARY]
    assert await store.get(secret_ref=REF) == CANARY


@pytest.mark.asyncio
async def test_enrolled_aws_custody_never_replaces_a_record_with_a_different_value(monkeypatch):
    store, client = _enrolled_aws_custody(monkeypatch)
    assert await store.create(secret_ref=REF, value=CANARY, expires_at=30) is True
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_unavailable$") as refused:
        await store.create(secret_ref=REF, value=CANARY + "-different", expires_at=30)
    assert CANARY not in "".join(traceback.format_exception(refused.value))
    assert _stored_bearers(client) == [CANARY]
    assert await store.get(secret_ref=REF) == CANARY


@pytest.mark.asyncio
async def test_enrolled_aws_concurrent_different_creates_keep_exactly_one_value(monkeypatch):
    store, client = _enrolled_aws_custody(monkeypatch)
    values = [CANARY, CANARY + "-different"]
    results = await asyncio.gather(*[
        store.create(secret_ref=REF, value=value, expires_at=30) for value in values
    ], return_exceptions=True)
    winners = [value for value, result in zip(values, results) if result is True]
    losers = [result for result in results if result is not True]
    assert len(winners) == 1
    assert [type(result) for result in losers] == [SessionIssuanceRefused]
    assert losers[0].reason == "issuance_custody_unavailable"
    assert _stored_bearers(client) == winners
    assert await store.get(secret_ref=REF) == winners[0]


def _http_custody():
    manager = SecretsServiceSecretsManager(SecretsManagerConfig(
        provider="secrets-service", component="ingress",
        url="http://synthetic-secrets", token="synthetic-reader", admin_token="synthetic-admin",
    ))
    return issuance_secret_custody(namespace=NAMESPACE, manager=manager, settings=HOST_SETTINGS)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"status": "ok"}, {}, [], None,
    {"status": "ok", "vault": {}},
    {"status": "ok", "vault": {"ok": 1, "code": "ok"}},
    {"status": "ok", "vault": {"ok": False, "code": "ok"}},
    {"status": "ok", "vault": {"ok": True, "code": "backend_unavailable"}},
    {"status": "ok", "vault": {"ok": True, "code": "ok"}, "extra": True},
    {"status": "ok", "vault": {"ok": True, "code": "ok", "extra": True}},
    {"status": "ok", "vault": {"ok": True, "code": "ok", "deployment_id": True}},
    {"status": "ok", "vault": {"ok": True, "code": "ok", "deployment_id": ""}},
])
async def test_declared_host_vault_refuses_unqualified_running_backend(monkeypatch, payload):
    client = _FakeSecretsHttpClient(_FakeHttpResponse(200, payload))
    monkeypatch.setattr(manager_module, "_get_httpx", lambda: _FakeHttpxModule(client))
    custody = _http_custody()
    assert custody.declared_backend == "secrets-service"
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_not_durable$"):
        await custody.qualify()
    assert client.requests == [("GET", f"http://synthetic-secrets/runtime-secrets/{NAMESPACE}/qualification",
                                {"headers": {"X-KDCUBE-SECRET-TOKEN": "synthetic-reader",
                                             "X-KDCUBE-ADMIN-TOKEN": "synthetic-admin"}})]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 503])
async def test_backend_health_failure_is_unavailable_without_response_disclosure(monkeypatch, status):
    client = _FakeSecretsHttpClient(_FakeHttpResponse(status, {"detail": CANARY}))
    monkeypatch.setattr(manager_module, "_get_httpx", lambda: _FakeHttpxModule(client))
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_unavailable$") as captured:
        await _http_custody().qualify()
    assert CANARY not in "".join(traceback.format_exception(captured.value))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["network", "json"])
async def test_health_transport_or_json_exception_is_sanitized(monkeypatch, failure):
    client = _FakeSecretsHttpClient(
        response=_FakeHttpResponse(200, ValueError(CANARY)),
        error=RuntimeError(CANARY) if failure == "network" else None,
    )
    monkeypatch.setattr(manager_module, "_get_httpx", lambda: _FakeHttpxModule(client))
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_unavailable$") as captured:
        await _http_custody().qualify()
    assert CANARY not in "".join(traceback.format_exception(captured.value))


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "get", "delete", "purge_expired"])
async def test_qualification_is_not_cached_across_backend_replacement(monkeypatch, operation):
    client = _FakeSecretsHttpClient(_FakeHttpResponse(200, qualification(NAMESPACE)))
    monkeypatch.setattr(manager_module, "_get_httpx", lambda: _FakeHttpxModule(client))
    custody = _http_custody()
    await custody.qualify()
    client.response = _FakeHttpResponse(200, {"status": "ok"})
    arguments = {
        "create": {"secret_ref": REF, "value": CANARY, "expires_at": 30},
        "get": {"secret_ref": REF},
        "delete": {"secret_ref": REF},
        "purge_expired": {"now": 10, "limit": 1},
    }
    monkeypatch.setattr(issuance_module, "time", SimpleNamespace(time=lambda: 10))
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_not_durable$"):
        await getattr(custody, operation)(**arguments[operation])
    assert len(client.requests) == 2
    assert all(request[1].endswith(f"/runtime-secrets/{NAMESPACE}/qualification") for request in client.requests)


@pytest.mark.asyncio
async def test_complete_namespace_bound_common_contract_is_qualified(monkeypatch):
    client = _FakeSecretsHttpClient(_FakeHttpResponse(200, qualification(NAMESPACE)))
    monkeypatch.setattr(manager_module, "_get_httpx", lambda: _FakeHttpxModule(client))
    await _http_custody().qualify()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["create", "get", "delete", "purge_expired"])
async def test_real_ephemeral_sidecar_refused_before_secret_io(monkeypatch, tmp_path, operation):
    """Real ASGI sidecar; no deployed service or credentials are used."""
    import httpx
    source = Path(__file__).resolve().parents[6] / "deployment/docker/all_in_one_kdcube/secrets/secrets_server.py"
    spec = importlib.util.spec_from_file_location("issuance_ephemeral_sidecar_fixture", source)
    sidecar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sidecar)
    monkeypatch.setattr(sidecar, "STORE_PATH", str(tmp_path / "store.json"))
    requests = []

    async def record_request(request):
        requests.append(request.url.path)
    real_client = httpx.AsyncClient
    transport = httpx.ASGITransport(app=sidecar.app)
    monkeypatch.setattr(manager_module, "_get_httpx", lambda: SimpleNamespace(
        AsyncClient=lambda **kwargs: real_client(
            **kwargs, transport=transport, event_hooks={"request": [record_request]},
        ),
    ))
    monkeypatch.setattr(issuance_module, "time", SimpleNamespace(time=lambda: 10))
    custody = _http_custody()
    arguments = {
        "create": {"secret_ref": REF, "value": CANARY, "expires_at": 30},
        "get": {"secret_ref": REF},
        "delete": {"secret_ref": REF},
        "purge_expired": {"now": 10, "limit": 1},
    }
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_not_durable$"):
        await getattr(custody, operation)(**arguments[operation])
    assert requests == [f"/runtime-secrets/{NAMESPACE}/qualification"]
    assert not (tmp_path / "store.json").exists()


def test_duck_typed_or_subclassed_store_is_not_a_qualified_adapter():
    for store in (SimpleNamespace(provider_type="secrets-service", namespace=NAMESPACE),
                  type("StoreSubclass", (KDCubeEphemeralSecretStore,), {})(
                      _ProviderFixture(), namespace=NAMESPACE)):
        with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_not_durable$"):
            KDCubeIssuanceSecretCustody(store, settings=HOST_SETTINGS)
