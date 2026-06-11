# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

from types import SimpleNamespace

from kdcube_ai_app.apps.chat.sdk.infra.auth_context import (
    PRINCIPAL_JOB,
    PRINCIPAL_SYSTEM,
    PRINCIPAL_USER,
    AuthContext,
    bind_auth_context,
    sign_auth_context_token,
    verify_auth_context_token,
)
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventActor,
    ExternalEventMeta,
    ExternalEventPayload,
    ExternalEventRouting,
    ExternalEventUser,
)


def test_auth_context_from_external_event_payload_preserves_user_identity():
    payload = ExternalEventPayload(
        meta=ExternalEventMeta(task_id="task-1", created_at=1.0),
        routing=ExternalEventRouting(
            bundle_id="task-tracker@1-0",
            session_id="session-1",
            conversation_id="conv-1",
            turn_id="turn-1",
        ),
        actor=ExternalEventActor(tenant_id="tenant-a", project_id="project-a"),
        user=ExternalEventUser(
            user_type="registered",
            user_id="user-1",
            roles=["kdcube:role:operator"],
            permissions=["tasks:write"],
        ),
    )

    ctx = AuthContext.from_external_event_payload(payload)

    assert ctx.principal_kind == PRINCIPAL_USER
    assert ctx.tenant == "tenant-a"
    assert ctx.project == "project-a"
    assert ctx.bundle_id == "task-tracker@1-0"
    assert ctx.user_id == "user-1"
    assert ctx.roles == ("kdcube:role:operator",)
    assert ctx.permissions == ("tasks:write",)
    assert ctx.session_id == "session-1"
    assert ctx.conversation_id == "conv-1"
    assert ctx.turn_id == "turn-1"


def test_auth_context_for_bundle_job_is_headless_and_not_a_user():
    ctx = AuthContext.for_bundle_job(
        tenant="tenant-a",
        project="project-a",
        bundle_id="task-tracker@1-0",
        job_alias="sweep",
    )

    assert ctx.principal_kind == PRINCIPAL_JOB
    assert ctx.is_headless is True
    assert ctx.user_id is None
    assert ctx.principal_id == "task-tracker@1-0:sweep"
    assert ctx.metadata["job_alias"] == "sweep"


def test_bundle_job_can_run_on_behalf_of_saved_user_context():
    user_ctx = AuthContext.from_mapping(
        {
            "tenant": "tenant-a",
            "project": "project-a",
            "user_id": "user-1",
            "user_type": "registered",
            "roles": ["kdcube:role:operator"],
        }
    )

    job_ctx = AuthContext.for_bundle_job(
        tenant="tenant-a",
        project="project-a",
        bundle_id="task-tracker@1-0",
        job_alias="reminder",
        on_behalf_of=user_ctx,
    )

    assert job_ctx.principal_kind == PRINCIPAL_USER
    assert job_ctx.user_id == "user-1"
    assert job_ctx.bundle_id == "task-tracker@1-0"
    assert job_ctx.source == "bundle_job"
    assert job_ctx.metadata["job_alias"] == "reminder"


def test_auth_context_from_data_bus_context_uses_actor_when_present():
    data_bus_ctx = SimpleNamespace(
        tenant="tenant-a",
        project="project-a",
        bundle_id="task-tracker@1-0",
        stream_id="stream-1",
        actor={
            "user_id": "user-1",
            "user_type": "registered",
            "roles": ["kdcube:role:operator"],
        },
    )

    ctx = AuthContext.from_data_bus_context(data_bus_ctx)

    assert ctx.principal_kind == PRINCIPAL_USER
    assert ctx.tenant == "tenant-a"
    assert ctx.project == "project-a"
    assert ctx.bundle_id == "task-tracker@1-0"
    assert ctx.stream_id == "stream-1"
    assert ctx.user_id == "user-1"


def test_auth_context_system_actor_is_serializable_for_transport_handoff():
    ctx = AuthContext.for_system(tenant="tenant-a", project="project-a", bundle_id="platform")

    actor = ctx.to_actor()

    assert ctx.principal_kind == PRINCIPAL_SYSTEM
    assert actor["principal_kind"] == PRINCIPAL_SYSTEM
    assert actor["tenant"] == "tenant-a"
    assert actor["project"] == "project-a"
    assert "user_id" not in actor


def test_current_auth_context_binding_takes_precedence():
    bound = AuthContext.for_system(tenant="tenant-a", project="project-a", bundle_id="platform")

    with bind_auth_context(bound):
        resolved = AuthContext.from_current_request_context()

    assert resolved is bound


def test_auth_context_signed_token_roundtrip_preserves_delegated_job_user():
    saved_user = AuthContext.from_mapping(
        {
            "tenant": "tenant-a",
            "project": "project-a",
            "user_id": "user-1",
            "user_type": "registered",
            "roles": ["kdcube:role:operator"],
        }
    )
    delegated_job = AuthContext.for_bundle_job(
        tenant="tenant-a",
        project="project-a",
        bundle_id="task-tracker@1-0",
        job_alias="email-check",
        on_behalf_of=saved_user,
    )

    token = sign_auth_context_token(
        delegated_job,
        secret="secret",
        audience="task-and-memo-app@1-0:mcp/email",
        ttl_seconds=300,
        metadata={"run_id": "run-1"},
    )
    restored = verify_auth_context_token(
        token,
        secret="secret",
        audience="task-and-memo-app@1-0:mcp/email",
    )

    assert restored.principal_kind == PRINCIPAL_USER
    assert restored.user_id == "user-1"
    assert restored.bundle_id == "task-tracker@1-0"
    assert restored.metadata["job_alias"] == "email-check"
    assert restored.metadata["run_id"] == "run-1"


def test_auth_context_signed_token_rejects_wrong_audience():
    ctx = AuthContext.for_system(tenant="tenant-a", project="project-a")
    token = sign_auth_context_token(ctx, secret="secret", audience="expected")

    try:
        verify_auth_context_token(token, secret="secret", audience="other")
    except ValueError as exc:
        assert "audience" in str(exc)
    else:
        raise AssertionError("wrong audience should be rejected")
