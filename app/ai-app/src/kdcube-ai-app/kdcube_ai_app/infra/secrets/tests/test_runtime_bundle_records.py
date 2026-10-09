# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""W670: runtime records belong to their owner bundle, one file per secret, no lock.

Operator, 2026-10-09: "but this is wrong because these secrets belong to bunlde" / "it musy be subfolder";
"NO!" to one file per purpose; "nolock". File layout:
``<root>/<owner bundle or platform>/<purpose>/<ref>.json``. Other backends carry the owner as one key
segment; a bundle-less (platform) record keeps its existing key there.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

import pytest

from kdcube_ai_app.infra.secrets import runtime_file
from kdcube_ai_app.infra.secrets.ephemeral import ephemeral_secret_store
from kdcube_ai_app.infra.secrets.manager import (
    AwsSecretsManagerSecretsManager, InMemorySecretsManager, SecretsFileSecretsManager, SecretsManagerConfig,
    SecretsManagerError, SecretsServiceSecretsManager,
)
from kdcube_ai_app.infra.secrets.runtime_file import RuntimeFileError, RuntimeFileStore

HUB = "connection-hub@1-0"
OTHER = "task-and-memo-app@1-0"
TOKENS, CARDS, LOGIN = "oauth-refresh-tokens", "card-credentials", "login-attempts"
REF = "c" * 32


def _manager(tmp_path):
    descriptor = tmp_path / "config" / "bundles.secrets.yaml"
    descriptor.parent.mkdir(exist_ok=True)
    descriptor.write_text("bundles: {version: '1', items: []}\n")
    return SecretsFileSecretsManager(SecretsManagerConfig(
        provider="secrets-file", component="proc", bundle_secrets_yaml=descriptor.as_uri(),
        runtime_secrets_root=str(tmp_path / "config" / "secrets"),
        runtime_secret_namespaces=(TOKENS, CARDS, LOGIN),
    ))


def _files(root):
    return sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file())


@pytest.mark.asyncio
async def test_records_land_in_owner_and_purpose_folders(tmp_path):
    manager, expiry = _manager(tmp_path), int(time.time()) + 60
    root = tmp_path / "config" / "secrets"
    for owner, purpose, ref in ((HUB, TOKENS, "1" * 32), (HUB, CARDS, "2" * 32), (None, LOGIN, "3" * 32)):
        store = ephemeral_secret_store(namespace=purpose, manager=manager, bundle_id=owner)
        assert await store.create(secret_ref=ref, value=f"synthetic-{purpose}", expires_at=expiry)
    assert _files(root) == [
        f"{HUB}/{CARDS}/{'2' * 32}.json", f"{HUB}/{TOKENS}/{'1' * 32}.json", f"platform/{LOGIN}/{'3' * 32}.json",
    ]
    for folder in (root, root / HUB, root / HUB / TOKENS, root / "platform", root / "platform" / LOGIN):
        assert folder.stat().st_mode & 0o777 == 0o700
    record = root / HUB / TOKENS / f"{'1' * 32}.json"
    assert record.stat().st_mode & 0o777 == 0o600
    assert json.loads(record.read_text()) == {"value": f"synthetic-{TOKENS}", "expires_at": expiry}
    assert not list(root.rglob("*.lock"))
    # A new process (a new manager) reads the same record.
    fresh = ephemeral_secret_store(namespace=TOKENS, manager=_manager(tmp_path), bundle_id=HUB)
    assert await fresh.get(secret_ref="1" * 32) == f"synthetic-{TOKENS}"
    assert await fresh.qualify_durable_backend() in {True, False}  # the mount rule is persistent_filesystem's


