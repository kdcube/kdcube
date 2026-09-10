# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat.proc.app_deployment import deprovision
from kdcube_ai_app.apps.chat.proc.app_deployment.deprovision import (
    AppDeprovisionError,
    deprovision_loaded_bundle_app_resources,
)
from kdcube_ai_app.infra.plugin.bundle_loader import BundleSpec


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str):
        return self.values.get(key)

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None):
        del ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script: str, key_count: int, key: str, expected: str):
        del script, key_count
        if self.values.get(key) != expected:
            return 0
        self.values.pop(key, None)
        return 1


def _specs(source: Path) -> tuple[BundleSpec, SimpleNamespace]:
    return (
        BundleSpec(
            id="app@1-0",
            path=str(source),
            module="entrypoint",
            singleton=True,
        ),
        SimpleNamespace(
            id="app@1-0",
            path=str(source),
            module="entrypoint",
            singleton=True,
            repo=None,
            ref=None,
            subdir=None,
            git_commit="commit-a",
        ),
    )


def _patch_dependencies(monkeypatch: pytest.MonkeyPatch, storage_root: Path) -> None:
    async def _props(**kwargs):
        del kwargs
        return {"feature": {"enabled": True}}

    async def _storage(**kwargs):
        assert kwargs["ensure"] is False
        return storage_root

    monkeypatch.setattr(deprovision, "get_bundle_props_from_authority", _props)
    monkeypatch.setattr(deprovision, "resolve_app_storage_root", _storage)
    monkeypatch.setattr(
        deprovision,
        "get_settings",
        lambda: SimpleNamespace(
            PLATFORM=SimpleNamespace(
                APPLICATIONS=SimpleNamespace(
                    BUNDLES_PRELOAD_LOCK_TTL_SECONDS=30,
                    BUNDLES_PRELOAD_BUNDLE_LOCK_TTL_SECONDS=30,
                )
            )
        ),
    )


@pytest.mark.asyncio
async def test_deprovision_runs_once_and_replays_completed_operation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    storage = tmp_path / "storage"
    _patch_dependencies(monkeypatch, storage)
    agentic_spec, bundle_spec = _specs(source)
    redis = _Redis()
    calls: list[dict[str, object]] = []

    class _Workflow:
        config = SimpleNamespace(log_level="INFO")
        bundle_props_defaults = {"from_default": True}

        async def on_app_deprovision(self, **kwargs) -> None:
            calls.append(kwargs)

    workflow = _Workflow()
    first = await deprovision_loaded_bundle_app_resources(
        workflow=workflow,
        module=SimpleNamespace(),
        agentic_spec=agentic_spec,
        bundle_spec=bundle_spec,
        tenant="tenant-a",
        project="project-a",
        operation_id="operation-a",
        purge_data=True,
        pg_pool="pool",
        redis=redis,
    )
    second = await deprovision_loaded_bundle_app_resources(
        workflow=workflow,
        module=SimpleNamespace(),
        agentic_spec=agentic_spec,
        bundle_spec=bundle_spec,
        tenant="tenant-a",
        project="project-a",
        operation_id="operation-a",
        purge_data=True,
        pg_pool="pool",
        redis=redis,
    )

    assert first["status"] == "ok"
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert len(calls) == 1
    assert calls[0]["operation_id"] == "operation-a"
    assert calls[0]["purge_data"] is True
    assert calls[0]["storage_root"] == storage
    assert calls[0]["props"] == {
        "from_default": True,
        "feature": {"enabled": True},
    }
    assert calls[0]["pg_pool"] == "pool"


@pytest.mark.asyncio
async def test_deprovision_without_hook_is_successful_noop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _patch_dependencies(monkeypatch, tmp_path / "storage")
    agentic_spec, bundle_spec = _specs(source)

    result = await deprovision_loaded_bundle_app_resources(
        workflow=SimpleNamespace(config=SimpleNamespace(log_level="INFO")),
        module=SimpleNamespace(),
        agentic_spec=agentic_spec,
        bundle_spec=bundle_spec,
        tenant="tenant-a",
        project="project-a",
        operation_id="operation-noop",
        purge_data=False,
        redis=_Redis(),
    )

    assert result["status"] == "ok"
    assert result["hook_present"] is False
    assert result["purge_data"] is False


@pytest.mark.asyncio
async def test_failed_hook_is_recorded_and_same_operation_can_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _patch_dependencies(monkeypatch, tmp_path / "storage")
    agentic_spec, bundle_spec = _specs(source)
    redis = _Redis()

    class _FailingWorkflow:
        config = SimpleNamespace(log_level="INFO")

        async def on_app_deprovision(self) -> None:
            raise RuntimeError("external cleanup failed")

    with pytest.raises(AppDeprovisionError, match="external cleanup failed"):
        await deprovision_loaded_bundle_app_resources(
            workflow=_FailingWorkflow(),
            module=SimpleNamespace(),
            agentic_spec=agentic_spec,
            bundle_spec=bundle_spec,
            tenant="tenant-a",
            project="project-a",
            operation_id="operation-retry",
            purge_data=False,
            redis=redis,
        )

    result_payloads = [
        json.loads(value)
        for key, value in redis.values.items()
        if "deprovision-result" in key
    ]
    assert result_payloads[0]["status"] == "error"

    calls = 0

    class _RecoveredWorkflow:
        config = SimpleNamespace(log_level="INFO")

        async def on_app_deprovision(self) -> None:
            nonlocal calls
            calls += 1

    recovered = await deprovision_loaded_bundle_app_resources(
        workflow=_RecoveredWorkflow(),
        module=SimpleNamespace(),
        agentic_spec=agentic_spec,
        bundle_spec=bundle_spec,
        tenant="tenant-a",
        project="project-a",
        operation_id="operation-retry",
        purge_data=False,
        redis=redis,
    )

    assert recovered["status"] == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_deprovision_rejects_synchronous_hook(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _patch_dependencies(monkeypatch, tmp_path / "storage")
    agentic_spec, bundle_spec = _specs(source)

    class _Workflow:
        config = SimpleNamespace(log_level="INFO")

        def on_app_deprovision(self) -> None:
            return None

    with pytest.raises(AppDeprovisionError, match="must be declared with async def"):
        await deprovision_loaded_bundle_app_resources(
            workflow=_Workflow(),
            module=SimpleNamespace(),
            agentic_spec=agentic_spec,
            bundle_spec=bundle_spec,
            tenant="tenant-a",
            project="project-a",
            operation_id="operation-sync",
            purge_data=False,
            redis=_Redis(),
        )
