# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace

import pytest

from kdcube_ai_app.infra.plugin.app_readiness import (
    ApplicationLifecycleState,
    ApplicationNotReadyError,
    ApplicationReadinessMode,
    ApplicationReadinessRegistry,
    DesiredApplicationState,
    application_readiness_registry,
)
from kdcube_ai_app.infra.plugin import bundle_loader
from kdcube_ai_app.infra.plugin.bundle_loader import BundleSpec
from kdcube_ai_app.infra.plugin.bundle_store import BundleEntry


def _desired(generation: str, readiness: str = "independent") -> DesiredApplicationState:
    return DesiredApplicationState(
        generation=generation,
        readiness=ApplicationReadinessMode(readiness),
    )


def test_bundle_service_readiness_is_optional_and_validated() -> None:
    default_entry = BundleEntry(id="app", path="/app")
    required_entry = BundleEntry(
        id="required",
        path="/required",
        service={"readiness": "required"},
    )

    assert default_entry.service is None
    assert required_entry.service is not None
    assert required_entry.service.readiness == "required"
    assert "service" not in default_entry.model_dump(exclude_none=True)

    with pytest.raises(ValueError):
        BundleEntry(id="bad", path="/bad", service={"readiness": "sometimes"})


def test_scope_is_compatibly_open_until_lifecycle_is_activated() -> None:
    registry = ApplicationReadinessRegistry()

    assert registry.require_ready(
        tenant="tenant-a",
        project="project-a",
        application_id="app",
    ) is None

    registry.replace_desired(
        tenant="tenant-a",
        project="project-a",
        applications={"known": _desired("generation-a")},
    )
    with pytest.raises(ApplicationNotReadyError) as raised:
        registry.require_ready(
            tenant="tenant-a",
            project="project-a",
            application_id="unknown",
        )
    assert raised.value.public_payload == {
        "type": "application_not_ready",
        "application_id": "unknown",
        "state": "preparing",
        "retryable": True,
    }


def test_stale_generation_completion_cannot_make_desired_state_ready() -> None:
    registry = ApplicationReadinessRegistry()
    registry.replace_desired(
        tenant="tenant-a",
        project="project-a",
        applications={"app": _desired("generation-a")},
    )
    assert registry.transition(
        tenant="tenant-a",
        project="project-a",
        application_id="app",
        generation="generation-a",
        state=ApplicationLifecycleState.READY,
        attempt=1,
    )

    registry.replace_desired(
        tenant="tenant-a",
        project="project-a",
        applications={"app": _desired("generation-b")},
    )
    assert not registry.transition(
        tenant="tenant-a",
        project="project-a",
        application_id="app",
        generation="generation-a",
        state=ApplicationLifecycleState.READY,
        attempt=1,
    )

    with pytest.raises(ApplicationNotReadyError) as raised:
        registry.require_ready(
            tenant="tenant-a",
            project="project-a",
            application_id="app",
        )
    assert "generation" not in raised.value.public_payload
    assert raised.value.snapshot.desired_generation == "generation-b"
    assert raised.value.snapshot.ready_generation == "generation-a"


def test_only_required_applications_block_aggregate_readiness() -> None:
    registry = ApplicationReadinessRegistry()
    registry.replace_desired(
        tenant="tenant-a",
        project="project-a",
        applications={
            "independent": _desired("independent-a"),
            "required": _desired("required-a", "required"),
        },
    )

    aggregate = registry.aggregate(tenant="tenant-a", project="project-a")
    assert aggregate.ready is False
    assert [item.application_id for item in aggregate.blocking] == ["required"]

    assert registry.transition(
        tenant="tenant-a",
        project="project-a",
        application_id="required",
        generation="required-a",
        state=ApplicationLifecycleState.READY,
        attempt=1,
    )
    aggregate = registry.aggregate(tenant="tenant-a", project="project-a")
    assert aggregate.ready is True
    assert aggregate.blocking == ()


@pytest.mark.parametrize(
    "state",
    [
        ApplicationLifecycleState.DEPROVISIONING,
        ApplicationLifecycleState.DEPROVISION_FAILED,
    ],
)
def test_deprovision_state_closes_admission_without_retry_signal(
    state: ApplicationLifecycleState,
) -> None:
    registry = ApplicationReadinessRegistry()
    registry.replace_desired(
        tenant="tenant-a",
        project="project-a",
        applications={"app": _desired("generation-a")},
    )
    assert registry.transition(
        tenant="tenant-a",
        project="project-a",
        application_id="app",
        generation="generation-a",
        state=state,
        error="cleanup failed" if state is ApplicationLifecycleState.DEPROVISION_FAILED else None,
    )

    with pytest.raises(ApplicationNotReadyError) as raised:
        registry.require_ready(
            tenant="tenant-a",
            project="project-a",
            application_id="app",
        )

    assert raised.value.public_payload == {
        "type": "application_deprovisioning",
        "application_id": "app",
        "state": state.value,
        "retryable": False,
    }


@pytest.mark.asyncio
async def test_runtime_loader_checks_admission_before_importing_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = "tenant-loader"
    project = "project-loader"
    application_id = "app@1-0"
    application_readiness_registry.replace_desired(
        tenant=tenant,
        project=project,
        applications={application_id: _desired("generation-a")},
    )
    imported = False

    def _load(*args, **kwargs):
        nonlocal imported
        del args, kwargs
        imported = True
        raise AssertionError("unready application must not be imported")

    monkeypatch.setattr(bundle_loader, "get_workflow_instance", _load)
    try:
        with pytest.raises(ApplicationNotReadyError):
            await bundle_loader.get_workflow_instance_async(
                BundleSpec(
                    id=application_id,
                    path="/not/materialized",
                    module="entrypoint",
                ),
                SimpleNamespace(tenant=tenant, project=project),
                comm_context=SimpleNamespace(
                    actor=SimpleNamespace(tenant_id=tenant, project_id=project)
                ),
            )
        assert imported is False
    finally:
        application_readiness_registry.deactivate_scope(
            tenant=tenant,
            project=project,
            clear=True,
        )
