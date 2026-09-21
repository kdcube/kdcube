# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from dataclasses import dataclass, replace
from typing import Any, Dict, Mapping, Optional, Tuple

from connection_hub.delegated_credentials.live_grant import (
    LiveGrantCardError,
    live_grants_for_resource,
    resolve_live_grant_card,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_credentials.serving import (
    delegated_card_store,
)
from connection_hub.delegated_credentials.resource_operations import (
    operations_for_resource,
)
from kdcube_ai_app.apps.chat.emitters import ChatCommunicator, ChatRelayCommunicator
from kdcube_ai_app.apps.chat.sdk.application_operations import (
    APPLICATION_OPERATION_POLICY_PROPERTY,
    ApplicationOperationPolicyError,
    application_operation_policy_declared,
    data_bus_application_operation_ref,
    delegated_application_operation_role,
    delegated_application_operation_selection,
)
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_roles import (
    delegated_role_projection,
)
from kdcube_ai_app.auth.role_hierarchy import roles_satisfy_any
from kdcube_ai_app.apps.chat.sdk.protocol import (
    ConversationCtx,
    ExternalEventActor,
    ExternalEventMeta,
    ExternalEventPayload,
    ExternalEventRequest,
    ExternalEventRouting,
    ExternalEventUser,
    ServiceCtx,
)
from kdcube_ai_app.apps.chat.sdk.infra.auth_context import AuthContext, bind_auth_context
from kdcube_ai_app.apps.chat.sdk.infra.bundle_operations import (
    bind_bundle_operation_caller,
    bind_bundle_operation_stream_caller,
    bind_bundle_named_service_caller,
    make_local_bundle_operation_caller,
    make_local_bundle_operation_stream_caller,
    make_local_bundle_named_service_caller,
)
from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import bind_current_request_context
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.locks import (
    RedisDataBusPartitionLocker,
    renew_lock_until_cancelled,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import DataBusClaim, RedisDataBusStream
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import (
    DATA_BUS_IDEMPOTENCY_REQUIRED,
    DATA_BUS_ORDERING_SERIAL_PER_PARTITION,
    DATA_BUS_PARTITION_OBJECT_REF,
    DataBusContext,
    DataBusHandlerSpec,
    DataBusMessage,
    DataBusReply,
    DataBusResult,
    coerce_data_bus_result,
    now_ms,
)
from kdcube_ai_app.infra.plugin.bundle_loader import (
    BundleSpec,
    apply_bundle_overrides,
    get_workflow_instance_async,
    load_bundle_manifest,
)
from kdcube_ai_app.infra.plugin.app_readiness import (
    ApplicationNotReadyError,
    application_readiness_registry,
)

_log = logging.getLogger("kdcube.data_bus.worker")

DATA_BUS_CLAIM_BLOCK_MS = max(1, int(os.getenv("DATA_BUS_CLAIM_BLOCK_MS", "1000") or "1000"))
DATA_BUS_HANDLER_TIMEOUT_SECONDS = max(1, int(os.getenv("DATA_BUS_HANDLER_TIMEOUT_SECONDS", "120") or "120"))
DATA_BUS_MAX_RETRIES = max(0, int(os.getenv("DATA_BUS_MAX_RETRIES", "5") or "5"))
DATA_BUS_LOCK_MAX_RETRIES = max(1, int(os.getenv("DATA_BUS_LOCK_MAX_RETRIES", "100") or "100"))
DATA_BUS_LOCK_TTL_SECONDS = max(1, int(os.getenv("DATA_BUS_LOCK_TTL_SECONDS", "60") or "60"))
DATA_BUS_LOCK_RENEW_INTERVAL_SECONDS = max(
    0.1,
    float(os.getenv("DATA_BUS_LOCK_RENEW_INTERVAL_SECONDS", "10") or "10"),
)
DATA_BUS_LOCK_RETRY_SLEEP_SECONDS = max(
    0.0,
    float(os.getenv("DATA_BUS_LOCK_RETRY_SLEEP_SECONDS", "0.05") or "0.05"),
)

_USER_TYPE_VISIBILITY_ORDER: dict[str, int] = {
    "anonymous": 0,
    "registered": 1,
    "paid": 2,
    "privileged": 3,
}


@dataclass(frozen=True)
class _DataBusWorkerKey:
    bundle_id: str


class _DelegatedCardDenial(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: Mapping[str, Any],
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details)


def _actor_user_id(actor: Mapping[str, Any] | None) -> str | None:
    actor = actor or {}
    return actor.get("user_id") or actor.get("fingerprint")


def _actor_raw_roles(actor: Mapping[str, Any] | None) -> set[str]:
    return {
        role for role in ((actor or {}).get("roles") or [])
        if isinstance(role, str) and role.startswith("kdcube:role:")
    }


def _raw_roles_visible(required_roles: tuple[str, ...] | list[str] | None, actor: Mapping[str, Any] | None) -> bool:
    return roles_satisfy_any(_actor_raw_roles(actor), required_roles)


def _user_types_visible(
    required_user_types: tuple[str, ...] | list[str] | None,
    actor: Mapping[str, Any] | None,
) -> bool:
    user_types = tuple(
        str(user_type or "").strip().lower()
        for user_type in (required_user_types or ())
        if str(user_type or "").strip()
    )
    if not user_types:
        return True
    current = str((actor or {}).get("user_type") or "").strip().lower()
    if not current:
        return False
    current_rank = _USER_TYPE_VISIBILITY_ORDER.get(current)
    if current_rank is None:
        return current in set(user_types)
    thresholds = [
        _USER_TYPE_VISIBILITY_ORDER[user_type]
        for user_type in user_types
        if user_type in _USER_TYPE_VISIBILITY_ORDER
    ]
    if not thresholds:
        return current in set(user_types)
    return current_rank >= min(thresholds)


def _handler_visible(handler_spec: DataBusHandlerSpec, actor: Mapping[str, Any] | None) -> bool:
    return _user_types_visible(
        handler_spec.user_types,
        actor,
    ) and _raw_roles_visible(handler_spec.roles, actor)


def _delegated_card_context(
    actor: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], str] | None:
    authority = (
        dict((actor or {}).get("identity_authority") or {})
        if isinstance((actor or {}).get("identity_authority"), Mapping)
        else {}
    )
    binding = authority.get("delegated_card_binding")
    if not isinstance(binding, Mapping):
        return None
    binding = dict(binding)
    if not str(binding.get("access_id") or "").strip():
        return None
    resource = str(authority.get("delegated_resource") or "").strip()
    if not resource:
        raise _DelegatedCardDenial(
            code="delegated_card_scope_invalid",
            message="Delegated Card resource binding is missing",
            details={},
        )
    return authority, binding, resource


async def _resolve_live_delegated_actor(
    redis: Any,
    *,
    tenant: str,
    project: str,
    actor: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project the current effective Card onto a queued Data Bus actor."""

    current_actor = dict(actor or {})
    context = _delegated_card_context(current_actor)
    if context is None:
        return current_actor
    authority, binding, resource = context
    access_id = str(binding.get("access_id") or "").strip()
    try:
        card = await resolve_live_grant_card(
            redis,
            tenant=tenant,
            project=project,
            access_id=access_id,
            expected_client_id=str(binding.get("client_id") or "").strip(),
            expected_grantor_subject=str(
                binding.get("grantor_user_id") or ""
            ).strip(),
            expected_delegate_subject=str(
                binding.get("delegate_identity") or ""
            ).strip(),
            card_store=delegated_card_store(tenant=tenant, project=project),
        )
    except LiveGrantCardError as exc:
        raise _DelegatedCardDenial(
            code="delegated_card_unavailable",
            message="Current delegated Card state is unavailable",
            details={"reason": exc.reason},
        ) from exc
    except Exception as exc:
        _log.warning(
            "[data_bus] delegated Card lookup failed access_id=%s",
            access_id,
            exc_info=True,
        )
        raise _DelegatedCardDenial(
            code="delegated_card_unavailable",
            message="Current delegated Card state is unavailable",
            details={},
        ) from exc
    if card is None:
        raise _DelegatedCardDenial(
            code="delegated_card_not_active",
            message="Delegated Card is no longer active",
            details={},
        )
    grants = live_grants_for_resource(card, resource)
    if grants is None:
        raise _DelegatedCardDenial(
            code="delegated_resource_not_granted",
            message="Delegated Card no longer covers this resource",
            details={"resource": resource},
        )

    operations = operations_for_resource(card.resource_operations, resource)
    role_projection = delegated_role_projection(
        grants,
        fallback_roles=current_actor.get("roles") or (),
    )
    if role_projection.selected_on_card:
        current_actor["roles"] = list(role_projection.roles)
        current_actor["permissions"] = list(grants)
        current_actor["user_type"] = role_projection.user_type
    authority.update(
        {
            "delegated_resource": resource,
            "grants": list(grants),
            "scopes": list(grants),
            "operations": list(operations),
            "resource_grants": {
                key: list(values)
                for key, values in card.resource_grants.items()
            },
            "resource_operations": {
                key: list(values)
                for key, values in card.resource_operations.items()
            },
            "roles": list(current_actor.get("roles") or []),
            "permissions": list(current_actor.get("permissions") or []),
            "delegated_roles_selected": role_projection.selected_on_card,
            "delegated_card_binding": {
                "schema": "connection_hub.delegated_card_binding.v1",
                "access_id": card.access_id,
                "client_id": card.client_id,
                "grantor_user_id": card.grantor_subject,
                "delegate_identity": card.delegate_subject,
                "expires_at": card.expires_at,
            },
        }
    )
    card_properties = getattr(card, "properties", None)
    if application_operation_policy_declared(card_properties):
        raw_policy = card_properties.get(APPLICATION_OPERATION_POLICY_PROPERTY)
        authority[APPLICATION_OPERATION_POLICY_PROPERTY] = (
            dict(raw_policy) if isinstance(raw_policy, Mapping) else raw_policy
        )
    else:
        authority.pop(APPLICATION_OPERATION_POLICY_PROPERTY, None)
    current_actor["identity_authority"] = authority
    current_actor["rate_limit_subject"] = f"card:{card.access_id}"
    return current_actor


def _project_application_operation_actor(
    actor: Mapping[str, Any] | None,
    *,
    operation_ref: str,
) -> dict[str, Any]:
    """Narrow one Data Bus invocation to its selected operation role."""

    current_actor = dict(actor or {})
    authority = (
        dict(current_actor.get("identity_authority") or {})
        if isinstance(current_actor.get("identity_authority"), Mapping)
        else {}
    )
    role = delegated_application_operation_role(
        authority,
        operation_ref=operation_ref,
    )
    if not role:
        return current_actor
    role_projection = delegated_role_projection((role,))
    resource_grants = authority.get("resource_grants")
    resource_operations = authority.get("resource_operations")
    authority.update(
        {
            "application_operation": operation_ref,
            "card_resource_grants": (
                {
                    resource: list(values)
                    for resource, values in resource_grants.items()
                }
                if isinstance(resource_grants, Mapping)
                else {}
            ),
            "card_resource_operations": (
                {
                    resource: list(values)
                    for resource, values in resource_operations.items()
                }
                if isinstance(resource_operations, Mapping)
                else {}
            ),
            "grants": [role],
            "scopes": [role],
            "operations": [operation_ref],
            "resource_grants": {"*": [role]},
            "resource_operations": {"*": [operation_ref]},
            "roles": list(role_projection.roles),
            "permissions": [role],
            "delegated_roles_selected": True,
        }
    )
    current_actor.update(
        {
            "roles": list(role_projection.roles),
            "permissions": [role],
            "user_type": role_projection.user_type,
            "identity_authority": authority,
        }
    )
    return current_actor


def _make_comm_context(message: DataBusMessage) -> ExternalEventPayload:
    actor = message.actor or {}
    reply = message.reply or {}
    session_id = str(reply.get("session_id") or actor.get("session_id") or "data-bus")
    socket_id = str(reply.get("socket_id") or "").strip() or None
    request_id = str((message.trace or {}).get("request_id") or message.message_id)
    return ExternalEventPayload(
        meta=ExternalEventMeta(
            task_id=message.message_id,
            created_at=now_ms() / 1000.0,
            instance_id=os.getenv("INSTANCE_ID"),
        ),
        routing=ExternalEventRouting(
            bundle_id=message.bundle_id,
            session_id=session_id,
            conversation_id=session_id,
            turn_id=None,
            socket_id=socket_id,
        ),
        actor=ExternalEventActor(
            tenant_id=message.tenant,
            project_id=message.project,
        ),
        user=ExternalEventUser(
            user_type=str(actor.get("user_type") or "anonymous"),
            user_id=actor.get("user_id"),
            username=actor.get("username"),
            email=actor.get("email"),
            fingerprint=actor.get("fingerprint"),
            roles=list(actor.get("roles") or []),
            permissions=list(actor.get("permissions") or []),
            timezone=actor.get("timezone"),
            identity_authority=dict(actor.get("identity_authority") or {}),
        ),
        request=ExternalEventRequest(
            request_id=request_id,
            operation="data_bus",
            payload={
                "subject": message.subject,
                "message_id": message.message_id,
            },
        ),
    )


def _make_reply_comm(
    *,
    message: DataBusMessage,
    relay: ChatRelayCommunicator,
) -> ChatCommunicator | None:
    reply = message.reply or {}
    session_id = str(reply.get("session_id") or (message.actor or {}).get("session_id") or "").strip()
    if not session_id:
        return None
    socket_id = str(reply.get("socket_id") or "").strip() or None
    request_id = str((message.trace or {}).get("request_id") or message.message_id)
    user_id = _actor_user_id(message.actor)
    service = ServiceCtx(
        request_id=request_id,
        tenant=message.tenant,
        project=message.project,
        user=user_id,
    )
    conversation = ConversationCtx(
        session_id=session_id,
        conversation_id=session_id,
        turn_id=None,
    )
    return ChatCommunicator(
        emitter=relay,
        service=service.model_dump(),
        conversation=conversation.model_dump(),
        room=session_id,
        target_sid=socket_id,
        tenant=message.tenant,
        project=message.project,
        user_id=(message.actor or {}).get("user_id"),
        user_type=(message.actor or {}).get("user_type"),
    )


async def _refresh_bundle_props(
    *,
    instance: Any,
    tenant: str,
    project: str,
    bundle_id: str,
) -> None:
    refresh_fn = getattr(instance, "refresh_bundle_props", None)
    if not callable(refresh_fn):
        return
    try:
        refresh_kwargs = {"state": {"tenant": tenant, "project": project}}
        try:
            refresh_params = inspect.signature(refresh_fn).parameters
        except (TypeError, ValueError):
            refresh_params = {}
        accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in refresh_params.values())
        if accepts_kwargs or "notify" in refresh_params:
            refresh_kwargs["notify"] = False
        if accepts_kwargs or "reason" in refresh_params:
            refresh_kwargs["reason"] = "data_bus"
        result = refresh_fn(**refresh_kwargs)
        if inspect.isawaitable(result):
            await result
    except Exception:
        _log.warning(
            "[data_bus] refresh_bundle_props failed for bundle=%s; proceeding with current props",
            bundle_id,
            exc_info=True,
        )


class DataBusBundleWorker:
    def __init__(
        self,
        *,
        redis: Any,
        redis_url: str | None,
        tenant: str,
        project: str,
        bundle_id: str,
        bundle_spec: BundleSpec,
        bundle_config: Any,
        handler_specs: Mapping[str, DataBusHandlerSpec],
        bundle_allowed_roles: tuple[str, ...] = (),
        instance_id: str,
    ) -> None:
        self.redis = redis
        self.redis_url = redis_url
        self.tenant = tenant
        self.project = project
        self.bundle_id = bundle_id
        self.bundle_spec = bundle_spec
        self.bundle_config = bundle_config
        self.handler_specs = dict(handler_specs or {})
        self.bundle_allowed_roles = tuple(bundle_allowed_roles or ())
        self.instance_id = instance_id
        self.consumer_name = f"{instance_id or os.getpid()}:{os.getpid()}:{bundle_id}"
        self.stream = RedisDataBusStream(
            redis,
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
        )
        self.locker = RedisDataBusPartitionLocker(redis, ttl_seconds=DATA_BUS_LOCK_TTL_SECONDS)
        self.relay = ChatRelayCommunicator(redis_url=redis_url)
        self._stop_event = asyncio.Event()

    async def run(self) -> None:
        await self.stream.ensure_group()
        _log.info(
            "[data_bus] Worker started: tenant=%s project=%s bundle=%s handlers=%s consumer=%s",
            self.tenant,
            self.project,
            self.bundle_id,
            sorted(self.handler_specs.keys()),
            self.consumer_name,
        )
        while not self._stop_event.is_set():
            try:
                claim = await self.stream.claim_next(
                    consumer_name=self.consumer_name,
                    count=1,
                    block_ms=DATA_BUS_CLAIM_BLOCK_MS,
                )
                if claim is None:
                    await asyncio.sleep(0)
                    continue
                await self._process_claim(claim)
            except asyncio.CancelledError:
                break
            except Exception:
                _log.warning("[data_bus] Worker loop failed: bundle=%s", self.bundle_id, exc_info=True)
                await asyncio.sleep(0.5)
        _log.info("[data_bus] Worker stopped: bundle=%s", self.bundle_id)

    def stop(self) -> None:
        self._stop_event.set()

    async def _process_claim(self, claim: DataBusClaim) -> None:
        message = claim.message
        try:
            application_readiness_registry.require_ready(
                tenant=message.tenant,
                project=message.project,
                application_id=message.bundle_id,
            )
        except ApplicationNotReadyError as exc:
            _log.info(
                "[data_bus] Deferring claim while application is unavailable: "
                "bundle=%s subject=%s message=%s state=%s",
                message.bundle_id,
                message.subject,
                message.message_id,
                exc.snapshot.state.value,
            )
            return
        handler_spec = self.handler_specs.get(message.subject)
        if handler_spec is None:
            result = DataBusResult.error_result(
                message,
                code="handler_not_found",
                message_text=f"No Data Bus handler registered for subject {message.subject}",
                status="rejected",
            )
            await self._complete_terminal_result(
                claim,
                result,
                dlq_reason="handler_not_found",
            )
            return
        try:
            current_actor = await _resolve_live_delegated_actor(
                getattr(self, "redis", None),
                tenant=message.tenant,
                project=message.project,
                actor=message.actor,
            )
        except _DelegatedCardDenial as exc:
            result = DataBusResult.error_result(
                message,
                code=exc.code,
                message_text=exc.message,
                details=exc.details,
                status="rejected",
            )
            await self._complete_terminal_result(claim, result)
            return
        if current_actor != message.actor:
            message = replace(message, actor=current_actor)
            claim = replace(claim, message=message)
        try:
            application_operations = delegated_application_operation_selection(
                (message.actor or {}).get("identity_authority")
            )
        except ApplicationOperationPolicyError as exc:
            result = DataBusResult.error_result(
                message,
                code="application_operation_policy_invalid",
                message_text="Data Bus application operation policy is invalid",
                details={"reason": exc.reason},
                status="rejected",
            )
            await self._complete_terminal_result(claim, result)
            return
        if application_operations is not None and handler_spec.operation_id_explicit:
            operation_ref = data_bus_application_operation_ref(
                application_id=message.bundle_id,
                subject=handler_spec.subject,
                operation_id=handler_spec.operation_id,
            )
            if operation_ref not in application_operations:
                result = DataBusResult.error_result(
                    message,
                    code="application_operation_not_granted",
                    message_text="Data Bus application operation is not granted",
                    details={"operation_ref": operation_ref},
                    status="rejected",
                )
                await self._complete_terminal_result(claim, result)
                return
            try:
                operation_actor = _project_application_operation_actor(
                    message.actor,
                    operation_ref=operation_ref,
                )
            except ApplicationOperationPolicyError as exc:
                result = DataBusResult.error_result(
                    message,
                    code="application_operation_policy_invalid",
                    message_text="Data Bus application operation policy is invalid",
                    details={"reason": exc.reason},
                    status="rejected",
                )
                await self._complete_terminal_result(claim, result)
                return
            message = replace(message, actor=operation_actor)
            claim = replace(claim, message=message)
        if not _raw_roles_visible(self.bundle_allowed_roles, message.actor):
            result = DataBusResult.error_result(
                message,
                code="bundle_not_visible",
                message_text="Data Bus bundle is not visible to this actor",
                status="rejected",
            )
            await self._complete_terminal_result(claim, result)
            return
        if not _handler_visible(handler_spec, message.actor):
            result = DataBusResult.error_result(
                message,
                code="handler_not_visible",
                message_text=f"Data Bus subject is not visible to this actor: {message.subject}",
                status="rejected",
            )
            await self._complete_terminal_result(claim, result)
            return
        if handler_spec.idempotency == DATA_BUS_IDEMPOTENCY_REQUIRED and not message.idempotency_key:
            result = DataBusResult.error_result(
                message,
                code="idempotency_key_required",
                message_text="Data Bus handler requires idempotency_key",
                status="rejected",
            )
            await self._complete_terminal_result(
                claim,
                result,
                dlq_reason="idempotency_key_required",
            )
            return

        lock = None
        renew_task: asyncio.Task | None = None
        try:
            if handler_spec.ordering == DATA_BUS_ORDERING_SERIAL_PER_PARTITION:
                partition_key = self._partition_key(handler_spec, message)
                if not partition_key:
                    result = DataBusResult.error_result(
                        message,
                        code="partition_required",
                        message_text="Data Bus handler requires an object partition",
                        status="rejected",
                    )
                    await self._complete_terminal_result(
                        claim,
                        result,
                        dlq_reason="partition_required",
                    )
                    return
                lock = await self.locker.acquire(partition_key)
                if lock is None:
                    if DATA_BUS_LOCK_RETRY_SLEEP_SECONDS > 0:
                        await asyncio.sleep(DATA_BUS_LOCK_RETRY_SLEEP_SECONDS)
                        lock = await self.locker.acquire(partition_key)
                if lock is None:
                    retry_count = int((message.trace or {}).get("retry_count") or 0)
                    if retry_count >= DATA_BUS_LOCK_MAX_RETRIES:
                        result = DataBusResult.error_result(
                            message,
                            code="partition_lock_busy",
                            message_text="Data Bus partition remained busy after the retry limit",
                            details={"retry_count": retry_count},
                        )
                        await self._complete_terminal_result(
                            claim,
                            result,
                            dlq_reason="partition_lock_busy",
                            dlq_details={"retry_count": retry_count},
                        )
                        return
                    await self.stream.requeue(
                        claim,
                        reason="partition_lock_busy",
                        max_retries=DATA_BUS_LOCK_MAX_RETRIES,
                    )
                    return
                renew_task = asyncio.create_task(
                    renew_lock_until_cancelled(
                        self.locker,
                        lock,
                        interval_seconds=DATA_BUS_LOCK_RENEW_INTERVAL_SECONDS,
                    ),
                    name=f"data-bus-lock-renew:{self.bundle_id}:{message.subject}",
                )

            try:
                result, reply_sent = await self._invoke_handler(claim, handler_spec)
            except Exception as exc:
                await self._handle_handler_failure(claim, exc)
                return
            await self.stream.write_result(result, stream_id=claim.stream_id)
            if not reply_sent:
                await self._send_default_reply(message, result)
            await self.stream.ack(claim)
        finally:
            if renew_task is not None:
                renew_task.cancel()
                try:
                    await renew_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    _log.debug("[data_bus] Lock renewal task ended with error", exc_info=True)
            if lock is not None:
                try:
                    await self.locker.release(lock)
                except Exception:
                    _log.debug("[data_bus] Failed to release partition lock: key=%s", lock.key, exc_info=True)

    async def _handle_handler_failure(
        self,
        claim: DataBusClaim,
        exc: Exception,
    ) -> None:
        message = claim.message
        retry_count = int((message.trace or {}).get("retry_count") or 0)
        if retry_count < DATA_BUS_MAX_RETRIES:
            _log.warning(
                "[data_bus] Handler failed; requeueing: bundle=%s subject=%s message=%s retry=%s",
                self.bundle_id,
                message.subject,
                message.message_id,
                retry_count + 1,
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            await self.stream.requeue(
                claim,
                reason="handler_error",
                max_retries=DATA_BUS_MAX_RETRIES,
            )
            return
        result = DataBusResult.error_result(
            message,
            code="handler_error",
            message_text=str(exc),
            details={"stream_id": claim.stream_id, "retry_count": retry_count},
        )
        await self._complete_terminal_result(
            claim,
            result,
            dlq_reason="handler_error",
            dlq_details={"retry_count": retry_count, "error": str(exc)},
        )

    async def _complete_terminal_result(
        self,
        claim: DataBusClaim,
        result: DataBusResult,
        *,
        dlq_reason: str | None = None,
        dlq_details: Mapping[str, Any] | None = None,
    ) -> None:
        message = claim.message
        await self.stream.write_result(result, stream_id=claim.stream_id)
        if dlq_reason:
            details = {"stream_id": claim.stream_id, **dict(dlq_details or {})}
            await self.stream.write_dlq(
                message,
                reason=dlq_reason,
                details=details,
            )
        error_code = ""
        if isinstance(result.error, Mapping):
            error_code = str(result.error.get("code") or "")
        _log.info(
            "[data_bus] Terminal claim result: bundle=%s subject=%s message=%s "
            "status=%s code=%s stream_id=%s",
            message.bundle_id,
            message.subject,
            message.message_id,
            result.status,
            error_code,
            claim.stream_id,
        )
        await self._send_default_reply(message, result)
        await self.stream.ack(claim)

    def _partition_key(self, handler_spec: DataBusHandlerSpec, message: DataBusMessage) -> str | None:
        if handler_spec.partition_by == DATA_BUS_PARTITION_OBJECT_REF:
            object_ref = str(message.object_ref or "").strip()
            if not object_ref:
                return None
            return f"{message.tenant}:{message.project}:{message.bundle_id}:{message.subject}:{object_ref}"
        return f"{message.tenant}:{message.project}:{message.bundle_id}:{message.subject}"

    async def _invoke_handler(self, claim: DataBusClaim, handler_spec: DataBusHandlerSpec) -> tuple[DataBusResult, bool]:
        message = claim.message
        comm_context = _make_comm_context(message)
        pg_pool = None
        try:
            from kdcube_ai_app.apps.chat.ingress.resolvers import get_pg_pool
            pg_pool = await get_pg_pool()
        except Exception:
            _log.debug("[data_bus] pg_pool not available for bundle=%s", self.bundle_id, exc_info=True)

        instance, _ = await get_workflow_instance_async(
            self.bundle_spec,
            self.bundle_config,
            comm_context=comm_context,
            redis=getattr(self.bundle_config, "redis", None),
            pg_pool=pg_pool,
        )
        await _refresh_bundle_props(
            instance=instance,
            tenant=message.tenant,
            project=message.project,
            bundle_id=message.bundle_id,
        )

        fn = getattr(instance, handler_spec.method_name, None)
        if fn is None:
            raise RuntimeError(f"Data Bus handler method not found: {handler_spec.method_name}")

        reply_comm = _make_reply_comm(message=message, relay=self.relay)
        reply = DataBusReply(message=message, comm=reply_comm)
        auth_context = AuthContext.from_mapping(
            {
                "tenant": message.tenant,
                "project": message.project,
                "bundle_id": message.bundle_id,
                "stream_id": claim.stream_id,
                "actor": dict(message.actor or {}),
                "request_id": message.message_id,
            },
            source="data_bus",
        )
        ctx = DataBusContext(
            tenant=message.tenant,
            project=message.project,
            bundle_id=message.bundle_id,
            actor=dict(message.actor or {}),
            auth_context=auth_context,
            bundle=instance,
            comm=reply_comm,
            reply=reply,
            stream_id=claim.stream_id,
            consumer_name=claim.consumer_name,
            handler=handler_spec,
        )
        named_service_caller = make_local_bundle_named_service_caller(
            redis=self.redis,
            pg_pool=pg_pool,
            comm_context=comm_context,
        )
        operation_caller = make_local_bundle_operation_caller(
            redis=self.redis,
            pg_pool=pg_pool,
            comm_context=comm_context,
        )
        operation_stream_caller = make_local_bundle_operation_stream_caller(
            redis=self.redis,
            pg_pool=pg_pool,
            comm_context=comm_context,
        )
        with (
            bind_auth_context(auth_context),
            bind_current_request_context(
                comm_context,
                comm=reply_comm,
                bundle_id=message.bundle_id,
            ),
            bind_bundle_operation_caller(operation_caller),
            bind_bundle_operation_stream_caller(operation_stream_caller),
            bind_bundle_named_service_caller(named_service_caller),
        ):
            if inspect.iscoroutinefunction(fn):
                result = await asyncio.wait_for(fn(ctx, message), timeout=DATA_BUS_HANDLER_TIMEOUT_SECONDS)
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(fn, ctx, message),
                    timeout=DATA_BUS_HANDLER_TIMEOUT_SECONDS,
                )
            if inspect.isawaitable(result):
                result = await asyncio.wait_for(result, timeout=DATA_BUS_HANDLER_TIMEOUT_SECONDS)
            return coerce_data_bus_result(result, message), reply.sent_count > 0

    async def _send_default_reply(self, message: DataBusMessage, result: DataBusResult) -> None:
        if not message.reply:
            return
        reply_comm = _make_reply_comm(message=message, relay=self.relay)
        reply = DataBusReply(message=message, comm=reply_comm)
        if result.status == "ok":
            await reply.ok(result.data)
        elif result.status == "conflict":
            await reply.conflict(result.data)
        elif result.error:
            await reply.error(
                str(result.error.get("code") or result.status or "error"),
                str(result.error.get("message") or "Data Bus handler failed"),
                result.error.get("details") if isinstance(result.error.get("details"), Mapping) else None,
            )


class DataBusRuntimeManager:
    def __init__(
        self,
        *,
        redis: Any,
        redis_url: Optional[str],
        tenant: str,
        project: str,
        instance_id: str,
    ) -> None:
        self._redis = redis
        self._redis_url = redis_url
        self._worker_redis = redis
        self._owns_worker_redis = False
        if redis_url:
            try:
                from kdcube_ai_app.infra.redis.client import create_async_redis_client
                self._worker_redis = create_async_redis_client(
                    redis_url,
                    client_name_kind="data_bus",
                )
                self._owns_worker_redis = True
            except Exception:
                _log.warning(
                    "[data_bus] Could not create dedicated worker Redis client; falling back to shared pool",
                    exc_info=True,
                )
                self._worker_redis = redis
        self._tenant = tenant
        self._project = project
        self._instance_id = instance_id
        self._workers: Dict[_DataBusWorkerKey, Tuple[asyncio.Task, str, DataBusBundleWorker]] = {}
        _log.info(
            "[data_bus] Manager initialised: tenant=%s project=%s instance=%s",
            tenant,
            project,
            instance_id,
        )

    async def reconcile(self, registry: Any) -> None:
        from kdcube_ai_app.apps.chat.sdk.runtime.bundle_scheduler import _make_headless_config, is_bundle_enabled
        from kdcube_ai_app.infra.plugin.app_readiness import (
            ApplicationNotReadyError,
            application_readiness_registry,
        )
        from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props

        desired: Dict[_DataBusWorkerKey, Tuple[str, BundleSpec, Any, Dict[str, DataBusHandlerSpec], Tuple[str, ...]]] = {}
        for bundle_id, entry in (getattr(registry, "bundles", None) or {}).items():
            try:
                application_readiness_registry.require_ready(
                    tenant=self._tenant,
                    project=self._project,
                    application_id=bundle_id,
                )
            except ApplicationNotReadyError:
                _log.info(
                    "[data_bus] Application is not ready; handlers remain inactive: bundle=%s",
                    bundle_id,
                )
                continue
            path = entry.path if hasattr(entry, "path") else entry.get("path", "")
            module = entry.module if hasattr(entry, "module") else entry.get("module")
            singleton = entry.singleton if hasattr(entry, "singleton") else entry.get("singleton", False)
            if not path:
                continue
            spec = BundleSpec(id=bundle_id, path=path, module=module, singleton=bool(singleton))
            try:
                manifest = load_bundle_manifest(spec, bundle_id=bundle_id)
            except Exception:
                _log.warning("[data_bus] Failed to load manifest for bundle=%s; skipping", bundle_id, exc_info=True)
                continue
            if not manifest.data_bus_handlers:
                continue
            try:
                props = await get_bundle_props(
                    self._redis,
                    tenant=self._tenant,
                    project=self._project,
                    bundle_id=bundle_id,
                )
            except Exception:
                _log.warning("[data_bus] Failed to load props for bundle=%s; using empty props", bundle_id, exc_info=True)
                props = {}
            if not is_bundle_enabled(props):
                _log.info("[data_bus] Bundle disabled via enabled.bundle: bundle=%s", bundle_id)
                continue
            effective_manifest = apply_bundle_overrides(manifest, dict(props or {}))
            handler_specs = {handler.subject: handler for handler in effective_manifest.data_bus_handlers}
            bundle_allowed_roles = tuple(effective_manifest.allowed_roles or ())
            signature = "|".join(
                f"{item.subject}:{item.operation_id}:{item.method_name}:{item.partition_by}:{item.ordering}:{item.idempotency}:{','.join(item.user_types)}:{','.join(item.roles)}"
                for item in sorted(handler_specs.values(), key=lambda spec_item: spec_item.subject)
            )
            if bundle_allowed_roles:
                signature = f"{signature}|bundle_roles={','.join(bundle_allowed_roles)}"
            bundle_config = _make_headless_config(
                tenant=self._tenant,
                project=self._project,
                bundle_id=bundle_id,
                bundle_spec=spec,
                redis=self._redis,
                props=props,
            )
            if inspect.isawaitable(bundle_config):
                bundle_config = await bundle_config
            desired[_DataBusWorkerKey(bundle_id=bundle_id)] = (
                signature,
                spec,
                bundle_config,
                handler_specs,
                bundle_allowed_roles,
            )

        for key in list(self._workers.keys()):
            task, old_signature, worker = self._workers[key]
            if key not in desired:
                _log.info("[data_bus] Cancelling removed worker: bundle=%s", key.bundle_id)
                worker.stop()
                task.cancel()
                del self._workers[key]
            elif desired[key][0] != old_signature:
                _log.info("[data_bus] Handler signature changed; restarting worker: bundle=%s", key.bundle_id)
                worker.stop()
                task.cancel()
                del self._workers[key]

        for key, (signature, spec, bundle_config, handler_specs, bundle_allowed_roles) in desired.items():
            if key in self._workers:
                continue
            worker = DataBusBundleWorker(
                redis=self._worker_redis,
                redis_url=self._redis_url,
                tenant=self._tenant,
                project=self._project,
                bundle_id=key.bundle_id,
                bundle_spec=spec,
                bundle_config=bundle_config,
                handler_specs=handler_specs,
                bundle_allowed_roles=bundle_allowed_roles,
                instance_id=self._instance_id,
            )
            task = asyncio.create_task(worker.run(), name=f"data-bus:{key.bundle_id}")
            self._workers[key] = (task, signature, worker)
            _log.info("[data_bus] Worker scheduled: bundle=%s subjects=%s", key.bundle_id, sorted(handler_specs.keys()))

    async def remove_bundle(self, bundle_id: str) -> None:
        """Stop one bundle's Data Bus worker and retain all other workers."""
        normalized_id = str(bundle_id or "").strip()
        removed_tasks: list[asyncio.Task] = []
        for key in list(self._workers):
            if key.bundle_id != normalized_id:
                continue
            task, _signature, worker = self._workers.pop(key)
            worker.stop()
            task.cancel()
            removed_tasks.append(task)
            _log.info("[data_bus] Cancelling worker for removed bundle: bundle=%s", key.bundle_id)
        if removed_tasks:
            await asyncio.gather(*removed_tasks, return_exceptions=True)

    async def shutdown(self) -> None:
        _log.info("[data_bus] Shutting down %d worker(s)", len(self._workers))
        for _key, (task, _signature, worker) in list(self._workers.items()):
            worker.stop()
            task.cancel()
        if self._workers:
            await asyncio.gather(*[task for task, _signature, _worker in self._workers.values()], return_exceptions=True)
        self._workers.clear()
        if self._owns_worker_redis:
            try:
                await self._worker_redis.close()
            except Exception:
                _log.debug("[data_bus] Failed to close dedicated Redis client", exc_info=True)
        _log.info("[data_bus] Shutdown complete")
