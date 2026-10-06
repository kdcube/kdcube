# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Real private files, process restart, writer races, expiry and scope refusal."""
import os
import threading
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from kdcube_ai_app.infra.secrets import runtime_file
from kdcube_ai_app.infra.secrets.runtime_file import RuntimeFileError, RuntimeFileStore
from kdcube_ai_app.infra.secrets.manager import SecretsFileSecretsManager, SecretsManagerConfig, SecretsManagerWriteError

NAMESPACE = "test-custody"
REF = "a" * 32


def _store(root, namespace=NAMESPACE):
    return RuntimeFileStore(root=root, namespace=namespace, authorized_namespaces=(NAMESPACE,))


def test_file_runtime_preserves_original_across_new_instance_and_process(tmp_path):
    root = tmp_path / "runtime"
    expiry = int(time.time()) + 3600
    assert _store(root).create(secret_ref=REF, value="synthetic-original", expires_at=expiry)
    assert not _store(root).create(secret_ref=REF, value="synthetic-other", expires_at=expiry)
    assert _store(root).get(secret_ref=REF) == "synthetic-original"
    code = (
        "from kdcube_ai_app.infra.secrets.runtime_file import RuntimeFileStore; "
        "import sys; s=RuntimeFileStore(root=sys.argv[1], namespace='test-custody', "
        "authorized_namespaces=('test-custody',)); "
        "sys.exit(0 if s.get(secret_ref='a'*32)=='synthetic-original' else 1)"
    )
    result = subprocess.run([sys.executable, "-c", code, str(root)], capture_output=True, timeout=20)
    assert result.returncode == 0
    assert (root / f"{NAMESPACE}.json").stat().st_mode & 0o777 == 0o600
    assert root.stat().st_mode & 0o777 == 0o700


def test_concurrent_processes_create_one_original(tmp_path):
    root = tmp_path / "runtime"
    code = (
        "from kdcube_ai_app.infra.secrets.runtime_file import RuntimeFileStore; "
        "import sys,time; s=RuntimeFileStore(root=sys.argv[1], namespace='test-custody', "
        "authorized_namespaces=('test-custody',)); "
        "sys.exit(0 if s.create(secret_ref='a'*32,value='synthetic-'+sys.argv[2], "
        "expires_at=int(time.time())+3600) else 3)"
    )

    def create(index):
        return subprocess.run([sys.executable, "-c", code, str(root), str(index)],
                              capture_output=True, timeout=20).returncode

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(8)))
    assert results.count(0) == 1
    assert results.count(3) == 7
    assert _store(root).get(secret_ref=REF).startswith("synthetic-")


def test_expiry_reads_and_bounded_purge(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_file, "time", SimpleNamespace(time=lambda: 1000))
    store = _store(tmp_path / "runtime")
    for index in range(3):
        assert store.create(secret_ref=f"{index:032x}", value="synthetic", expires_at=1001)
    assert store.create(secret_ref=REF, value="synthetic-live", expires_at=1003)
    monkeypatch.setattr(runtime_file, "time", SimpleNamespace(time=lambda: 1001))
    assert store.get(secret_ref="0" * 32) is None
    assert store.purge_expired(now=1001, limit=2) == 2
    assert store.purge_expired(now=1001, limit=2) == 1
    assert store.purge_expired(now=1001, limit=2) == 0
    assert store.get(secret_ref=REF) == "synthetic-live"


def test_scope_is_explicit_not_namespace_spelling(tmp_path):
    with pytest.raises(RuntimeFileError, match="runtime_secret_scope_forbidden"):
        _store(tmp_path / "runtime", "different-custody")
    assert list(tmp_path.iterdir()) == []


def test_writer_waiting_on_lock_cannot_create_after_deadline(tmp_path, monkeypatch):
    clock = [1000]
    monkeypatch.setattr(runtime_file, "time", SimpleNamespace(time=lambda: clock[0]))
    store = _store(tmp_path / "runtime")
    ready = threading.Event()
    original_prepare = store._prepare_root

    def prepare():
        original_prepare()
        ready.set()

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with store._locked():
            monkeypatch.setattr(store, "_prepare_root", prepare)
            future = pool.submit(store.create, secret_ref=REF, value="synthetic", expires_at=1001)
            assert ready.wait(timeout=3)
            clock[0] = 1002
        with pytest.raises(RuntimeFileError, match="runtime_secret_expired"):
            future.result(timeout=3)
    finally:
        pool.shutdown(wait=True)
    assert store.get(secret_ref=REF) is None


