# SPDX-License-Identifier: MIT
"""Host-vault protocol, identity, durability, and secret-boundary proofs.

Every identity here is FAKE test material minted in memory by the same X.509
code the host CA uses; no real secret value, key, or deployment identity is
read or written. Most cases use the labeled in-memory root-key fake; custody
cases use generated temporary file keys with synthetic volume classification."""

from __future__ import annotations

import concurrent.futures
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from kdcube_ai_app.infra.secrets.host_vault import (
    audit,
    broker,
    identity,
    keys,
    protocol,
    service,
    storage,
    transport,
)
from kdcube_ai_app.infra.secrets.host_vault.protocol import (
    ErrorCode,
    Operation,
    SecretNamespace,
    SecretReference,
    VaultError,
    VaultRequest,
)

NS = SecretNamespace("demo-tenant", "demo-project", "connection-hub@1-0")
OTHER_APP = SecretNamespace("demo-tenant", "demo-project", "other-app@1-0")
OTHER_PROJECT = SecretNamespace("demo-tenant", "other-project", "connection-hub@1-0")
OTHER_TENANT = SecretNamespace("other-tenant", "demo-project", "connection-hub@1-0")
KEY = "users.u1.bundles.b1.secrets.token"
CANARY = "CANARY-secret-value-9f3a"
ENROLLED = object()  # Rig.call default: the rig's own enrolled certificate


# ── fixtures ──────────────────────────────────────────────────────────────


