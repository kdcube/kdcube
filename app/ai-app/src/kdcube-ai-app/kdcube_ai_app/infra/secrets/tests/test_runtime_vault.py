# SPDX-License-Identifier: MIT
"""Native broker/service/encrypted disk store, synthetic identity and root keys."""
from __future__ import annotations

import json
import time
import traceback

import pytest

from kdcube_ai_app.infra.secrets import runtime_vault
from kdcube_ai_app.infra.secrets.host_vault import audit, broker, identity, keys, protocol, service, storage

REF = "a" * 32
CANARY = "synthetic-runtime-value"


class Transport(broker.VaultTransport):
    def __init__(self, vault, cert):
        self.vault = vault
        self.cert = cert
        self.requests = []
        self.before_delete = None
        self.before_set = None

    def call(self, request):
        self.requests.append(request)
        if request.operation is protocol.Operation.DELETE and self.before_delete:
            hook, self.before_delete = self.before_delete, None
            hook()
        if request.operation is protocol.Operation.SET and self.before_set:
            hook, self.before_set = self.before_set, None
            hook()
        return self.vault.handle(request.to_wire(), peer_cert_pem=self.cert)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    ca = identity.HostIssuingCA.generate()
    registry = identity.TrustRegistry(tmp_path / "trust.json", ca=ca)
    namespace = protocol.SecretNamespace("test-tenant", "test-project", "kdcube-runtime")
    ticket = registry.mint_ticket(deployment_id="synthetic-deployment", namespaces=[namespace.path])
    deployment_key = identity.DeploymentKey.generate()
    cert, _ = registry.enroll(ticket_id=ticket.ticket_id, csr_pem=deployment_key.csr())
    provider = keys.FileRootKeyProvider(tmp_path / "keys")
    provider.rotate()
    store = storage.FileDurableSecretStore(tmp_path / "store", provider)
    vault = service.HostVaultService(store=store, registry=registry, audit=audit.MemoryAuditSink())
    transport = Transport(vault, cert)
    native = broker.SecretsBroker(transport=transport, tenant=namespace.tenant, project=namespace.project)
    wall = [int(time.time())]
    monkeypatch.setattr(runtime_vault.time, "time", lambda: wall[0])

    def adapter(namespace="custody", application="kdcube-runtime", grants=("custody",)):
        return runtime_vault.RuntimeVaultStore(broker=native, application=application,
                                              namespace=namespace, authorized_namespaces=grants)

    return adapter, native, transport, store, provider, wall


def test_same_original_record_survives_store_restart_and_refuses_replacement(rig):
    adapter, native, transport, store, provider, wall = rig
    first = adapter()
    assert first.create(secret_ref=REF, value=CANARY, expires_at=wall[0] + 30)
    restarted = storage.FileDurableSecretStore(store._root, provider)
    transport.vault._store = restarted
    fresh = adapter()
    assert not fresh.create(secret_ref=REF, value="synthetic-rival", expires_at=wall[0] + 90)
    assert fresh.get(secret_ref=REF) == CANARY
    assert native.read(application="kdcube-runtime", key=first._key(REF)).generation == 1
    wall[0] += 30
    assert fresh.get(secret_ref=REF) is None


def test_deleted_generation_cannot_be_recreated_as_a_fresh_reference(rig):
    adapter, _, transport, _, _, wall = rig
    store = adapter()
    assert store.create(secret_ref=REF, value=CANARY, expires_at=wall[0] + 30)
    store.delete(secret_ref=REF)
    assert store.get(secret_ref=REF) is None
    assert not store.create(secret_ref=REF, value="synthetic-rival", expires_at=wall[0] + 60)
    deletes = [request for request in transport.requests if request.operation is protocol.Operation.DELETE]
    assert deletes[0].expected_generation == 1


def test_purge_is_bounded_and_preserves_unexpired_records(rig):
    adapter, _, transport, _, _, wall = rig
    store = adapter()
    for index in range(4):
        assert store.create(secret_ref=f"{index:032x}", value=CANARY, expires_at=wall[0] + 10)
    assert store.create(secret_ref=REF, value=CANARY, expires_at=wall[0] + 60)
    wall[0] += 10
    assert store.purge_expired(now=wall[0], limit=2) == 2
    assert store.purge_expired(now=wall[0], limit=2) == 2
    assert store.purge_expired(now=wall[0], limit=2) == 0
    assert store.get(secret_ref=REF) == CANARY
    for request in transport.requests:
        if request.operation is protocol.Operation.DELETE:
            assert request.expected_generation == 1


