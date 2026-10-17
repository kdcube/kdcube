# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace

import pytest

from connection_hub.delegated_credentials.live_grant import LiveGrantCardError
from kdcube_ai_app.apps.chat.emitters import (
    ChatCommunicator,
    build_comm_from_comm_context,
)
from kdcube_ai_app.apps.chat.sdk.application_operations import (
    APPLICATION_OPERATION_POLICY_PROPERTY,
    APPLICATION_OPERATION_POLICY_SCHEMA_V2,
    application_operation_policy,
    application_operation_ref,
)
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    call_bundle_operation,
    get_current_bundle_operation_caller,
    get_current_bundle_operation_stream_caller,
    get_current_bundle_named_service_caller,
)
from kdcube_ai_app.apps.chat.sdk.identity_authority import (
    NativePlatformIdentity,
    PlatformAuthorityResolutionError,
    authority_from_source,
)
from kdcube_ai_app.apps.chat.sdk.protocol import ExternalEventPayload
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus import (
    delegated_authority as delegated_authority_module,
    publisher as publisher_module,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.publisher import DataBusPublisher
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
from kdcube_ai_app.auth.role_hierarchy import (
    ADMIN_ROLE,
    PAID_ROLE,
    REGISTERED_ROLE,
    SUPER_ADMIN_ROLE,
)
from kdcube_ai_app.infra.plugin.app_readiness import (
    ApplicationReadinessMode,
    DesiredApplicationState,
    application_readiness_registry,
)


def test_data_bus_role_visibility_uses_platform_dominance_only() -> None:
    actor = {"roles": ["kdcube:role:super-admin"]}

    assert worker_module._raw_roles_visible(
        ("kdcube:role:registered",),
        actor,
    ) is True
    assert worker_module._raw_roles_visible(
        ("kdcube:role:paid",),
        actor,
    ) is True
    assert worker_module._raw_roles_visible(
        ("kdcube:role:service",),
        actor,
    ) is False


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
    default_role: str = "",
    operation_roles: dict[str, str] | None = None,
):
    policy = application_operation_policy()
    if default_role:
        policy = {
            "schema": APPLICATION_OPERATION_POLICY_SCHEMA_V2,
            "mode": "selected",
            "default_role": default_role,
            "operation_roles": dict(operation_roles or {}),
        }
    return SimpleNamespace(
        access_id="card-a",
        card_revision=7,
        client_id="worker-client-a",
        grantor_subject="grantor-user",
        delegate_subject="worker-a",
        expires_at=2_000_000_000,
        resource_grants={
            _DELEGATED_RESOURCE: grants,
            "*": (default_role,) if default_role else grants,
        },
        resource_operations={"*": operations},
        properties=(
            {
                APPLICATION_OPERATION_POLICY_PROPERTY: policy,
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


@pytest.fixture
def native_authority(monkeypatch):
    state = SimpleNamespace(
        roles=(REGISTERED_ROLE,),
        permissions=("native:read",),
        error=None,
        reads=[],
        target_authorities=[],
    )

    async def resolve(
        *, tenant, project, platform_user_id, target_authority_id="platform",
    ):
        state.reads.append((tenant, project, platform_user_id))
        state.target_authorities.append(target_authority_id)
        assert (tenant, project, platform_user_id) == (
            "tenant-data-bus", "project-data-bus", "grantor-user",
        )
        if target_authority_id not in ("platform", "kdcube.platform", "kdcube-front-shell"):
            raise PlatformAuthorityResolutionError("required_authority_not_linked")
        if state.error is not None:
            raise state.error
        return NativePlatformIdentity(
            authority_id="kdcube.platform",
            user_id="grantor-user",
            roles=state.roles,
            permissions=state.permissions,
            source="bundle_session_native_user",
            updated_at=123,
        )

    monkeypatch.setattr(
        delegated_authority_module, "resolve_native_platform_identity", resolve,
    )
    return state


def _queued_delegated_invocation(monkeypatch, card, *, operation=""):
    _patch_live_card(monkeypatch, card)
    stream = _RecordingStream()
    handler = DataBusHandlerSpec(
        method_name="handle_publish",
        subject="report.publish.requested",
        operation_id="report.publish" if operation else "",
        operation_id_explicit=bool(operation),
    )
    worker = _worker_with_handler(stream, handler)
    observed = []

    async def invoke(claim, _handler):
        observed.append(claim.message.actor)
        return DataBusResult.ok(claim.message), True

    async def reply(_message, _result):
        stream.calls.append("reply")

    worker._invoke_handler = invoke
    worker._send_default_reply = reply
    message = DataBusMessage(
        message_id="message-native-authority",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="reports@1-0",
        subject=handler.subject,
        actor=_delegated_actor(
            selected_operations=(operation,) if operation else (),
        ),
    )
    return worker, message, stream, observed


@pytest.fixture
def publish_default_actor(monkeypatch):
    """Use the normal communicator/publisher/stream; replace only Redis I/O."""
    class Redis:
        def __init__(self):
            self.entries = []

        async def xgroup_create(self, *_args, **_kwargs):
            return True

        async def xadd(self, _key, fields, **_kwargs):
            self.entries.append(fields["json"])
            return "1-0"

    async def publish(user):
        redis = Redis()
        monkeypatch.setattr(
            publisher_module, "get_async_redis_client", lambda _url: redis,
        )
        context = ExternalEventPayload.model_validate({
            "actor": {
                "tenant_id": "tenant-data-bus", "project_id": "project-data-bus",
            },
            "routing": {
                "bundle_id": "reports@1-0", "session_id": "producer-session",
            },
            "user": user,
            "request": {"request_id": "producer-request"},
        })
        comm = build_comm_from_comm_context(
            context,
            relay=SimpleNamespace(_comm=SimpleNamespace(redis_url="redis://producer-test/0")),
        )
        assert isinstance(comm, ChatCommunicator)
        assert isinstance(comm.data_bus, DataBusPublisher)
        ack = await comm.data_bus.publish(
            bundle_id="reports@1-0",
            subject="report.publish.requested",
            message_id="message-default-producer",
            payload={"report_id": "report-one"},
        )
        assert len(redis.entries) == 1
        queued = DataBusMessage.from_json(redis.entries[0])
        assert ack.message_id == queued.message_id
        assert queued.actor["session_id"] == "producer-session"
        assert queued.actor["roles"] == user["roles"]
        return queued

    return publish


def _producer_worker(*, required_role=REGISTERED_ROLE):
    stream = _RecordingStream()
    handler = DataBusHandlerSpec(
        method_name="handle_publish", subject="report.publish.requested",
        roles=(required_role,), user_types=("registered",),
    )
    worker = _worker_with_handler(stream, handler)
    observed = []

    async def invoke(claim, _handler):
        observed.append(claim.message.actor)
        return DataBusResult.ok(claim.message), False

    async def reply(_message, _result):
        stream.calls.append("reply")

    worker._invoke_handler = invoke
    worker._send_default_reply = reply
    return worker, stream, observed


def _legacy_producer_user():
    return {
        "user_id": "grantor-user", "user_type": "privileged",
        "roles": [ADMIN_ROLE], "permissions": ["kdcube:*"],
        "identity_authority": {
            "schema": "connection_hub.delegated_rest_runtime_authority.v1",
            "authority_id": "delegated_client",
            "issuer_authority_id": "delegated_client",
            "credential_kind": "delegated_client_access",
            "credential_id": "legacy-credential",
            "actor_identity": "worker-a", "actor_user_id": "worker-a",
            "delegate_identity": "worker-a",
            "grantor_user_id": "grantor-user", "platform_user_id": "grantor-user",
            "economics_user_id": "grantor-user",
            "user_id": "grantor-user", "subject_user_id": "grantor-user",
            "delegated_resource": _DELEGATED_RESOURCE,
            "resource_grants": {_DELEGATED_RESOURCE: ["reports:read"]},
            "resource_operations": {_DELEGATED_RESOURCE: ["reports.read"]},
            "scopes": ["reports:read"], "operations": ["reports.read"],
            "delegated_roles_selected": False,
            "grants": ["reports:read"], "permissions": ["kdcube:*"],
            "roles": [ADMIN_ROLE], "platform_roles": [ADMIN_ROLE],
            "grantor_roles": [ADMIN_ROLE], "platform_permissions": ["kdcube:*"],
            "grantor_permissions": ["kdcube:*"], "economics_budget_bypass": True,
            "budget_bypass": True,
            "provenance": {
                "schema": "connection_hub.delegated_actor_provenance.v1",
                "actor_identity": "worker-a", "delegate_identity": "worker-a",
                "grantor_user_id": "grantor-user", "issuer_authority_id": "delegated_client",
                "credential_id": "legacy-credential", "credential_kind": "delegated_client_access",
            },
            "delegation": {
                "schema": "connection_hub.delegation_edge.v1",
                "delegate_identity": "worker-a", "grantor_user_id": "grantor-user",
                "authority_id": "delegated_client", "credential_kind": "delegated_client_access",
            },
            "required_authority_cast": {
                "schema": "kdcube.required_authority_cast.v1",
                "source_authority": "delegated_client", "actor_identity": "worker-a",
                "target_identity": "grantor-user", "target_authority": "kdcube.platform",
                "card_id": "legacy-credential", "card_revision": 0,
                "authority_updated_at": 100,
                "status": "resolved",
            },
        },
    }


def _forbid_card_lookup(monkeypatch):
    async def resolve(*_args, **_kwargs):
        raise AssertionError("A pointerless invocation must not manufacture a live Card lookup")

    monkeypatch.setattr(worker_module, "resolve_live_grant_card", resolve)


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
async def test_worker_rejects_malformed_application_operation_policy(
    monkeypatch,
) -> None:
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
    )
    worker = _worker_with_handler(stream, handler)
    card = _live_card(
        operations=(operation,),
        default_role="kdcube:role:registered",
    )
    card.properties[APPLICATION_OPERATION_POLICY_PROPERTY]["default_role"] = (
        "kdcube:role:super-admin"
    )
    _patch_live_card(monkeypatch, card)

    async def _invoke_handler(*_args, **_kwargs):
        raise AssertionError("bundle code must not run")

    async def _send_default_reply(_message, _result) -> None:
        stream.calls.append("reply")

    worker._invoke_handler = _invoke_handler
    worker._send_default_reply = _send_default_reply
    message = DataBusMessage(
        message_id="message-invalid-policy",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="reports@1-0",
        subject=handler.subject,
        actor=_delegated_actor(selected_operations=(operation,)),
    )

    await worker._process_claim(_claim(message))

    assert stream.calls == ["result", "reply", "ack"]
    assert stream.results[0].error["code"] == (
        "application_operation_policy_invalid"
    )
    assert stream.results[0].error["details"] == {
        "reason": "application_default_role_mismatch"
    }


@pytest.mark.asyncio
async def test_pre_policy_empty_wildcard_row_stays_compatible_and_live_role_downscopes(
    monkeypatch,
    native_authority,
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
async def test_super_admin_card_enters_lower_tier_bundle_and_handler(
    monkeypatch,
    native_authority,
) -> None:
    native_authority.roles = (SUPER_ADMIN_ROLE,)
    stream = _RecordingStream()
    handler = DataBusHandlerSpec(
        method_name="handle_publish",
        subject="report.publish.requested",
        user_types=("registered",),
        roles=("kdcube:role:registered",),
    )
    worker = _worker_with_handler(stream, handler)
    worker.bundle_allowed_roles = ("kdcube:role:paid",)
    _patch_live_card(
        monkeypatch,
        _live_card(
            grants=("kdcube:role:super-admin",),
            policy_enabled=False,
        ),
    )
    invoked = False

    async def _invoke_handler(claim, _handler):
        nonlocal invoked
        invoked = True
        assert claim.message.actor["roles"] == ["kdcube:role:super-admin"]
        return DataBusResult.ok(claim.message, {"handled": True}), True

    worker._invoke_handler = _invoke_handler
    message = DataBusMessage(
        message_id="message-dominant-role",
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
async def test_live_registered_card_cannot_enter_privileged_handler(
    monkeypatch, native_authority,
) -> None:
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
async def test_operation_role_override_is_local_to_one_data_bus_invocation(
    monkeypatch,
    native_authority,
) -> None:
    native_authority.roles = (SUPER_ADMIN_ROLE,)
    publish = application_operation_ref(
        application_id="reports@1-0",
        operation_id="report.publish",
    )
    read = application_operation_ref(
        application_id="reports@1-0",
        operation_id="report.read",
    )
    _patch_live_card(
        monkeypatch,
        _live_card(
            operations=(publish, read),
            default_role="kdcube:role:registered",
            operation_roles={publish: "kdcube:role:super-admin"},
        ),
    )

    observed_roles: list[list[str]] = []
    for subject, operation_id, required_user_type in (
        ("report.publish.requested", "report.publish", "privileged"),
        ("report.read.requested", "report.read", "registered"),
    ):
        stream = _RecordingStream()
        handler = DataBusHandlerSpec(
            method_name="handle_report",
            subject=subject,
            operation_id=operation_id,
            operation_id_explicit=True,
            user_types=(required_user_type,),
        )
        worker = _worker_with_handler(stream, handler)

        async def _invoke_handler(claim, _handler):
            observed_roles.append(list(claim.message.actor["roles"]))
            authority = claim.message.actor["identity_authority"]
            assert authority["operations"] == [
                application_operation_ref(
                    application_id="reports@1-0",
                    operation_id=operation_id,
                )
            ]
            return DataBusResult.ok(claim.message, {"handled": True}), True

        worker._invoke_handler = _invoke_handler
        message = DataBusMessage(
            message_id=f"message-{operation_id}",
            tenant="tenant-data-bus",
            project="project-data-bus",
            bundle_id="reports@1-0",
            subject=subject,
            actor=_delegated_actor(selected_operations=(publish, read)),
        )

        await worker._process_claim(_claim(message))

        assert stream.calls == ["result", "ack"]
        assert stream.results[0].status == "ok"

    assert observed_roles == [
        ["kdcube:role:super-admin"],
        ["kdcube:role:registered"],
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_role", [PAID_ROLE, ADMIN_ROLE])
async def test_native_demotion_after_enqueue_rejects_unchanged_card(
    monkeypatch, native_authority, selected_role,
) -> None:
    native_authority.roles = (selected_role,)
    card = _live_card(grants=(selected_role,), policy_enabled=False)
    worker, queued, stream, observed = _queued_delegated_invocation(
        monkeypatch, card,
    )
    queued.actor["roles"] = [selected_role]

    native_authority.roles = (REGISTERED_ROLE,)
    await worker._process_claim(_claim(queued))

    assert card.resource_grants[_DELEGATED_RESOURCE] == (selected_role,)
    assert observed == []
    assert len(native_authority.reads) == 1
    assert stream.calls == ["result", "reply", "ack"]
    assert stream.results[0].status == "rejected"
    assert stream.results[0].error == {
        "code": "grantor_role_changed",
        "message": "Native target authority does not admit this Data Bus invocation",
        "details": {
            "reason": "grantor_role_changed",
            "status_code": 403,
            "retryable": False,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("override_role", [PAID_ROLE, SUPER_ADMIN_ROLE])
async def test_operation_override_requires_current_native_role(
    monkeypatch, native_authority, override_role,
) -> None:
    operation = application_operation_ref(
        application_id="reports@1-0", operation_id="report.publish",
    )
    card = _live_card(
        operations=(operation,),
        default_role=REGISTERED_ROLE,
        operation_roles={operation: override_role},
    )
    worker, queued, stream, observed = _queued_delegated_invocation(
        monkeypatch, card, operation=operation,
    )

    await worker._process_claim(_claim(queued))

    assert observed == []
    assert len(native_authority.reads) == 1
    assert stream.calls == ["result", "reply", "ack"]
    assert stream.results[0].error["code"] == "grantor_role_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "status_code", "result_status"),
    [
        ("target_authority_identity_unresolved", 403, "rejected"),
        ("target_authority_unavailable", 503, "error"),
    ],
)
async def test_native_authority_failure_preserves_status_and_retryability(
    monkeypatch, native_authority, reason, status_code, result_status,
) -> None:
    worker, queued, stream, observed = _queued_delegated_invocation(
        monkeypatch, _live_card(policy_enabled=False),
    )
    native_authority.error = PlatformAuthorityResolutionError(reason, status_code)

    await worker._process_claim(_claim(queued))

    assert observed == []
    assert len(native_authority.reads) == 1
    assert stream.calls == ["result", "reply", "ack"]
    assert stream.results[0].status == result_status
    assert stream.results[0].error["code"] == reason
    assert stream.results[0].error["details"] == {
        "reason": reason,
        "status_code": status_code,
        "retryable": status_code == 503,
    }


@pytest.mark.asyncio
async def test_legacy_actor_aliases_cannot_restore_unselected_privilege(
    monkeypatch, native_authority,
) -> None:
    native_authority.roles = (REGISTERED_ROLE, SUPER_ADMIN_ROLE)
    native_authority.permissions = ("native:read", "kdcube:*")
    worker, queued, stream, observed = _queued_delegated_invocation(
        monkeypatch,
        _live_card(grants=("reports:read",), policy_enabled=False),
    )
    aliases = {
        "platform_roles": [SUPER_ADMIN_ROLE],
        "grantor_roles": [SUPER_ADMIN_ROLE],
        "platform_permissions": ["kdcube:*"],
        "grantor_permissions": ["kdcube:*"],
        "economics_budget_bypass": True,
    }
    queued.actor["identity_authority"].update(aliases)

    await worker._process_claim(_claim(queued))

    assert stream.calls == ["result", "ack"]
    assert stream.results[0].status == "ok"
    assert len(observed) == 1
    actor = observed[0]
    authority = actor["identity_authority"]
    assert actor["roles"] == [REGISTERED_ROLE]
    assert actor["permissions"] == ["reports:read"]
    assert actor["user_type"] == "registered"
    assert not set(aliases).intersection(authority)
    assert authority["budget_bypass"] is False
    assert authority["delegated_roles_selected"] is False
    assert authority["required_authority_cast"]["target_identity"] == "grantor-user"
    assert authority["required_authority_cast"]["card_revision"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_role", [PAID_ROLE, ADMIN_ROLE])
async def test_default_producer_preserves_card_binding_for_native_demotion(
    monkeypatch, native_authority, publish_default_actor, selected_role,
) -> None:
    native_authority.roles = (selected_role,)
    user = _delegated_actor()
    user["roles"] = [selected_role]
    card = _live_card(grants=(selected_role,), policy_enabled=False)
    _patch_live_card(monkeypatch, card)
    queued = await publish_default_actor(user)
    assert queued.actor["identity_authority"]["delegated_card_binding"] == (
        user["identity_authority"]["delegated_card_binding"]
    )
    assert queued.actor["identity_authority"]["delegated_resource"] == _DELEGATED_RESOURCE
    worker, stream, observed = _producer_worker(required_role=selected_role)

    native_authority.roles = (REGISTERED_ROLE,)
    await worker._process_claim(_claim(queued))

    assert card.resource_grants[_DELEGATED_RESOURCE] == (selected_role,)
    assert observed == []
    assert native_authority.reads == [
        ("tenant-data-bus", "project-data-bus", "grantor-user"),
    ]
    assert stream.calls == ["result", "reply", "ack"]
    assert stream.results[0].status == "rejected"
    assert stream.results[0].error["code"] == "grantor_role_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("required_role", [REGISTERED_ROLE, ADMIN_ROLE])
@pytest.mark.parametrize("optional_metadata", [True, False])
async def test_pointerless_default_producer_uses_current_native_authority_before_visibility(
    monkeypatch, native_authority, publish_default_actor, required_role, optional_metadata,
) -> None:
    user = _legacy_producer_user()
    if not optional_metadata:
        for key in (
            "actor_identity", "actor_user_id", "authority_id", "issuer_authority_id",
            "economics_user_id", "user_id", "subject_user_id", "provenance", "delegation",
        ):
            user["identity_authority"].pop(key)
    native_authority.roles = (ADMIN_ROLE,)
    queued = await publish_default_actor(user)
    assert queued.actor["identity_authority"] == user["identity_authority"]
    assert "delegated_card_binding" not in queued.actor["identity_authority"]
    _forbid_card_lookup(monkeypatch)
    worker, stream, observed = _producer_worker(required_role=required_role)
    handler_visible = worker_module._handler_visible

    def current_native_visibility(handler, actor):
        assert len(native_authority.reads) == 1
        economics = authority_from_source(actor["identity_authority"])
        assert economics["roles"] == [REGISTERED_ROLE]
        assert economics["budget_bypass"] is False
        return handler_visible(handler, actor)

    monkeypatch.setattr(worker_module, "_handler_visible", current_native_visibility)

    native_authority.roles = (REGISTERED_ROLE,)
    await worker._process_claim(_claim(queued))

    assert native_authority.reads == [
        ("tenant-data-bus", "project-data-bus", "grantor-user"),
    ]
    assert native_authority.target_authorities == ["kdcube.platform"]
    assert stream.calls == ["result", "reply", "ack"]
    if required_role == ADMIN_ROLE:
        assert observed == []
        assert stream.results[0].status == "rejected"
        assert stream.results[0].error["code"] == "handler_not_visible"
    else:
        assert stream.results[0].status == "ok"
        assert len(observed) == 1
        actor = observed[0]
        authority = actor["identity_authority"]
        assert actor["roles"] == [REGISTERED_ROLE]
        assert actor["permissions"] == ["native:read"]
        assert actor["user_type"] == "registered"
        assert authority_from_source(authority)["budget_bypass"] is False
        assert authority_from_source(authority)["roles"] == [REGISTERED_ROLE]
        assert not {
            "platform_roles", "grantor_roles", "platform_permissions",
            "grantor_permissions", "economics_budget_bypass", "delegated_card_binding",
        }.intersection(authority)
        assert authority["required_authority_cast"]["target_identity"] == "grantor-user"
        assert authority["required_authority_cast"]["authority_updated_at"] == 123
        assert authority["required_authority_cast"]["card_id"] == "legacy-credential"
    assert "delegated_card_binding" not in queued.actor["identity_authority"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("authority.required_authority_cast", None),
        ("authority.required_authority_cast", "malformed"),
        ("cast.schema", "unknown.schema"),
        ("cast.status", None),
        ("cast.status", "unresolved"),
        ("cast.actor_identity", ""),
        ("cast.target_identity", ""),
        ("cast.target_authority", ""),
        ("cast.source_authority", ""),
        ("cast.actor_identity", "other-worker"),
        ("cast.target_identity", "other-grantor"),
        ("authority.delegate_identity", ""),
        ("authority.actor_identity", "other-worker"),
        ("authority.actor_user_id", "other-worker"),
        ("authority.grantor_user_id", None),
        ("authority.grantor_user_id", "other-grantor"),
        ("authority.platform_user_id", None),
        ("authority.platform_user_id", "other-grantor"),
        ("authority.authority_id", "other-authority"),
        ("authority.issuer_authority_id", "other-authority"),
        ("authority.economics_user_id", "other-grantor"),
        ("authority.user_id", "other-grantor"),
        ("authority.subject_user_id", "other-grantor"),
        ("authority.provenance", "malformed"),
        ("authority.delegation", ["malformed"]),
        ("provenance.actor_identity", "other-worker"),
        ("provenance.delegate_identity", "other-worker"),
        ("provenance.grantor_user_id", "other-grantor"),
        ("provenance.issuer_authority_id", "other-authority"),
        ("provenance.authority_id", "other-authority"),
        ("delegation.actor_identity", "other-worker"),
        ("delegation.delegate_identity", "other-worker"),
        ("delegation.grantor_user_id", "other-grantor"),
        ("delegation.issuer_authority_id", "other-authority"),
        ("delegation.authority_id", "other-authority"),
        ("user.user_id", "other-grantor"),
    ],
)
async def test_pointerless_default_producer_rejects_missing_or_contradictory_native_mapping(
    monkeypatch, native_authority, publish_default_actor, field, value,
) -> None:
    user = _legacy_producer_user()
    location, key = field.split(".", 1)
    target = {
        "user": user,
        "authority": user["identity_authority"],
        "cast": user["identity_authority"]["required_authority_cast"],
        "provenance": user["identity_authority"]["provenance"],
        "delegation": user["identity_authority"]["delegation"],
    }[location]
    if value is None:
        target.pop(key)
    else:
        target[key] = value
    queued = await publish_default_actor(user)
    assert queued.actor["identity_authority"] == user["identity_authority"]
    _forbid_card_lookup(monkeypatch)
    worker, stream, observed = _producer_worker()

    await worker._process_claim(_claim(queued))

    assert observed == []
    assert native_authority.reads == []
    assert stream.calls == ["result", "reply", "ack"]
    assert stream.results[0].status == "rejected"
    assert stream.results[0].error["code"] == "required_authority_not_linked"
    assert stream.results[0].error["details"] == {
        "reason": "required_authority_not_linked", "status_code": 403, "retryable": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("grants", ["reports:read", ["reports:read", 7]])
async def test_pointerless_default_producer_rejects_malformed_grants(
    monkeypatch, native_authority, publish_default_actor, grants,
) -> None:
    user = _legacy_producer_user()
    user["identity_authority"]["grants"] = grants
    queued = await publish_default_actor(user)
    _forbid_card_lookup(monkeypatch)
    worker, stream, observed = _producer_worker()

    await worker._process_claim(_claim(queued))

    assert observed == []
    assert len(native_authority.reads) == 1
    assert stream.calls == ["result", "reply", "ack"]
    assert stream.results[0].status == "rejected"
    assert stream.results[0].error["code"] == "required_authority_not_linked"
    assert stream.results[0].error["details"]["retryable"] is False


@pytest.mark.asyncio
async def test_pointerless_default_producer_forwards_exact_target_authority_for_validation(
    monkeypatch, native_authority, publish_default_actor,
) -> None:
    user = _legacy_producer_user()
    user["identity_authority"]["required_authority_cast"]["target_authority"] = "other-authority"
    queued = await publish_default_actor(user)
    _forbid_card_lookup(monkeypatch)
    worker, stream, observed = _producer_worker()

    await worker._process_claim(_claim(queued))

    assert observed == []
    assert native_authority.target_authorities == ["other-authority"]
    assert native_authority.reads == [
        ("tenant-data-bus", "project-data-bus", "grantor-user"),
    ]
    assert stream.calls == ["result", "reply", "ack"]
    assert stream.results[0].status == "rejected"
    assert stream.results[0].error["code"] == "required_authority_not_linked"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "status_code", "result_status"),
    [
        ("target_authority_identity_unresolved", 403, "rejected"),
        ("target_authority_unavailable", 503, "error"),
    ],
)
async def test_pointerless_default_producer_preserves_native_failure_status(
    monkeypatch, native_authority, publish_default_actor, reason, status_code, result_status,
) -> None:
    queued = await publish_default_actor(_legacy_producer_user())
    _forbid_card_lookup(monkeypatch)
    worker, stream, observed = _producer_worker()
    native_authority.error = PlatformAuthorityResolutionError(reason, status_code)

    await worker._process_claim(_claim(queued))

    assert observed == []
    assert len(native_authority.reads) == 1
    assert native_authority.target_authorities == ["kdcube.platform"]
    assert stream.calls == ["result", "reply", "ack"]
    assert stream.results[0].status == result_status
    assert stream.results[0].error["code"] == reason
    assert stream.results[0].error["details"] == {
        "reason": reason, "status_code": status_code, "retryable": status_code == 503,
    }


@pytest.mark.asyncio
async def test_default_producer_keeps_ordinary_native_actor_unchanged(
    monkeypatch, native_authority, publish_default_actor,
) -> None:
    user = {
        "user_id": "native-user", "user_type": "privileged",
        "roles": [ADMIN_ROLE], "permissions": ["native:admin"],
    }
    queued = await publish_default_actor(user)
    # This positive control must pass before AND after metadata propagation:
    # an ordinary native actor needs neither a delegation mapping nor a cast.
    assert "identity_authority" not in queued.actor
    _forbid_card_lookup(monkeypatch)
    worker, stream, observed = _producer_worker(required_role=ADMIN_ROLE)

    await worker._process_claim(_claim(queued))

    assert native_authority.reads == []
    assert stream.calls == ["result", "reply", "ack"]
    assert stream.results[0].status == "ok"
    assert observed == [queued.actor]
    assert observed[0] == {**user, "session_id": "producer-session"}


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
async def test_implicit_data_bus_handler_keeps_dynamic_dispatch_authority(
    monkeypatch, native_authority,
) -> None:
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
async def test_handler_binds_local_bundle_callers_for_its_lifetime(
    monkeypatch,
) -> None:
    operation_calls = []

    async def operation_caller(call):
        operation_calls.append(call)
        return {"ok": True, "control_id": call.data["control_id"]}

    operation_stream_caller = object()
    named_service_caller = object()

    class _Bundle:
        async def handle(self, ctx, message):
            del ctx, message
            assert get_current_bundle_operation_caller() is operation_caller
            assert (
                get_current_bundle_operation_stream_caller()
                is operation_stream_caller
            )
            assert (
                get_current_bundle_named_service_caller()
                is named_service_caller
            )
            peer = await call_bundle_operation(
                bundle_id="connection-hub@1-0",
                operation="control_card_get",
                data={"control_id": "control-project-one"},
            )
            return {"status": "ok", "data": dict(peer)}

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
        "make_local_bundle_operation_caller",
        lambda **_kwargs: operation_caller,
    )
    monkeypatch.setattr(
        worker_module,
        "make_local_bundle_operation_stream_caller",
        lambda **_kwargs: operation_stream_caller,
    )
    monkeypatch.setattr(
        worker_module,
        "make_local_bundle_named_service_caller",
        lambda **_kwargs: named_service_caller,
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
    assert result.data == {
        "ok": True,
        "control_id": "control-project-one",
    }
    assert len(operation_calls) == 1
    assert operation_calls[0].bundle_id == "connection-hub@1-0"
    assert operation_calls[0].operation == "control_card_get"
    assert reply_sent is False
    assert get_current_bundle_operation_caller() is None
    assert get_current_bundle_operation_stream_caller() is None
    assert get_current_bundle_named_service_caller() is None



@pytest.mark.asyncio
async def test_worker_card_lookup_reads_through_the_durable_store(monkeypatch) -> None:
    seen: dict = {}
    store = object()

    async def _resolve(*_args, **kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(worker_module, "resolve_live_grant_card", _resolve)
    monkeypatch.setattr(worker_module, "delegated_card_store", lambda **scope: store)
    stream = _RecordingStream()
    handler = DataBusHandlerSpec(
        method_name="handle_publish",
        subject="report.publish.requested",
    )
    worker = _worker_with_handler(stream, handler)

    async def _send_default_reply(_message, _result) -> None:
        stream.calls.append("reply")

    worker._send_default_reply = _send_default_reply
    message = DataBusMessage(
        message_id="message-store-read-through",
        tenant="tenant-data-bus",
        project="project-data-bus",
        bundle_id="reports@1-0",
        subject=handler.subject,
        actor=_delegated_actor(),
    )

    await worker._process_claim(_claim(message))

    assert seen["card_store"] is store
    assert seen["expected_grantor_subject"]
