# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Invocation-scoped service context for headless bundle jobs."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from kdcube_ai_app.apps.chat.sdk.infra.auth_context import (
    AuthContext,
    bind_auth_context,
    get_current_auth_context,
)
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    BundleOperationCall,
    bind_bundle_mcp_caller,
    bind_bundle_named_service_caller,
    bind_bundle_operation_caller,
    bind_bundle_operation_stream_caller,
    make_local_bundle_operation_caller,
)
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ExternalEventActor,
    ExternalEventPayload,
    ExternalEventRouting,
    ExternalEventUser,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
    bind_current_request_context,
    get_current_bundle_id,
    get_current_request_context,
)


@contextmanager
def bind_bundle_job_context(
    auth_context: AuthContext, *, redis: Any, pg_pool: Any,
) -> Iterator[ExternalEventPayload]:
    """Bind the existing local operation bridge as the job's service actor.

    The scheduler supplies its own ``AuthContext.for_bundle_job``. Peer bundle
    admission and secret resolution remain the normal local bridge's concern.
    A shared lifetime guard also expires copies inherited by child tasks.
    """
    if (
        auth_context.principal_kind != "job"
        or auth_context.user_type != "service"
        or auth_context.user_id is not None
        or auth_context.session_id is not None
        or auth_context.roles
        or auth_context.permissions
    ):
        raise RuntimeError("Scheduled bundle caller requires a non-interactive job context")
    if not all(str(value or "").strip() for value in (
        auth_context.tenant, auth_context.project, auth_context.bundle_id,
    )):
        raise RuntimeError("Scheduled bundle caller requires a complete job scope")

    comm_context = ExternalEventPayload(
        actor=ExternalEventActor(
            tenant_id=auth_context.tenant, project_id=auth_context.project,
        ),
        routing=ExternalEventRouting(bundle_id=auth_context.bundle_id, session_id=""),
        # The job's service principal remains in AuthContext. The normal local
        # bridge consumes a browser UserSession projection, so headless calls
        # use its existing identity-free type rather than inventing a sign-in.
        user=ExternalEventUser(user_type="anonymous"),
    )
    local_caller = make_local_bundle_operation_caller(
        redis=redis, pg_pool=pg_pool, comm_context=comm_context.model_copy(deep=True),
    )
    active = True

    async def call(operation: BundleOperationCall) -> Mapping[str, Any]:
        if (
            not active
            or get_current_auth_context() is not auth_context
            or get_current_bundle_id() != auth_context.bundle_id
            or get_current_request_context() is not comm_context
        ):
            raise RuntimeError("Scheduled bundle caller requires its active job context")
        if (
            (operation.tenant is not None and operation.tenant != auth_context.tenant)
            or (operation.project is not None and operation.project != auth_context.project)
        ):
            raise RuntimeError("Scheduled bundle caller cannot override its job scope")
        return await local_caller(operation)

    try:
        with (
            bind_auth_context(auth_context),
            bind_current_request_context(comm_context, comm=None),
            bind_bundle_mcp_caller(None),
            bind_bundle_named_service_caller(None),
            bind_bundle_operation_stream_caller(None),
            bind_bundle_operation_caller(call),
        ):
            yield comm_context
    finally:
        active = False
