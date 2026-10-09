"""Runtime secret records on the file secrets manager's folder lane, through the store the Hub uses.

``KDCubeEphemeralSecretStore`` calls the configured manager's runtime operations. The ``secrets-file``
manager keeps those records in ``secrets.runtime.root``: one private file per enrolled namespace, in a folder
separate from the descriptor YAML (``RuntimeFileStore``). Backends exercised:

- ``secrets-file``  a real SecretsFileSecretsManager: descriptors in ``<tmp>/config``, records in the
                    separate folder ``<tmp>/runtime``;
- ``in-memory``     InMemorySecretsManager: a development backend that never qualifies as durable.

``runtime_contract.persistent_filesystem`` decides whether a folder qualifies; those cases simulate Linux
``/proc/self/mountinfo``. No container, mount or real secret is involved, and no value is printed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import time
from types import SimpleNamespace

import pytest
import yaml

import kdcube_ai_app.infra.secrets.runtime_contract as runtime_contract
from kdcube_ai_app.infra.secrets.ephemeral import ephemeral_secret_store
from kdcube_ai_app.infra.secrets.manager import (
    InMemorySecretsManager,
    SecretsFileSecretsManager,
    SecretsManagerConfig,
    SecretsManagerError,
)

OWNER = "connection-hub@1-0"
NAMESPACE = "folder-lane-custody"
OTHER_NAMESPACE = "folder-lane-other"
UNENROLLED_NAMESPACE = "folder-lane-not-enrolled"
REF = "c" * 32
CANARY = "synthetic-folder-lane-value-canary"


class Backend:
    """``open()`` gives a manager over the backend's storage; ``restart()`` a fresh process's manager."""

    def __init__(self, name, tmp_path):
        self.name = name
        self.durable = name == "secrets-file"
        self.config_dir = tmp_path / "config"
        self.runtime_root = tmp_path / "runtime"
        self.bundle_yaml = self.config_dir / "bundles.secrets.yaml"
        self._memory = InMemorySecretsManager()
        if name == "secrets-file":
            self.config_dir.mkdir()
            self.bundle_yaml.write_text(yaml.safe_dump({"bundles": {"version": "1", "items": [
                {"id": OWNER, "secrets": {"descriptor_secret": "existing-descriptor-value"}}]}}))

    def file_config(self, **overrides):
        values = dict(provider="secrets-file", component="proc", bundle_secrets_yaml=self.bundle_yaml.as_uri(),
                      runtime_secrets_root=str(self.runtime_root),
                      runtime_secret_namespaces=(NAMESPACE, OTHER_NAMESPACE))
        values.update(overrides)
        return SecretsManagerConfig(**values)

    def open(self):
        if self.name == "in-memory":
            return self._memory
        return SecretsFileSecretsManager(self.file_config())

    def restart(self):
        if self.name == "in-memory":
            self._memory = InMemorySecretsManager()  # its records die with the process
        return self.open()


@pytest.fixture(params=["secrets-file", "in-memory"])
def backend(request, tmp_path):
    return Backend(request.param, tmp_path)


@pytest.fixture
def folder(tmp_path):
    return Backend("secrets-file", tmp_path)


def _store(manager, *, namespace=NAMESPACE):
    return ephemeral_secret_store(namespace=namespace, manager=manager)


def _later(seconds=300):
    return int(time.time()) + seconds


def _folder_text(folder):
    if not folder.runtime_root.exists():
        return ""
    return "".join(p.read_text() for p in folder.runtime_root.rglob("*") if p.is_file())


# 1. Readback and restart.

@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", CANARY, "ü€" * 500, "x" * 8192],
                         ids=["empty", "ascii", "unicode", "8k"])
async def test_a_created_record_reads_back_exactly(backend, value):
    store = _store(backend.open())
    assert await store.create(secret_ref=REF, value=value, expires_at=_later()) is True
    assert await store.get(secret_ref=REF) == value


@pytest.mark.asyncio
async def test_the_folder_keeps_a_record_across_a_restart_and_qualifies(folder):
    assert await _store(folder.open()).create(secret_ref=REF, value=CANARY, expires_at=_later()) is True
    after = _store(folder.restart())
    assert await after.get(secret_ref=REF) == CANARY
    # The test folder is on this machine's local disk (APFS on macOS, ext4/xfs on Linux runners).
    assert await after.qualify_durable_backend() is True


@pytest.mark.asyncio
async def test_in_memory_loses_records_on_restart_and_never_qualifies():
    memory = Backend("in-memory", pathlib.Path("/nonexistent"))
    assert await _store(memory.open()).create(secret_ref=REF, value=CANARY, expires_at=_later()) is True
    after = _store(memory.restart())
    assert await after.get(secret_ref=REF) is None
    assert await after.qualify_durable_backend() is False


# 2. Create-only.

@pytest.mark.asyncio
async def test_a_second_create_of_one_reference_is_refused_and_keeps_the_first_value(backend):
    store = _store(backend.open())
    assert await store.create(secret_ref=REF, value=CANARY, expires_at=_later()) is True
    assert await store.create(secret_ref=REF, value="replacement", expires_at=_later(900)) is False
    assert await store.get(secret_ref=REF) == CANARY


@pytest.mark.asyncio
async def test_a_create_replayed_after_delete_does_not_bring_the_reference_back(folder):
    # A delayed duplicate of the original create must not mint a new incarnation of a retired reference.
    store = _store(folder.open())
    assert await store.create(secret_ref=REF, value=CANARY, expires_at=_later()) is True
    await store.delete(secret_ref=REF)
    assert await store.get(secret_ref=REF) is None
    assert await store.create(secret_ref=REF, value=CANARY, expires_at=_later()) is False
    assert await store.get(secret_ref=REF) is None


@pytest.mark.asyncio
async def test_two_processes_creating_one_reference_yield_exactly_one_winner(folder):
    # Two managers (two app processes) over the same folder; the file lock serializes them.
    first, second = _store(folder.open()), _store(folder.open())
    results = await asyncio.gather(*[
        store.create(secret_ref=REF, value=value, expires_at=_later())
        for store, value in ((first, "value-from-process-a"), (second, "value-from-process-b"))])
    assert sorted(results) == [False, True]
    winner = "value-from-process-a" if results[0] else "value-from-process-b"
    assert await _store(folder.open()).get(secret_ref=REF) == winner


# 3. Expiry.

@pytest.mark.asyncio
async def test_a_record_reads_none_once_expired(folder, monkeypatch):
    store = _store(folder.open())
    expires_at = _later(60)
    assert await store.create(secret_ref=REF, value=CANARY, expires_at=expires_at) is True
    monkeypatch.setattr(time, "time", lambda: float(expires_at))
    assert await store.get(secret_ref=REF) is None


@pytest.mark.asyncio
async def test_creating_an_already_expired_record_is_refused(folder):
    store = _store(folder.open())
    with pytest.raises(SecretsManagerError):
        await store.create(secret_ref=REF, value=CANARY, expires_at=int(time.time()) - 1)
    assert await store.get(secret_ref=REF) is None


# 4. Purge, delete.

@pytest.mark.asyncio
async def test_purge_is_bounded_removes_only_expired_records_and_only_in_its_own_namespace(folder, monkeypatch):
    manager = folder.open()
    store, other = _store(manager), _store(manager, namespace=OTHER_NAMESPACE)
    now = int(time.time())
    expired = [f"{n:032x}" for n in range(1, 4)]
    live = [f"{n:032x}" for n in range(10, 12)]
    for ref in expired:
        assert await store.create(secret_ref=ref, value=CANARY, expires_at=now + 30)
    for ref in live:
        assert await store.create(secret_ref=ref, value=CANARY, expires_at=now + 3600)
    assert await other.create(secret_ref=expired[0], value="other-namespace", expires_at=now + 30)
    monkeypatch.setattr(time, "time", lambda: float(now + 60))  # the clock moves past the short expiry

    assert await store.purge_expired(now=now + 60, limit=2) == 2
    assert await store.purge_expired(now=now + 60, limit=2) == 1
    assert await store.purge_expired(now=now + 60, limit=2) == 0
    monkeypatch.undo()  # back to the real clock: the other namespace's record is still live, so not purged
    for ref in live:
        assert await store.get(secret_ref=ref) == CANARY
    assert await other.get(secret_ref=expired[0]) == "other-namespace"


@pytest.mark.asyncio
async def test_purge_refuses_a_now_ahead_of_the_clock(folder):
    # A caller-supplied future "now" must not retire records that are still live.
    store = _store(folder.open())
    assert await store.create(secret_ref=REF, value=CANARY, expires_at=_later(60)) is True
    with pytest.raises(SecretsManagerError):
        await store.purge_expired(now=_later(3600), limit=10)
    assert await store.get(secret_ref=REF) == CANARY


@pytest.mark.asyncio
async def test_delete_is_idempotent_for_a_used_and_an_unknown_reference(backend):
    store = _store(backend.open())
    assert await store.create(secret_ref=REF, value=CANARY, expires_at=_later()) is True
    await store.delete(secret_ref=REF)
    await store.delete(secret_ref=REF)
    await store.delete(secret_ref="d" * 32)
    assert await store.get(secret_ref=REF) is None


# 5. Scope and placement: a folder beside the descriptors, never inside them.

@pytest.mark.asyncio
async def test_records_live_in_the_runtime_folder_never_in_the_descriptor_yaml_or_listing(folder):
    manager = folder.open()
    assert await _store(manager).create(secret_ref=REF, value=CANARY, expires_at=_later()) is True
    assert CANARY not in folder.bundle_yaml.read_text()
    assert CANARY in _folder_text(folder)
    assert await manager.get_secret(f"bundles.{OWNER}.secrets.descriptor_secret") == "existing-descriptor-value"
    listed = await manager.list_all_secret_keys()
    assert not [key for key in listed if REF in key or NAMESPACE in key], listed


@pytest.mark.asyncio
async def test_the_runtime_folder_and_its_files_are_private(folder):
    assert await _store(folder.open()).create(secret_ref=REF, value=CANARY, expires_at=_later()) is True
    assert oct(os.stat(folder.runtime_root).st_mode & 0o777) == oct(0o700)
    for path in folder.runtime_root.rglob("*"):
        if path.is_file():
            assert oct(os.stat(path).st_mode & 0o777) == oct(0o600), path.name


@pytest.mark.asyncio
async def test_a_namespace_the_host_did_not_enroll_is_refused(folder):
    store = _store(folder.open(), namespace=UNENROLLED_NAMESPACE)
    with pytest.raises(SecretsManagerError):
        await store.create(secret_ref=REF, value=CANARY, expires_at=_later())
    assert CANARY not in _folder_text(folder)


@pytest.mark.asyncio
async def test_the_runtime_folder_must_not_be_the_descriptor_folder(folder):
    manager = SecretsFileSecretsManager(folder.file_config(runtime_secrets_root=str(folder.config_dir)))
    with pytest.raises(SecretsManagerError):
        await _store(manager).create(secret_ref=REF, value=CANARY, expires_at=_later())
    assert CANARY not in folder.bundle_yaml.read_text()


@pytest.mark.asyncio
async def test_a_broad_runtime_folder_is_refused_not_repaired(folder):
    folder.runtime_root.mkdir()
    os.chmod(folder.runtime_root, 0o755)
    with pytest.raises(SecretsManagerError):
        await _store(folder.open()).create(secret_ref=REF, value=CANARY, expires_at=_later())
    assert oct(os.stat(folder.runtime_root).st_mode & 0o777) == oct(0o755)
    assert CANARY not in _folder_text(folder)


@pytest.mark.asyncio
async def test_without_an_explicit_runtime_folder_records_go_to_secrets_beside_the_descriptors(folder):
    # With no secrets.runtime.root the file backend uses <descriptor folder>/secrets: a separate private
    # folder next to bundles.secrets.yaml, never the YAML itself.
    store = _store(SecretsFileSecretsManager(folder.file_config(runtime_secrets_root=None)))
    assert await store.create(secret_ref=REF, value=CANARY, expires_at=_later()) is True
    assert await store.get(secret_ref=REF) == CANARY
    assert await store.qualify_durable_backend() is True
    default_root = folder.config_dir / "secrets"
    assert oct(os.stat(default_root).st_mode & 0o777) == oct(0o700)
    assert any(CANARY in p.read_text() for p in default_root.rglob("*") if p.is_file())
    assert CANARY not in folder.bundle_yaml.read_text()
    assert not folder.runtime_root.exists()


# 6. Refusal is fixed and value-free.

@pytest.mark.asyncio
async def test_a_storage_failure_fails_closed_without_the_value_in_the_error_or_log(folder, caplog):
    folder.runtime_root.mkdir()
    os.chmod(folder.runtime_root, 0o500)  # an unwritable folder: the write must fail
    caplog.set_level(logging.DEBUG)
    try:
        with pytest.raises(Exception) as refused:
            await _store(folder.open()).create(secret_ref=REF, value=CANARY, expires_at=_later())
    finally:
        os.chmod(folder.runtime_root, 0o700)
    assert CANARY not in str(refused.value)
    assert CANARY not in caplog.text


# 7. Which mounts qualify the folder as persistent (simulated Linux /proc/self/mountinfo).

def _linux_mounts(monkeypatch, lines):
    monkeypatch.setattr(runtime_contract.os, "uname", lambda: SimpleNamespace(sysname="Linux"))
    real_read_text = pathlib.Path.read_text

    def read_text(self, *args, **kwargs):
        if str(self) == "/proc/self/mountinfo":
            return "\n".join(lines) + "\n"
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)


_CONTAINER_ROOT = "1 0 0:1 / / rw,relatime - overlay overlay rw,lowerdir=/l,upperdir=/u,workdir=/w"


@pytest.mark.parametrize("fs_type, source, persistent", [
    ("ext4", "/dev/vda1", True),      # e.g. a retained Docker named volume
    ("xfs", "/dev/nvme0n1p1", True),
    ("tmpfs", "tmpfs", False),
    ("overlay", "overlay", False),
    ("nfs4", "server:/export", False),  # unknown types stay refused
])
def test_the_folder_qualifies_by_its_mount_type(monkeypatch, fs_type, source, persistent):
    _linux_mounts(monkeypatch, [_CONTAINER_ROOT,
                                f"2 1 0:2 /data /kdcube-runtime rw,relatime - {fs_type} {source} rw"])
    assert runtime_contract.persistent_filesystem(pathlib.Path("/kdcube-runtime/records")) is persistent


# Docker Desktop shows a host folder inside a Linux container as mount type "fakeowner", with a source under
# /run/host_mark/; the host disk backs it, and /config is mounted that way. Exactly that pairing qualifies, for
# example a records folder at /config/secrets; "fakeowner" from any other source does not.
_HOST_SHARE = "2 1 0:2 /Users/x/.kdcube/runtime/config /config rw,relatime - fakeowner {source} rw,fakeowner"


@pytest.mark.parametrize("source, persistent", [
    ("/run/host_mark/Users", True),
    ("/run/host_mark/private", True),
    ("/run/elsewhere/Users", False),
    ("fakeowner", False),
    ("/run/host_markX/Users", False),
])
def test_docker_desktop_host_share_qualifies_only_from_run_host_mark(monkeypatch, source, persistent):
    _linux_mounts(monkeypatch, [_CONTAINER_ROOT, _HOST_SHARE.format(source=source)])
    assert runtime_contract.persistent_filesystem(pathlib.Path("/config/secrets")) is persistent


def test_a_tmpfs_mounted_over_the_records_folder_still_refuses(monkeypatch):
    # The closest mount decides: a transient mount inside the host share must not inherit its acceptance.
    _linux_mounts(monkeypatch, [_CONTAINER_ROOT, _HOST_SHARE.format(source="/run/host_mark/Users"),
                                "3 2 0:3 / /config/secrets rw - tmpfs tmpfs rw"])
    assert runtime_contract.persistent_filesystem(pathlib.Path("/config/secrets")) is False


def test_a_folder_on_the_containers_own_layer_does_not_qualify(monkeypatch):
    # A runtime folder not under any mounted volume lives on the container's overlay: lost on replacement.
    _linux_mounts(monkeypatch, [_CONTAINER_ROOT,
                                "2 1 0:2 /data /config rw,relatime - ext4 /dev/vda1 rw"])
    assert runtime_contract.persistent_filesystem(pathlib.Path("/var/kdcube-runtime")) is False
