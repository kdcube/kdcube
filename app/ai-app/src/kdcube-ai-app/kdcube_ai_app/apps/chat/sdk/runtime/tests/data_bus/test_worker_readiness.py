# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace

import pytest

from connection_hub.delegated_credentials.live_grant import LiveGrantCardError
from kdcube_ai_app.apps.chat.sdk.application_operations import (
    APPLICATION_OPERATION_POLICY_PROPERTY,
    application_operation_policy,
    application_operation_ref,
)
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    get_current_bundle_named_service_caller,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import DataBusClaim
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import (
    DATA_BUS_ORDERING_SERIAL_PER_PARTITION,
    DATA_BUS_PARTITION_OBJECT_REF,
    DataBusHandlerSpec,
    DataBusMessage,
    DataBusResult,
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


_DELEGATED_RESOURCE = (
    "https://board.example/api/integrations/bundles/tenant-data-bus/"
    "project-data-bus/reports@1-0/public/mcp/reports"
)


def _delegated_actor(*, selected_operations: tuple[str, ...] = ()) -> dict:
    return {
        "user_id": "grantor-user",
        "user_type": "privileged",
        "roles": ["kdcube:role:super-admin"],
        "permissions": ["kdcube:*"],
        "identity_authority": {
            APPLICATION_OPERATION_POLICY_PROPERTY: application_operation_policy(),
            "delegated_resource": _DELEGATED_RESOURCE,
            "delegated_card_binding": {
                "access_id": "card-a",
                "client_id": "worker-client-a",
                "grantor_user_id": "grantor-user",
                "delegate_identity": "worker-a",
            },
            "resource_operations": {"*": list(selected_operations)},
        },
    }


def _live_card(
    *,
    operations: tuple[str, ...] = (),
    policy_enabled: bool = True,
    grants: tuple[str, ...] = ("kdcube:role:registered",),
):
    return SimpleNamespace(
        access_id="card-a",
        client_id="worker-client-a",
        grantor_subject="grantor-user",
        delegate_subject="worker-a",
        expires_at=2_000_000_000,
        resource_grants={_DELEGATED_RESOURCE: grants},
        resource_operations={"*": operations},
        properties=(
            {
                APPLICATION_OPERATION_POLICY_PROPERTY: application_operation_policy(),
            }
            if policy_enabled
            else {}
        ),
    )


def _worker_with_handler(stream: _RecordingStream, handler: DataBusHandlerSpec):
    worker = object.__new__(DataBusBundleWorker)
    worker.redis = object()
    worker.stream = stream
    worker.bundle_allowed_roles = ()
    worker.handler_specs = {handler.subject: handler}
    return worker


def _claim(message: DataBusMessage, *, stream_id: str = "1-0") -> DataBusClaim:
    return DataBusClaim(
        stream_key="messages",
        stream_id=stream_id,
        consumer_name="worker-a",
        fields={},
        message=message,
    )


def _patch_live_card(monkeypatch, card) -> None:
    async def _resolve(*_args, **_kwargs):
        return card

    monkeypatch.setattr(worker_module, "resolve_live_grant_card", _resolve)


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
async def test_worker_rechecks_selected_application_operation_before_invocation(
    monkeypatch,
) -> None:
    stream = _RecordingStream()
    handler = DataBusHandlerSpec(
        method_name="handle_publish",
        subject="report.publish.requested",
        operation_id="report.publish",
        operation_id_explicit=True,
    )
    worker = _worker_with_handler(stream, handler)
    invoked = False

    async def _invoke_handler(*args, **kwargs):
        nonlocal invoked
        del args, kwargs
        invoked = True
        raise AssertionError("bundle code must not run")

    replies = []

    async def _send_default_reply(message, result) -> None:
        stream.calls.append("reply")
        replies.append((message, result))

    worker._invoke_handler = _invoke_handler
    worker._send_default_reply = _send_default_reply
    queued_operation = application_operation_ref(
        application_id="reports@1-0",
        operation_id="report.publish",
    )
    current_operation = application_operation_ref(
        application_id="reports@1-0",
        operation_id="report.read",
    )
    _patch_live_card(monkeypatch, _live_card(operations=(current_operation,)))
    message = DataBusMessage(
        message_id="message-card-denial",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="reports@1-0",
        subject="report.publish.requested",
        actor=_delegated_actor(selected_operations=(queued_operation,)),
    )

    await worker._process_claim(_claim(message))

    assert invoked is False
    assert stream.calls == ["result", "reply", "ack"]
    assert len(replies) == 1
    assert replies[0][0].message_id == message.message_id
    assert replies[0][0].actor["roles"] == ["kdcube:role:registered"]
    assert replies[0][1] == stream.results[0]
    assert stream.results[0].error["code"] == "application_operation_not_granted"
    assert stream.results[0].error["details"]["operation_ref"] == (
        "urn:kdcube:application-operation:reports%401-0:report.publish"
    )


@pytest.mark.asyncio
async def test_pre_policy_empty_wildcard_row_stays_compatible_and_live_role_downscopes(
    monkeypatch,
) -> None:
    stream = _RecordingStream()
    handler = DataBusHandlerSpec(
        method_name="handle_publish",
        subject="report.publish.requested",
        operation_id="report.publish",
        operation_id_explicit=True,
        user_types=("registered",),
    )
    worker = _worker_with_handler(stream, handler)
    _patch_live_card(
        monkeypatch,
        _live_card(operations=(), policy_enabled=False),
    )
    observed_actor = {}

    async def _invoke_handler(claim, _handler):
        observed_actor.update(claim.message.actor)
        return DataBusResult.ok(claim.message, {"handled": True}), True

    worker._invoke_handler = _invoke_handler
    message = DataBusMessage(
        message_id="message-legacy-card",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="reports@1-0",
        subject=handler.subject,
        actor=_delegated_actor(),
    )

    await worker._process_claim(_claim(message))

    assert stream.calls == ["result", "ack"]
    assert stream.results[0].status == "ok"
    assert observed_actor["user_type"] == "registered"
    assert observed_actor["roles"] == ["kdcube:role:registered"]
    assert "kdcube:role:super-admin" not in observed_actor["roles"]
    assert APPLICATION_OPERATION_POLICY_PROPERTY not in observed_actor[
        "identity_authority"
    ]


@pytest.mark.asyncio
async def test_live_registered_card_cannot_enter_privileged_handler(monkeypatch) -> None:
    stream = _RecordingStream()
    operation = application_operation_ref(
        application_id="reports@1-0",
        operation_id="report.publish",
    )
    handler = DataBusHandlerSpec(
        method_name="handle_publish",
        subject="report.publish.requested",
        operation_id="report.publish",
        operation_id_explicit=True,
        user_types=("privileged",),
    )
    worker = _worker_with_handler(stream, handler)
    _patch_live_card(monkeypatch, _live_card(operations=(operation,)))

    async def _invoke_handler(*_args, **_kwargs):
        raise AssertionError("bundle code must not run")

    async def _send_default_reply(_message, _result) -> None:
        stream.calls.append("reply")

    worker._invoke_handler = _invoke_handler
    worker._send_default_reply = _send_default_reply
    message = DataBusMessage(
        message_id="message-privileged-denial",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="reports@1-0",
        subject=handler.subject,
        actor=_delegated_actor(selected_operations=(operation,)),
    )

    await worker._process_claim(_claim(message))

    assert stream.calls == ["result", "reply", "ack"]
    assert stream.results[0].error["code"] == "handler_not_visible"


@pytest.mark.asyncio
async def test_revoked_card_is_rejected_before_bundle_code(monkeypatch) -> None:
    stream = _RecordingStream()
    handler = DataBusHandlerSpec(
        method_name="handle_publish",
        subject="report.publish.requested",
    )
    worker = _worker_with_handler(stream, handler)
    _patch_live_card(monkeypatch, None)

    async def _invoke_handler(*_args, **_kwargs):
        raise AssertionError("bundle code must not run")

    async def _send_default_reply(_message, _result) -> None:
        stream.calls.append("reply")

    worker._invoke_handler = _invoke_handler
    worker._send_default_reply = _send_default_reply
    message = DataBusMessage(
        message_id="message-revoked-card",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="reports@1-0",
        subject=handler.subject,
        actor=_delegated_actor(),
    )

    await worker._process_claim(_claim(message))

    assert stream.calls == ["result", "reply", "ack"]
    assert stream.results[0].error["code"] == "delegated_card_not_active"


@pytest.mark.asyncio
async def test_unavailable_card_state_is_rejected_with_reason(monkeypatch) -> None:
    stream = _RecordingStream()
    handler = DataBusHandlerSpec(
        method_name="handle_publish",
        subject="report.publish.requested",
    )
    worker = _worker_with_handler(stream, handler)

    async def _resolve(*_args, **_kwargs):
        raise LiveGrantCardError("card_updating")

    monkeypatch.setattr(worker_module, "resolve_live_grant_card", _resolve)

    async def _send_default_reply(_message, _result) -> None:
        stream.calls.append("reply")

    worker._send_default_reply = _send_default_reply
    message = DataBusMessage(
        message_id="message-card-unavailable",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="reports@1-0",
        subject=handler.subject,
        actor=_delegated_actor(),
    )

    await worker._process_claim(_claim(message))

    assert stream.calls == ["result", "reply", "ack"]
    assert stream.results[0].error["code"] == "delegated_card_unavailable"
    assert stream.results[0].error["details"] == {"reason": "card_updating"}


@pytest.mark.asyncio
async def test_implicit_data_bus_handler_keeps_dynamic_dispatch_authority(monkeypatch) -> None:
    stream = _RecordingStream()
    handler = DataBusHandlerSpec(
        method_name="handle_command",
        subject="problem_board.command.v1",
        operation_id="data_bus.problem_board.command.v1",
        operation_id_explicit=False,
        user_types=("registered",),
    )
    worker = _worker_with_handler(stream, handler)
    _patch_live_card(monkeypatch, _live_card(operations=()))
    invoked = False

    async def _invoke_handler(claim, _handler):
        nonlocal invoked
        invoked = True
        assert claim.message.actor["roles"] == ["kdcube:role:registered"]
        return DataBusResult.ok(claim.message, {"handled": True}), True

    worker._invoke_handler = _invoke_handler
    message = DataBusMessage(
        message_id="message-dynamic-dispatch",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="reports@1-0",
        subject=handler.subject,
        actor=_delegated_actor(),
    )

    await worker._process_claim(_claim(message))

    assert invoked is True
    assert stream.calls == ["result", "ack"]
    assert stream.results[0].status == "ok"


@pytest.mark.asyncio
async def test_terminal_rejection_persists_dlq_and_replies_before_acknowledgement() -> None:
    stream = _RecordingStream()
    worker = object.__new__(DataBusBundleWorker)
    worker.stream = stream
    worker.handler_specs = {}
    message = DataBusMessage(
        message_id="message-missing-handler",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="reports@1-0",
        subject="report.missing",
    )
    claim = DataBusClaim(
        stream_key="messages",
        stream_id="2-0",
        consumer_name="worker-a",
        fields={},
        message=message,
    )

    async def _send_default_reply(reply_message, result) -> None:
        assert reply_message is message
        assert result.error["code"] == "handler_not_found"
        stream.calls.append("reply")

    worker._send_default_reply = _send_default_reply

    await worker._process_claim(claim)

    assert stream.calls == ["result", "dlq", "reply", "ack"]


@pytest.mark.asyncio
async def test_terminal_rejection_is_not_acknowledged_when_reply_delivery_fails() -> None:
    stream = _RecordingStream()
    worker = object.__new__(DataBusBundleWorker)
    worker.stream = stream
    worker.handler_specs = {}
    message = DataBusMessage(
        message_id="message-reply-failure",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="reports@1-0",
        subject="report.missing",
    )
    claim = DataBusClaim(
        stream_key="messages",
        stream_id="3-0",
        consumer_name="worker-a",
        fields={},
        message=message,
    )

    async def _send_default_reply(*_args, **_kwargs) -> None:
        stream.calls.append("reply")
        raise RuntimeError("reply unavailable")

    worker._send_default_reply = _send_default_reply

    with pytest.raises(RuntimeError, match="reply unavailable"):
        await worker._process_claim(claim)

    assert stream.calls == ["result", "dlq", "reply"]


@pytest.mark.asyncio
async def test_completed_handler_is_not_retried_when_reply_delivery_fails() -> None:
    stream = _RecordingStream()
    worker = object.__new__(DataBusBundleWorker)
    worker.stream = stream
    worker.bundle_allowed_roles = ()
    message = DataBusMessage(
        message_id="message-completed-handler",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="reports@1-0",
        subject="report.publish.requested",
    )
    worker.handler_specs = {
        message.subject: DataBusHandlerSpec(
            method_name="handle_publish",
            subject=message.subject,
        )
    }
    claim = DataBusClaim(
        stream_key="messages",
        stream_id="4-0",
        consumer_name="worker-a",
        fields={},
        message=message,
    )
    invocation_count = 0

    async def _invoke_handler(*_args, **_kwargs):
        nonlocal invocation_count
        invocation_count += 1
        return DataBusResult.ok(message, {"stored": True}), False

    async def _send_default_reply(*_args, **_kwargs) -> None:
        stream.calls.append("reply")
        raise RuntimeError("reply unavailable")

    worker._invoke_handler = _invoke_handler
    worker._send_default_reply = _send_default_reply

    with pytest.raises(RuntimeError, match="reply unavailable"):
        await worker._process_claim(claim)

    assert invocation_count == 1
    assert stream.calls == ["result", "reply"]


@pytest.mark.asyncio
async def test_exhausted_partition_lock_retry_returns_terminal_result(
    monkeypatch,
) -> None:
    class _BusyLocker:
        async def acquire(self, _partition_key):
            return None

    stream = _RecordingStream()
    worker = object.__new__(DataBusBundleWorker)
    worker.stream = stream
    worker.locker = _BusyLocker()
    worker.bundle_allowed_roles = ()
    message = DataBusMessage(
        message_id="message-lock-exhausted",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="reports@1-0",
        subject="report.publish.requested",
        object_ref="report:one",
        trace={"retry_count": 2},
    )
    worker.handler_specs = {
        message.subject: DataBusHandlerSpec(
            method_name="handle_publish",
            subject=message.subject,
            ordering=DATA_BUS_ORDERING_SERIAL_PER_PARTITION,
            partition_by=DATA_BUS_PARTITION_OBJECT_REF,
        )
    }
    claim = DataBusClaim(
        stream_key="messages",
        stream_id="5-0",
        consumer_name="worker-a",
        fields={},
        message=message,
    )

    async def _send_default_reply(_message, result) -> None:
        assert result.error["code"] == "partition_lock_busy"
        stream.calls.append("reply")

    worker._send_default_reply = _send_default_reply
    monkeypatch.setattr(worker_module, "DATA_BUS_LOCK_MAX_RETRIES", 2)
    monkeypatch.setattr(worker_module, "DATA_BUS_LOCK_RETRY_SLEEP_SECONDS", 0)

    await worker._process_claim(claim)

    assert stream.calls == ["result", "dlq", "reply", "ack"]


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