@pytest.mark.asyncio
async def test_owners_and_purposes_are_isolated(tmp_path):
    manager, expiry = _manager(tmp_path), int(time.time()) + 60
    hub_tokens = ephemeral_secret_store(namespace=TOKENS, manager=manager, bundle_id=HUB)
    assert await hub_tokens.create(secret_ref=REF, value="synthetic-hub", expires_at=expiry)
    for owner, purpose in ((OTHER, TOKENS), (None, TOKENS), (HUB, CARDS)):
        other = ephemeral_secret_store(namespace=purpose, manager=manager, bundle_id=owner)
        assert await other.get(secret_ref=REF) is None
        # The same reference is a different record elsewhere; neither replaces the other.
        assert await other.create(secret_ref=REF, value="synthetic-other", expires_at=expiry)
        await other.delete(secret_ref=REF)
        assert await other.purge_expired(now=int(time.time()), limit=10) == 0
    assert await hub_tokens.get(secret_ref=REF) == "synthetic-hub"


@pytest.mark.parametrize("bundle_id", ["", "platform", "..", "../escape", "a/b", "a.b", "/abs", " hub", "a" * 129])
def test_owner_outside_the_bundle_id_rule_is_refused_before_any_file(tmp_path, bundle_id):
    root = tmp_path / "secrets"
    with pytest.raises(RuntimeFileError, match="^runtime_secret_owner_invalid$"):
        RuntimeFileStore(root=root, namespace=TOKENS, authorized_namespaces=(TOKENS,), owner=bundle_id)
    assert not root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("bundle_id", ["../escape", "a.b", "platform"])
async def test_every_backend_refuses_an_invalid_owner(tmp_path, bundle_id):
    expiry = int(time.time()) + 60
    with pytest.raises(SecretsManagerError, match="^runtime_secret_owner_invalid$"):
        await _manager(tmp_path).create_ephemeral_secret(
            namespace=TOKENS, bundle_id=bundle_id, secret_ref=REF, value="v", expires_at=expiry)
    assert not (tmp_path / "config" / "secrets").exists()
    with pytest.raises(SecretsManagerError, match="^runtime_secret_owner_invalid$"):
        await InMemorySecretsManager().create_ephemeral_secret(
            namespace=TOKENS, bundle_id=bundle_id, secret_ref=REF, value="v", expires_at=expiry)
    aws = AwsSecretsManagerSecretsManager(SecretsManagerConfig(
        provider="aws-sm", component="proc", runtime_secret_namespaces=(TOKENS,)))
    with pytest.raises(SecretsManagerError, match="^runtime_secret_owner_invalid$"):
        aws._ephemeral_secret_id(TOKENS, REF, bundle_id)
    assert await aws.qualify_runtime_custody(namespace=TOKENS, bundle_id=bundle_id) is False
    service = SecretsServiceSecretsManager(SecretsManagerConfig(
        provider="secrets-service", component="proc", url="http://synthetic-service",
        token="synthetic-reader", admin_token="synthetic-writer"))
    with pytest.raises(SecretsManagerError, match="^runtime_secret_owner_invalid$"):
        await service.get_ephemeral_secret(namespace=TOKENS, bundle_id=bundle_id, secret_ref=REF)
    assert await service.qualify_runtime_custody(namespace=TOKENS, bundle_id=bundle_id) is False


def test_aws_and_generic_keys_gain_the_owner_and_keep_platform_keys():
    aws = AwsSecretsManagerSecretsManager(SecretsManagerConfig(
        provider="aws-sm", component="proc", aws_sm_prefix="synthetic", runtime_secret_namespaces=(TOKENS,)))
    assert aws._ephemeral_secret_id(TOKENS, REF, HUB) == f"synthetic/runtime/{HUB}/{TOKENS}/{REF}"
    assert aws._ephemeral_secret_id(TOKENS, REF) == f"synthetic/runtime/{TOKENS}/{REF}"
    from kdcube_ai_app.infra.secrets.manager import _ephemeral_inventory_key, _ephemeral_provider_key

    assert _ephemeral_provider_key(TOKENS, REF, HUB) == f"platform.runtime.{HUB}.{TOKENS}.{REF}"
    assert _ephemeral_provider_key(TOKENS, REF) == f"platform.runtime.{TOKENS}.{REF}"
    assert _ephemeral_inventory_key(TOKENS, HUB) == f"platform.runtime.{HUB}.{TOKENS}.__keys"


