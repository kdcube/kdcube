# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""
Loader discovery tests for @cron-decorated bundle methods.

Verifies that CronJobSpec entries are correctly collected into
BundleInterfaceManifest.scheduled_jobs by discover_bundle_interface_manifest().
"""
from __future__ import annotations

import pytest

from kdcube_ai_app.infra.plugin.agentic_loader import (
    cron,
    CronJobSpec,
    discover_bundle_interface_manifest,
)


# ---------------------------------------------------------------------------
# Minimal stub bundle classes
# ---------------------------------------------------------------------------

class _BundleWithInlineExpr:
    BUNDLE_ID = "test.inline"

    @cron(alias="do-work", cron_expression="*/5 * * * *", span="process")
    async def do_work(self) -> None:
        pass


class _BundleWithExprConfig:
    BUNDLE_ID = "test.cfg"

    @cron(alias="rebuild", expr_config="routines.rebuild.cron", span="system")
    async def rebuild(self) -> None:
        pass


class _BundleWithBothSources:
    BUNDLE_ID = "test.both"

    @cron(
        alias="job",
        cron_expression="*/15 * * * *",
        expr_config="routines.job.cron",
        span="instance",
    )
    async def job(self) -> None:
        pass


class _BundleWithNoAlias:
    BUNDLE_ID = "test.noalias"

    @cron(cron_expression="0 * * * *", span="process")
    async def hourly_task(self) -> None:
        pass


class _BundleWithMultipleCronMethods:
    BUNDLE_ID = "test.multi"

    @cron(alias="job-a", cron_expression="*/5 * * * *", span="process")
    async def job_a(self) -> None:
        pass

    @cron(alias="job-b", cron_expression="0 * * * *", span="system")
    async def job_b(self) -> None:
        pass


class _BundleWithSyncMethod:
    BUNDLE_ID = "test.sync"

    @cron(alias="sync-job", cron_expression="*/10 * * * *", span="process")
    def sync_job(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_cron_expression_discovered():
    manifest = discover_bundle_interface_manifest(_BundleWithInlineExpr, bundle_id="test.inline")
    assert len(manifest.scheduled_jobs) == 1
    job = manifest.scheduled_jobs[0]
    assert isinstance(job, CronJobSpec)
    assert job.alias == "do-work"
    assert job.cron_expression == "*/5 * * * *"
    assert job.expr_config is None
    assert job.span == "process"
    assert job.method_name == "do_work"


def test_expr_config_discovered():
    manifest = discover_bundle_interface_manifest(_BundleWithExprConfig, bundle_id="test.cfg")
    assert len(manifest.scheduled_jobs) == 1
    job = manifest.scheduled_jobs[0]
    assert job.alias == "rebuild"
    assert job.cron_expression is None
    assert job.expr_config == "routines.rebuild.cron"
    assert job.span == "system"


def test_both_sources_preserved_in_manifest():
    manifest = discover_bundle_interface_manifest(_BundleWithBothSources, bundle_id="test.both")
    assert len(manifest.scheduled_jobs) == 1
    job = manifest.scheduled_jobs[0]
    assert job.cron_expression == "*/15 * * * *"
    assert job.expr_config == "routines.job.cron"
    assert job.span == "instance"


def test_alias_defaults_to_method_name():
    manifest = discover_bundle_interface_manifest(_BundleWithNoAlias, bundle_id="test.noalias")
    assert len(manifest.scheduled_jobs) == 1
    job = manifest.scheduled_jobs[0]
    assert job.alias == "hourly_task"
    assert job.method_name == "hourly_task"


def test_multiple_cron_methods_on_same_bundle_allowed():
    manifest = discover_bundle_interface_manifest(_BundleWithMultipleCronMethods, bundle_id="test.multi")
    assert len(manifest.scheduled_jobs) == 2
    aliases = {j.alias for j in manifest.scheduled_jobs}
    assert aliases == {"job-a", "job-b"}


def test_sync_method_discovered():
    manifest = discover_bundle_interface_manifest(_BundleWithSyncMethod, bundle_id="test.sync")
    assert len(manifest.scheduled_jobs) == 1
    assert manifest.scheduled_jobs[0].alias == "sync-job"


def test_invalid_span_rejected_at_decoration_time():
    with pytest.raises(ValueError, match="Invalid cron span"):
        @cron(cron_expression="* * * * *", span="weekly")
        async def bad(self) -> None:
            pass


def test_empty_span_defaults_to_system():
    class _Bundle:
        BUNDLE_ID = "test.empty-span"

        @cron(cron_expression="* * * * *", span="")
        async def job(self) -> None:
            pass

    manifest = discover_bundle_interface_manifest(_Bundle, bundle_id="test.empty-span")
    assert manifest.scheduled_jobs[0].span == "system"


def test_default_span_is_system():
    class _Bundle:
        BUNDLE_ID = "test.default-span"

        @cron(cron_expression="* * * * *")
        async def job(self) -> None:
            pass

    manifest = discover_bundle_interface_manifest(_Bundle, bundle_id="test.default-span")
    assert manifest.scheduled_jobs[0].span == "system"


def test_bundle_without_cron_has_empty_scheduled_jobs():
    class _NoCronBundle:
        BUNDLE_ID = "test.nocron"

        async def regular_method(self) -> None:
            pass

    manifest = discover_bundle_interface_manifest(_NoCronBundle, bundle_id="test.nocron")
    assert manifest.scheduled_jobs == ()


def test_scheduled_jobs_sorted_by_alias():
    class _Sorted:
        BUNDLE_ID = "test.sorted"

        @cron(alias="zzz", cron_expression="* * * * *", span="process")
        async def zzz(self) -> None:
            pass

        @cron(alias="aaa", cron_expression="* * * * *", span="process")
        async def aaa(self) -> None:
            pass

    manifest = discover_bundle_interface_manifest(_Sorted, bundle_id="test.sorted")
    assert [j.alias for j in manifest.scheduled_jobs] == ["aaa", "zzz"]