class Rig:
    """A host CA, trust registry, store, service, and one enrolled deployment."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.ca = identity.HostIssuingCA.generate()
        self.registry = identity.TrustRegistry(root / "trust.json", ca=self.ca)
        self.keys = keys.FakeInMemoryRootKeyProvider()
        self.store = storage.FileDurableSecretStore(root / "store", self.keys)
        self.audit = audit.MemoryAuditSink()
        self.service = service.HostVaultService(store=self.store, registry=self.registry, audit=self.audit)
        self.key = identity.DeploymentKey.generate()
        ticket = self.registry.mint_ticket(deployment_id="dep-1", namespaces=[NS.path])
        self.cert, self.record = self.registry.enroll(ticket_id=ticket.ticket_id, csr_pem=self.key.csr())

    def request(self, op: Operation, ns: SecretNamespace = NS, key: str = KEY, **kw) -> VaultRequest:
        ref = None if op is Operation.HEALTH else SecretReference(ns, key)
        return VaultRequest.new(op, ref, **kw)

    def call(self, request: VaultRequest, *, cert: Any = ENROLLED, now: float | None = None):
        peer = self.cert if cert is ENROLLED else cert
        return self.service.handle(request.to_wire(), peer_cert_pem=peer, now=now)


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


# 1. exact namespace authorization ─────────────────────────────────────────


def test_namespace_authorization_is_exact(rig: Rig):
    assert rig.call(rig.request(Operation.SET, value=CANARY)).ok
    assert rig.call(rig.request(Operation.GET)).value == CANARY
    for ns in (OTHER_APP, OTHER_PROJECT, OTHER_TENANT):
        response = rig.call(rig.request(Operation.GET, ns))
        assert response.ok is False and response.code is ErrorCode.FORBIDDEN, ns.path
        assert response.value is None
    # a wildcard ACL covers the project, still not another tenant
    ticket = rig.registry.mint_ticket(deployment_id="dep-wild", namespaces=["demo-tenant/demo-project/*"])
    key2 = identity.DeploymentKey.generate()
    cert2, _ = rig.registry.enroll(ticket_id=ticket.ticket_id, csr_pem=key2.csr())
    assert rig.call(rig.request(Operation.GET, OTHER_APP), cert=cert2).code is ErrorCode.NOT_FOUND
    assert rig.call(rig.request(Operation.GET, OTHER_TENANT), cert=cert2).code is ErrorCode.FORBIDDEN


def test_inventory_lists_only_live_names_in_the_exact_prefix(rig: Rig):
    prefix = "users.u1.bundles.b1.secrets."
    first = f"{prefix}token"
    second = f"{prefix}refresh_token"
    outside = "users.u1.bundles.other.secrets.token"
    selector = f"{prefix}__keys"

    assert rig.call(rig.request(Operation.SET, key=first, value=CANARY)).ok
    assert rig.call(rig.request(Operation.SET, key=second, value="second")).ok
    assert rig.call(rig.request(Operation.SET, key=outside, value="outside")).ok

    listed = rig.call(rig.request(Operation.LIST, key=selector))
    assert listed.ok and listed.extra["names"] == sorted([first, second])
    assert rig.call(rig.request(Operation.LIST, OTHER_TENANT, selector)).code is (
        ErrorCode.FORBIDDEN
    )

    assert rig.call(rig.request(Operation.DELETE, key=second)).ok
    assert rig.call(rig.request(Operation.LIST, key=selector)).extra["names"] == [
        first
    ]

    serialized_records = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (rig.root / "store").rglob("*.json")
    )
    assert first not in serialized_records
    assert second not in serialized_records
    assert CANARY not in serialized_records


def test_reference_accepts_longest_management_bundle_provider_key() -> None:
    bundle_id = "b" * 256
    secret_key = "k" * 512
    provider_key = f"bundles.{bundle_id}.secrets.{secret_key}"

    reference = SecretReference(NS, provider_key)

    assert reference.name == provider_key
    assert len(provider_key) < protocol.MAX_NAME_CHARS


def test_legacy_record_without_encrypted_name_remains_readable(rig: Rig):
    prefix = "users.u1.bundles.b1.secrets."
    key = f"{prefix}legacy"
    selector = f"{prefix}__keys"
    assert rig.call(rig.request(Operation.SET, key=key, value=CANARY)).ok

    path = next((rig.root / "store").rglob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("sealed_name")
    payload.pop("integrity")
    payload["integrity"] = storage.FileDurableSecretStore._digest(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert rig.call(rig.request(Operation.GET, key=key)).value == CANARY
    assert rig.call(rig.request(Operation.LIST, key=selector)).extra["names"] == []


def test_broker_validates_inventory_response(rig: Rig):
    prefix = "users.u1.bundles.b1.secrets."
    key = f"{prefix}token"
    selector = f"{prefix}__keys"
    transport_ = _Direct(rig)
    target = broker.SecretsBroker(
        transport=transport_,
        tenant="demo-tenant",
        project="demo-project",
    )

    assert target.set(application="connection-hub@1-0", key=key, value=CANARY).ok
    listed = target.list_names(
        application="connection-hub@1-0",
        metadata_key=selector,
    )

    assert listed.ok
    assert listed.names == (key,)


# 2. certificate identity and live ACL ─────────────────────────────────────


def test_only_enrolled_certificates_identify_a_deployment(rig: Rig):
    stranger_ca = identity.HostIssuingCA.generate()
    stranger_key = identity.DeploymentKey.generate()
    stranger_cert = stranger_ca.issue(stranger_key.csr(), deployment_id="dep-1")  # same id, other CA
    response = rig.call(rig.request(Operation.HEALTH), cert=stranger_cert)
    assert response.code is ErrorCode.UNAUTHENTICATED
    assert rig.call(rig.request(Operation.HEALTH), cert=None).code is ErrorCode.UNAUTHENTICATED
    # the CSR's requested subject is not trusted: the host assigns the id
    ok = rig.call(rig.request(Operation.HEALTH))
    assert ok.ok and ok.extra["deployment_id"] == "dep-1"


def test_enrollment_ticket_is_one_use_and_expires(rig: Rig):
    ticket = rig.registry.mint_ticket(deployment_id="dep-2", namespaces=[NS.path])
    key = identity.DeploymentKey.generate()
    rig.registry.enroll(ticket_id=ticket.ticket_id, csr_pem=key.csr())
    with pytest.raises(VaultError) as exc:
        rig.registry.enroll(ticket_id=ticket.ticket_id, csr_pem=key.csr())
    assert exc.value.code is ErrorCode.UNAUTHENTICATED
    stale = rig.registry.mint_ticket(deployment_id="dep-3", namespaces=[NS.path], ttl_seconds=-1)
    with pytest.raises(VaultError):
        rig.registry.enroll(ticket_id=stale.ticket_id, csr_pem=key.csr())


# 3. revoked and expired identity denial ───────────────────────────────────


def test_revoked_and_expired_identities_are_denied(rig: Rig):
    rig.call(rig.request(Operation.SET, value=CANARY))
    expired_at = rig.record.not_after + 1
    assert rig.call(rig.request(Operation.GET), now=expired_at).code is ErrorCode.UNAUTHENTICATED
    rig.registry.revoke(rig.record.fingerprint)
    assert rig.call(rig.request(Operation.GET)).code is ErrorCode.UNAUTHENTICATED


def test_revocation_by_another_process_lands_on_the_next_identification(rig: Rig):
    operator_view = identity.TrustRegistry(rig.root / "trust.json", ca=rig.ca)
    server_view = identity.TrustRegistry(rig.root / "trust.json")  # identify-only: no CA key
    assert server_view.identify(rig.cert).deployment_id == "dep-1"
    time.sleep(0.01)
    operator_view.revoke(rig.record.fingerprint)
    with pytest.raises(VaultError) as exc:
        server_view.identify(rig.cert)
    assert exc.value.code is ErrorCode.UNAUTHENTICATED
    with pytest.raises(VaultError):  # an identify-only registry cannot issue
        server_view.mint_ticket(deployment_id="x", namespaces=[NS.path])
        server_view.enroll(ticket_id="nope", csr_pem=b"")


def test_rotation_overlaps_then_the_old_certificate_lapses(rig: Rig):
    new_key = identity.DeploymentKey.generate()
    new_cert, new_record = rig.registry.rotate(
        current_fingerprint=rig.record.fingerprint, csr_pem=new_key.csr(), overlap_seconds=60,
    )
    assert new_record.supersedes == rig.record.fingerprint
    assert rig.call(rig.request(Operation.HEALTH), cert=rig.cert).ok  # still inside overlap
    assert rig.call(rig.request(Operation.HEALTH), cert=new_cert).ok
    later = time.time() + 120
    assert rig.call(rig.request(Operation.HEALTH), cert=rig.cert, now=later).code is ErrorCode.UNAUTHENTICATED
    assert rig.call(rig.request(Operation.HEALTH), cert=new_cert, now=later).ok
    # the registry survives a restart with the same trust decisions
    reloaded = identity.TrustRegistry(rig.root / "trust.json", ca=rig.ca)
    assert reloaded.identify(new_cert).deployment_id == "dep-1"


# 4. replay / idempotency ──────────────────────────────────────────────────


def test_replayed_mutation_returns_the_original_result_without_a_second_commit(rig: Rig):
    request = rig.request(Operation.SET, value=CANARY)
    first = rig.call(request)
    again = rig.call(request)
    assert first.ok and again.ok and first.generation == again.generation == 1
    # same request id, different body -> rejected, nothing committed
    forged = VaultRequest(
        operation=Operation.SET, reference=request.reference, request_id=request.request_id,
        issued_at=request.issued_at, value="tampered",
    )
    assert rig.call(forged).code is ErrorCode.REPLAY_REJECTED
    assert rig.call(rig.request(Operation.GET)).value == CANARY
    stale = VaultRequest(operation=Operation.GET, reference=request.reference, request_id="stale-request-1",
                         issued_at=time.time() - 3600)
    assert rig.call(stale).code is ErrorCode.REPLAY_REJECTED


def test_concurrent_replay_cannot_enter_the_store_twice(
    rig: Rig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = rig.request(Operation.SET, value=CANARY)
    original_put = rig.store.put
    first_entered = threading.Event()
    release_first = threading.Event()
    count_lock = threading.Lock()
    calls = 0

    def delayed_put(*args, **kwargs):
        nonlocal calls
        with count_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_entered.set()
            assert release_first.wait(timeout=5)
        return original_put(*args, **kwargs)

    monkeypatch.setattr(rig.store, "put", delayed_put)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(rig.call, request)
        assert first_entered.wait(timeout=5)
        second = executor.submit(rig.call, request)
        try:
            time.sleep(0.05)
            assert not second.done()
        finally:
            release_first.set()
        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)

    assert calls == 1
    assert first_result.generation == second_result.generation == 1


def test_replay_receipt_is_explicitly_process_lifetime(rig: Rig) -> None:
    request = rig.request(Operation.SET, value=CANARY)
    assert rig.call(request).generation == 1

    restarted = service.HostVaultService(
        store=rig.store,
        registry=rig.registry,
        audit=rig.audit,
    )
    repeated = restarted.handle(request.to_wire(), peer_cert_pem=rig.cert)

    assert repeated.ok
    assert repeated.generation == 2


# 5. atomic set / replace / delete and generation conflicts ────────────────


def test_generations_guard_concurrent_replacement(rig: Rig):
    assert rig.call(rig.request(Operation.SET, value="v1")).generation == 1
    assert rig.call(rig.request(Operation.SET, value="v2", expected_generation=1)).generation == 2
    stale = rig.call(rig.request(Operation.SET, value="v3", expected_generation=1))
    assert stale.code is ErrorCode.CONFLICT
    assert rig.call(rig.request(Operation.GET)).value == "v2"
    assert rig.call(rig.request(Operation.ROTATE, value="v4", expected_generation=2)).generation == 3
    assert rig.call(rig.request(Operation.ROTATE, ns=NS, key="never-set", value="x")).code is ErrorCode.NOT_FOUND
    assert rig.call(rig.request(Operation.DELETE, expected_generation=2)).code is ErrorCode.CONFLICT
    assert rig.call(rig.request(Operation.DELETE, expected_generation=3)).generation == 4
    assert rig.call(rig.request(Operation.GET)).code is ErrorCode.NOT_FOUND
    assert rig.call(rig.request(Operation.DELETE)).code is ErrorCode.NOT_FOUND
    assert rig.call(rig.request(Operation.SET, value="v5")).generation == 5  # sequence continues past the tombstone


# 6. crash between candidate write and commit ─────────────────────────────


def test_crash_before_commit_preserves_the_previous_value(rig: Rig, monkeypatch):
    rig.call(rig.request(Operation.SET, value="committed"))

    def crash() -> None:
        raise OSError("disk pulled")

    monkeypatch.setattr(rig.store, "_commit_hook", crash)
    response = rig.call(rig.request(Operation.SET, value="never"))
    assert response.code is ErrorCode.BACKEND_UNAVAILABLE
    monkeypatch.undo()
    assert list(rig.root.rglob("*.candidate")), "the aborted candidate is on disk"
    restarted = storage.FileDurableSecretStore(rig.root / "store", rig.keys)  # recovery on start
    assert not list(rig.root.rglob("*.candidate"))
    record, value = restarted.get(SecretReference(NS, KEY))
    assert value == b"committed" and record.generation == 1


def test_atomic_record_writer_completes_partial_os_writes(
    rig: Rig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_write = storage.os.write
    calls = 0

    def partial_write(fd: int, data: memoryview) -> int:
        nonlocal calls
        calls += 1
        chunk_size = max(1, len(data) // 2)
        return original_write(fd, data[:chunk_size])

    monkeypatch.setattr(storage.os, "write", partial_write)

    response = rig.call(rig.request(Operation.SET, value=CANARY))

    assert response.ok
    assert calls > 1
    assert rig.call(rig.request(Operation.GET)).value == CANARY


# 7. restart preserves committed values and deletions ─────────────────────


def test_restart_preserves_values_and_deletions(rig: Rig):
    rig.call(rig.request(Operation.SET, value=CANARY))
    rig.call(rig.request(Operation.SET, ns=NS, key="second", value="two"))
    rig.call(rig.request(Operation.DELETE, ns=NS, key="second"))
    fresh = storage.FileDurableSecretStore(rig.root / "store", rig.keys)
    fresh_service = service.HostVaultService(store=fresh, registry=identity.TrustRegistry(rig.root / "trust.json", ca=rig.ca))
    assert fresh_service.handle(rig.request(Operation.GET).to_wire(), peer_cert_pem=rig.cert).value == CANARY
    assert fresh_service.handle(rig.request(Operation.GET, key="second").to_wire(), peer_cert_pem=rig.cert).code is ErrorCode.NOT_FOUND


# 8. corruption fails closed ───────────────────────────────────────────────


def _record_path(rig: Rig) -> Path:
    return next(p for p in (rig.root / "store").rglob("*.json"))


@pytest.mark.parametrize("tamper", ["ciphertext", "metadata", "key_version"])
def test_corrupt_records_fail_closed(rig: Rig, tamper: str):
    rig.call(rig.request(Operation.SET, value=CANARY))
    path = _record_path(rig)
    payload = json.loads(path.read_text())
    if tamper == "ciphertext":
        blob = bytearray(__import__("base64").b64decode(payload["sealed"]["ciphertext"]))
        blob[-1] ^= 0x01
        payload["sealed"]["ciphertext"] = __import__("base64").b64encode(bytes(blob)).decode()
        # re-sign integrity so ONLY the AEAD catches it
        payload.pop("integrity")
        payload["integrity"] = storage.FileDurableSecretStore._digest(payload)
    elif tamper == "metadata":
        payload["generation"] = 99  # integrity digest no longer matches
    else:
        payload["sealed"]["root_key_id"] = "fake-999"
        payload.pop("integrity")
        payload["integrity"] = storage.FileDurableSecretStore._digest(payload)
    path.write_text(json.dumps(payload))
    response = rig.call(rig.request(Operation.GET))
    assert response.code is ErrorCode.CORRUPT_RECORD and response.value is None
    assert "fake-999" not in response.message and "disk" not in response.message


# 9. audit records ─────────────────────────────────────────────────────────


def test_audit_records_carry_identity_and_digest_never_secrets(rig: Rig):
    rig.call(rig.request(Operation.SET, value=CANARY))
    rig.call(rig.request(Operation.GET, OTHER_TENANT))
    serialized = json.dumps([event.to_dict() for event in rig.audit.events])
    assert CANARY not in serialized and KEY not in serialized
    set_event = rig.audit.events[0]
    assert set_event.deployment_id == "dep-1"
    assert set_event.fingerprint == rig.record.fingerprint
    assert set_event.operation == "secret.set" and set_event.code == "ok" and set_event.generation == 1
    assert set_event.reference_digest == SecretReference(NS, KEY).digest
    assert set_event.request_id and set_event.time > 0
    denied = rig.audit.events[1]
    assert denied.code == "forbidden" and denied.application == "connection-hub@1-0"


# 10. adversarial exception text is sanitized ─────────────────────────────


def test_backend_exceptions_with_canaries_never_reach_the_caller(rig: Rig, monkeypatch):
    def explode(*args, **kwargs):
        raise OSError(f"/var/lib/kdcube/vault/{CANARY}/store.json: permission denied")

    monkeypatch.setattr(rig.store, "get", explode)
    response = rig.call(rig.request(Operation.GET))
    assert response.code is ErrorCode.BACKEND_UNAVAILABLE
    assert CANARY not in json.dumps(response.to_wire())

    def boom(*args, **kwargs):
        raise RuntimeError(f"root key {CANARY} unwrap failed")

    monkeypatch.setattr(rig.store, "get", boom)
    response = rig.call(rig.request(Operation.GET))
    assert response.code is ErrorCode.INTERNAL and CANARY not in json.dumps(response.to_wire())


# 11. the broker keeps no authoritative value ──────────────────────────────


class _Direct(broker.VaultTransport):
    """Transport that calls the service in-process with the rig's certificate."""

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.calls: list[VaultRequest] = []

    def call(self, request: VaultRequest):
        self.calls.append(request)
        return self.rig.service.handle(request.to_wire(), peer_cert_pem=self.rig.cert)


