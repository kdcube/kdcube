from __future__ import annotations

import pytest

from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleMCPCall,
    BundleMCPResult,
    BundleOperationCall,
    _apply_request_projection_to_session,
    _local_named_service_context,
    _raw_roles_visible,
    _target_comm_context,
    bind_bundle_mcp_caller,
    bind_bundle_operation_caller,
    call_bundle_mcp_surface,
    call_bundle_operation,
    get_current_bundle_mcp_caller,
    get_current_bundle_operation_caller,
)
from kdcube_ai_app.apps.chat.sdk.infra.auth_context import AuthContext
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    SOURCE_BUNDLE_ID_METADATA,
)
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventActor,
    ExternalEventPayload,
    ExternalEventRequest,
    ExternalEventRouting,
    ExternalEventUser,
)
from kdcube_ai_app.auth.sessions import UserSession, UserType


def test_bundle_operation_role_visibility_uses_platform_dominance_only():
    session = UserSession(
        session_id="session-role-hierarchy",
        user_type=UserType.PRIVILEGED,
        roles=["kdcube:role:super-admin"],
    )

    assert _raw_roles_visible(("kdcube:role:registered",), session) is True
    assert _raw_roles_visible(("kdcube:role:paid",), session) is True
    assert _raw_roles_visible(("kdcube:role:service",), session) is False


@pytest.mark.asyncio
async def test_call_bundle_operation_uses_bound_request_caller():
    calls: list[BundleOperationCall] = []

    async def _caller(call: BundleOperationCall):
        calls.append(call)
        return {"ok": True, "operation": call.operation, "data": call.data}

    with bind_bundle_operation_caller(_caller):
        result = await call_bundle_operation(
            tenant="tenant-a",
            project="project-a",
            bundle_id="task-tracker@1-0",
            operation="named_service",
            data={"operation": "provider.about"},
        )

    assert result == {"ok": True, "operation": "named_service", "data": {"operation": "provider.about"}}
    assert calls == [
        BundleOperationCall(
            tenant="tenant-a",
            project="project-a",
            bundle_id="task-tracker@1-0",
            operation="named_service",
            data={"operation": "provider.about"},
            route="operations",
        )
    ]
    assert get_current_bundle_operation_caller() is None


@pytest.mark.asyncio
async def test_call_bundle_operation_requires_request_caller():
    with pytest.raises(RuntimeError, match="No request-bound bundle operation caller"):
        await call_bundle_operation(bundle_id="task-tracker@1-0", operation="named_service")


@pytest.mark.asyncio
async def test_call_bundle_mcp_surface_uses_bound_credential_free_caller():
    calls: list[BundleMCPCall] = []

    async def _caller(call: BundleMCPCall) -> BundleMCPResult:
        calls.append(call)
        return BundleMCPResult(status_code=200, body=b'{"jsonrpc":"2.0"}')

    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    with bind_bundle_mcp_caller(_caller):
        result = await call_bundle_mcp_surface(
            tenant="tenant-a",
            project="project-a",
            bundle_id="knowledge@1-0",
            endpoint_alias="knowledge",
            message=message,
            route="public",
        )

    assert result.status_code == 200
    assert calls == [
        BundleMCPCall(
            tenant="tenant-a",
            project="project-a",
            bundle_id="knowledge@1-0",
            endpoint_alias="knowledge",
            message=message,
            route="public",
        )
    ]
    assert get_current_bundle_mcp_caller() is None


@pytest.mark.asyncio
async def test_call_bundle_mcp_surface_requires_request_caller():
    with pytest.raises(RuntimeError, match="No request-bound bundle MCP caller"):
        await call_bundle_mcp_surface(
            bundle_id="knowledge@1-0",
            endpoint_alias="knowledge",
            message={"jsonrpc": "2.0", "method": "tools/list"},
        )


def test_target_comm_context_preserves_identity_authority():
    source = ExternalEventPayload(
        request=ExternalEventRequest(request_id="request-1"),
        routing=ExternalEventRouting(
            bundle_id="user-memories@2026-06-26",
            session_id="session-1",
            conversation_id="conversation-1",
            turn_id="turn-1",
        ),
        actor=ExternalEventActor(tenant_id="tenant-a", project_id="project-a"),
        user=ExternalEventUser(user_type="registered", user_id="telegram_42"),
    )
    session = UserSession(
        session_id="session-1",
        user_type=UserType.PRIVILEGED,
        user_id="telegram_42",
        roles=["kdcube:role:super-admin"],
        permissions=["memories:read"],
        identity_authority={
            "authority_id": "telegram.kdcube_ref",
            "actor_user_id": "telegram_42",
            "platform_user_id": "platform-user-1",
        },
    )

    target = _target_comm_context(
        source,
        bundle_id="connection-hub@1-0",
        tenant="tenant-a",
        project="project-a",
        session=session,
    )

    assert target.user.user_id == "telegram_42"
    assert target.user.identity_authority["platform_user_id"] == "platform-user-1"
    assert target.user.roles == ["kdcube:role:super-admin"]


def test_local_named_service_context_keeps_target_identity_and_source_provenance():
    source = ExternalEventPayload(
        request=ExternalEventRequest(request_id="request-source"),
        routing=ExternalEventRouting(
            bundle_id="problem-board@1-0",
            session_id="session-source",
        ),
        actor=ExternalEventActor(tenant_id="tenant-a", project_id="project-a"),
        user=ExternalEventUser(user_type="registered", user_id="user-1"),
    )
    target_auth = AuthContext(
        tenant="tenant-a",
        project="project-a",
        bundle_id="connection-hub@1-0",
        principal_kind="user",
        principal_id="user-1",
        user_id="user-1",
        user_type="registered",
    )

    context = _local_named_service_context(target_auth, source)

    assert context.bundle_id == "connection-hub@1-0"
    assert context.metadata == {
        SOURCE_BUNDLE_ID_METADATA: "problem-board@1-0",
    }


def test_request_projection_overlays_stored_session_for_peer_calls():
    source = ExternalEventPayload(
        request=ExternalEventRequest(request_id="request-1"),
        routing=ExternalEventRouting(
            bundle_id="user-memories@2026-06-26",
            session_id="session-1",
        ),
        actor=ExternalEventActor(tenant_id="tenant-a", project_id="project-a"),
        user=ExternalEventUser(
            user_type="privileged",
            user_id="telegram_42",
            roles=["kdcube:role:super-admin"],
            permissions=["memories:read"],
            identity_authority={
                "authority_id": "telegram.kdcube_ref",
                "actor_user_id": "telegram_42",
                "platform_user_id": "platform-user-1",
            },
        ),
    )
    stored = UserSession(
        session_id="session-1",
        user_type=UserType.REGISTERED,
        user_id="telegram_42",
        roles=[],
        permissions=[],
    )

    session = _apply_request_projection_to_session(stored, source)

    assert session.identity_authority["platform_user_id"] == "platform-user-1"
    assert session.user_type == "privileged"
    assert session.roles == ["kdcube:role:super-admin"]
    assert session.permissions == ["memories:read"]
