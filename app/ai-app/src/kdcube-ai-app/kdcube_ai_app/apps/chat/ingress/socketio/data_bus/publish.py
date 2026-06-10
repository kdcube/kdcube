# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.ingress.ingress_core import (
    build_ws_chat_request_context,
    map_gateway_error,
    run_gateway_checks,
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
from kdcube_ai_app.auth.sessions import RequestContext, UserSession, UserType
from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props, load_registry

logger = logging.getLogger("kdcube.data_bus.socketio")

DATA_BUS_PACKAGE_MAX_BYTES = max(
    4096,
    int(os.getenv("DATA_BUS_PACKAGE_MAX_BYTES", str(1024 * 1024)) or str(1024 * 1024)),
)
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
    }


def _allowed_subjects_from_socket_meta(socket_session: Mapping[str, Any] | None) -> set[str]:
    claims = (socket_session or {}).get("federated_claims")
    if not isinstance(claims, Mapping):
        return set()
    return {
        str(subject).strip()
        for subject in (claims.get("allowed_subjects") or ())
        if str(subject).strip()
    }


def _request_context_from_socket_meta(socket_session: Mapping[str, Any] | None) -> RequestContext:
    ctx_data = (socket_session or {}).get("request_context")
    if isinstance(ctx_data, Mapping):
        try:
            return RequestContext(**dict(ctx_data))
        except Exception:
            logger.debug("[data_bus.publish] invalid stored socket request_context", exc_info=True)
    return build_ws_chat_request_context()


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
        if encoded_size > DATA_BUS_PACKAGE_MAX_BYTES:
            return self._ack(status="rejected", rejected=[{"index": None, "error": "payload too large"}])

        bundle_id = str(data.get("bundle_id") or "").strip()
        if not bundle_id:
            return self._ack(status="rejected", rejected=[{"index": None, "error": "bundle_id is required"}])
        federated_claims = (socket_session or {}).get("federated_claims")
        if isinstance(federated_claims, Mapping):
            scoped_bundle_id = str(federated_claims.get("bundle_id") or "").strip()
            if scoped_bundle_id and scoped_bundle_id != bundle_id:
                return self._ack(status="rejected", rejected=[{
                    "index": None,
                    "error": "bundle_id is not allowed by federated token",
                }])
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._ack(status="rejected", rejected=[{"index": None, "error": "messages[] is required"}])

        settings = get_settings()
        tenant = str((socket_session or {}).get("tenant") or settings.TENANT or "").strip()
        project = str((socket_session or {}).get("project") or settings.PROJECT or "").strip()
        if not tenant or not project:
            return self._ack(status="rejected", rejected=[{"index": None, "error": "tenant/project scope is required"}])

        session = _session_from_socket_meta(socket_session)

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
                "[data_bus.publish] Failed to load handler contract tenant=%s project=%s bundle=%s",
                tenant,
                project,
                bundle_id,
                exc_info=True,
            )
            return self._ack(status="rejected", rejected=[{"index": None, "error": "bundle contract unavailable"}])

        props = await get_bundle_props(self._redis(), tenant=tenant, project=project, bundle_id=bundle_id)
        if not _bundle_enabled(props):
            return self._ack(status="rejected", rejected=[{"index": None, "error": "bundle is disabled"}])
        allowed_subjects = _allowed_subjects_from_socket_meta(socket_session)
        gateway_adapter = getattr(getattr(self.app, "state", None), "gateway_adapter", None)
        if gateway_adapter is None:
            logger.warning(
                "[data_bus.publish] rejected package because gateway_adapter is unavailable tenant=%s project=%s bundle=%s sid=%s",
                tenant,
                project,
                bundle_id,
                sid,
            )
            return self._ack(
                status="rejected",
                rejected=[{
                    "index": None,
                    "error": "gateway unavailable",
                    "error_type": "gateway_unavailable",
                    "status": 503,
                }],
            )
        gateway_result = await run_gateway_checks(
            gateway_adapter=gateway_adapter,
            session=session,
            context=_request_context_from_socket_meta(socket_session),
            endpoint="/socket.io/data_bus.publish",
        )
        if gateway_result.kind != "ok":
            mapped = map_gateway_error(gateway_result)
            return self._ack(
                status="rejected",
                rejected=[{
                    "index": None,
                    "error": mapped["message"],
                    "error_type": mapped["error_type"],
                    "status": mapped["status"],
                    "retry_after": mapped.get("retry_after"),
                }],
            )

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
                    allowed_subjects=allowed_subjects,
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
    ) -> tuple[Any, dict[str, DataBusHandlerSpec]]:
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
        allowed_subjects: set[str],
    ) -> DataBusMessage:
        if not isinstance(item, Mapping):
            raise ValueError("message must be an object")
        subject = normalize_subject(item.get("subject"))
        if allowed_subjects and subject not in allowed_subjects:
            raise ValueError(f"subject is not allowed by federated token: {subject}")

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