def test_broker_is_stateless_and_acknowledges_only_committed_writes(rig: Rig, monkeypatch):
    transport_ = _Direct(rig)
    b = broker.SecretsBroker(transport=transport_, tenant="demo-tenant", project="demo-project")
    assert b.set(application="connection-hub@1-0", key=KEY, value=CANARY).ok
    assert b.get(application="connection-hub@1-0", key=KEY) == CANARY
    read = b.read(application="connection-hub@1-0", key=KEY)
    assert read.ok and read.value == CANARY and read.generation == 1
    assert CANARY not in json.dumps({k: str(v) for k, v in vars(b).items()})
    assert b.get(application="other-app@1-0", key=KEY) is None  # forbidden reads as absent
    denied = b.read(application="other-app@1-0", key=KEY)
    assert denied.ok is False and denied.code is ErrorCode.FORBIDDEN
    # a write the store refused is NOT acknowledged
    monkeypatch.setattr(rig.store, "_commit_hook", lambda: (_ for _ in ()).throw(OSError("crash")))
    result = b.set(application="connection-hub@1-0", key=KEY, value="lost")
    assert result.ok is False and result.code is ErrorCode.BACKEND_UNAVAILABLE
    monkeypatch.undo()
    assert b.get(application="connection-hub@1-0", key=KEY) == CANARY
    assert b.delete(application="connection-hub@1-0", key=KEY).ok
    assert b.delete(application="connection-hub@1-0", key=KEY).code is ErrorCode.NOT_FOUND  # settled
    assert b.get(application="connection-hub@1-0", key=KEY) is None
    # references are derived by the broker: the namespace never comes from the key
    assert all(req.reference.namespace.tenant == "demo-tenant" for req in transport_.calls if req.reference)