@pytest.mark.parametrize("operation", ["purge", "delete"])
def test_generation_guard_preserves_a_racing_replacement(rig, operation):
    adapter, native, transport, _, _, wall = rig
    store = adapter()
    assert store.create(secret_ref=REF, value=CANARY, expires_at=wall[0] + 10)
    wall[0] += 10

    def replace():
        payload = json.loads(native.get(application="kdcube-runtime", key=store._key(REF)))
        payload.update(value="synthetic-replacement", expires_at=wall[0] + 60)
        assert native.set(application="kdcube-runtime", key=store._key(REF),
                          value=json.dumps(payload), expected_generation=1).ok

    transport.before_delete = replace
    if operation == "purge":
        assert store.purge_expired(now=wall[0], limit=1) == 0
    else:
        with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_conflict$"):
            store.delete(secret_ref=REF)
    assert store.get(secret_ref=REF) == "synthetic-replacement"
    assert native.read(application="kdcube-runtime", key=store._key(REF)).generation == 2


@pytest.mark.parametrize("raw", [CANARY, "{}", "[]", "null", '{"value":"x","value":"y"}',
                                 json.dumps({"schema": "unknown", "value": CANARY})])
def test_malformed_encrypted_records_fail_closed_without_value_disclosure(rig, raw):
    adapter, native, _, _, _, _ = rig
    store = adapter()
    assert native.set(application="kdcube-runtime", key=store._key(REF), value=raw,
                      expected_generation=0).ok
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_record_invalid$") as refused:
        store.get(secret_ref=REF)
    assert CANARY not in "".join(traceback.format_exception(refused.value))


@pytest.mark.parametrize("field, bad", [("namespace", "other"), ("secret_ref", "b" * 32),
    ("schema", "unknown"), ("value", True), ("expires_at", True), ("expires_at", 0)])
def test_record_binding_and_types_are_strict(rig, field, bad):
    adapter, native, _, _, _, wall = rig
    store = adapter()
    payload = {"schema": runtime_vault.SCHEMA, "namespace": "custody", "secret_ref": REF,
               "value": CANARY, "expires_at": wall[0] + 30}
    payload[field] = bad
    assert native.set(application="kdcube-runtime", key=store._key(REF), value=json.dumps(payload),
                      expected_generation=0).ok
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_record_invalid$"):
        store.get(secret_ref=REF)


@pytest.mark.parametrize("value, expiry", [(True, 1), ("\ud800", 1), ("x" * 65536, 1),
                                           (CANARY, True), (CANARY, "1"), (CANARY, 0)])
def test_invalid_create_is_rejected_before_transport(rig, value, expiry):
    adapter, _, transport, _, _, _ = rig
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_value_invalid$"):
        adapter().create(secret_ref=REF, value=value, expires_at=expiry)
    assert transport.requests == []


@pytest.mark.parametrize("limit", [True, 0, -1, 1001, 1.0, "1"])
def test_invalid_purge_bounds_are_rejected_before_transport(rig, limit):
    adapter, _, transport, _, _, wall = rig
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_purge_invalid$"):
        adapter().purge_expired(now=wall[0], limit=limit)
    assert transport.requests == []


def test_future_purge_and_expired_creation_are_rejected_before_transport(rig):
    adapter, _, transport, _, _, wall = rig
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_purge_invalid$"):
        adapter().purge_expired(now=wall[0] + 1, limit=1)
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_expired$"):
        adapter().create(secret_ref=REF, value=CANARY, expires_at=wall[0])
    assert transport.requests == []


def test_expiry_during_transport_never_returns_an_acknowledged_live_record(rig):
    adapter, _, transport, _, _, wall = rig
    store = adapter()
    expiry = wall[0] + 10
    transport.before_set = lambda: wall.__setitem__(0, expiry)
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_expired$"):
        store.create(secret_ref=REF, value=CANARY, expires_at=expiry)
    assert store.get(secret_ref=REF) is None
    assert not store.create(secret_ref=REF, value="synthetic-rival", expires_at=wall[0] + 60)


def test_namespace_grant_and_native_application_acl_are_both_required(rig):
    adapter, _, transport, _, _, wall = rig
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_scope_forbidden$"):
        adapter(namespace="other")
    assert transport.requests == []
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_storage_unavailable$"):
        adapter(application="ungranted-app").create(secret_ref=REF, value=CANARY, expires_at=wall[0] + 30)


def test_health_never_substitutes_for_custody_qualification(rig, monkeypatch):
    adapter, native, _, _, _, _ = rig
    monkeypatch.setattr(storage, "persistent_filesystem", lambda root: False)
    assert native.health()["ok"] is True
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_custody_unqualified$"):
        adapter().qualify()


def test_backend_failure_is_not_absence_and_discloses_no_exception_text(rig, monkeypatch):
    adapter, _, transport, _, _, _ = rig

    def unavailable(request):
        raise RuntimeError(CANARY)

    monkeypatch.setattr(transport, "call", unavailable)
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_storage_unavailable$") as refused:
        adapter().get(secret_ref=REF)
    assert CANARY not in "".join(traceback.format_exception(refused.value))


