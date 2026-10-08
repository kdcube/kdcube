# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Synthetic clone admission, strict conversion, crash replay and native encryption."""
from __future__ import annotations

import json
import os
import traceback
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from kdcube_ai_app.infra.secrets.host_vault import audit, broker, identity, keys, protocol, service, storage
from kdcube_ai_app.infra.secrets.runtime_conversion import (
    CloneReceipt, ConversionError, Inventory, Observation, SourceRecord, convert,
)
from kdcube_ai_app.infra.secrets.runtime_conversion.file_store import CopiedFileVault
from kdcube_ai_app.infra.secrets.runtime_conversion.guard import (
    Admission, JOURNAL, MARKER, MARKER_SCHEMA, read_private, write_private,
)
from kdcube_ai_app.infra.secrets.runtime_conversion.model import canonical, digest, preflight
from kdcube_ai_app.infra.secrets.runtime_vault import RuntimeVaultStore

SCOPE = ("test-tenant", "test-project", "kdcube-runtime")
NAMESPACE = "synthetic-family"
CANARY = "synthetic-only-é-☃"
PIN = "a" * 64


class Parser:
    """Test-only closed family, not a deployed domain's coverage proof."""
    source_sha256 = PIN

    def validate(self, payload, *, namespace, secret_ref, scope):
        if (set(payload) != {"schema", "namespace", "secret_ref", "scope", "expires_at", "payload"}
                or payload["schema"] != "synthetic.family.v1"
                or payload["namespace"] != namespace or payload["secret_ref"] != secret_ref
                or payload["scope"] != list(scope) or type(payload["payload"]) is not str
                or type(payload["expires_at"]) is not int or payload["expires_at"] <= 0):
            raise ValueError(CANARY)
        return payload["expires_at"]


def record(index=1, *, expires_at=4000000000, payload=CANARY):
    secret_ref = f"{index:032x}"
    # Noncanonical whitespace/UTF-8 proves exact inner-byte preservation.
    value = json.dumps({"schema": "synthetic.family.v1", "namespace": NAMESPACE,
                        "secret_ref": secret_ref, "scope": list(SCOPE),
                        "expires_at": expires_at, "payload": payload},
                       ensure_ascii=False, indent=2).encode()
    return SourceRecord(NAMESPACE, secret_ref, 1, "present", value, expires_at)


def inventory(*records):
    return Inventory(SCOPE, tuple(records), ((NAMESPACE, PIN),))


def clone(tmp_path, inputs):
    root = tmp_path / "execution-clone"
    root.mkdir(mode=0o700)
    receipt = CloneReceipt(root, uuid.uuid4().hex, inputs.sha256(), 1,
                           (tmp_path / "sealed",), (tmp_path / "live",),
                           (tmp_path / "live-open",))
    write_private(root, MARKER, {"schema": MARKER_SCHEMA, "clone_id": receipt.clone_id,
                                "inventory_sha256": receipt.inventory_sha256,
                                "attempt": receipt.attempt, "state": "ready"})
    return receipt


class MemoryStore:
    def __init__(self, records):
        self.records = {r.key: Observation(r.generation, r.state, r.value) for r in records}
        self.writes = []

    def read(self, source):
        return self.records[source.key]

    def replace(self, source, value):
        if self.records[source.key].generation != source.generation:
            raise ValueError(CANARY)
        self.writes.append(source.key)
        self.records[source.key] = Observation(source.generation + 1, "present", value)
        return source.generation + 1


def invoke(receipt, inputs, store, **kwargs):
    return convert(receipt=receipt, inventory=inputs, parsers={NAMESPACE: Parser()},
                   store_factory=lambda root: store, **kwargs)


