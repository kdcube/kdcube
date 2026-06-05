# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import dataclasses
import inspect
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional


DATA_BUS_INGRESS_SCHEMA = "kdcube.data_bus.ingress.v1"
DATA_BUS_MESSAGE_SCHEMA = "kdcube.data_bus.message.v1"
DATA_BUS_RESULT_SCHEMA = "kdcube.data_bus.result.v1"

DATA_BUS_ORDERING_PARALLEL = "parallel"
DATA_BUS_ORDERING_SERIAL_PER_PARTITION = "serial_per_partition"
DATA_BUS_ORDERINGS = frozenset({
    DATA_BUS_ORDERING_PARALLEL,
    DATA_BUS_ORDERING_SERIAL_PER_PARTITION,
})

DATA_BUS_PARTITION_NONE = "none"
DATA_BUS_PARTITION_OBJECT_REF = "object_ref"
DATA_BUS_PARTITIONS = frozenset({
    DATA_BUS_PARTITION_NONE,
    DATA_BUS_PARTITION_OBJECT_REF,
})

DATA_BUS_IDEMPOTENCY_OPTIONAL = "optional"
DATA_BUS_IDEMPOTENCY_REQUIRED = "required"
DATA_BUS_IDEMPOTENCY = frozenset({
    DATA_BUS_IDEMPOTENCY_OPTIONAL,
    DATA_BUS_IDEMPOTENCY_REQUIRED,
})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def now_ms() -> int:
    return int(time.time() * 1000)


def ensure_json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"{field_name} must be a JSON object")


def ensure_json_serializable(value: Any, *, field_name: str) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must be JSON-serializable") from exc
    return value


def normalize_subject(value: Any) -> str:
    subject = str(value or "").strip()
    if not subject:
        raise ValueError("Data Bus message subject is required")
    return subject


def normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def data_bus_stream_key(*, tenant: str, project: str, bundle_id: str, kind: str = "messages") -> str:
    return f"kdcube:data-bus:{tenant}:{project}:{bundle_id}:{kind}"


def data_bus_group_name(*, tenant: str, project: str, bundle_id: str) -> str:
    return f"kdcube:data-bus:{tenant}:{project}:{bundle_id}:handlers"


@dataclass(frozen=True)
class DataBusHandlerSpec:
    method_name: str
    subject: str
    partition_by: str = DATA_BUS_PARTITION_NONE
    ordering: str = DATA_BUS_ORDERING_PARALLEL
    idempotency: str = DATA_BUS_IDEMPOTENCY_OPTIONAL
    user_types: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class DataBusMessage:
    message_id: str
    tenant: str
    project: str
    bundle_id: str
    subject: str
    object_ref: str | None = None
    idempotency_key: str | None = None
    actor: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    reply: dict[str, Any] | None = None
    trace: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    schema: str = DATA_BUS_MESSAGE_SCHEMA

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DataBusMessage":
        data = dict(value or {})
        return cls(
            schema=str(data.get("schema") or DATA_BUS_MESSAGE_SCHEMA),
            message_id=str(data.get("message_id") or f"dbmsg_{uuid.uuid4().hex}"),
            tenant=str(data.get("tenant") or ""),
            project=str(data.get("project") or ""),
            bundle_id=str(data.get("bundle_id") or ""),
            subject=normalize_subject(data.get("subject")),
            object_ref=normalize_optional_string(data.get("object_ref")),
            idempotency_key=normalize_optional_string(data.get("idempotency_key")),
            actor=ensure_json_object(data.get("actor"), field_name="actor"),
            payload=ensure_json_object(data.get("payload"), field_name="payload"),
            reply=(
                ensure_json_object(data.get("reply"), field_name="reply")
                if data.get("reply") is not None
                else None
            ),
            trace=ensure_json_object(data.get("trace"), field_name="trace"),
            created_at=str(data.get("created_at") or utc_now_iso()),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "message_id": self.message_id,
            "tenant": self.tenant,
            "project": self.project,
            "bundle_id": self.bundle_id,
            "subject": self.subject,
            "object_ref": self.object_ref,
            "idempotency_key": self.idempotency_key,
            "actor": dict(self.actor or {}),
            "payload": dict(self.payload or {}),
            "reply": dict(self.reply or {}) if self.reply is not None else None,
            "trace": dict(self.trace or {}),
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, value: str | bytes) -> "DataBusMessage":
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        decoded = json.loads(value or "{}")
        if not isinstance(decoded, Mapping):
            raise ValueError("Data Bus stream json field must decode to an object")
        return cls.from_dict(decoded)