# 12. root / data-key rotation ─────────────────────────────────────────────


def test_root_key_rotation_rewraps_without_touching_values(rig: Rig):
    rig.call(rig.request(Operation.SET, value=CANARY))
    before = json.loads(_record_path(rig).read_text())
    old_key_id = before["sealed"]["root_key_id"]
    new_key_id = rig.keys.rotate()
    assert new_key_id != old_key_id
    assert rig.call(rig.request(Operation.GET)).value == CANARY  # old version still opens
    assert rig.store.rewrap_all() == 1
    after = json.loads(_record_path(rig).read_text())
    assert after["sealed"]["root_key_id"] == new_key_id
    assert after["sealed_name"]["root_key_id"] == new_key_id
    assert after["sealed"]["ciphertext"] == before["sealed"]["ciphertext"]  # value bytes untouched
    assert after["sealed"]["wrapped_data_key"] != before["sealed"]["wrapped_data_key"]
    assert rig.call(rig.request(Operation.GET)).value == CANARY
    rig.call(rig.request(Operation.SET, ns=NS, key="fresh", value="new"))
    fresh_payload = [json.loads(p.read_text()) for p in (rig.root / "store").rglob("*.json")]
    assert all(row["sealed"]["root_key_id"] == new_key_id for row in fresh_payload if not row["deleted"])


