"""App readiness and the periodic reconcile refresh the site catalog.

A catalog published last by an older peer stays until some process refreshes
it; readiness and the periodic loop are the refresh points.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from kdcube_ai_app.apps.chat import processor as processor_module
from kdcube_ai_app.apps.chat.processor import EnhancedChatRequestProcessor
from kdcube_ai_app.apps.chat.sdk.solutions import sites


@pytest.fixture
def catalog_refreshes(monkeypatch):
    refreshes: list[dict] = []

    async def _refresh(redis, **kwargs):
        refreshes.append({"redis": redis, **kwargs})

    monkeypatch.setattr(sites, "refresh_application_site_catalog", _refresh)
    monkeypatch.setattr(
        processor_module,
        "get_settings",
        lambda: SimpleNamespace(TENANT="tenant-a", PROJECT="project-a", plain=lambda key, default=None: None),
    )
    return refreshes


def _processor() -> EnhancedChatRequestProcessor:
    instance = object.__new__(EnhancedChatRequestProcessor)
    instance.redis = object()
    instance.scheduler_reasons = []

    async def _scheduler_reconcile(reason):
        instance.scheduler_reasons.append(reason)

    instance._reconcile_bundle_scheduler_from_authority = _scheduler_reconcile
    return instance


@pytest.mark.asyncio
async def test_application_ready_refreshes_the_site_catalog(catalog_refreshes) -> None:
    processor = _processor()

    await processor.reconcile_application_runtime("application-ready:landing@1")

    assert processor.scheduler_reasons == ["application-ready:landing@1"]
    assert len(catalog_refreshes) == 1
    refresh = catalog_refreshes[0]
    assert refresh["redis"] is processor.redis
    assert (refresh["tenant"], refresh["project"]) == ("tenant-a", "project-a")
    assert refresh["runtime"] is sites.application_site_catalog_runtime


@pytest.mark.asyncio
async def test_periodic_reconcile_refreshes_the_site_catalog(catalog_refreshes) -> None:
    processor = _processor()
    processor._bundle_scheduler_reconcile_interval_sec = 0.0
    processor._stop_event = asyncio.Event()

    async def _scheduler_reconcile(reason):
        processor.scheduler_reasons.append(reason)
        if len(processor.scheduler_reasons) >= 2:
            processor._stop_event.set()

    processor._reconcile_bundle_scheduler_from_authority = _scheduler_reconcile

    await asyncio.wait_for(processor._bundle_scheduler_reconcile_loop(), timeout=5)

    assert processor.scheduler_reasons == ["periodic", "periodic"]
    assert len(catalog_refreshes) == 2


@pytest.mark.asyncio
async def test_catalog_reconcile_failure_does_not_break_readiness(monkeypatch, catalog_refreshes) -> None:
    async def _fail(redis, **kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(sites, "refresh_application_site_catalog", _fail)
    processor = _processor()

    await processor.reconcile_application_runtime("application-ready:landing@1")

    assert processor.scheduler_reasons == ["application-ready:landing@1"]


@pytest.mark.asyncio
async def test_application_ready_refreshes_the_catalog_when_the_scheduler_fails(catalog_refreshes) -> None:
    processor = _processor()

    async def _scheduler_fails(reason):
        raise RuntimeError("scheduler reconcile failed")

    processor._reconcile_bundle_scheduler_from_authority = _scheduler_fails

    with pytest.raises(RuntimeError, match="scheduler reconcile failed"):
        await processor.reconcile_application_runtime("application-ready:landing@1")

    assert len(catalog_refreshes) == 1


@pytest.mark.asyncio
async def test_application_ready_cancellation_skips_the_catalog_refresh(catalog_refreshes) -> None:
    processor = _processor()

    async def _cancelled(reason):
        raise asyncio.CancelledError()

    processor._reconcile_bundle_scheduler_from_authority = _cancelled

    with pytest.raises(asyncio.CancelledError):
        await processor.reconcile_application_runtime("application-ready:landing@1")

    assert catalog_refreshes == []


@pytest.mark.asyncio
async def test_periodic_reconcile_refreshes_the_catalog_when_the_scheduler_fails(catalog_refreshes) -> None:
    processor = _processor()
    processor._bundle_scheduler_reconcile_interval_sec = 0.0
    processor._stop_event = asyncio.Event()
    attempts: list[str] = []

    async def _scheduler_fails(reason):
        attempts.append(reason)
        if len(attempts) >= 2:
            processor._stop_event.set()
        raise RuntimeError("scheduler reconcile failed")

    processor._reconcile_bundle_scheduler_from_authority = _scheduler_fails

    await asyncio.wait_for(processor._bundle_scheduler_reconcile_loop(), timeout=5)

    assert attempts == ["periodic", "periodic"]
    assert len(catalog_refreshes) == 2
