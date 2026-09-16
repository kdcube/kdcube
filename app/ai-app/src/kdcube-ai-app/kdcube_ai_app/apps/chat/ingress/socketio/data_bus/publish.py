# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Mapping

from connection_hub.delegated_credentials.live_grant import (
    LiveGrantCardError,
    live_grants_for_resource,
    resolve_live_grant_card,
)
from connection_hub.delegated_credentials.resource_operations import (
    operations_for_resource,
)
from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.integrations.connection_hub.delegated_roles import (
    delegated_role_projection,
)
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import RedisDataBusStream
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import (
    DATA_BUS_INGRESS_SCHEMA,
    DataBusMessage,
    ensure_json_object,
    ensure_json_serializable,
    normalize_optional_string,
    normalize_subject,
    timestamp_message_id,
    utc_now_iso,
)
from kdcube_ai_app.auth.sessions import UserSession, UserType
from kdcube_ai_app.infra.gateway.data_bus_limiter import (
    DataBusPublishLimitResult,
    check_data_bus_publish_limits,
)
from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props, load_registry

logger = logging.getLogger("kdcube.data_bus.socketio")

_DISABLED_VALUES = frozenset({"false", "disable", "disabled", "off", "0"})


def _is_truthy_enabled(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    return str(value).strip().lower() not in _DISABLED_VALUES


def _bundle_enabled(props: Mapping[str, Any] | None) -> bool:
    enabled = (props or {}).get("enabled")
    if not isinstance(enabled, Mapping):
        return True
    return _is_truthy_enabled(enabled.get("bundle"))


def _session_user_type(session: UserSession) -> str:
    value = getattr(getattr(session, "user_type", None), "value", None)
    return str(value or getattr(session, "user_type", "") or "").strip().lower()


def _session_from_socket_meta(socket_session: Mapping[str, Any] | None) -> UserSession:
    data = dict((socket_session or {}).get("user_session") or {})
    return UserSession(
        session_id=str(data.get("session_id") or "unknown"),
        user_type=UserType(data.get("user_type") or "anonymous"),
        fingerprint=data.get("fingerprint") or "unknown",
        user_id=data.get("user_id"),
        username=data.get("username"),
        email=data.get("email"),
        roles=list(data.get("roles") or []),
        permissions=list(data.get("permissions") or []),
        timezone=data.get("timezone") or "unknown",
        identity_authority=(
            dict(data.get("identity_authority"))
            if isinstance(data.get("identity_authority"), Mapping)
            else None
        ),
        rate_limit_subject=data.get("rate_limit_subject"),
    )


def _actor_from_session(session: UserSession) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "user_type": _session_user_type(session),
        "user_id": session.user_id,
        "username": session.username,
        "email": session.email,
        "fingerprint": session.fingerprint,
        "roles": list(session.roles or []),
        "permissions": list(session.permissions or []),
        "timezone": session.timezone,
        "identity_authority": dict(session.identity_authority or {}),
        "rate_limit_subject": session.rate_limit_subject,
    }


async def _live_delegated_card(
    *,
    redis: Any,
    tenant: str,
    project: str,
    scope: Mapping[str, Any],
) -> tuple[Any | None, dict[str, Any] | None]:
    access_id = str(scope.get("access_id") or "").strip()
    resource = str(scope.get("resource") or "").strip()
    if not access_id or not resource:
        return None, {
            "index": None,
            "error": "delegated Card scope is incomplete",
            "error_type": "delegated_card_scope_invalid",
            "status": 401,
        }
    try:
        card = await resolve_live_grant_card(
            redis,
            tenant=tenant,
            project=project,
            access_id=access_id,
            expected_client_id=str(scope.get("client_id") or "").strip(),
            expected_delegate_subject=str(
                scope.get("delegate_identity") or ""
            ).strip(),
        )
    except LiveGrantCardError as exc:
        return None, {
            "index": None,
            "error": "current delegated Card state is unavailable",
            "error_type": "delegated_card_unavailable",
            "reason": exc.reason,
            "status": 503,
        }
    except Exception:
        logger.warning(
            "[data_bus.publish] delegated Card lookup failed access_id=%s",
            access_id,
            exc_info=True,
        )
        return None, {
            "index": None,
            "error": "current delegated Card state is unavailable",
            "error_type": "delegated_card_unavailable",
            "status": 503,
        }
    if card is None:
        return None, {
            "index": None,
            "error": "delegated Card is no longer active",
            "error_type": "delegated_card_not_active",
            "status": 403,
        }
    if live_grants_for_resource(card, resource) is None:
        return None, {
            "index": None,
            "error": "delegated Card no longer covers this resource",
            "error_type": "delegated_resource_not_granted",
            "status": 403,
        }
    return card, None