def test_root_key_rotation_skips_records_that_fail_normal_integrity_decode(
    rig: Rig,
) -> None:
    rig.call(rig.request(Operation.SET, value=CANARY))
    path = _record_path(rig)
    payload = json.loads(path.read_text())
    payload["generation"] = 99
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    rig.keys.rotate()

    assert rig.store.rewrap_all() == 0
    assert path.read_bytes() == before
    assert rig.call(rig.request(Operation.GET)).code is ErrorCode.CORRUPT_RECORD


def test_file_root_key_provider_refuses_readable_keys(tmp_path: Path):
    provider = keys.FileRootKeyProvider(tmp_path / "rootkeys")
    key_id = provider.rotate()
    assert provider.current_key_id() == key_id and len(provider.key(key_id)) == 32
    (tmp_path / "rootkeys" / f"{key_id}.key").chmod(0o644)
    with pytest.raises(VaultError) as exc:
        provider.key(key_id)
    assert exc.value.code is ErrorCode.BACKEND_UNAVAILABLE


# 13. no caller bearer is workload proof (over real mTLS) ──────────────────


@pytest.fixture
def served(rig: Rig):
    skey, scert = rig.ca.issue_server(hostnames=["localhost", "127.0.0.1"])
    for name, data in (("server.key", skey), ("server.crt", scert), ("ca.crt", rig.ca.cert_pem)):
        (rig.root / name).write_bytes(data)
    server = transport.HostVaultServer(
        tls=transport.ServerTLS(rig.root / "server.crt", rig.root / "server.key", rig.root / "ca.crt"),
        handler=lambda body, peer: rig.service.handle(body, peer_cert_pem=peer),
    )
    server.serve_in_thread()
    rig.key.write_identity_files(rig.root / "identity", cert_pem=rig.cert, ca_pem=rig.ca.cert_pem)
    yield server
    server.shutdown()


