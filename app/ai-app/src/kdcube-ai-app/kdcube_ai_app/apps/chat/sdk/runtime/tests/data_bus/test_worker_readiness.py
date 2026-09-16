# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    get_current_bundle_named_service_caller,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import DataBusClaim
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import (
    DataBusHandlerSpec,
    DataBusMessage,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus import worker as worker_module
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.worker import DataBusBundleWorker
from kdcube_ai_app.infra.plugin.app_readiness import (
    ApplicationReadinessMode,
    DesiredApplicationState,
    application_readiness_registry,
)


class _RecordingStream:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.results = []

    async def ack(self, claim) -> None:
        del claim
        self.calls.append("ack")

    async def write_result(self, *args, **kwargs) -> None:
        del kwargs
        self.calls.append("result")
        self.results.append(args[0])

    async def write_dlq(self, *args, **kwargs) -> None:
        del args, kwargs
        self.calls.append("dlq")


@pytest.mark.asyncio
async def test_unready_application_claim_is_deferred_without_acknowledgement() -> None:
    tenant = "tenant-data-bus"
    project = "project-data-bus"
    application_id = "app@1-0"
    application_readiness_registry.replace_desired(
        tenant=tenant,
        project=project,
        applications={
            application_id: DesiredApplicationState(
                generation="generation-a",
                readiness=ApplicationReadinessMode.INDEPENDENT,
            )
        },
    )
    try:
        stream = _RecordingStream()
        worker = object.__new__(DataBusBundleWorker)
        worker.stream = stream
        worker.handler_specs = {}
        claim = DataBusClaim(
            stream_key="messages",
            stream_id="1-0",
            consumer_name="worker-a",
            fields={},
            message=DataBusMessage(
                message_id="message-a",
                tenant=tenant,
                project=project,
                bundle_id=application_id,
                subject="object.updated",
            ),
        )

        await worker._process_claim(claim)

        assert stream.calls == []
    finally:
        application_readiness_registry.deactivate_scope(
            tenant=tenant,
            project=project,
            clear=True,
        )


@pytest.mark.asyncio
async def test_worker_rechecks_selected_application_operation_before_invocation() -> None:
    stream = _RecordingStream()
    worker = object.__new__(DataBusBundleWorker)
    worker.stream = stream
    worker.bundle_allowed_roles = ()
    worker.handler_specs = {
        "report.publish.requested": DataBusHandlerSpec(
            method_name="handle_publish",
            subject="report.publish.requested",
            operation_id="report.publish",
            operation_id_explicit=True,
        )
    }
    invoked = False

    async def _invoke_handler(*args, **kwargs):
        nonlocal invoked
        del args, kwargs
        invoked = True
        raise AssertionError("bundle code must not run")

    worker._invoke_handler = _invoke_handler
    message = DataBusMessage(
        message_id="message-card-denial",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="reports@1-0",
        subject="report.publish.requested",
        actor={
            "user_id": "grantor-user",
            "user_type": "registered",
            "roles": ["kdcube:role:registered"],
            "identity_authority": {
                "delegated_card_binding": {"access_id": "card-a"},
                "resource_operations": {
                    "*": [
                        "urn:kdcube:application-operation:reports%401-0:report.read"
                    ]
                },
            },
        },
    )
    claim = DataBusClaim(
        stream_key="messages",
        stream_id="1-0",
        consumer_name="worker-a",
        fields={},
        message=message,
    )

    await worker._process_claim(claim)

    assert invoked is False
    assert stream.calls == ["result", "ack"]
    assert stream.results[0].error["code"] == "application_operation_not_granted"
    assert stream.results[0].error["details"]["operation_ref"] == (
        "urn:kdcube:application-operation:reports%401-0:report.publish"
    )


@pytest.mark.asyncio
async def test_handler_binds_local_named_service_caller_for_its_lifetime(
    monkeypatch,
) -> None:
    sentinel = object()

    class _Bundle:
        async def handle(self, ctx, message):
            del ctx, message
            assert get_current_bundle_named_service_caller() is sentinel
            return {"status": "ok", "data": {"handled": True}}

    async def _workflow(*_args, **_kwargs):
        return _Bundle(), None

    async def _refresh(**_kwargs):
        return None

    async def _pg_pool():
        return object()

    monkeypatch.setattr(worker_module, "get_workflow_instance_async", _workflow)
    monkeypatch.setattr(worker_module, "_refresh_bundle_props", _refresh)
    monkeypatch.setattr(
        worker_module,
        "make_local_bundle_named_service_caller",
        lambda **_kwargs: sentinel,
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.ingress.resolvers.get_pg_pool",
        _pg_pool,
    )

    worker = object.__new__(DataBusBundleWorker)
    worker.bundle_spec = object()
    worker.bundle_config = object()
    worker.bundle_id = "provider@1-0"
    worker.redis = object()
    worker.relay = None
    message = DataBusMessage(
        message_id="message-caller-binding",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="provider@1-0",
        subject="named-service.relay",
        actor={"user_id": "user-1", "user_type": "registered"},
    )
    claim = DataBusClaim(
        stream_key="messages",
        stream_id="1-0",
        consumer_name="worker-a",
        fields={},
        message=message,
    )
    handler = DataBusHandlerSpec(
        method_name="handle",
        subject=message.subject,
    )

    result, reply_sent = await worker._invoke_handler(claim, handler)

    assert result.status == "ok"
    assert result.data == {"handled": True}
    assert reply_sent is False
    assert get_current_bundle_named_service_caller() is None
