# SPDX-License-Identifier: MIT
"""Real spawned processes sharing only synthetic encrypted vault files."""

from __future__ import annotations

import multiprocessing
import os
from pathlib import Path
from queue import Empty

import pytest

from kdcube_ai_app.infra.secrets.host_vault import storage
from kdcube_ai_app.infra.secrets.host_vault.keys import FileRootKeyProvider
from kdcube_ai_app.infra.secrets.host_vault.protocol import (
    ErrorCode,
    SecretNamespace,
    SecretReference,
    VaultError,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX host-vault custody")
NS = SecretNamespace("test-tenant", "test-project", "test-app@1-0")
REFERENCE = SecretReference(NS, "synthetic-token")


def _store(root: str) -> storage.FileDurableSecretStore:
    base = Path(root)
    return storage.FileDurableSecretStore(base / "store", FileRootKeyProvider(base / "keys"))


def _paused_writer(root, candidate_ready, release, done, results, expected):
    try:
        store = _store(root)

        def before_commit():
            candidate_ready.set()
            if not release.wait(15):
                raise RuntimeError("test writer was not released")

        store._commit_hook = before_commit
        record = store.put(REFERENCE, b"synthetic-new", expected_generation=expected)
        results.put(("written", record.generation))
    except VaultError as exc:
        results.put(("refused", exc.code.value))
    except Exception as exc:
        results.put(("failed", type(exc).__name__))
    finally:
        done.set()


def _operation(root, operation, ready, go, attempted, done, results):
    try:
        # Construct before the first writer starts: this test isolates each
        # operation's lock from the independent startup-recovery lock test.
        store = _store(root)
        ready.set()
        if not go.wait(15):
            raise RuntimeError("test operation was not released")
        attempted.set()
        if operation == "put":
            result = store.put(REFERENCE, b"synthetic-rival", expected_generation=0).generation
        elif operation == "delete":
            result = store.delete(REFERENCE, expected_generation=1).generation
        elif operation == "get":
            record, value = store.get(REFERENCE)
            result = (record.generation, value == b"synthetic-new")
        elif operation == "list":
            result = store.list_names(NS, prefix="synthetic-", limit=10)
        elif operation == "rewrap":
            result = store.rewrap_all()
        elif operation == "recover":
            result = store.recover()
        else:
            raise AssertionError("unknown test operation")
        results.put(("result", result))
    except VaultError as exc:
        results.put(("refused", exc.code.value))
    except Exception as exc:
        results.put(("failed", type(exc).__name__))
    finally:
        done.set()


def _startup(root, attempted, done, results):
    attempted.set()
    try:
        store = _store(root)
        record, value = store.get(REFERENCE)
        results.put(("read", record.generation, value == b"synthetic-new"))
    except Exception as exc:
        results.put(("failed", type(exc).__name__))
    finally:
        done.set()


def _join(process):
    process.join(10)
    assert not process.is_alive(), "synthetic vault test process hung"
    assert process.exitcode == 0


def _cleanup(processes, releases):
    for event in releases:
        event.set()
    for process in processes:
        if process.pid is None:
            continue
        process.join(2)
        if process.is_alive():
            process.terminate()
            process.join(5)


@pytest.fixture
def root(tmp_path):
    FileRootKeyProvider(tmp_path / "keys").rotate()
    _store(str(tmp_path))
    return str(tmp_path)


@pytest.mark.parametrize("operation", ["put", "delete", "get", "list", "rewrap", "recover"])
def test_operations_serialize_with_an_active_writer_across_processes(root, operation):
    context = multiprocessing.get_context("spawn")
    initial_generation = 0 if operation == "put" else 1
    if initial_generation:
        _store(root).put(REFERENCE, b"synthetic-old", expected_generation=0)
    ready, go, attempted, operation_done = [context.Event() for _ in range(4)]
    candidate_ready, release, writer_done = [context.Event() for _ in range(3)]
    results = context.Queue()
    other = context.Process(target=_operation, args=(
        root, operation, ready, go, attempted, operation_done, results,
    ))
    writer = context.Process(target=_paused_writer, args=(
        root, candidate_ready, release, writer_done, results, initial_generation,
    ))
    try:
        other.start()
        assert ready.wait(10)
        writer.start()
        assert candidate_ready.wait(10)
        candidates = list((Path(root) / "store").rglob("*.candidate"))
        assert len(candidates) == 1
        go.set()
        assert attempted.wait(10)
        assert not operation_done.wait(0.5), "operation bypassed another process's store lock"
        assert candidates[0].is_file(), "recovery removed an active candidate"
        release.set()
        assert writer_done.wait(10)
        assert operation_done.wait(10)
        _join(writer)
        _join(other)
        outcomes = [results.get(timeout=2), results.get(timeout=2)]
        assert ("written", initial_generation + 1) in outcomes
        if operation in {"put", "delete"}:
            assert ("refused", ErrorCode.CONFLICT.value) in outcomes
        else:
            expected = {
                "get": (2, True),
                "list": [REFERENCE.name],
                "rewrap": 0,
                "recover": 0,
            }[operation]
            assert ("result", expected) in outcomes
        record, value = _store(root).get(REFERENCE)
        assert record.generation == initial_generation + 1
        assert value == b"synthetic-new"
    finally:
        _cleanup([writer, other], [release, go])
        results.close()
        results.join_thread()


def test_startup_recovery_waits_for_another_process_to_commit(root):
    context = multiprocessing.get_context("spawn")
    candidate_ready, release, writer_done, attempted, startup_done = [
        context.Event() for _ in range(5)
    ]
    results = context.Queue()
    writer = context.Process(target=_paused_writer, args=(
        root, candidate_ready, release, writer_done, results, 0,
    ))
    startup = context.Process(target=_startup, args=(root, attempted, startup_done, results))
    try:
        writer.start()
        assert candidate_ready.wait(10)
        candidate = next((Path(root) / "store").rglob("*.candidate"))
        startup.start()
        assert attempted.wait(10)
        assert not startup_done.wait(0.5), "startup bypassed an active write"
        assert candidate.is_file()
        release.set()
        assert writer_done.wait(10)
        assert startup_done.wait(10)
        _join(writer)
        _join(startup)
        outcomes = [results.get(timeout=2), results.get(timeout=2)]
        assert ("written", 1) in outcomes
        assert ("read", 1, True) in outcomes
        assert not candidate.exists()
    finally:
        _cleanup([writer, startup], [release])
        results.close()
        results.join_thread()


@pytest.mark.parametrize("previous", ["absent", "live", "deleted"])
def test_process_crash_releases_lock_and_only_abandoned_candidate_is_removed(root, previous):
    context = multiprocessing.get_context("spawn")
    expected = 0
    if previous != "absent":
        expected = _store(root).put(REFERENCE, b"synthetic-old", expected_generation=0).generation
    if previous == "deleted":
        expected = _store(root).delete(REFERENCE, expected_generation=expected).generation
    candidate_ready, release, writer_done = [context.Event() for _ in range(3)]
    results = context.Queue()
    writer = context.Process(target=_paused_writer, args=(
        root, candidate_ready, release, writer_done, results, expected,
    ))
    try:
        writer.start()
        assert candidate_ready.wait(10)
        candidate = next((Path(root) / "store").rglob("*.candidate"))
        writer.terminate()  # Exact synthetic test child; models death before rename.
        writer.join(10)
        assert not writer.is_alive()
        assert candidate.is_file()
        assert not writer_done.is_set()
        with pytest.raises(Empty):
            results.get(timeout=0.1)
        restarted = _store(root)
        assert not candidate.exists()
        committed = restarted.get(REFERENCE)
        if previous == "live":
            assert committed[0].generation == 1
            assert committed[1] == b"synthetic-old"
        else:
            assert committed is None
        if expected:
            with pytest.raises(VaultError) as refused:
                restarted.put(REFERENCE, b"synthetic-rival", expected_generation=0)
            assert refused.value.code is ErrorCode.CONFLICT
        assert restarted.put(
            REFERENCE, b"synthetic-retry", expected_generation=expected,
        ).generation == expected + 1
    finally:
        # A killed Event waiter cannot acknowledge Condition.notify_all;
        # never signal its Event after death. Closing the OS store lock is
        # exactly what this test exercises, not multiprocessing synchronization.
        _cleanup([writer], [])
        results.close()
        results.join_thread()


@pytest.mark.parametrize("unsafe", ["readable", "symlink", "hardlink", "directory", "fifo"])
def test_unsafe_lock_inode_fails_closed_without_disclosing_paths(root, unsafe):
    lock_path = Path(root) / "store" / storage.FileDurableSecretStore.LOCK_NAME
    lock_path.unlink()
    target = Path(root) / "synthetic-target"
    target.write_bytes(b"synthetic-private")
    target.chmod(0o600)
    if unsafe == "readable":
        lock_path.write_bytes(b"")
        lock_path.chmod(0o644)
    elif unsafe == "symlink":
        lock_path.symlink_to(target)
    elif unsafe == "hardlink":
        os.link(target, lock_path)
    elif unsafe == "directory":
        lock_path.mkdir()
    elif unsafe == "fifo":
        os.mkfifo(lock_path, 0o600)
    with pytest.raises(VaultError) as refused:
        _store(root)
    assert refused.value.code is ErrorCode.BACKEND_UNAVAILABLE
    assert str(refused.value) == ErrorCode.BACKEND_UNAVAILABLE.value
    assert refused.value.detail == ""
    assert target.read_bytes() == b"synthetic-private"


def test_unsupported_os_lock_fails_closed(root, monkeypatch):
    monkeypatch.setattr(storage, "fcntl", None)
    with pytest.raises(VaultError) as refused:
        _store(root)
    assert refused.value.code is ErrorCode.BACKEND_UNAVAILABLE


def test_lock_owned_by_another_uid_fails_closed(root, monkeypatch):
    monkeypatch.setattr(storage.os, "geteuid", lambda: os.getuid() + 1)
    with pytest.raises(VaultError) as refused:
        _store(root)
    assert refused.value.code is ErrorCode.BACKEND_UNAVAILABLE


def test_lock_entry_replaced_during_acquisition_fails_closed(root, monkeypatch):
    actual_flock = storage.fcntl.flock

    def replace_entry(fd, operation):
        actual_flock(fd, operation)
        lock_path = Path(root) / "store" / storage.FileDurableSecretStore.LOCK_NAME
        replacement = lock_path.with_suffix(".replacement")
        replacement.write_bytes(b"")
        replacement.chmod(0o600)
        os.replace(replacement, lock_path)

    monkeypatch.setattr(storage.fcntl, "flock", replace_entry)
    with pytest.raises(VaultError) as refused:
        _store(root)
    assert refused.value.code is ErrorCode.BACKEND_UNAVAILABLE


def test_interrupted_acquisition_closes_the_lock_descriptor(root, monkeypatch):
    actual_flock = storage.fcntl.flock

    def interrupted(fd, operation):
        actual_flock(fd, operation)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(storage.fcntl, "flock", interrupted)
        with pytest.raises(KeyboardInterrupt):
            _store(root)
    fd = os.open(Path(root) / "store" / storage.FileDurableSecretStore.LOCK_NAME, os.O_RDWR)
    try:
        actual_flock(fd, storage.fcntl.LOCK_EX | storage.fcntl.LOCK_NB)
    finally:
        os.close(fd)
