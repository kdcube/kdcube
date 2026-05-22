# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""
Descriptor serialization tests for scheduled_jobs.

Verifies that _manifest_to_descriptor() and _manifest_to_descriptor_filtered()
include the declared scheduled_jobs list and that it is not role-filtered.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from kdcube_ai_app.apps.chat.proc.rest.integrations.integrations import (
    _manifest_to_descriptor,
    _manifest_to_descriptor_filtered,
)
from kdcube_ai_app.infra.plugin.bundle_loader import (
    BundleInterfaceManifest,
    CronJobSpec,
    OnJobSpec,
)


def _make_manifest(jobs: list[CronJobSpec]) -> BundleInterfaceManifest:
    return BundleInterfaceManifest(
        bundle_id="test.bundle",
        scheduled_jobs=tuple(jobs),
    )


def _make_manifest_with_on_job() -> BundleInterfaceManifest:
    return BundleInterfaceManifest(
        bundle_id="test.bundle",
        on_job=OnJobSpec(method_name="on_job"),
    )


def _make_session(roles: list[str] = None) -> MagicMock:
    session = MagicMock()
    session.roles = roles or []
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_descriptor_includes_scheduled_jobs():
    jobs = [
        CronJobSpec(
            method_name="rebuild_indexes",
            alias="rebuild",
            cron_expression=None,
            expr_config="apps.app1.routines.cron",
            timezone="Europe/Berlin",
            tz_config="apps.app1.routines.timezone",
            span="system",
        )
    ]
    desc = _manifest_to_descriptor(_make_manifest(jobs))
    assert "scheduled_jobs" in desc
    assert len(desc["scheduled_jobs"]) == 1
    job = desc["scheduled_jobs"][0]
    assert job["method_name"] == "rebuild_indexes"
    assert job["alias"] == "rebuild"
    assert job["cron_expression"] is None
    assert job["expr_config"] == "apps.app1.routines.cron"
    assert job["timezone"] == "Europe/Berlin"
    assert job["tz_config"] == "apps.app1.routines.timezone"
    assert job["span"] == "system"


def test_descriptor_empty_when_no_jobs():
    desc = _manifest_to_descriptor(_make_manifest([]))
    assert desc["scheduled_jobs"] == []


def test_descriptor_includes_on_job():
    desc = _manifest_to_descriptor(_make_manifest_with_on_job())
    filtered = _manifest_to_descriptor_filtered(_make_manifest_with_on_job(), _make_session(roles=[]))

    assert desc["on_job"] == "on_job"
    assert filtered["on_job"] == "on_job"


def test_descriptor_multiple_jobs():
    jobs = [
        CronJobSpec(method_name="job_a", alias="job-a", cron_expression="* * * * *", span="process"),
        CronJobSpec(method_name="job_b", alias="job-b", expr_config="routines.b.cron", span="system"),
    ]
    desc = _manifest_to_descriptor(_make_manifest(jobs))
    assert len(desc["scheduled_jobs"]) == 2
    aliases = {j["alias"] for j in desc["scheduled_jobs"]}
    assert aliases == {"job-a", "job-b"}


def test_descriptor_filtered_includes_scheduled_jobs_regardless_of_role():
    """scheduled_jobs are system metadata — not filtered by user role."""
    jobs = [
        CronJobSpec(method_name="heartbeat", alias="hb", cron_expression="* * * * *", span="system"),
    ]
    manifest = _make_manifest(jobs)

    # Session with no roles
    desc = _manifest_to_descriptor_filtered(manifest, _make_session(roles=[]))
    assert len(desc["scheduled_jobs"]) == 1
    assert desc["scheduled_jobs"][0]["alias"] == "hb"

    # Session with arbitrary roles
    desc2 = _manifest_to_descriptor_filtered(manifest, _make_session(roles=["kdcube:role:chat-user"]))
    assert len(desc2["scheduled_jobs"]) == 1


def test_descriptor_preserves_all_fields():
    """Cron descriptor exposes both the effective values (after expr_config /
    tz_config overrides) and the decorator-declared defaults."""
    job = CronJobSpec(
        method_name="my_method",
        alias="my-alias",
        cron_expression="*/30 * * * *",
        expr_config="some.config.path",
        timezone="Europe/Berlin",
        tz_config="some.timezone.path",
        span="instance",
    )
    # No props: expr_config / tz_config paths do not resolve; effective cron
    # is None (job not scheduled), effective timezone falls back to decorator
    # default.
    desc = _manifest_to_descriptor(_make_manifest([job]))
    serialized = desc["scheduled_jobs"][0]
    assert serialized["method_name"] == "my_method"
    assert serialized["alias"] == "my-alias"
    assert serialized["cron_expression"] is None
    assert serialized["cron_expression_default"] == "*/30 * * * *"
    assert serialized["expr_config"] == "some.config.path"
    assert serialized["timezone"] == "Europe/Berlin"
    assert serialized["timezone_default"] == "Europe/Berlin"
    assert serialized["tz_config"] == "some.timezone.path"
    assert serialized["span"] == "instance"

    # With props that resolve the override paths, effective values reflect
    # the override and the *_overridden flags are set.
    desc2 = _manifest_to_descriptor(
        _make_manifest([job]),
        props={
            "some": {
                "config": {"path": "*/15 * * * *"},
                "timezone": {"path": "UTC"},
            },
        },
    )
    serialized2 = desc2["scheduled_jobs"][0]
    assert serialized2["cron_expression"] == "*/15 * * * *"
    assert serialized2["cron_expression_default"] == "*/30 * * * *"
    assert serialized2["cron_expression_overridden"] is True
    assert serialized2["timezone"] == "UTC"
    assert serialized2["timezone_default"] == "Europe/Berlin"
    assert serialized2["timezone_overridden"] is True