def test_purge_reads_current_deadline_after_writer_lock(tmp_path, monkeypatch):
    clock = [1000]
    monkeypatch.setattr(runtime_file, "time", SimpleNamespace(time=lambda: clock[0]))
    store = _store(tmp_path / "runtime")
    store.create(secret_ref=REF, value="synthetic-old", expires_at=1001)
    clock[0] = 1001
    ready = threading.Event()
    original_prepare = store._prepare_root

    def prepare():
        original_prepare()
        ready.set()

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with store._locked():
            monkeypatch.setattr(store, "_prepare_root", prepare)
            future = pool.submit(store.purge_expired, now=1001, limit=1)
            assert ready.wait(timeout=3)
            # Emulate a writer replacing an expired generation while holding
            # the same OS lock. Purge must re-read after obtaining that lock.
            store._save({REF: {"value": "synthetic-live", "expires_at": 1003}})
        assert future.result(timeout=3) == 0
    finally:
        pool.shutdown(wait=True)
    assert store.get(secret_ref=REF) == "synthetic-live"


def test_public_or_symlink_storage_is_refused(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o755)
    with pytest.raises(RuntimeFileError, match="runtime_secret_storage_unavailable"):
        _store(root).qualify()
    root.chmod(0o700)
    (root / f"{NAMESPACE}.lock").symlink_to(tmp_path / "unrelated")
    with pytest.raises(RuntimeFileError, match="runtime_secret_storage_unavailable"):
        _store(root).qualify()
    assert not (tmp_path / "unrelated").exists()


@pytest.mark.parametrize("now,limit", [(True, 1), (1002, 1), (1000, True), (1000, 0), (1000, 1001)])
def test_bad_purge_cannot_remove_live_record(tmp_path, monkeypatch, now, limit):
    monkeypatch.setattr(runtime_file, "time", SimpleNamespace(time=lambda: 1000))
    store = _store(tmp_path / "runtime")
    store.create(secret_ref=REF, value="synthetic-live", expires_at=1001)
    with pytest.raises(RuntimeFileError, match="runtime_secret_purge_invalid"):
        store.purge_expired(now=now, limit=limit)
    assert store.get(secret_ref=REF) == "synthetic-live"


def test_corrupt_storage_is_not_treated_as_missing(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir(mode=0o700)
    path = root / f"{NAMESPACE}.json"
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(b'{"duplicate":1,"duplicate":2}')
    with pytest.raises(RuntimeFileError, match="runtime_secret_storage_unavailable"):
        _store(root).get(secret_ref=REF)


@pytest.mark.asyncio
async def test_file_manager_runtime_values_never_enter_descriptors(tmp_path):
    descriptor = tmp_path / "secrets.yaml"
    before = b"platform: {}\n"
    descriptor.write_bytes(before)
    config = SecretsManagerConfig(
        provider="secrets-file", component="proc",
        global_secrets_yaml=descriptor.as_uri(),
        runtime_secrets_root=str(tmp_path / "runtime"),
        runtime_secret_namespaces=(NAMESPACE,),
    )
    manager = SecretsFileSecretsManager(config)
    expiry = int(time.time()) + 60
    assert await manager.create_ephemeral_secret(
        namespace=NAMESPACE, secret_ref=REF, value="synthetic-runtime", expires_at=expiry,
    )
    fresh = SecretsFileSecretsManager(config)
    assert await fresh.get_ephemeral_secret(namespace=NAMESPACE, secret_ref=REF) == "synthetic-runtime"
    assert not await fresh.create_ephemeral_secret(
        namespace=NAMESPACE, secret_ref=REF, value="synthetic-other", expires_at=expiry,
    )
    assert descriptor.read_bytes() == before
    with pytest.raises(SecretsManagerWriteError, match="runtime_secret_scope_forbidden"):
        await fresh.get_ephemeral_secret(namespace="unauthorized", secret_ref=REF)
    assert await fresh.purge_expired_ephemeral_secrets(namespace=NAMESPACE, now=int(time.time()), limit=1) == 0
    await fresh.delete_ephemeral_secret(namespace=NAMESPACE, secret_ref=REF)
    assert await fresh.get_ephemeral_secret(namespace=NAMESPACE, secret_ref=REF) is None
    assert descriptor.read_bytes() == before


@pytest.mark.asyncio
async def test_runtime_root_cannot_be_the_descriptor_directory(tmp_path):
    manager = SecretsFileSecretsManager(SecretsManagerConfig(
        provider="secrets-file", component="proc",
        global_secrets_yaml=(tmp_path / "secrets.yaml").as_uri(),
        runtime_secrets_root=str(tmp_path),
        runtime_secret_namespaces=(NAMESPACE,),
    ))
    with pytest.raises(SecretsManagerWriteError, match="runtime_secret_storage_must_be_separate"):
        await manager.create_ephemeral_secret(
            namespace=NAMESPACE, secret_ref=REF, value="synthetic", expires_at=int(time.time()) + 60,
        )
    assert list(tmp_path.iterdir()) == []