@pytest.mark.parametrize("failure", [
    "missing-marker", "wrong-id", "wrong-pin", "wrong-attempt", "unknown-state", "marker-extra",
    "marker-mode", "marker-link", "root-mode", "root-link", "root-relative", "live-equal",
    "live-child", "live-parent", "sealed-equal", "open-path", "empty-exclusion", "internal-link",
    "internal-hardlink", "internal-fifo", "candidate", "orphan-receipt", "lock-link", "lock-mode",
])
def test_wrong_target_refused_before_store_or_root_key_reads(tmp_path, failure):
    inputs = inventory(record())
    receipt = clone(tmp_path, inputs)
    root = receipt.target
    marker = read_private(root, MARKER)
    if failure == "missing-marker":
        (root / MARKER).unlink()
    elif failure in {"wrong-id", "wrong-pin", "wrong-attempt", "unknown-state", "marker-extra"}:
        field, value = {"wrong-id": ("clone_id", "b" * 32), "wrong-pin": ("inventory_sha256", "b" * 64),
                        "wrong-attempt": ("attempt", True), "unknown-state": ("state", "invalid"),
                        "marker-extra": ("extra", "unknown")}[failure]
        write_private(root, MARKER, {**marker, field: value})
    elif failure == "marker-mode":
        (root / MARKER).chmod(0o644)
    elif failure == "marker-link":
        (root / MARKER).rename(root / "other")
        (root / MARKER).symlink_to(root / "other")
    elif failure == "root-mode":
        root.chmod(0o755)
    elif failure == "root-link":
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
        receipt = replace(receipt, target=alias)
    elif failure == "root-relative":
        receipt = replace(receipt, target=Path("."))
    elif failure in {"live-equal", "live-child", "live-parent"}:
        excluded = {"live-equal": root, "live-child": root.parent, "live-parent": root / "descendant"}[failure]
        receipt = replace(receipt, live_roots=(excluded,))
    elif failure == "sealed-equal":
        receipt = replace(receipt, sealed_roots=(root,))
    elif failure == "open-path":
        receipt = replace(receipt, live_open_paths=(root / "key",))
    elif failure == "empty-exclusion":
        receipt = replace(receipt, live_open_paths=())
    elif failure == "internal-link":
        (root / "store").symlink_to(tmp_path / "live")
    elif failure == "internal-hardlink":
        os.link(root / MARKER, root / "hardlink")
    elif failure == "internal-fifo":
        os.mkfifo(root / "fifo", mode=0o600)
    elif failure in {"candidate", "orphan-receipt"}:
        name = "record.candidate" if failure == "candidate" else ".conversion-orphan.tmp"
        (root / name).touch(mode=0o600)
    elif failure == "lock-link":
        (root / ".runtime-conversion.lock").symlink_to(root / MARKER)
    elif failure == "lock-mode":
        (root / ".runtime-conversion.lock").touch(mode=0o644)
    calls = []
    with pytest.raises(ConversionError):
        convert(receipt=receipt, inventory=inputs, parsers={NAMESPACE: Parser()},
                store_factory=lambda root: calls.append(root))
    assert calls == []
    assert root.exists()  # Never delete an arbitrary rejected target.


@pytest.mark.parametrize("failure", ["duplicate", "unknown-family", "parser-pin", "schema", "scope",
    "reference", "namespace", "expiry", "expiry-bool", "payload-type", "unknown-field",
    "duplicate-json", "invalid-utf8", "json-list", "nan", "oversize", "generation-bool",
    "tombstone-value", "absent-generation", "empty-inventory"])