@pytest.mark.asyncio
async def test_in_memory_owned_and_platform_records_are_separate():
    manager, expiry = InMemorySecretsManager(), int(time.time()) + 60
    owned = ephemeral_secret_store(namespace=TOKENS, manager=manager, bundle_id=HUB)
    platform = ephemeral_secret_store(namespace=TOKENS, manager=manager)
    assert await owned.create(secret_ref=REF, value="synthetic-owned", expires_at=expiry)
    assert await platform.create(secret_ref=REF, value="synthetic-platform", expires_at=expiry)
    assert await owned.get(secret_ref=REF) == "synthetic-owned"
    assert await platform.get(secret_ref=REF) == "synthetic-platform"


@pytest.mark.asyncio
async def test_platform_store_calls_a_manager_without_the_owner_keyword():
    calls = []

    class Legacy(InMemorySecretsManager):
        async def get_ephemeral_secret(self, *, namespace, secret_ref):
            calls.append((namespace, secret_ref))
            return None

    assert await ephemeral_secret_store(namespace=LOGIN, manager=Legacy()).get(secret_ref=REF) is None
    assert calls == [(LOGIN, REF)]


def test_resident_card_records_are_hub_owned_card_credentials(monkeypatch):
    from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.cards import (
        credential_handles,
    )

    calls = []
    monkeypatch.setattr(credential_handles, "ephemeral_secret_store", lambda **kwargs: calls.append(kwargs))
    credential_handles.resident_card_secret_store("synthetic-settings")
    assert calls == [{"namespace": CARDS, "settings": "synthetic-settings", "bundle_id": HUB}]


def _hub_store(tmp_path):
    return RuntimeFileStore(root=tmp_path / "secrets", namespace=TOKENS, authorized_namespaces=(TOKENS,), owner=HUB)


