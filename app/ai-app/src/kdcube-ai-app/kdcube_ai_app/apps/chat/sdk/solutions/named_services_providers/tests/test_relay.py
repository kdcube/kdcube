# SPDX-License-Identifier: MIT

"""Named-service relay over the Data Bus.

Surfaced case: a named-service call from generated code (exec supervisor)
died with `named_service_api_endpoint_unavailable` — no live registry caller
exists outside the host proc. The relay publishes the request to the provider
bundle's Data Bus stream and waits for the recorded result; the server side
is idempotent per message id so bus redelivery never re-runs the action.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventActor,
    ExternalEventPayload,
    ExternalEventRouting,
    ExternalEventUser,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import relay
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.admission import (
    NamedServiceAdmission,
    NamedServiceAdmissionDecision,
    NamedServiceAdmissionSelector,
    DELEGATED_SELECTOR_BEARER,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.named_service_admission import (
    DELEGATED_CARD_BINDING_SCHEMA,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    NamedServiceRequest,
    NamedServiceResponse,
)


def _request_context() -> ExternalEventPayload:
    return ExternalEventPayload(
        routing=ExternalEventRouting(bundle_id="workspace@2026-03-31-13-36", session_id="sess-1"),
        actor=ExternalEventActor(tenant_id="demo-tenant", project_id="demo-project"),
        user=ExternalEventUser(user_type="registered", user_id="user-1", roles=["kdcube:role:registered"]),
    )


def _send_request() -> NamedServiceRequest:
    return NamedServiceRequest(
        operation="object.action",
        namespace="mail",
        object_ref="mail:gmail:acc-1",
        action="send",
        payload={"to": "user@example.test", "subject": "Hi"},
    )


def _application_admission() -> NamedServiceAdmission:
    return NamedServiceAdmission.application(source="test.relay")


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str):
        self.store[key] = value


@pytest.mark.asyncio
async def test_relay_call_carries_identity_and_returns_provider_response(monkeypatch):
    captured: dict[str, Any] = {}

    async def _publish_and_wait(self, **kwargs):
        captured.update(kwargs)
        return {
            "status": "ok",
            "message_id": kwargs["message_id"],
            "data": {"response": {"ok": True, "ret": {"attrs": {"namespace": "mail"}}, "status": 200}},
        }

    monkeypatch.setattr(relay.DataBusPublisher, "publish_and_wait", _publish_and_wait)

    with bind_current_request_context(_request_context()):
        response = await relay.relay_named_service_call(
            bundle_id="kdcube-services@1-0",
            request=_send_request(),
            admission=_application_admission(),
        )

    assert isinstance(response, NamedServiceResponse)
    assert response.ok is True
    assert captured["subject"] == relay.NAMED_SERVICE_RELAY_SUBJECT
    # The carried request identity rides as the bus actor — the provider
    # authorizes against the real user, never a service account.
    assert captured["actor"]["user_id"] == "user-1"
    assert captured["actor"]["user_type"] == "registered"
    assert captured["payload"]["request"]["action"] == "send"
    assert captured["payload"]["admission"] == {
        "mode": "application",
        "source": "test.relay",
    }
    assert "admission" not in captured["payload"]["request"]
    # Redelivery protection: the message id doubles as the idempotency key.
    assert captured["idempotency_key"] == captured["message_id"]


@pytest.mark.asyncio
async def test_relay_call_times_out_loudly(monkeypatch):
    async def _publish_and_wait(self, **kwargs):
        raise TimeoutError("no result")

    monkeypatch.setattr(relay.DataBusPublisher, "publish_and_wait", _publish_and_wait)

    with bind_current_request_context(_request_context()):
        response = await relay.relay_named_service_call(
            bundle_id="kdcube-services@1-0",
            request=_send_request(),
            admission=_application_admission(),
        )

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "named_service_relay_timeout"
    assert response.status == 504


@pytest.mark.asyncio
async def test_relay_call_reports_a_lost_result_body_as_outcome_unknown(monkeypatch):
    """W827 S2d: a large result whose body expired or changed is outcome-unknown (504), never an empty success."""
    from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import DataBusResultBodyUnavailable

    async def _publish_and_wait(self, **kwargs):
        raise DataBusResultBodyUnavailable("result body expired or missing")

    monkeypatch.setattr(relay.DataBusPublisher, "publish_and_wait", _publish_and_wait)

    with bind_current_request_context(_request_context()):
        response = await relay.relay_named_service_call(
            bundle_id="kdcube-services@1-0",
            request=_send_request(),
            admission=_application_admission(),
        )

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "data_bus_result_body_unavailable"
    assert response.status == 504


@pytest.mark.asyncio
async def test_relay_call_requires_bound_identity():
    response = await relay.relay_named_service_call(
        bundle_id="kdcube-services@1-0",
        request=_send_request(),
        admission=_application_admission(),
        tenant="demo-tenant",
        project="demo-project",
    )
    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "named_service_relay_identity_missing"


@pytest.mark.asyncio
async def test_relay_handler_dispatches_once_and_replays_recorded_result(monkeypatch):
    calls: list[NamedServiceRequest] = []

    class _FakeClient:
        def __init__(self, registry, *, auth_context=None, **kwargs):
            del registry, auth_context, kwargs

        async def call(self, request):
            calls.append(request)
            return NamedServiceResponse(ok=True, ret={"attrs": {"namespace": request.namespace}})

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client.NamedServiceClient",
        _FakeClient,
    )

    redis = _FakeRedis()
    bundle = SimpleNamespace(redis=redis, named_services=lambda: object())
    ctx = SimpleNamespace(bundle=bundle)
    message = SimpleNamespace(
        tenant="demo-tenant",
        project="demo-project",
        bundle_id="kdcube-services@1-0",
        message_id="nsrelay_1",
        actor={"source_bundle_id": "workspace@2026-03-31-13-36"},
        payload={
            "request": _send_request().to_dict(),
            "admission": _application_admission().relay_selector(),
        },
    )

    with bind_current_request_context(_request_context()):
        first = await relay.handle_named_service_relay(ctx, message)
        # The bus redelivers at-least-once; the second delivery must answer
        # from the recorded result without re-running the send.
        second = await relay.handle_named_service_relay(ctx, message)

    assert first["status"] == "ok"
    assert first["data"]["response"]["ok"] is True
    assert second == first
    assert len(calls) == 1
    cached = json.loads(list(redis.store.values())[0])
    assert cached["data"]["response"]["ok"] is True


@pytest.mark.asyncio
async def test_relay_handler_rejects_message_without_request():
    ctx = SimpleNamespace(bundle=SimpleNamespace(redis=None, named_services=lambda: object()))
    message = SimpleNamespace(
        tenant="demo-tenant",
        project="demo-project",
        bundle_id="kdcube-services@1-0",
        message_id="nsrelay_2",
        actor={},
        payload={},
    )
    result = await relay.handle_named_service_relay(ctx, message)
    assert result["status"] == "rejected"
    assert result["error"]["code"] == "named_service_relay_request_invalid"


@pytest.mark.asyncio
async def test_relay_handler_rejects_message_without_admission():
    ctx = SimpleNamespace(bundle=SimpleNamespace(redis=None, named_services=lambda: object()))
    message = SimpleNamespace(
        tenant="demo-tenant",
        project="demo-project",
        bundle_id="kdcube-services@1-0",
        message_id="nsrelay_no_admission",
        actor={"source_bundle_id": "workspace@2026-03-31-13-36"},
        payload={"request": _send_request().to_dict()},
    )

    result = await relay.handle_named_service_relay(ctx, message)

    assert result["status"] == "rejected"
    assert result["error"]["code"] == "named_service_relay_admission_missing"


@pytest.mark.asyncio
async def test_bearer_relay_resolves_once_and_binds_scope_for_dispatch(monkeypatch):
    state = {"authorizations": 0, "provider_calls": 0, "bound": False}

    class _Scope:
        @contextmanager
        def bind(self):
            state["bound"] = True
            try:
                yield
            finally:
                state["bound"] = False

    async def _authorize(self, request):
        state["authorizations"] += 1
        return NamedServiceAdmissionDecision.allow(execution_scope=_Scope())

    class _FakeClient:
        def __init__(self, registry, *, auth_context=None, **kwargs):
            del registry, auth_context, kwargs

        async def call(self, request):
            assert state["bound"] is True
            state["provider_calls"] += 1
            return NamedServiceResponse.ok_response(namespace=request.namespace)

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.named_service_admission.HubNamedServiceAdmissionAuthorizer.authorize",
        _authorize,
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client.NamedServiceClient",
        _FakeClient,
    )

    selector = NamedServiceAdmissionSelector(
        mode="delegated",
        source="managed_mcp.named_services",
        delegated_kind=DELEGATED_SELECTOR_BEARER,
        access_id="oauth-access-1",
        client_id="claude",
        grantor_user_id="user-1",
        delegate_identity="integration:claude:user-1",
    )
    actor = {
        "user_id": "user-1",
        "source_bundle_id": "workspace@2026-03-31-13-36",
        "identity_authority": {
            "delegated_card_binding": {
                "schema": DELEGATED_CARD_BINDING_SCHEMA,
                "access_id": selector.access_id,
                "client_id": selector.client_id,
                "grantor_user_id": selector.grantor_user_id,
                "delegate_identity": selector.delegate_identity,
            }
        },
    }
    redis = _FakeRedis()
    ctx = SimpleNamespace(
        bundle=SimpleNamespace(redis=redis, named_services=lambda: object())
    )
    message = SimpleNamespace(
        tenant="demo-tenant",
        project="demo-project",
        bundle_id="kdcube-services@1-0",
        message_id="nsrelay_bearer_1",
        actor=actor,
        payload={"request": _send_request().to_dict(), "admission": selector.to_dict()},
    )

    first = await relay.handle_named_service_relay(ctx, message)
    replay = await relay.handle_named_service_relay(ctx, message)

    assert first["status"] == "ok"
    assert replay == first
    assert state == {"authorizations": 1, "provider_calls": 1, "bound": False}


@pytest.mark.asyncio
async def test_relay_replays_an_admission_denial_without_reauthorizing(monkeypatch):
    state = {"authorizations": 0, "provider_calls": 0}

    async def _authorize(self, request):
        state["authorizations"] += 1
        return NamedServiceAdmissionDecision.deny(
            NamedServiceResponse.error_response(
                code="delegated_card_not_active",
                message="The delegated card is no longer active.",
                status=403,
                namespace=request.namespace,
            )
        )

    class _FakeClient:
        def __init__(self, registry, *, auth_context=None, **kwargs):
            del registry, auth_context, kwargs

        async def call(self, request):
            del request
            state["provider_calls"] += 1
            raise AssertionError("denied admission must not invoke the provider")

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.named_service_admission."
        "HubNamedServiceAdmissionAuthorizer.authorize",
        _authorize,
    )
    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.client."
        "NamedServiceClient",
        _FakeClient,
    )

    selector = NamedServiceAdmissionSelector(
        mode="delegated",
        source="managed_mcp.named_services",
        delegated_kind=DELEGATED_SELECTOR_BEARER,
        access_id="oauth-access-1",
        client_id="claude",
        grantor_user_id="user-1",
        delegate_identity="integration:claude:user-1",
    )
    actor = {
        "user_id": "user-1",
        "source_bundle_id": "workspace@2026-03-31-13-36",
        "identity_authority": {
            "delegated_card_binding": {
                "schema": DELEGATED_CARD_BINDING_SCHEMA,
                "access_id": selector.access_id,
                "client_id": selector.client_id,
                "grantor_user_id": selector.grantor_user_id,
                "delegate_identity": selector.delegate_identity,
            }
        },
    }
    redis = _FakeRedis()
    ctx = SimpleNamespace(
        bundle=SimpleNamespace(redis=redis, named_services=lambda: object())
    )
    message = SimpleNamespace(
        tenant="demo-tenant",
        project="demo-project",
        bundle_id="kdcube-services@1-0",
        message_id="nsrelay_bearer_denied",
        actor=actor,
        payload={"request": _send_request().to_dict(), "admission": selector.to_dict()},
    )

    first = await relay.handle_named_service_relay(ctx, message)
    replay = await relay.handle_named_service_relay(ctx, message)

    assert first["data"]["response"]["error"]["code"] == "delegated_card_not_active"
    assert replay == first
    assert state == {"authorizations": 1, "provider_calls": 0}


@pytest.mark.asyncio
async def test_bearer_relay_rejects_actor_binding_mismatch_before_hub(monkeypatch):
    async def _never(*_args, **_kwargs):
        raise AssertionError("Hub admission must not run for a forged selector")

    monkeypatch.setattr(
        "kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.named_service_admission.HubNamedServiceAdmissionAuthorizer.authorize",
        _never,
    )
    selector = NamedServiceAdmissionSelector(
        mode="delegated",
        source="managed_mcp.named_services",
        delegated_kind=DELEGATED_SELECTOR_BEARER,
        access_id="oauth-access-1",
        client_id="claude",
        grantor_user_id="user-1",
        delegate_identity="integration:claude:user-1",
    )
    ctx = SimpleNamespace(
        bundle=SimpleNamespace(redis=None, named_services=lambda: object())
    )
    message = SimpleNamespace(
        tenant="demo-tenant",
        project="demo-project",
        bundle_id="kdcube-services@1-0",
        message_id="nsrelay_bearer_bad",
        actor={
            "user_id": "user-1",
            "source_bundle_id": "workspace@2026-03-31-13-36",
            "identity_authority": {
                "delegated_card_binding": {
                    "schema": DELEGATED_CARD_BINDING_SCHEMA,
                    "access_id": "different-card",
                    "client_id": "claude",
                    "grantor_user_id": "user-1",
                    "delegate_identity": "integration:claude:user-1",
                }
            },
        },
        payload={"request": _send_request().to_dict(), "admission": selector.to_dict()},
    )

    result = await relay.handle_named_service_relay(ctx, message)

    assert result["status"] == "rejected"
    assert result["error"]["code"] == "named_service_relay_admission_invalid"
    assert "does not match" in result["error"]["message"]

    message.message_id = "nsrelay_bearer_bad_grantor"
    message.actor = {
        "user_id": "different-user",
        "source_bundle_id": "workspace@2026-03-31-13-36",
        "identity_authority": {
            "delegated_card_binding": {
                "schema": DELEGATED_CARD_BINDING_SCHEMA,
                "access_id": selector.access_id,
                "client_id": selector.client_id,
                "grantor_user_id": selector.grantor_user_id,
                "delegate_identity": selector.delegate_identity,
            }
        },
    }

    wrong_grantor = await relay.handle_named_service_relay(ctx, message)

    assert wrong_grantor["status"] == "rejected"
    assert wrong_grantor["error"]["code"] == "named_service_relay_admission_invalid"
    assert "grantor identity" in wrong_grantor["error"]["message"]