def test_whole_inventory_preflight_has_zero_key_reads_or_writes(tmp_path, failure):
    first, second = record(), record(2)
    inputs = inventory(first, second)
    if failure == "duplicate":
        inputs = inventory(first, first)
    elif failure == "unknown-family":
        inputs = inventory(first, replace(second, namespace="uncovered"))
    elif failure == "parser-pin":
        inputs = replace(inputs, parser_pins=((NAMESPACE, "b" * 64),))
    elif failure in {"schema", "scope", "reference", "namespace", "expiry", "expiry-bool",
                     "payload-type", "unknown-field"}:
        payload = json.loads(second.value)
        field, value = {"schema": ("schema", "unknown"), "scope": ("scope", ["other"]),
                        "reference": ("secret_ref", "b" * 32), "namespace": ("namespace", "other"),
                        "expiry": ("expires_at", second.expires_at + 1),
                        "expiry-bool": ("expires_at", True), "payload-type": ("payload", True),
                        "unknown-field": ("unknown", 1)}[failure]
        payload[field] = value
        inputs = inventory(first, replace(second, value=canonical(payload)))
    elif failure in {"duplicate-json", "invalid-utf8", "json-list", "nan"}:
        bad = {"duplicate-json": b'{"schema":1,"schema":2}', "invalid-utf8": b'\xff',
               "json-list": b'[]', "nan": b'{"x":NaN}'}[failure]
        inputs = inventory(first, replace(second, value=bad))
    elif failure == "oversize":
        # Inner fits; JSON-string quoting makes the actual wrapper exceed 64 KiB.
        second = record(2, payload="x" * 65100)
        assert len(second.value) <= protocol.MAX_VALUE_BYTES
        inputs = inventory(first, second)
    elif failure == "generation-bool":
        inputs = inventory(first, replace(second, generation=True))
    elif failure == "tombstone-value":
        inputs = inventory(first, replace(second, state="tombstone", expires_at=None))
    elif failure == "absent-generation":
        inputs = inventory(first, replace(second, state="absent", value=None, expires_at=None))
    elif failure == "empty-inventory":
        inputs = inventory()
    receipt = clone(tmp_path, inputs)
    calls = []
    with pytest.raises(ConversionError, match="^runtime_conversion_discard_clone_required$") as error:
        convert(receipt=receipt, inventory=inputs, parsers={NAMESPACE: Parser()},
                store_factory=lambda root: calls.append(root))
    assert calls == []
    assert read_private(receipt.target, MARKER)["state"] == "invalid"
    assert CANARY not in "".join(traceback.format_exception(error.value))


def test_exact_inner_bytes_deadlines_absence_and_tombstones(tmp_path):
    first, expired = record(), record(2, expires_at=1)
    absent = SourceRecord(NAMESPACE, f"{3:032x}", 0, "absent")
    tombstone = SourceRecord(NAMESPACE, f"{4:032x}", 2, "tombstone")
    inputs = inventory(first, expired, absent, tombstone)
    receipt = clone(tmp_path, inputs)
    store = MemoryStore(inputs.records)
    result = invoke(receipt, inputs, store)
    assert (result.converted, result.replayed, result.preserved, result.attempt) == (2, 0, 2, 1)
    for original in (first, expired):
        wrapped = json.loads(store.read(original).value)
        assert wrapped["value"].encode() == original.value
        assert wrapped["expires_at"] == original.expires_at
        assert wrapped["secret_ref"] == original.secret_ref
    assert store.read(absent) == Observation(0, "absent")
    assert store.read(tombstone) == Observation(2, "tombstone")
    assert invoke(receipt, inputs, store).replayed == 2
    assert len(store.writes) == 2
    progress = (receipt.target / JOURNAL).read_bytes()
    assert CANARY.encode() not in progress and first.secret_ref.encode() not in progress


class Crash(BaseException):
    pass


def test_crash_after_replace_before_journal_replays_once(tmp_path):
    inputs = inventory(record(), record(2))
    receipt = clone(tmp_path, inputs)
    store = MemoryStore(inputs.records)
    def crash():
        raise Crash
    with pytest.raises(Crash):
        invoke(receipt, inputs, store, _after_replace=crash)
    assert read_private(receipt.target, MARKER)["state"] == "running"
    assert read_private(receipt.target, JOURNAL) is None
    result = invoke(receipt, inputs, store)
    assert (result.converted, result.replayed) == (1, 1)
    assert len(store.writes) == 2
    assert invoke(receipt, inputs, store).replayed == 2


@pytest.mark.parametrize("failure", ["different-source", "different-generation", "bool-generation",
    "cas-conflict", "bad-ack", "bad-readback", "exception", "operator-abort",
    "journal-unknown", "journal-bool", "inventory-pin", "replay-value", "replay-generation"])