def _private_disk_classification(monkeypatch):
    # Synthetic source gate: this is not a mounted filesystem witness.
    monkeypatch.setattr(storage, "persistent_filesystem", lambda root: True)
    monkeypatch.setattr(keys, "persistent_filesystem", lambda root: True)


def test_native_qualification_is_fresh_scoped_and_survives_store_restart(rig, monkeypatch):
    adapter, _, transport, store, provider, _ = rig
    _private_disk_classification(monkeypatch)
    adapter().qualify()
    first = transport.requests[-1]
    assert first.operation is protocol.Operation.QUALIFY
    assert first.reference.name == "platform.runtime.custody.__keys"
    assert first.value is first.expected_generation is None
    assert all(request.operation is not protocol.Operation.HEALTH for request in transport.requests)

    fresh_provider = keys.FileRootKeyProvider(provider._dir)
    transport.vault._store = storage.FileDurableSecretStore(store._root, fresh_provider)
    adapter().qualify()
    assert transport.requests[-1].request_id != first.request_id


@pytest.mark.parametrize("module", [storage, keys])
@pytest.mark.parametrize("classification", [False, None, 1, "true"])
def test_native_qualification_refuses_every_nontrue_storage_classification(
        rig, monkeypatch, module, classification):
    adapter, native, _, _, _, _ = rig
    _private_disk_classification(monkeypatch)
    monkeypatch.setattr(module, "persistent_filesystem", lambda root: classification)
    assert native.health()["ok"] is True
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_custody_unqualified$"):
        adapter().qualify()


def test_memory_root_keys_never_qualify_a_healthy_file_store(rig, monkeypatch):
    adapter, native, transport, store, _, _ = rig
    _private_disk_classification(monkeypatch)
    transport.vault._store = storage.FileDurableSecretStore(store._root, keys.FakeInMemoryRootKeyProvider())
    assert native.health()["ok"] is True
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_custody_unqualified$"):
        adapter().qualify()


@pytest.mark.parametrize("part", ["root_mode", "root_link", "key_mode", "key_link", "historical_key", "lock"])
def test_native_qualification_refuses_unsafe_disk_or_key_custody(rig, monkeypatch, part):
    adapter, native, transport, store, provider, _ = rig
    _private_disk_classification(monkeypatch)
    key = provider._dir / (provider.current_key_id() + ".key")
    if part == "root_mode":
        store._root.chmod(0o755)
    elif part == "root_link":
        original = store._root.with_name("original-store")
        store._root.rename(original)
        store._root.symlink_to(original, target_is_directory=True)
    elif part == "key_mode":
        key.chmod(0o644)
    elif part == "key_link":
        original = key.with_name("original-key")
        key.rename(original)
        key.symlink_to(original)
    elif part == "historical_key":
        provider.rotate()
        key.chmod(0o644)
    else:
        (store._root / store.LOCK_NAME).chmod(0o644)
    assert native.health()["ok"] is True
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_custody_unqualified$"):
        adapter().qualify()
    response = transport.vault.handle(transport.requests[-1].to_wire(), peer_cert_pem=transport.cert)
    assert response.code is protocol.ErrorCode.BACKEND_UNAVAILABLE
    assert response.extra == {} and response.value is response.generation is None


@pytest.mark.parametrize("failure", ["no_lock", "foreign_uid", "missing_qualifier", "probe_error", "false_result"])
def test_native_qualification_backend_refusals_are_value_free(rig, monkeypatch, failure):
    adapter, native, transport, store, _, _ = rig
    _private_disk_classification(monkeypatch)
    if failure == "no_lock":
        monkeypatch.setattr(storage, "fcntl", None)
    elif failure == "foreign_uid":
        current = storage.os.geteuid()
        monkeypatch.setattr(storage.os, "geteuid", lambda: current + 1)
    elif failure == "missing_qualifier":
        transport.vault._store = object()
    elif failure == "false_result":
        monkeypatch.setattr(store, "qualify_custody", lambda: True)
    else:
        def unavailable(root):
            raise RuntimeError(CANARY)
        monkeypatch.setattr(storage, "persistent_filesystem", unavailable)
    assert native.health()["ok"] is True
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_custody_unqualified$") as refused:
        adapter().qualify()
    assert CANARY not in "".join(traceback.format_exception(refused.value))


def test_qualification_requires_explicit_native_application_grant(rig, monkeypatch):
    adapter, _, transport, _, _, _ = rig
    _private_disk_classification(monkeypatch)
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_custody_unqualified$"):
        adapter(application="ungranted-app").qualify()
    registry = transport.vault._registry
    ticket = registry.mint_ticket(deployment_id="synthetic-wildcard", namespaces=["test-tenant/test-project/*"])
    key = identity.DeploymentKey.generate()
    transport.cert, _ = registry.enroll(ticket_id=ticket.ticket_id, csr_pem=key.csr())
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_custody_unqualified$"):
        adapter().qualify()