def _client(rig: Rig, server, *, cert="host-vault-client.crt", key="host-vault-client.key",
            directory="identity") -> transport.HostVaultClient:
    host, port = server.address
    tls = transport.ClientTLS(rig.root / directory / cert, rig.root / directory / key,
                              rig.root / directory / "host-vault-ca.crt")
    return transport.HostVaultClient(host=host, port=port, tls=tls, server_hostname="localhost")


def test_mtls_round_trip_and_identity_file_modes(rig: Rig, served):
    assert (rig.root / "identity" / "host-vault-client.key").stat().st_mode & 0o777 == 0o400
    client = _client(rig, served)
    b = broker.SecretsBroker(transport=client, tenant="demo-tenant", project="demo-project")
    assert b.health()["deployment_id"] == "dep-1"
    assert b.set(application="connection-hub@1-0", key=KEY, value=CANARY).ok
    assert b.get(application="connection-hub@1-0", key=KEY) == CANARY


def _disk_custody(rig: Rig, monkeypatch):
    # Real file provider/store and TLS, synthetic filesystem classification.
    # This gate does not attest any deployment mount or host isolation.
    monkeypatch.setattr(storage, "persistent_filesystem", lambda root: True)
    monkeypatch.setattr(keys, "persistent_filesystem", lambda root: True)
    provider = keys.FileRootKeyProvider(rig.root / "rootkeys")
    provider.rotate()
    rig.keys = provider
    rig.store = storage.FileDurableSecretStore(rig.root / "store", provider)
    rig.service._store = rig.store


def test_mtls_custody_qualification_and_original_runtime_record_survive_reconstruction(
        rig: Rig, served, monkeypatch):
    from kdcube_ai_app.infra.secrets.runtime_vault import RuntimeVaultStore

    _disk_custody(rig, monkeypatch)
    b = broker.SecretsBroker(transport=_client(rig, served), tenant=NS.tenant, project=NS.project)
    adapter = RuntimeVaultStore(broker=b, application=NS.application, namespace="custody",
                               authorized_namespaces=("custody",))
    adapter.qualify()
    request = rig.request(Operation.QUALIFY, key="platform.runtime.custody.__keys")
    response = _client(rig, served).call(request)
    assert response.extra == {"custody": protocol.custody_qualification(request.reference)}
    assert response.request_id == request.request_id
    assert response.value is response.generation is None
    secret_ref = "a" * 32
    assert adapter.create(secret_ref=secret_ref, value=CANARY, expires_at=int(time.time()) + 60)

    rig.keys = keys.FileRootKeyProvider(rig.keys._dir)
    rig.store = storage.FileDurableSecretStore(rig.store._root, rig.keys)
    rig.service = service.HostVaultService(store=rig.store, registry=rig.registry, audit=rig.audit)
    fresh = RuntimeVaultStore(broker=b, application=NS.application, namespace="custody",
                             authorized_namespaces=("custody",))
    fresh.qualify()
    assert fresh.get(secret_ref=secret_ref) == CANARY
    assert not fresh.create(secret_ref=secret_ref, value="synthetic-rival", expires_at=int(time.time()) + 60)


@pytest.mark.parametrize("failure", ["memory_keys", "transient_store", "key_mode", "lock_mode",
                                     "foreign_application", "revoked", "wildcard_only"])
