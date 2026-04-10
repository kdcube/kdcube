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
from kdcube_ai_app.infra.plugin.agentic_loader import (
    BundleInterfaceManifest,
    CronJobSpec,
)


def _make_manifest(jobs: list[CronJobSpec]) -> BundleInterfaceManifest:
    return BundleInterfaceManifest(
        bundle_id="test.bundle",
        scheduled_jobs=tuple(jobs),
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
    assert job["span"] == "system"


def test_descriptor_empty_when_no_jobs():
    desc = _manifest_to_descriptor(_make_manifest([]))
    assert desc["scheduled_jobs"] == []


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
    job = CronJobSpec(
        method_name="my_method",
        alias="my-alias",
        cron_expression="*/30 * * * *",
        expr_config="some.config.path",
        span="instance",
    )
    desc = _manifest_to_descriptor(_make_manifest([job]))
    serialized = desc["scheduled_jobs"][0]
    assert serialized["method_name"] == "my_method"
    assert serialized["alias"] == "my-alias"
    assert serialized["cron_expression"] == "*/30 * * * *"
    assert serialized["expr_config"] == "some.config.path"
    assert serialized["span"] == "instance"