@pytest.mark.parametrize("changes", [{"value": None}, {"value": CANARY},
    {"expected_generation": None}, {"expected_generation": 0}, {"reference": "kdv1:test-tenant/test-project/kdcube-runtime/ordinary-key"}])
def test_qualification_wire_rejects_inapplicable_fields_before_storage(rig, monkeypatch, changes):
    _, _, transport, store, _, _ = rig
    def must_not_qualify():
        pytest.fail("malformed request reached storage qualification")
    monkeypatch.setattr(store, "qualify_custody", must_not_qualify)
    namespace = protocol.SecretNamespace("test-tenant", "test-project", "kdcube-runtime")
    request = protocol.VaultRequest.new(protocol.Operation.QUALIFY,
        protocol.SecretReference(namespace, "platform.runtime.custody.__keys"))
    response = transport.vault.handle({**request.to_wire(), **changes}, peer_cert_pem=transport.cert)
    assert response.code is protocol.ErrorCode.INVALID_REQUEST


@pytest.mark.parametrize("wrong", ["health", "request_id", "namespace", "reference", "missing_flag", "extra_flag", "integer_flag", "value"])
def test_broker_rejects_uncorrelated_or_malformed_qualification_answers(rig, monkeypatch, wrong):
    adapter, _, transport, _, _, _ = rig
    _private_disk_classification(monkeypatch)

    def answer(request):
        custody = protocol.custody_qualification(request.reference)
        if wrong == "health":
            return protocol.VaultResponse.success(request, deployment_id="synthetic-deployment")
        if wrong == "namespace":
            custody["namespace"] = "test-tenant/other-project/kdcube-runtime"
        elif wrong == "reference":
            custody["reference_digest"] = "f" * 24
        elif wrong == "missing_flag":
            custody.pop("process_lock")
        elif wrong == "extra_flag":
            custody["healthy"] = True
        elif wrong == "integer_flag":
            custody["encrypted"] = 1
        response = protocol.VaultResponse.success(request, custody=custody,
            value=CANARY if wrong == "value" else None)
        if wrong == "request_id":
            return protocol.VaultResponse(ok=True, code=protocol.ErrorCode.OK,
                message="ok", request_id="unrelated-request", extra=response.extra)
        return response

    monkeypatch.setattr(transport, "call", answer)
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_custody_unqualified$") as refused:
        adapter().qualify()
    assert CANARY not in "".join(traceback.format_exception(refused.value))


def test_native_qualification_is_rechecked_after_revocation(rig, monkeypatch):
    adapter, _, transport, _, _, _ = rig
    _private_disk_classification(monkeypatch)
    adapter().qualify()
    transport.vault._registry.revoke(identity.fingerprint_of(transport.cert))
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_custody_unqualified$"):
        adapter().qualify()


@pytest.mark.parametrize("changed", ["root_mode", "root_inode", "lock_mode", "lock_inode"])
def test_native_qualification_rechecks_custody_after_the_provider_probe(rig, monkeypatch, changed):
    adapter, _, _, store, provider, _ = rig
    _private_disk_classification(monkeypatch)
    qualify_keys = provider.qualify_custody

    def change_after_probe():
        qualify_keys()
        if changed == "root_mode":
            store._root.chmod(0o755)
        elif changed == "root_inode":
            store._root.rename(store._root.with_name("previous-store"))
            store._root.mkdir(mode=0o700)
        elif changed == "lock_mode":
            (store._root / store.LOCK_NAME).chmod(0o644)
        else:
            lock = store._root / store.LOCK_NAME
            lock.rename(lock.with_name("previous-lock"))
            lock.touch(mode=0o600)

    monkeypatch.setattr(provider, "qualify_custody", change_after_probe)
    with pytest.raises(runtime_vault.RuntimeVaultError, match="^runtime_secret_custody_unqualified$"):
        adapter().qualify()


@pytest.mark.parametrize("field", ["value", "generation"])
def test_qualification_answer_rejects_even_null_inapplicable_fields(field):
    namespace = protocol.SecretNamespace("test-tenant", "test-project", "kdcube-runtime")
    reference = protocol.SecretReference(namespace, "platform.runtime.custody.__keys")
    request = protocol.VaultRequest.new(protocol.Operation.QUALIFY, reference)
    answer = protocol.VaultResponse.success(request, custody=protocol.custody_qualification(reference)).to_wire()
    with pytest.raises(protocol.VaultError):
        protocol.VaultResponse.from_wire({**answer, field: None})
