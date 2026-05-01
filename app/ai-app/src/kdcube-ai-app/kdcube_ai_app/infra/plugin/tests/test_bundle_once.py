# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import time

import pytest

from kdcube_ai_app.infra.plugin.bundle_once import (
    SharedStorageOnceTimeout,
    run_once_for_shared_bundle_storage,
)


def test_run_once_for_shared_bundle_storage_runs_action_and_writes_signature(tmp_path):
    storage_root = tmp_path / "storage"
    signature_path = storage_root / ".demo.signature"
    output_path = storage_root / "output.txt"
    calls = []

    async def _case():
        async def _action():
            calls.append("run")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("ok", encoding="utf-8")

        result = await run_once_for_shared_bundle_storage(
            storage_root=storage_root,
            operation="demo",
            signature_path=signature_path,
            signature="sig-1",
            ready=output_path.exists,
            action=_action,
        )
        return result

    result = asyncio.run(_case())

    assert result.status == "ran"
    assert result.ran is True
    assert calls == ["run"]
    assert signature_path.read_text(encoding="utf-8").strip() == "sig-1"


def test_run_once_for_shared_bundle_storage_skips_when_current(tmp_path):
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    signature_path = storage_root / ".demo.signature"
    output_path = storage_root / "output.txt"
    signature_path.write_text("sig-1\n", encoding="utf-8")
    output_path.write_text("ok", encoding="utf-8")
    calls = []

    async def _action():
        calls.append("run")

    result = asyncio.run(
        run_once_for_shared_bundle_storage(
            storage_root=storage_root,
            operation="demo",
            signature_path=signature_path,
            signature="sig-1",
            ready=output_path.exists,
            action=_action,
        )
    )

    assert result.status == "already_current"
    assert result.ran is False
    assert calls == []


def test_run_once_for_shared_bundle_storage_requires_async_action(tmp_path):
    storage_root = tmp_path / "storage"
    signature_path = storage_root / ".demo.signature"
    output_path = storage_root / "output.txt"

    with pytest.raises(TypeError, match="action must return an awaitable"):
        asyncio.run(
            run_once_for_shared_bundle_storage(
                storage_root=storage_root,
                operation="demo",
                signature_path=signature_path,
                signature="sig-1",
                ready=output_path.exists,
                action=lambda: output_path.write_text("ok", encoding="utf-8"),
            )
        )


def test_run_once_for_shared_bundle_storage_coalesces_concurrent_callers(tmp_path):
    storage_root = tmp_path / "storage"
    signature_path = storage_root / ".demo.signature"
    output_path = storage_root / "output.txt"
    calls = []

    async def _action():
        calls.append("run")
        await asyncio.sleep(0.05)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("ok", encoding="utf-8")

    async def _case():
        return await asyncio.gather(
            run_once_for_shared_bundle_storage(
                storage_root=storage_root,
                operation="demo",
                signature_path=signature_path,
                signature="sig-1",
                ready=output_path.exists,
                action=_action,
            ),
            run_once_for_shared_bundle_storage(
                storage_root=storage_root,
                operation="demo",
                signature_path=signature_path,
                signature="sig-1",
                ready=output_path.exists,
                action=_action,
            ),
        )

    results = asyncio.run(_case())

    assert calls == ["run"]
    assert sorted(result.status for result in results) == ["became_current", "ran"]
    assert signature_path.read_text(encoding="utf-8").strip() == "sig-1"


def test_run_once_for_shared_bundle_storage_raises_when_lock_times_out_without_output(tmp_path):
    storage_root = tmp_path / "storage"
    lock_path = storage_root / ".kdcube.once" / "demo.lock"
    lock_path.mkdir(parents=True)
    signature_path = storage_root / ".demo.signature"
    output_path = storage_root / "output.txt"

    async def _action():
        output_path.write_text("ok", encoding="utf-8")

    with pytest.raises(SharedStorageOnceTimeout):
        asyncio.run(
            run_once_for_shared_bundle_storage(
                storage_root=storage_root,
                operation="demo",
                signature_path=signature_path,
                signature="sig-1",
                ready=output_path.exists,
                action=_action,
                lock_wait_seconds=0.01,
                lock_ttl_seconds=3600,
                poll_interval_seconds=0.01,
                allow_existing_on_timeout=False,
            )
        )


def test_run_once_for_shared_bundle_storage_removes_stale_lock(tmp_path):
    storage_root = tmp_path / "storage"
    lock_path = storage_root / ".kdcube.once" / "demo.lock"
    lock_path.mkdir(parents=True)
    old = time.time() - 3600
    lock_path.touch()
    lock_path.chmod(0o755)
    import os

    os.utime(lock_path, (old, old))
    signature_path = storage_root / ".demo.signature"
    output_path = storage_root / "output.txt"

    async def _action():
        output_path.write_text("ok", encoding="utf-8")

    result = asyncio.run(
        run_once_for_shared_bundle_storage(
            storage_root=storage_root,
            operation="demo",
            signature_path=signature_path,
            signature="sig-1",
            ready=output_path.exists,
            action=_action,
            lock_ttl_seconds=0.01,
        )
    )

    assert result.status == "ran"
    assert output_path.exists()