def test_conflict_or_ambiguous_abort_invalidates_clone_without_repair(tmp_path, failure):
    inputs = inventory(record(), record(2))
    receipt = clone(tmp_path, inputs)
    store = MemoryStore(inputs.records)
    second = inputs.records[1]
    if failure in {"different-source", "different-generation", "bool-generation"}:
        value = b"different" if failure == "different-source" else second.value
        generation = 2 if failure == "different-generation" else True if failure == "bool-generation" else 1
        store.records[second.key] = Observation(generation, "present", value)
    elif failure in {"cas-conflict", "bad-ack", "bad-readback", "exception", "operator-abort"}:
        original_replace = store.replace
        def failing_replace(source, value):
            if failure in {"cas-conflict", "exception"}:
                raise ValueError(CANARY)
            if failure == "operator-abort":
                raise KeyboardInterrupt
            generation = original_replace(source, value)
            if failure == "bad-readback":
                store.records[source.key] = Observation(generation, "present", b"corrupt")
            return generation + 1 if failure == "bad-ack" else generation
        store.replace = failing_replace
    elif failure in {"journal-unknown", "journal-bool"}:
        expected = preflight(inputs, {NAMESPACE: Parser()})[0]
        entry = {"generation": 2, "wrapper_sha256": digest(expected.wrapper),
                 "inner_sha256": digest(expected.source.value), "expires_at": expected.source.expires_at}
        if failure == "journal-bool":
            entry["generation"] = True
        completed = {"unknown" if failure == "journal-unknown" else digest(expected.source.key.encode()): entry}
        write_private(receipt.target, JOURNAL, {"schema": "kdcube.runtime_conversion_progress.v1",
                      "clone_id": receipt.clone_id, "attempt": receipt.attempt,
                      "inventory_sha256": inputs.sha256(), "completed": completed})
    elif failure == "inventory-pin":
        inputs = inventory(record(3))
    elif failure in {"replay-value", "replay-generation"}:
        invoke(receipt, inputs, store)
        current = store.read(second)
        store.records[second.key] = replace(current, value=b"corrupt") if failure == "replay-value" else replace(current, generation=3)
    with pytest.raises(ConversionError, match="^runtime_conversion_discard_clone_required$") as error:
        invoke(receipt, inputs, store)
    assert read_private(receipt.target, MARKER)["state"] == "invalid"
    count = len(store.writes)
    with pytest.raises(ConversionError, match="^runtime_conversion_target_refused$"):
        invoke(receipt, inputs, store)
    assert len(store.writes) == count
    if failure in {"different-source", "different-generation", "bool-generation", "journal-unknown", "journal-bool", "inventory-pin"}:
        assert count == 0  # Even the valid first record is not replaced.
    assert CANARY not in "".join(traceback.format_exception(error.value))


def test_target_identity_replacement_and_concurrent_run_refused(tmp_path):
    inputs = inventory(record())
    receipt = clone(tmp_path, inputs)
    admission = Admission(receipt)
    with admission.locked():
        with pytest.raises(ConversionError, match="^runtime_conversion_already_running$"):
            with Admission(receipt).locked():
                pytest.fail("second run admitted")
        root = receipt.target
        root.rename(tmp_path / "old-clone")
        root.mkdir(mode=0o700)
        write_private(root, MARKER, read_private(tmp_path / "old-clone", MARKER))
        with pytest.raises(ConversionError, match="^runtime_conversion_target_refused$"):
            admission.check()


class Transport(broker.VaultTransport):
    def __init__(self, vault, cert):
        self.vault, self.cert = vault, cert

    def call(self, request):
        return self.vault.handle(request.to_wire(), peer_cert_pem=self.cert)


def native_clone(tmp_path, inputs):
    receipt = clone(tmp_path, inputs)
    root = receipt.target
    provider = keys.FileRootKeyProvider(root / "root-keys")
    provider.rotate()  # New SYNTHETIC key only. Converter never calls rotate.
    (root / "root-keys" / "CURRENT").chmod(0o600)
    store = storage.FileDurableSecretStore(root / "store", provider)
    scope = protocol.SecretNamespace(*SCOPE)
    for source in inputs.records:
        reference = protocol.SecretReference.derive(namespace=scope, internal_key=source.key)
        if source.state != "absent":
            store.put(reference, source.value or b"synthetic-deleted", expected_generation=0)
            if source.state == "tombstone":
                store.delete(reference, expected_generation=1)
    return receipt, provider, store, scope