def test_mtls_healthy_vault_does_not_qualify_unsafe_or_ungranted_custody(
        rig: Rig, served, monkeypatch, failure):
    from kdcube_ai_app.infra.secrets.runtime_vault import RuntimeVaultError, RuntimeVaultStore

    _disk_custody(rig, monkeypatch)
    application = NS.application
    directory = "identity"
    if failure == "memory_keys":
        rig.service._store = storage.FileDurableSecretStore(rig.store._root, keys.FakeInMemoryRootKeyProvider())
    elif failure == "transient_store":
        monkeypatch.setattr(storage, "persistent_filesystem", lambda root: False)
    elif failure == "key_mode":
        (rig.keys._dir / (rig.keys.current_key_id() + ".key")).chmod(0o644)
    elif failure == "lock_mode":
        (rig.store._root / rig.store.LOCK_NAME).chmod(0o644)
    elif failure == "foreign_application":
        application = OTHER_APP.application
    elif failure == "wildcard_only":
        ticket = rig.registry.mint_ticket(deployment_id="synthetic-wildcard", namespaces=["demo-tenant/demo-project/*"])
        key = identity.DeploymentKey.generate()
        cert, _ = rig.registry.enroll(ticket_id=ticket.ticket_id, csr_pem=key.csr())
        directory = "wildcard-identity"
        key.write_identity_files(rig.root / directory, cert_pem=cert, ca_pem=rig.ca.cert_pem)

    b = broker.SecretsBroker(transport=_client(rig, served, directory=directory), tenant=NS.tenant, project=NS.project)
    assert b.health()["ok"] is True
    adapter = RuntimeVaultStore(broker=b, application=application, namespace="custody",
                               authorized_namespaces=("custody",))
    if failure == "revoked":
        adapter.qualify()
        rig.registry.revoke(rig.record.fingerprint)
    with pytest.raises(RuntimeVaultError, match="^runtime_secret_custody_unqualified$"):
        adapter.qualify()


