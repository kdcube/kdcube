from __future__ import annotations

import asyncio
import hashlib
import json
import traceback
from types import SimpleNamespace

import pytest

from kdcube_ai_app.auth.bundle.session_bound_issuer import _custody_call
from kdcube_ai_app.auth.bundle.session_issuance import SessionIssuanceRefused
from kdcube_ai_app.infra.secrets import ephemeral as ephemeral_module
from kdcube_ai_app.infra.secrets import issuance as issuance_module
from kdcube_ai_app.infra.secrets.ephemeral import KDCubeEphemeralSecretStore, ephemeral_secret_store
from kdcube_ai_app.infra.secrets.issuance import KDCubeIssuanceSecretCustody, issuance_secret_custody
from kdcube_ai_app.infra.secrets.manager import (
    AwsSecretsManagerSecretsManager, InMemorySecretsManager, SecretsManagerConfig, SecretsManagerError,
)
from kdcube_ai_app.infra.secrets.tests.test_manager import _FakeAwsSecretsClient, _FakeAwsSession

NAMESPACE = "connection-hub-issuance-custody"
REF = "a" * 32
CANARY = "synthetic-bearer-for-digest-comparison"
HOST_SETTINGS = SimpleNamespace(SECRETS_SERVICE_BACKEND="host-vault")


class _ProviderFixture(InMemorySecretsManager):
    """Test seam only: same manager protocol, NOT a production durability proof."""
    provider_type = "secrets-service"


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


def test_in_memory_rejected_by_factory_and_direct_custody_constructor():
    manager = InMemorySecretsManager()
    with pytest.raises(SecretsManagerError, match="cannot use in-memory"):
        issuance_secret_custody(namespace=NAMESPACE, manager=manager)
    ordinary = KDCubeEphemeralSecretStore(manager, namespace=NAMESPACE)
    with pytest.raises(SessionIssuanceRefused, match="^issuance_custody_not_durable$"):
        KDCubeIssuanceSecretCustody(ordinary)


@pytest.mark.parametrize("backend", [None, "ephemeral", "unknown"])
@pytest.mark.parametrize("injected", [False, True])
def test_production_factory_refuses_non_host_vault_secrets_service(monkeypatch, backend, injected):
    monkeypatch.setattr(ephemeral_module, "_runtime_secret_manager", lambda settings: _ProviderFixture())
    with pytest.raises(SecretsManagerError, match="requires the host-vault backend"):
        issuance_secret_custody(
            namespace=NAMESPACE, settings=SimpleNamespace(SECRETS_SERVICE_BACKEND=backend),
            manager=_ProviderFixture() if injected else None,
        )


def test_production_factory_allows_explicit_host_vault_selection(monkeypatch):
    monkeypatch.setattr(ephemeral_module, "_runtime_secret_manager", lambda settings: _ProviderFixture())
    assert issuance_secret_custody(
        namespace=NAMESPACE, settings=SimpleNamespace(SECRETS_SERVICE_BACKEND="host-vault"),
    )


def test_wrapper_exposes_exact_namespace_and_validated_effective_backend(custody):
    store, _ = custody
    assert store.namespace == NAMESPACE
    assert store.effective_backend == "host-vault"
    with pytest.raises(AttributeError):
        store.namespace = "other-namespace"
    with pytest.raises(AttributeError):
        store.effective_backend = "aws-sm"


@pytest.mark.parametrize("backend", [None, "ephemeral", "unknown"])
def test_direct_wrapper_constructor_requires_actual_host_vault_setting(backend):
    store = KDCubeEphemeralSecretStore(_ProviderFixture(), namespace=NAMESPACE)
    with pytest.raises(SecretsManagerError, match="requires the host-vault backend"):
        KDCubeIssuanceSecretCustody(store, settings=SimpleNamespace(SECRETS_SERVICE_BACKEND=backend))


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
async def test_aws_adapter_identical_replay_preserves_original_version(monkeypatch):
    """AWS protocol fake: not a live endpoint, IAM or durability qualification."""
    monkeypatch.setattr(issuance_module, "time", SimpleNamespace(time=lambda: 10))
    manager = AwsSecretsManagerSecretsManager(SecretsManagerConfig(
        provider="aws-sm", component="ingress", aws_sm_prefix="synthetic/issuance",
    ))
    client = _FakeAwsSecretsClient()
    manager._session = _FakeAwsSession(client)
    store = issuance_secret_custody(namespace=NAMESPACE, manager=manager)
    assert store.namespace == NAMESPACE and store.effective_backend == "aws-sm"
    assert await store.create(secret_ref=REF, value=CANARY, expires_at=30)
    original_versions = dict(client.version_tokens)
    assert await store.create(secret_ref=REF, value=CANARY, expires_at=30)
    assert client.version_tokens == original_versions
    assert len(client.data) == 1
    assert digest(await store.get(secret_ref=REF)) == digest(CANARY)


@pytest.mark.asyncio
async def test_aws_adapter_different_value_contender_cannot_replace_original(monkeypatch):
    """Concurrent caller surface over a protocol fake, not AWS atomicity proof."""
    monkeypatch.setattr(issuance_module, "time", SimpleNamespace(time=lambda: 10))
    manager = AwsSecretsManagerSecretsManager(SecretsManagerConfig(
        provider="aws-sm", component="ingress", aws_sm_prefix="synthetic/issuance",
    ))
    client = _FakeAwsSecretsClient()
    manager._session = _FakeAwsSession(client)
    store = issuance_secret_custody(namespace=NAMESPACE, manager=manager)
    values = [CANARY, CANARY + "-different"]
    results = await asyncio.gather(*[
        store.create(secret_ref=REF, value=value, expires_at=30) for value in values
    ], return_exceptions=True)
    assert sum(result is True for result in results) == 1
    loser = next(result for result in results if result is not True)
    assert isinstance(loser, SessionIssuanceRefused)
    assert loser.reason == "issuance_custody_unavailable"
    assert len(client.data) == 1
    winner = next(value for value, result in zip(values, results) if result is True)
    assert digest(await store.get(secret_ref=REF)) == digest(winner)