@dataclass(frozen=True)
class DataBusResult:
    message_id: str
    status: str = "ok"
    subject: str = ""
    object_ref: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    processed_at: str = field(default_factory=utc_now_iso)
    schema: str = DATA_BUS_RESULT_SCHEMA

    @classmethod
    def ok(cls, message: DataBusMessage, data: Mapping[str, Any] | None = None) -> "DataBusResult":
        return cls(
            message_id=message.message_id,
            status="ok",
            subject=message.subject,
            object_ref=message.object_ref,
            data=dict(data or {}),
        )

    @classmethod
    def conflict(cls, message: DataBusMessage, data: Mapping[str, Any] | None = None) -> "DataBusResult":
        return cls(
            message_id=message.message_id,
            status="conflict",
            subject=message.subject,
            object_ref=message.object_ref,
            data=dict(data or {}),
        )

    @classmethod
    def error_result(
        cls,
        message: DataBusMessage,
        *,
        code: str,
        message_text: str,
        details: Mapping[str, Any] | None = None,
        status: str = "error",
    ) -> "DataBusResult":
        return cls(
            message_id=message.message_id,
            status=status,
            subject=message.subject,
            object_ref=message.object_ref,
            error={
                "code": str(code or "error"),
                "message": str(message_text or "Data Bus handler failed"),
                "details": dict(details or {}),
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "message_id": self.message_id,
            "status": self.status,
            "subject": self.subject,
            "object_ref": self.object_ref,
            "data": dict(self.data or {}),
            "error": dict(self.error or {}) if self.error is not None else None,
            "processed_at": self.processed_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


class DataBusReply:
    def __init__(
        self,
        *,
        message: DataBusMessage,
        comm: Any | None = None,
    ) -> None:
        self.message = message
        self.comm = comm
        self.sent_count = 0

    async def accepted(self, data: Mapping[str, Any] | None = None) -> None:
        await self.event("kdcube.data_bus.accepted", data=dict(data or {}), status="running")

    async def ok(self, data: Mapping[str, Any] | None = None) -> None:
        await self.event("kdcube.data_bus.result", data=dict(data or {}), status="completed")

    async def conflict(self, data: Mapping[str, Any] | None = None) -> None:
        await self.event("kdcube.data_bus.conflict", data=dict(data or {}), status="completed")

    async def error(
        self,
        code: str,
        message: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        await self.event(
            "kdcube.data_bus.error",
            data={
                "code": str(code or "error"),
                "message": str(message or "Data Bus handler failed"),
                "details": dict(details or {}),
            },
            status="error",
        )

    async def event(
        self,
        type: str,
        data: Mapping[str, Any] | None = None,
        *,
        status: str = "running",
        title: str | None = None,
        step: str = "data_bus",
        broadcast: bool = False,
    ) -> None:
        if self.comm is None:
            return
        payload = {
            "message_id": self.message.message_id,
            "subject": self.message.subject,
            "object_ref": self.message.object_ref,
            "data": dict(data or {}),
        }
        result = self.comm.service_event(
            type=str(type or "kdcube.data_bus.event"),
            step=step,
            status=status,
            title=title,
            data=payload,
            agent="data_bus",
            broadcast=broadcast,
        )
        if inspect.isawaitable(result):
            await result
        self.sent_count += 1


@dataclass(frozen=True)
class DataBusContext:
    tenant: str
    project: str
    bundle_id: str
    actor: dict[str, Any]
    bundle: Any
    comm: Any | None
    reply: DataBusReply
    stream_id: str | None = None
    consumer_name: str | None = None
    handler: DataBusHandlerSpec | None = None


def coerce_data_bus_result(result: Any, message: DataBusMessage) -> DataBusResult:
    if isinstance(result, DataBusResult):
        return result
    if result is None:
        return DataBusResult.ok(message)
    if dataclasses.is_dataclass(result) and hasattr(result, "to_dict"):
        result = result.to_dict()
    if isinstance(result, Mapping):
        status = str(result.get("status") or "ok")
        data = result.get("data")
        error = result.get("error")
        return DataBusResult(
            message_id=str(result.get("message_id") or message.message_id),
            status=status,
            subject=str(result.get("subject") or message.subject),
            object_ref=normalize_optional_string(result.get("object_ref")) or message.object_ref,
            data=dict(data or {}) if isinstance(data, Mapping) else {},
            error=dict(error or {}) if isinstance(error, Mapping) else None,
            processed_at=str(result.get("processed_at") or utc_now_iso()),
        )
    return DataBusResult.ok(message, {"result": ensure_json_serializable(result, field_name="handler result")})


def validate_handler_spec_values(
    *,
    subject: str,
    partition_by: str,
    ordering: str,
    idempotency: str,
) -> tuple[str, str, str, str]:
    resolved_subject = normalize_subject(subject)
    resolved_partition_by = str(partition_by or DATA_BUS_PARTITION_NONE).strip()
    if resolved_partition_by not in DATA_BUS_PARTITIONS:
        raise ValueError(f"Unsupported Data Bus partition_by: {partition_by!r}")
    resolved_ordering = str(ordering or DATA_BUS_ORDERING_PARALLEL).strip()
    if resolved_ordering not in DATA_BUS_ORDERINGS:
        raise ValueError(f"Unsupported Data Bus ordering: {ordering!r}")
    resolved_idempotency = str(idempotency or DATA_BUS_IDEMPOTENCY_OPTIONAL).strip()
    if resolved_idempotency not in DATA_BUS_IDEMPOTENCY:
        raise ValueError(f"Unsupported Data Bus idempotency: {idempotency!r}")
    if (
        resolved_ordering == DATA_BUS_ORDERING_SERIAL_PER_PARTITION
        and resolved_partition_by == DATA_BUS_PARTITION_NONE
    ):
        raise ValueError("serial_per_partition requires a partition_by value")
    return resolved_subject, resolved_partition_by, resolved_ordering, resolved_idempotency


def tuple_str(values: Any) -> tuple[str, ...]:
    return tuple(str(item).strip() for item in (values or ()) if str(item).strip())


HandlerCallable = Callable[[DataBusContext, DataBusMessage], Any]
