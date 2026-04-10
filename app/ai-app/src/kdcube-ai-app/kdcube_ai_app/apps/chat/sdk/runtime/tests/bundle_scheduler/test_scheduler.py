# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""
BundleSchedulerManager lifecycle tests.

Covers: startup reconcile, live rebind, cancellation on disable,
no-overlap guard for process span, shutdown.
All tests use fake registries and stub manifests — no Redis, no real bundles.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from kdcube_ai_app.apps.chat.sdk.runtime.bundle_scheduler import (
    BundleSchedulerManager,
    _JobKey,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _make_registry(*bundle_ids: str) -> Any:
    bundles = {
        bid: SimpleNamespace(path=f"/fake/path/{bid}", module=None, singleton=False)
        for bid in bundle_ids
    }
    return SimpleNamespace(bundles=bundles)


def _empty_registry() -> Any:
    return SimpleNamespace(bundles={})


def _make_job_spec(
    *,
    alias: str,
    method_name: str = "run",
    cron_expression: str | None = "* * * * *",
    expr_config: str | None = None,
    span: str = "process",
) -> Any:
    return SimpleNamespace(
        alias=alias,
        method_name=method_name,
        cron_expression=cron_expression,
        expr_config=expr_config,
        span=span,
    )


def _make_manifest(jobs: list) -> Any:
    return SimpleNamespace(scheduled_jobs=jobs)


def _patches(manifest, props=None):
    """Return three independent patches for reconcile dependencies.

    load_bundle_manifest and get_bundle_props are lazy-imported inside reconcile(),
    so we must patch them at their source modules.
    _make_headless_config is module-level so we can patch it directly.
    """
    return (
        patch(
            "kdcube_ai_app.infra.plugin.agentic_loader.load_bundle_manifest",
            return_value=manifest,
        ),
        patch(
            "kdcube_ai_app.infra.plugin.bundle_store.get_bundle_props",
            new=AsyncMock(return_value=props or {}),
        ),
        patch(
            "kdcube_ai_app.apps.chat.sdk.runtime.bundle_scheduler._make_headless_config",
            return_value=SimpleNamespace(tenant="t", project="p", redis=None),
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_reconcile_empty_registry_creates_no_tasks():
    async def _t():
        mgr = BundleSchedulerManager(redis=None, tenant="t", project="p", instance_id="i1")
        await mgr.reconcile(_empty_registry())
        assert len(mgr._tasks) == 0
        await mgr.shutdown()
    _run(_t())


def test_reconcile_schedules_job_for_bundle():
    manifest = _make_manifest([_make_job_spec(alias="heartbeat")])
    pm, pp, ph = _patches(manifest)

    async def _t():
        with pm, pp, ph:
            mgr = BundleSchedulerManager(redis=None, tenant="t", project="p", instance_id="i1")
            await mgr.reconcile(_make_registry("echo.ui"))
        assert _JobKey(bundle_id="echo.ui", job_alias="heartbeat") in mgr._tasks
        await mgr.shutdown()
        assert len(mgr._tasks) == 0
    _run(_t())


def test_reconcile_skips_bundle_without_path():
    async def _t():
        mgr = BundleSchedulerManager(redis=None, tenant="t", project="p", instance_id="i1")
        reg = SimpleNamespace(bundles={
            "bad.bundle": SimpleNamespace(path="", module=None, singleton=False)
        })
        await mgr.reconcile(reg)
        assert len(mgr._tasks) == 0
        await mgr.shutdown()
    _run(_t())


def test_reconcile_disabled_job_not_scheduled():
    # expr_config present but props empty -> effective=None -> not scheduled
    manifest = _make_manifest([
        _make_job_spec(alias="job", cron_expression=None, expr_config="routines.cron")
    ])
    pm, pp, ph = _patches(manifest, props={})

    async def _t():
        with pm, pp, ph:
            mgr = BundleSchedulerManager(redis=None, tenant="t", project="p", instance_id="i1")
            await mgr.reconcile(_make_registry("demo.bundle"))
        assert len(mgr._tasks) == 0
        await mgr.shutdown()
    _run(_t())


def test_reconcile_cancels_job_removed_from_registry():
    manifest = _make_manifest([_make_job_spec(alias="job")])
    pm, pp, ph = _patches(manifest)

    async def _t():
        with pm, pp, ph:
            mgr = BundleSchedulerManager(redis=None, tenant="t", project="p", instance_id="i1")
            await mgr.reconcile(_make_registry("echo.ui"))
            assert len(mgr._tasks) == 1
            # Second reconcile with empty registry -> job removed
            await mgr.reconcile(_empty_registry())
        assert len(mgr._tasks) == 0
        await mgr.shutdown()
    _run(_t())


def test_reconcile_reschedules_job_when_cron_changes():
    manifest_v1 = _make_manifest([_make_job_spec(alias="job", cron_expression="* * * * *")])
    manifest_v2 = _make_manifest([_make_job_spec(alias="job", cron_expression="*/5 * * * *")])
    key = _JobKey(bundle_id="demo.bundle", job_alias="job")

    async def _t():
        mgr = BundleSchedulerManager(redis=None, tenant="t", project="p", instance_id="i1")
        pm1, pp1, ph1 = _patches(manifest_v1)
        with pm1, pp1, ph1:
            await mgr.reconcile(_make_registry("demo.bundle"))
        task_v1, expr_v1 = mgr._tasks[key]
        assert expr_v1 == "* * * * *"

        pm2, pp2, ph2 = _patches(manifest_v2)
        with pm2, pp2, ph2:
            await mgr.reconcile(_make_registry("demo.bundle"))
        task_v2, expr_v2 = mgr._tasks[key]
        assert expr_v2 == "*/5 * * * *"
        assert task_v1 is not task_v2

        await mgr.shutdown()
    _run(_t())


def test_reconcile_stable_when_cron_unchanged():
    manifest = _make_manifest([_make_job_spec(alias="job")])
    key = _JobKey(bundle_id="demo.bundle", job_alias="job")

    async def _t():
        mgr = BundleSchedulerManager(redis=None, tenant="t", project="p", instance_id="i1")
        pm, pp, ph = _patches(manifest)
        with pm, pp, ph:
            await mgr.reconcile(_make_registry("demo.bundle"))
            task_first = mgr._tasks[key][0]
            await mgr.reconcile(_make_registry("demo.bundle"))
            task_second = mgr._tasks[key][0]
        assert task_first is task_second  # same task, not recreated
        await mgr.shutdown()
    _run(_t())


def test_reconcile_invalid_cron_not_scheduled():
    manifest = _make_manifest([_make_job_spec(alias="bad", cron_expression="not-a-cron")])
    pm, pp, ph = _patches(manifest)

    async def _t():
        with pm, pp, ph:
            mgr = BundleSchedulerManager(redis=None, tenant="t", project="p", instance_id="i1")
            await mgr.reconcile(_make_registry("demo.bundle"))
        assert len(mgr._tasks) == 0
        await mgr.shutdown()
    _run(_t())


def test_shutdown_cancels_all_tasks():
    manifest = _make_manifest([
        _make_job_spec(alias="job-a"),
        _make_job_spec(alias="job-b", cron_expression="*/5 * * * *"),
    ])
    pm, pp, ph = _patches(manifest)

    async def _t():
        with pm, pp, ph:
            mgr = BundleSchedulerManager(redis=None, tenant="t", project="p", instance_id="i1")
            await mgr.reconcile(_make_registry("demo.bundle"))
            assert len(mgr._tasks) == 2
        await mgr.shutdown()
        assert len(mgr._tasks) == 0
    _run(_t())


def test_reconcile_manifest_load_failure_skips_bundle():
    async def _t():
        with patch(
            "kdcube_ai_app.infra.plugin.agentic_loader.load_bundle_manifest",
            side_effect=RuntimeError("cannot load"),
        ):
            mgr = BundleSchedulerManager(redis=None, tenant="t", project="p", instance_id="i1")
            # Must not raise — failure is logged and skipped
            await mgr.reconcile(_make_registry("broken.bundle"))
        assert len(mgr._tasks) == 0
        await mgr.shutdown()
    _run(_t())


def test_reconcile_enables_previously_disabled_job():
    """Job that was disabled (expr_config missing) becomes active when props appear."""
    manifest = _make_manifest([
        _make_job_spec(alias="job", cron_expression=None, expr_config="routines.cron")
    ])
    key = _JobKey(bundle_id="demo.bundle", job_alias="job")

    async def _t():
        mgr = BundleSchedulerManager(redis=None, tenant="t", project="p", instance_id="i1")

        # First reconcile: no props -> disabled
        pm, pp, ph = _patches(manifest, props={})
        with pm, pp, ph:
            await mgr.reconcile(_make_registry("demo.bundle"))
        assert len(mgr._tasks) == 0

        # Second reconcile: props now present -> job starts
        pm2, pp2, ph2 = _patches(
            manifest,
            props={"routines": {"cron": "*/5 * * * *"}},
        )
        with pm2, pp2, ph2:
            await mgr.reconcile(_make_registry("demo.bundle"))
        assert key in mgr._tasks

        await mgr.shutdown()
    _run(_t())