def test_transport_rejects_non_json_request_content_type(rig: Rig, served):
    connection = _client(rig, served)._connect()
    try:
        connection.request(
            "POST",
            transport.VAULT_PATH,
            body=json.dumps(rig.request(Operation.HEALTH).to_wire()),
            headers={"Content-Type": "text/plain"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
    finally:
        connection.close()

    assert response.status == 400
    assert payload["code"] == ErrorCode.INVALID_REQUEST.value


def test_bearer_without_client_certificate_is_not_workload_proof(rig: Rig, served):
    import http.client
    import ssl

    host, port = served.address
    ctx = ssl.create_default_context(cafile=str(rig.root / "ca.crt"))
    ctx.check_hostname = False
    connection = http.client.HTTPSConnection(host, port, context=ctx, timeout=5)
    body = json.dumps({**rig.request(Operation.GET).to_wire(), "deployment_id": "dep-1", "bearer": "kst1.copied"})
    with pytest.raises((OSError, http.client.HTTPException)):
        connection.request("POST", transport.VAULT_PATH, body=body, headers={
            "Authorization": "Bearer kst1.copied", "X-KDCUBE-ADMIN-TOKEN": "copied",
        })
        connection.getresponse().read()
    # and the service, handed a body that CLAIMS an identity but no peer certificate, refuses too
    response = rig.service.handle(json.loads(body), peer_cert_pem=None)
    assert response.code is ErrorCode.UNAUTHENTICATED


def test_revoked_certificate_fails_on_the_next_connection(rig: Rig, served):
    client = _client(rig, served)
    b = broker.SecretsBroker(transport=client, tenant="demo-tenant", project="demo-project")
    assert b.health()["ok"]
    rig.registry.revoke(rig.record.fingerprint)
    assert b.health() == {"ok": False, "code": "unauthenticated"}


def test_tls_failures_are_sanitized(rig: Rig, served):
    stranger = identity.HostIssuingCA.generate()
    skey = identity.DeploymentKey.generate()
    (rig.root / "identity" / "stranger.crt").write_bytes(stranger.issue(skey.csr(), deployment_id="dep-1"))
    (rig.root / "identity" / "stranger.key").write_bytes(skey.pem)
    client = _client(rig, served, cert="stranger.crt", key="stranger.key")
    with pytest.raises(VaultError) as exc:
        client.call(rig.request(Operation.HEALTH))
    assert exc.value.code is ErrorCode.BACKEND_UNAVAILABLE
    assert "certificate" not in exc.value.message.lower() and "ssl" not in exc.value.message.lower()


# protocol grammar ─────────────────────────────────────────────────────────


def test_reference_grammar_rejects_arbitrary_paths():
    for bad in ("kdv1:demo-tenant/demo-project/app/../x", "kdv1:a/b", "vault:/etc/passwd", "kdv1:demo tenant/p/a/n"):
        with pytest.raises(VaultError) as exc:
            SecretReference.parse(bad)
        assert exc.value.code is ErrorCode.INVALID_REQUEST
    ref = SecretReference.parse("kdv1:demo-tenant/demo-project/connection-hub@1-0/users.u1.secrets.token")
    assert ref.namespace == NS and ref.name == "users.u1.secrets.token"
    assert SecretReference.derive(namespace=NS, internal_key="users.u1.secrets.token") == ref


def test_direct_reference_construction_normalizes_canonical_text() -> None:
    namespace = SecretNamespace(" demo-tenant ", " demo-project ", " app@1-0 ")
    reference = SecretReference(namespace, " services.provider.token ")

    assert namespace.path == "demo-tenant/demo-project/app@1-0"
    assert reference.name == "services.provider.token"
    assert reference.wire == (
        "kdv1:demo-tenant/demo-project/app@1-0/services.provider.token"
    )


def test_values_are_bounded(rig: Rig):
    too_big = "x" * (protocol.MAX_VALUE_BYTES + 1)
    assert rig.call(rig.request(Operation.SET, value=too_big)).code is ErrorCode.TOO_LARGE


def test_list_requires_an_exact_inventory_selector(rig: Rig):
    response = rig.call(rig.request(Operation.LIST, key=KEY))

    assert response.ok is False
    assert response.code is ErrorCode.INVALID_REQUEST
    assert response.extra == {}


@pytest.mark.parametrize(
    "changes",
    [
        {"unknown": "ignored-before"},
        {"request_id": 12345678},
        {"issued_at": True},
        {"issued_at": float("inf")},
        {"expected_generation": True},
        {"expected_generation": -1},
    ],
)
def test_mutation_request_rejects_ambiguous_wire_fields(changes: dict[str, Any]):
    body = VaultRequest.new(
        Operation.SET,
        SecretReference(NS, KEY),
        value=CANARY,
    ).to_wire()
    body.update(changes)

    with pytest.raises(VaultError) as captured:
        VaultRequest.from_wire(body, deployment_id="dep-1")

    assert captured.value.code is ErrorCode.INVALID_REQUEST


@pytest.mark.parametrize(
    "operation,changes",
    [
        (Operation.HEALTH, {"reference": SecretReference(NS, KEY).wire}),
        (Operation.GET, {"value": CANARY}),
        (Operation.GET, {"expected_generation": 0}),
        (Operation.DELETE, {"value": CANARY}),
        (Operation.LIST, {"expected_generation": 0}),
    ],
)
def test_read_request_rejects_operation_inapplicable_fields(
    operation: Operation,
    changes: dict[str, Any],
):
    reference = None
    if operation is not Operation.HEALTH:
        key = "users.u1.bundles.b1.secrets.__keys" if operation is Operation.LIST else KEY
        reference = SecretReference(NS, key)
    body = VaultRequest.new(operation, reference).to_wire()
    body.update(changes)

    with pytest.raises(VaultError) as captured:
        VaultRequest.from_wire(body, deployment_id="dep-1")

    assert captured.value.code is ErrorCode.INVALID_REQUEST


@pytest.mark.parametrize(
    "changes",
    [
        {"ok": "true"},
        {"ok": True, "code": "forbidden"},
        {"generation": True},
        {"names": "not-a-list"},
        {"deployment_id": "not/a/segment"},
        {"unknown": "ignored-before"},
    ],
)
def test_response_rejects_ambiguous_wire_fields(changes: dict[str, Any]):
    response = protocol.VaultResponse.success(
        VaultRequest.new(Operation.HEALTH),
    ).to_wire()
    response.update(changes)

    with pytest.raises(VaultError) as captured:
        protocol.VaultResponse.from_wire(response)

    assert captured.value.code is ErrorCode.INTERNAL


@pytest.mark.parametrize(
    "names",
    [
        ["services.valid.token", "services.valid.token"],
        ["services.valid.token", "services.before.token"],
        ["services...token"],
        ["services.__keys"],
    ],
)
def test_response_rejects_noncanonical_inventory_names(names: list[str]) -> None:
    response = protocol.VaultResponse.success(
        VaultRequest.new(Operation.LIST, SecretReference(NS, "services.__keys")),
        names=names,
    )

    with pytest.raises(VaultError) as captured:
        response.to_wire()

    assert captured.value.code is ErrorCode.INTERNAL


def test_response_extra_cannot_override_protocol_fields() -> None:
    response = protocol.VaultResponse.success(
        VaultRequest.new(Operation.HEALTH),
        protocol="attacker-controlled",
    )

    with pytest.raises(VaultError) as captured:
        response.to_wire()

    assert captured.value.code is ErrorCode.INTERNAL
