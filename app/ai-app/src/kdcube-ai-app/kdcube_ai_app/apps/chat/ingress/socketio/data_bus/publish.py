# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Mapping

from kdcube_ai_app.apps.chat.sdk.config import get_settings
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.stream import RedisDataBusStream
from kdcube_ai_app.apps.chat.sdk.runtime.data_bus.types import (
    DATA_BUS_IDEMPOTENCY_REQUIRED,
    DATA_BUS_INGRESS_SCHEMA,
    DATA_BUS_ORDERING_SERIAL_PER_PARTITION,
    DATA_BUS_PARTITION_OBJECT_REF,
    DataBusHandlerSpec,
    DataBusMessage,
    ensure_json_object,
    ensure_json_serializable,
    normalize_optional_string,
    normalize_subject,
    utc_now_iso,
)
from kdcube_ai_app.auth.sessions import UserSession, UserType
from kdcube_ai_app.infra.plugin.bundle_loader import (
    BundleSpec,
    apply_bundle_overrides,
    load_bundle_manifest,
)
from kdcube_ai_app.infra.plugin.bundle_store import get_bundle_props, load_registry

logger = logging.getLogger("kdcube.data_bus.socketio")

DATA_BUS_PACKAGE_MAX_BYTES = max(
    4096,
    int(os.getenv("DATA_BUS_PACKAGE_MAX_BYTES", str(1024 * 1024)) or str(1024 * 1024)),
)
_DISABLED_VALUES = frozenset({"false", "disable", "disabled", "off", "0"})
_USER_TYPE_VISIBILITY_ORDER = {
    "anonymous": 0,
    "registered": 1,
    "paid": 2,
    "privileged": 3,
}


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


def _user_types_visible(required_user_types: tuple[str, ...] | list[str] | None, session: UserSession) -> bool:
    user_types = tuple(
        str(user_type or "").strip().lower()
        for user_type in (required_user_types or ())
        if str(user_type or "").strip()
    )
    if not user_types:
        return True
    current = _session_user_type(session)
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


def _user_raw_roles(session: UserSession) -> set[str]:
    return {
        role for role in (session.roles or [])
        if isinstance(role, str) and role.startswith("kdcube:role:")
    }


def _raw_roles_visible(required_roles: tuple[str, ...] | list[str] | None, session: UserSession) -> bool:
    roles = tuple(str(role or "").strip() for role in (required_roles or ()) if str(role or "").strip())
    if not roles:
        return True
    return bool(_user_raw_roles(session) & set(roles))


def _endpoint_visible(
    required_user_types: tuple[str, ...] | list[str] | None,
    required_roles: tuple[str, ...] | list[str] | None,
    session: UserSession,
) -> bool:
    return _user_types_visible(required_user_types, session) and _raw_roles_visible(required_roles, session)


def _bundle_allowed_for_session(manifest: Any, session: UserSession, props: Mapping[str, Any] | None) -> bool:
    effective = apply_bundle_overrides(manifest, dict(props or {}))
    if not effective.allowed_roles:
        return True
    return bool(_user_raw_roles(session) & set(effective.allowed_roles))


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


class DataBusSocketIOIngress:
    def __init__(self, *, app: Any, redis: Any | None = None) -> None:
        self.app = app
        self.redis = redis

    def _redis(self) -> Any:
        redis = self.redis or getattr(getattr(self.app, "state", None), "redis_async", None)
        if redis is None:
            raise RuntimeError("redis_async is not initialized on app.state")
        return redis

    async def handle_publish(self, *, sid: str, socket_session: Mapping[str, Any] | None, data: Any) -> dict[str, Any]:
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
            manifest, handler_by_subject = await self._load_handler_contract(
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
        if not _bundle_allowed_for_session(manifest, session, props):
            return self._ack(status="rejected", rejected=[{"index": None, "error": "bundle is not visible to this user"}])

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
                    handler_by_subject=handler_by_subject,
                )
                result = await stream.publish(message)
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

    async def _load_handler_contract(
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
        spec = BundleSpec(
            path=entry.path,
            module=entry.module,
            singleton=bool(getattr(entry, "singleton", False)),
        )
        manifest = load_bundle_manifest(spec, bundle_id=bundle_id)
        handler_by_subject = {handler.subject: handler for handler in manifest.data_bus_handlers}
        if not handler_by_subject:
            raise ValueError("bundle has no Data Bus handlers")
        return manifest, handler_by_subject

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
        handler_by_subject: Mapping[str, DataBusHandlerSpec],
    ) -> DataBusMessage:
        if not isinstance(item, Mapping):
            raise ValueError("message must be an object")
        subject = normalize_subject(item.get("subject"))
        handler = handler_by_subject.get(subject)
        if handler is None:
            raise ValueError(f"subject is not handled by bundle: {subject}")
        if not _endpoint_visible(handler.user_types, handler.roles, session):
            raise ValueError(f"subject is not visible to this user: {subject}")

        object_ref = normalize_optional_string(item.get("object_ref"))
        if (
            handler.partition_by == DATA_BUS_PARTITION_OBJECT_REF
            or handler.ordering == DATA_BUS_ORDERING_SERIAL_PER_PARTITION
        ) and not object_ref:
            raise ValueError(f"subject requires object_ref: {subject}")

        idempotency_key = normalize_optional_string(item.get("idempotency_key"))
        if handler.idempotency == DATA_BUS_IDEMPOTENCY_REQUIRED and not idempotency_key:
            raise ValueError(f"subject requires idempotency_key: {subject}")

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

        return DataBusMessage(
            message_id=str(item.get("message_id") or f"dbmsg_{uuid.uuid4().hex}"),
            tenant=tenant,
            project=project,
            bundle_id=bundle_id,
            subject=subject,
            object_ref=object_ref,
            idempotency_key=idempotency_key,
            actor=_actor_from_session(session),
            payload=payload,
            reply={
                "transport": "socketio",
                "session_id": session.session_id,
                "socket_id": sid,
            },
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