def _apply_live_delegated_card(
    session: UserSession,
    *,
    card: Any,
    resource: str,
) -> None:
    grants = live_grants_for_resource(card, resource) or ()
    operations = operations_for_resource(card.resource_operations, resource)
    role_projection = delegated_role_projection(
        grants,
        fallback_roles=session.roles,
    )
    if role_projection.selected_on_card:
        session.roles = list(role_projection.roles)
        session.permissions = list(grants)
        session.user_type = UserType(role_projection.user_type)
    authority = dict(session.identity_authority or {})
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
            "roles": list(session.roles or []),
            "permissions": list(session.permissions or []),
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
    session.identity_authority = authority
    session.rate_limit_subject = f"card:{card.access_id}"


class DataBusSocketIOIngress:
    def __init__(self, *, app: Any, redis: Any | None = None) -> None:
        self.app = app
        self.redis = redis

    def _redis(self) -> Any:
        redis = self.redis or getattr(getattr(self.app, "state", None), "redis_async", None)
        if redis is None:
            raise RuntimeError("redis_async is not initialized on app.state")
        return redis

    async def handle_publish(
        self,
        *,
        sid: str,
        socket_session: Mapping[str, Any] | None,
        data: Any,
        reply_transport: str = "socketio",
    ) -> dict[str, Any]:
        if not isinstance(data, Mapping):
            return self._ack(status="rejected", rejected=[{"index": None, "error": "payload must be an object"}])
        try:
            encoded_size = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
        except Exception:
            return self._ack(status="rejected", rejected=[{"index": None, "error": "payload must be JSON-serializable"}])

        bundle_id = str(data.get("bundle_id") or "").strip()
        if not bundle_id:
            return self._ack(status="rejected", rejected=[{"index": None, "error": "bundle_id is required"}])
        federated_claims = (socket_session or {}).get("federated_claims")
        data_bus_scope = (socket_session or {}).get("data_bus_scope")
        if not isinstance(data_bus_scope, Mapping) and isinstance(
            federated_claims, Mapping
        ):
            data_bus_scope = {
                "credential_kind": "derived_session",
                "bundle_id": federated_claims.get("bundle_id"),
                "expires_at": federated_claims.get("exp"),
            }
        if isinstance(data_bus_scope, Mapping):
            scope_kind = str(data_bus_scope.get("credential_kind") or "").strip()
            delegated_scope = scope_kind == "delegated_card"
            scoped_bundle_id = str(data_bus_scope.get("bundle_id") or "").strip()
            if scoped_bundle_id and scoped_bundle_id != bundle_id:
                return self._ack(status="rejected", rejected=[{
                    "index": None,
                    "error": (
                        "bundle_id is not allowed by the delegated Card"
                        if delegated_scope
                        else "bundle_id is not allowed by federated token"
                    ),
                }])
            try:
                expires_at = int(data_bus_scope.get("expires_at") or 0)
            except (TypeError, ValueError):
                expires_at = 0
            if expires_at <= int(time.time()):
                return self._ack(
                    status="rejected",
                    rejected=[{
                        "index": None,
                        "error": (
                            "delegated Card is expired"
                            if delegated_scope
                            else "federated Data Bus session is expired"
                        ),
                        "error_type": (
                            "delegated_card_expired"
                            if delegated_scope
                            else "federated_token_expired"
                        ),
                        "status": 401,
                    }],
                )
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._ack(status="rejected", rejected=[{"index": None, "error": "messages[] is required"}])

        settings = get_settings()
        tenant = str((socket_session or {}).get("tenant") or settings.TENANT or "").strip()
        project = str((socket_session or {}).get("project") or settings.PROJECT or "").strip()
        if not tenant or not project:
            return self._ack(status="rejected", rejected=[{"index": None, "error": "tenant/project scope is required"}])

        session = _session_from_socket_meta(socket_session)
        if (
            isinstance(data_bus_scope, Mapping)
            and str(data_bus_scope.get("credential_kind") or "").strip()
            == "delegated_card"
        ):
            scoped_tenant = str(data_bus_scope.get("tenant") or "").strip()
            scoped_project = str(data_bus_scope.get("project") or "").strip()
            if (scoped_tenant, scoped_project) != (tenant, project):
                return self._ack(
                    status="rejected",
                    rejected=[{
                        "index": None,
                        "error": "tenant/project is not allowed by the delegated Card",
                        "error_type": "delegated_card_scope_mismatch",
                        "status": 403,
                    }],
                )
            card, rejection = await _live_delegated_card(
                redis=self._redis(),
                tenant=tenant,
                project=project,
                scope=data_bus_scope,
            )
            if rejection is not None:
                return self._ack(status="rejected", rejected=[rejection])
            _apply_live_delegated_card(
                session,
                card=card,
                resource=str(data_bus_scope.get("resource") or "").strip(),
            )
        gateway_adapter = getattr(getattr(self.app, "state", None), "gateway_adapter", None)
        gateway_config = getattr(getattr(gateway_adapter, "gateway", None), "gateway_config", None)
        if gateway_config is None:
            logger.warning(
                "[data_bus.publish] rejected package because gateway configuration is unavailable tenant=%s project=%s bundle=%s sid=%s",
                tenant,
                project,
                bundle_id,
                sid,
            )
            return self._ack(
                status="rejected",
                rejected=[{
                    "index": None,
                    "error": "gateway configuration unavailable",
                    "error_type": "gateway_unavailable",
                    "status": 503,
                }],
            )
        limit_result = await check_data_bus_publish_limits(
            redis=self._redis(),
            gateway_config=gateway_config,
            session=session,
            package_bytes=encoded_size,
            message_count=len(messages),
        )
        if not limit_result.ok:
            return self._ack(status="rejected", rejected=[self._limit_rejection(limit_result)])

        try:
            await self._ensure_registered_bundle(
                tenant=tenant,
                project=project,
                bundle_id=bundle_id,
            )
        except ValueError as exc:
            return self._ack(status="rejected", rejected=[{"index": None, "error": str(exc)}])
        except Exception:
            logger.warning(
                "[data_bus.publish] Failed to resolve bundle registration tenant=%s project=%s bundle=%s",
                tenant,
                project,
                bundle_id,
                exc_info=True,
            )
            return self._ack(status="rejected", rejected=[{"index": None, "error": "bundle registration unavailable"}])

        props = await get_bundle_props(self._redis(), tenant=tenant, project=project, bundle_id=bundle_id)
        if not _bundle_enabled(props):
            return self._ack(status="rejected", rejected=[{"index": None, "error": "bundle is disabled"}])
        logger.info(
            "[data_bus.publish] received package tenant=%s project=%s bundle=%s sid=%s messages=%s bytes=%s",
            tenant,
            project,
            bundle_id,
            sid,
            len(messages),
            encoded_size,
        )
        stream = RedisDataBusStream(
            self._redis(),
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
        )
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for index, item in enumerate(messages):
            try:
                message = self._normalize_message(
                    item,
                    index=index,
                    tenant=tenant,
                    project=project,
                    bundle_id=bundle_id,
                    session=session,
                    sid=sid,
                    reply_transport=reply_transport,
                )
                logger.info(
                    "[data_bus.publish] received message tenant=%s project=%s bundle=%s subject=%s object_ref=%s message_id=%s sid=%s index=%s",
                    tenant,
                    project,
                    bundle_id,
                    message.subject,
                    message.object_ref,
                    message.message_id,
                    sid,
                    index,
                )
                result = await stream.publish(message)
                logger.info(
                    "[data_bus.publish] accepted message tenant=%s project=%s bundle=%s subject=%s object_ref=%s message_id=%s stream_id=%s",
                    tenant,
                    project,
                    bundle_id,
                    message.subject,
                    message.object_ref,
                    result.message_id,
                    result.stream_id,
                )
                accepted.append({
                    "message_id": result.message_id,
                    "stream_id": result.stream_id,
                })
            except Exception as exc:
                rejected.append({
                    "index": index,
                    "message_id": (
                        str(item.get("message_id"))
                        if isinstance(item, Mapping) and item.get("message_id")
                        else None
                    ),
                    "error": str(exc),
                })
        status = "accepted" if accepted and not rejected else "partial" if accepted else "rejected"
        return self._ack(status=status, accepted=accepted, rejected=rejected)

    async def _ensure_registered_bundle(
        self,
        *,
        tenant: str,
        project: str,
        bundle_id: str,
    ) -> None:
        """Verify stream ownership without importing processor-owned app code."""

        reg = await load_registry(self._redis(), tenant, project)
        entry = (getattr(reg, "bundles", None) or {}).get(bundle_id)
        if entry is None:
            raise ValueError("bundle not found")

    def _normalize_message(
        self,
        item: Any,
        *,
        index: int,
        tenant: str,
        project: str,
        bundle_id: str,
        session: UserSession,
        sid: str,
        reply_transport: str,
    ) -> DataBusMessage:
        if not isinstance(item, Mapping):
            raise ValueError("message must be an object")
        subject = normalize_subject(item.get("subject"))

        object_ref = normalize_optional_string(item.get("object_ref"))
        idempotency_key = normalize_optional_string(item.get("idempotency_key"))

        payload = ensure_json_object(item.get("payload"), field_name="payload")
        ensure_json_serializable(payload, field_name="payload")

        client = item.get("client")
        trace = ensure_json_object(item.get("trace"), field_name="trace")
        trace.update({
            "request_id": str(trace.get("request_id") or uuid.uuid4()),
            "client_message_index": index,
            "socket_id": sid,
        })
        if isinstance(client, Mapping):
            trace["client"] = dict(client)

        reply = {
            "transport": str(reply_transport or "socketio"),
            "session_id": session.session_id,
        }
        if sid:
            reply["socket_id"] = sid
            if str(reply_transport or "").strip().lower() == "sse":
                reply["stream_id"] = sid

        return DataBusMessage(
            message_id=str(item.get("message_id") or timestamp_message_id()),
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            subject=subject,
            object_ref=object_ref,
            idempotency_key=idempotency_key,
            actor=_actor_from_session(session),
            payload=payload,
            reply=reply,
            trace=trace,
            created_at=str(item.get("created_at") or utc_now_iso()),
        )

    @staticmethod
    def _ack(
        *,
        status: str,
        accepted: list[dict[str, Any]] | None = None,
        rejected: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema": DATA_BUS_INGRESS_SCHEMA,
            "status": status,
            "accepted": accepted or [],
            "rejected": rejected or [],
        }

    @staticmethod
    def _limit_rejection(result: DataBusPublishLimitResult) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "index": None,
            "error": result.error,
            "error_type": result.error_type,
            "status": 429,
            "limit": result.limit,
            "limit_value": result.limit_value,
            "observed": result.observed,
            "window_seconds": result.window_seconds,
        }
        if result.retry_after is not None:
            payload["retry_after"] = result.retry_after
        if result.stats:
            payload["stats"] = dict(result.stats)
        return payload


def attach_data_bus_socketio_handlers(chat_handler: Any) -> None:
    sio = getattr(chat_handler, "sio", None)
    if sio is None:
        return
    ingress = DataBusSocketIOIngress(app=chat_handler.app)
    chat_handler._data_bus_ingress = ingress

    @sio.on("data_bus.publish")
    async def _on_data_bus_publish(sid, data):
        try:
            socket_session = await sio.get_session(sid)
        except Exception:
            socket_session = {}
        return await ingress.handle_publish(sid=sid, socket_session=socket_session or {}, data=data)