def private_copy_modes(root):
    # The copy owner normalizes the writable CLONE, never the live vault.
    # Existing store fan-out directories can be 0755 under a private root.
    for path in root.rglob("*"):
        if path.is_dir():
            path.chmod(0o700)
        elif not path.name.endswith(".key"):
            path.chmod(0o600)


def test_native_encrypted_clone_preserves_ordinary_key_and_expired_runtime(tmp_path):
    first, expired = record(), record(2, expires_at=1)
    absent = SourceRecord(NAMESPACE, f"{3:032x}", 0, "absent")
    tombstone = SourceRecord(NAMESPACE, f"{4:032x}", 2, "tombstone")
    inputs = inventory(first, expired, absent, tombstone)
    receipt, provider, store, scope = native_clone(tmp_path, inputs)
    ordinary = protocol.SecretReference.derive(namespace=scope, internal_key="ordinary.synthetic")
    store.put(ordinary, b"synthetic-ordinary", expected_generation=0)
    private_copy_modes(receipt.target)
    ordinary_digest = digest(store._path(ordinary).read_bytes())
    factory = lambda root: CopiedFileVault(root, scope=SCOPE)
    result = convert(receipt=receipt, inventory=inputs, parsers={NAMESPACE: Parser()}, store_factory=factory)
    assert (result.converted, result.preserved) == (2, 2)
    assert digest(store._path(ordinary).read_bytes()) == ordinary_digest
    assert store.get(ordinary)[1] == b"synthetic-ordinary"
    assert factory(receipt.target).read(tombstone) == Observation(2, "tombstone")
    assert convert(receipt=receipt, inventory=inputs, parsers={NAMESPACE: Parser()}, store_factory=factory).replayed == 2
    ca = identity.HostIssuingCA.generate()
    registry = identity.TrustRegistry(tmp_path / "synthetic-trust.json", ca=ca)
    ticket = registry.mint_ticket(deployment_id="synthetic-deployment", namespaces=[scope.path])
    deployment_key = identity.DeploymentKey.generate()
    cert, _ = registry.enroll(ticket_id=ticket.ticket_id, csr_pem=deployment_key.csr())
    restarted = storage.FileDurableSecretStore(receipt.target / "store", provider)
    vault = service.HostVaultService(store=restarted, registry=registry, audit=audit.MemoryAuditSink())
    native = broker.SecretsBroker(transport=Transport(vault, cert), tenant=SCOPE[0], project=SCOPE[1])
    runtime = RuntimeVaultStore(broker=native, application=SCOPE[2], namespace=NAMESPACE,
                                authorized_namespaces=(NAMESPACE,))
    assert runtime.get(secret_ref=first.secret_ref).encode() == first.value
    assert runtime.get(secret_ref=expired.secret_ref) is None
    assert runtime.get(secret_ref=absent.secret_ref) is None
    assert runtime.get(secret_ref=tombstone.secret_ref) is None


def test_native_encrypted_restart_recovers_post_commit_pre_journal_crash(tmp_path):
    inputs = inventory(record())
    receipt, provider, store, scope = native_clone(tmp_path, inputs)
    private_copy_modes(receipt.target)
    factory = lambda root: CopiedFileVault(root, scope=SCOPE)
    def crash():
        raise Crash
    with pytest.raises(Crash):
        convert(receipt=receipt, inventory=inputs, parsers={NAMESPACE: Parser()},
                store_factory=factory, _after_replace=crash)
    replay = convert(receipt=receipt, inventory=inputs, parsers={NAMESPACE: Parser()}, store_factory=factory)
    assert (replay.converted, replay.replayed) == (0, 1)
    reference = protocol.SecretReference.derive(namespace=scope, internal_key=inputs.records[0].key)
    assert store.get(reference)[0].generation == 2