def test_two_threads_creating_one_ref_have_exactly_one_winner(tmp_path):
    expiry, results, barrier = int(time.time()) + 60, [], threading.Barrier(8)

    def create(index):
        barrier.wait()
        results.append((index, _hub_store(tmp_path).create(secret_ref=REF, value=f"synthetic-{index}",
                                                           expires_at=expiry)))

    threads = [threading.Thread(target=create, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    winners = [index for index, created in results if created]
    assert len(winners) == 1
    assert _hub_store(tmp_path).get(secret_ref=REF) == f"synthetic-{winners[0]}"


def _publish_halfway(store, value, expires_at):
    """The state between the publication link and the removal of the temporary name."""
    store._prepare()
    temporary = store._folder / ".record-synthetic"
    temporary.write_text(json.dumps({"value": value, "expires_at": expires_at}))
    temporary.chmod(0o600)
    os.link(temporary, store._path(REF))
    return temporary


def test_a_reader_waits_for_a_record_still_being_published(tmp_path):
    store = _hub_store(tmp_path)
    temporary = _publish_halfway(store, "synthetic-late", int(time.time()) + 60)

    def finish():
        time.sleep(0.1)
        temporary.unlink()

    writer = threading.Thread(target=finish)
    writer.start()
    try:
        assert store.get(secret_ref=REF) == "synthetic-late"
    finally:
        writer.join()


def test_a_crash_before_the_link_leaves_the_reference_free(tmp_path, monkeypatch):
    store = _hub_store(tmp_path)

    def crash(*_args):
        raise OSError("synthetic crash")

    monkeypatch.setattr(runtime_file.os, "link", crash)
    with pytest.raises(RuntimeFileError, match="^runtime_secret_storage_unavailable$"):
        store.create(secret_ref=REF, value="synthetic-first", expires_at=int(time.time()) + 60)
    monkeypatch.undo()
    assert list((tmp_path / "secrets" / HUB / TOKENS).iterdir()) == []
    assert store.get(secret_ref=REF) is None
    assert store.create(secret_ref=REF, value="synthetic-retry", expires_at=int(time.time()) + 60)
    assert store.get(secret_ref=REF) == "synthetic-retry"


def test_a_crash_after_the_link_keeps_the_original_and_never_mints_a_replacement(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_file, "_INCOMPLETE_READ_PAUSE", 0)
    store = _hub_store(tmp_path)
    temporary = _publish_halfway(store, "synthetic-original", int(time.time()) + 60)
    with pytest.raises(RuntimeFileError, match="^runtime_secret_storage_unavailable$"):
        store.get(secret_ref=REF)  # still doubly linked: refused, never read as a single private record
    assert not store.create(secret_ref=REF, value="synthetic-replacement", expires_at=int(time.time()) + 60)
    temporary.unlink()  # the operator's cleanup of the leftover temporary name
    assert store.get(secret_ref=REF) == "synthetic-original"


@pytest.mark.parametrize("content", [b"", b'{"expires_at":9999999999,"value":"synthetic-par', b"{}"])
def test_a_partial_or_malformed_record_is_refused_not_read(tmp_path, content):
    store = _hub_store(tmp_path)
    store._prepare()
    descriptor = os.open(store._path(REF), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(descriptor, content)
    os.close(descriptor)
    with pytest.raises(RuntimeFileError, match="^runtime_secret_storage_unavailable$"):
        store.get(secret_ref=REF)
    assert not store.create(secret_ref=REF, value="synthetic", expires_at=int(time.time()) + 60)


def test_purge_is_bounded_and_skips_tombstones_without_opening_them(tmp_path, monkeypatch):
    clock = [1000]
    monkeypatch.setattr(runtime_file, "time", type("Clock", (), {"time": staticmethod(lambda: clock[0])}))
    store = _hub_store(tmp_path)
    for index in range(5):
        assert store.create(secret_ref=f"{index:032x}", value="synthetic", expires_at=1001)
    assert store.create(secret_ref=REF, value="synthetic-live", expires_at=2000)
    clock[0] = 1001
    assert store.purge_expired(now=1001, limit=3) == 3
    reads = []
    original = store._read
    monkeypatch.setattr(store, "_read", lambda ref: reads.append(ref) or original(ref))
    assert store.purge_expired(now=1001, limit=3) == 2
    assert len(reads) == 3  # the two remaining expired records and the live one; no tombstone is opened
    assert store.purge_expired(now=1001, limit=3) == 0
    assert store.get(secret_ref=REF) == "synthetic-live"
    for index in range(5):
        assert not store.create(secret_ref=f"{index:032x}", value="synthetic", expires_at=3000)
    assert len(list((tmp_path / "secrets" / HUB / TOKENS).iterdir())) == 6


def test_values_never_reach_logs_or_errors(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    store = _hub_store(tmp_path)
    assert store.create(secret_ref=REF, value="synthetic-never-logged", expires_at=int(time.time()) + 60)
    store.get(secret_ref=REF)
    store.delete(secret_ref=REF)
    try:
        store.create(secret_ref=REF, value="synthetic-never-logged", expires_at=1)
    except RuntimeFileError as exc:
        assert "synthetic-never-logged" not in str(exc)
    assert "synthetic-never-logged" not in caplog.text
    assert "synthetic-never-logged" not in (tmp_path / "secrets" / HUB / TOKENS / f"{REF}.json").read_text()


@pytest.mark.parametrize("level", ["root", "owner", "purpose"])
def test_a_symlink_at_any_folder_level_is_refused(tmp_path, level):
    root, elsewhere = tmp_path / "secrets", tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    chain = {"root": root, "owner": root / HUB, "purpose": root / HUB / TOKENS}
    for name in ("root", "owner", "purpose"):
        if name == level:
            chain[name].symlink_to(elsewhere)
            break
        chain[name].mkdir(mode=0o700)
    with pytest.raises(RuntimeFileError, match="^runtime_secret_storage_unavailable$"):
        _hub_store(tmp_path).create(secret_ref=REF, value="synthetic", expires_at=int(time.time()) + 60)
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o700])
def test_a_record_with_a_broad_mode_is_refused(tmp_path, mode):
    store = _hub_store(tmp_path)
    assert store.create(secret_ref=REF, value="synthetic", expires_at=int(time.time()) + 60)
    store._path(REF).chmod(mode)
    with pytest.raises(RuntimeFileError, match="^runtime_secret_storage_unavailable$"):
        store.get(secret_ref=REF)
