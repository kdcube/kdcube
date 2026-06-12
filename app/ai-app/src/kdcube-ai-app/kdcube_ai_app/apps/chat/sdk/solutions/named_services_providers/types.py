# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterable, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.infra.auth_context import AuthContext


NAMED_SERVICE_REQUEST_SCHEMA = "kdcube.named_service.request.v1"
NAMED_SERVICE_RESPONSE_SCHEMA = "kdcube.named_service.response.v1"

TRANSPORT_LOCAL = "local"
TRANSPORT_API = "api"
TRANSPORT_MCP = "mcp"
TRANSPORT_DATA_BUS = "data_bus"
KNOWN_TRANSPORTS = frozenset({TRANSPORT_LOCAL, TRANSPORT_API, TRANSPORT_MCP, TRANSPORT_DATA_BUS})

PROVIDER_ABOUT = "provider.about"
PROVIDER_CAPABILITIES = "provider.capabilities"
PROVIDER_OPERATION = "provider.operation"
OBJECT_LIST = "object.list"
OBJECT_SEARCH = "object.search"
OBJECT_GET = "object.get"
OBJECT_HOST_FILE = "object.host_file"
OBJECT_SCHEMA = "object.schema"
OBJECT_UPSERT = "object.upsert"
OBJECT_DELETE = "object.delete"
OBJECT_ACTION = "object.action"
OBJECT_RESOLVE = "object.resolve"
RELATION_LIST = "relation.list"
RELATION_SEARCH = "relation.search"
EVENT_RESOLVE = "event.resolve"
EVENT_ACTION = "event.action"
BLOCK_PRODUCE = "block.produce"
BLOCK_RENDER = "block.render"

STANDARD_OPERATIONS = (
    PROVIDER_ABOUT,
    PROVIDER_CAPABILITIES,
    PROVIDER_OPERATION,
    OBJECT_LIST,
    OBJECT_SEARCH,
    OBJECT_GET,
    OBJECT_HOST_FILE,
    OBJECT_SCHEMA,
    OBJECT_UPSERT,
    OBJECT_DELETE,
    OBJECT_ACTION,
    OBJECT_RESOLVE,
    RELATION_LIST,
    RELATION_SEARCH,
    EVENT_RESOLVE,
    EVENT_ACTION,
    BLOCK_PRODUCE,
    BLOCK_RENDER,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"{field_name} must be a JSON object")


def ensure_json_list(value: Any, *, field_name: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"{field_name} must be a JSON list")


def ensure_json_serializable(value: Any, *, field_name: str) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must be JSON-serializable") from exc
    return value


def normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_required_string(value: Any, *, field_name: str) -> str:
    text = normalize_optional_string(value)
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def normalize_tuple(value: Sequence[str] | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def namespace_for_ref(value: Any) -> str:
    ref = str(value or "").strip()
    if ":" not in ref:
        return ""
    namespace, _ = ref.split(":", 1)
    return namespace.strip().lower()


def ref_matches_pattern(object_ref: str, pattern: str) -> bool:
    ref = str(object_ref or "").strip()
    pat = str(pattern or "").strip()
    if not ref or not pat:
        return False
    if "*" in pat or "?" in pat:
        return fnmatch.fnmatchcase(ref, pat)
    return ref == pat or ref.startswith(pat.rstrip("/") + "/")


@dataclass(frozen=True)
class NamedServiceError:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": str(self.code or "error"),
            "message": str(self.message or "Named service request failed"),
            "details": dict(self.details or {}),
        }


@dataclass(frozen=True)
class NamedServiceContext:
    tenant: str = ""
    project: str = ""
    auth_context: AuthContext | None = None
    principal_kind: str = ""
    principal_id: str | None = None
    user_id: str | None = None
    user_type: str = ""
    roles: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    bundle_id: str | None = None
    session_id: str | None = None
    conversation_id: str | None = None
    turn_id: str | None = None
    request_id: str | None = None
    stream_id: str | None = None
    actor: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.auth_context is None:
            return
        auth = self.auth_context
        object.__setattr__(self, "tenant", self.tenant or auth.tenant)
        object.__setattr__(self, "project", self.project or auth.project)
        object.__setattr__(self, "principal_kind", self.principal_kind or auth.principal_kind)
        object.__setattr__(self, "principal_id", self.principal_id or auth.principal_id)
        object.__setattr__(self, "user_id", self.user_id or auth.user_id)
        object.__setattr__(self, "user_type", self.user_type or auth.user_type)
        object.__setattr__(self, "roles", self.roles or auth.roles)
        object.__setattr__(self, "permissions", self.permissions or auth.permissions)
        object.__setattr__(self, "bundle_id", self.bundle_id or auth.bundle_id)
        object.__setattr__(self, "session_id", self.session_id or auth.session_id)
        object.__setattr__(self, "conversation_id", self.conversation_id or auth.conversation_id)
        object.__setattr__(self, "turn_id", self.turn_id or auth.turn_id)
        object.__setattr__(self, "request_id", self.request_id or auth.request_id)
        object.__setattr__(self, "stream_id", self.stream_id or auth.stream_id)
        object.__setattr__(self, "actor", self.actor or auth.to_actor())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "NamedServiceContext":
        data = dict(value or {})
        auth_data = data.get("auth")
        if auth_data is None:
            auth_data = data.get("auth_context")
        auth_context = AuthContext.from_mapping(auth_data) if isinstance(auth_data, Mapping) else None
        return cls(
            tenant=str(data.get("tenant") or ""),
            project=str(data.get("project") or ""),
            auth_context=auth_context,
            principal_kind=str(data.get("principal_kind") or ""),
            principal_id=normalize_optional_string(data.get("principal_id")),
            user_id=normalize_optional_string(data.get("user_id")),
            user_type=str(data.get("user_type") or ""),
            roles=normalize_tuple(data.get("roles") or ()),
            permissions=normalize_tuple(data.get("permissions") or ()),
            bundle_id=normalize_optional_string(data.get("bundle_id")),
            session_id=normalize_optional_string(data.get("session_id")),
            conversation_id=normalize_optional_string(data.get("conversation_id")),
            turn_id=normalize_optional_string(data.get("turn_id")),
            request_id=normalize_optional_string(data.get("request_id")),
            stream_id=normalize_optional_string(data.get("stream_id")),
            actor=ensure_json_object(data.get("actor"), field_name="actor"),
            metadata=ensure_json_object(data.get("metadata"), field_name="metadata"),
        )

    @classmethod
    def from_auth_context(
        cls,
        auth: AuthContext,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> "NamedServiceContext":
        return cls(auth_context=auth, metadata=dict(metadata or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant": self.tenant,
            "project": self.project,
            "auth_context": self.auth_context.to_dict() if self.auth_context else None,
            "principal_kind": self.principal_kind,
            "principal_id": self.principal_id,
            "user_id": self.user_id,
            "user_type": self.user_type,
            "roles": list(self.roles or ()),
            "permissions": list(self.permissions or ()),
            "bundle_id": self.bundle_id,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "request_id": self.request_id,
            "stream_id": self.stream_id,
            "actor": dict(self.actor or {}),
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class NamedServiceOperationSpec:
    operation: str
    transports: tuple[str, ...] = (TRANSPORT_LOCAL,)

    @classmethod
    def from_value(cls, operation: str, value: Any = None) -> "NamedServiceOperationSpec":
        if isinstance(value, NamedServiceOperationSpec):
            return value
        if value is None:
            return cls(operation=operation)
        if isinstance(value, Mapping):
            transports = value.get("transports") or (TRANSPORT_LOCAL,)
            return cls(operation=operation, transports=normalize_tuple(transports))
        if isinstance(value, (list, tuple)):
            return cls(operation=operation, transports=normalize_tuple(value))
        raise TypeError(f"operation spec for {operation!r} must be a mapping or transport list")

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "transports": list(self.transports or ()),
        }


def build_default_operations(
    transports: Sequence[str] = (TRANSPORT_LOCAL,),
    *,
    include_mutations: bool = True,
) -> dict[str, NamedServiceOperationSpec]:
    read_ops = (
        PROVIDER_ABOUT,
        PROVIDER_CAPABILITIES,
        OBJECT_LIST,
        OBJECT_SEARCH,
        OBJECT_GET,
        OBJECT_SCHEMA,
        OBJECT_ACTION,
        OBJECT_RESOLVE,
        RELATION_LIST,
        RELATION_SEARCH,
        EVENT_RESOLVE,
        EVENT_ACTION,
        BLOCK_PRODUCE,
        BLOCK_RENDER,
    )
    operations = {op: NamedServiceOperationSpec(op, normalize_tuple(transports)) for op in read_ops}
    if include_mutations:
        for op in (PROVIDER_OPERATION, OBJECT_HOST_FILE, OBJECT_UPSERT, OBJECT_DELETE):
            operations[op] = NamedServiceOperationSpec(op, normalize_tuple(transports))
    return operations


@dataclass(frozen=True)
class NamedServiceProviderSpec:
    provider_id: str
    bundle_id: str | None = None
    namespace: str | None = None
    namespaces: tuple[str, ...] = ()
    refs: tuple[str, ...] = ()
    object_kinds: tuple[str, ...] = ()
    operations: dict[str, NamedServiceOperationSpec] = field(default_factory=dict)
    label: str | None = None
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        provider_id = normalize_required_string(self.provider_id, field_name="provider_id")
        object.__setattr__(self, "provider_id", provider_id)
        namespaces = tuple(
            sorted({
                *(ns.strip().lower() for ns in self.namespaces if str(ns).strip()),
                *([self.namespace.strip().lower()] if self.namespace and self.namespace.strip() else []),
                *(namespace_for_ref(ref) for ref in self.refs if namespace_for_ref(ref)),
            })
        )
        object.__setattr__(self, "namespaces", namespaces)
        object.__setattr__(self, "namespace", namespaces[0] if namespaces else None)
        operations = {
            operation: NamedServiceOperationSpec.from_value(operation, spec)
            for operation, spec in dict(self.operations or {}).items()
        }
        object.__setattr__(self, "operations", operations)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NamedServiceProviderSpec":
        data = dict(value or {})
        operations = {
            str(operation): NamedServiceOperationSpec.from_value(str(operation), spec)
            for operation, spec in dict(data.get("operations") or {}).items()
        }
        return cls(
            provider_id=str(data.get("provider_id") or data.get("id") or ""),
            bundle_id=normalize_optional_string(data.get("bundle_id")),
            namespace=normalize_optional_string(data.get("namespace")),
            namespaces=normalize_tuple(data.get("namespaces") or ()),
            refs=normalize_tuple(data.get("refs") or ()),
            object_kinds=normalize_tuple(data.get("object_kinds") or ()),
            operations=operations,
            label=normalize_optional_string(data.get("label")),
            description=normalize_optional_string(data.get("description")),
            metadata=ensure_json_object(data.get("metadata"), field_name="metadata"),
        )

    def supports_operation(self, operation: str) -> bool:
        if not self.operations:
            return operation in STANDARD_OPERATIONS
        return operation in self.operations

    def supports_transport(self, operation: str, transport: str) -> bool:
        if not self.supports_operation(operation):
            return False
        spec = self.operations.get(operation)
        if spec is None:
            return transport == TRANSPORT_LOCAL
        return transport in spec.transports

    def matches_ref(self, object_ref: str) -> bool:
        ref = str(object_ref or "").strip()
        if not ref:
            return False
        if self.refs:
            return any(ref_matches_pattern(ref, pattern) for pattern in self.refs)
        namespace = namespace_for_ref(ref)
        return bool(namespace and namespace in set(self.namespaces or ()))

    def match_score(self, object_ref: str) -> int:
        if not self.matches_ref(object_ref):
            return -1
        matched_patterns = [
            len(str(pattern or "").replace("*", "").replace("?", ""))
            for pattern in self.refs
            if ref_matches_pattern(object_ref, pattern)
        ]
        if matched_patterns:
            return max(matched_patterns)
        return len(self.namespace or "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "bundle_id": self.bundle_id,
            "namespace": self.namespace,
            "namespaces": list(self.namespaces or ()),
            "refs": list(self.refs or ()),
            "object_kinds": list(self.object_kinds or ()),
            "operations": {op: spec.to_dict() for op, spec in (self.operations or {}).items()},
            "label": self.label,
            "description": self.description,
            "metadata": dict(self.metadata or {}),
        }


@dataclass(frozen=True)
class NamedServiceRequest:
    operation: str
    provider: str | None = None
    namespace: str | None = None
    object_ref: str | None = None
    object_id: str | None = None
    collection: str | None = None
    cursor: str | None = None
    limit: int | None = None
    query: str | None = None
    search_mode: str | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    sort: list[Any] = field(default_factory=list)
    include: list[Any] = field(default_factory=list)
    action: str | None = None
    object: dict[str, Any] = field(default_factory=dict)
    base_revision: str | None = None
    idempotency_key: str | None = None
    response_mode: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    schema: str = NAMED_SERVICE_REQUEST_SCHEMA

    @classmethod
    def coerce(cls, value: Any) -> "NamedServiceRequest":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls.from_dict(value)
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return cls.from_dict(to_dict())
        data = getattr(value, "__dict__", None)
        if isinstance(data, Mapping):
            return cls.from_dict(data)
        raise TypeError(f"Cannot coerce {type(value).__name__} to NamedServiceRequest")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NamedServiceRequest":
        data = dict(value or {})
        operation = normalize_required_string(data.get("operation"), field_name="operation")
        limit = data.get("limit")
        return cls(
            schema=str(data.get("schema") or NAMED_SERVICE_REQUEST_SCHEMA),
            operation=operation,
            provider=normalize_optional_string(data.get("provider") or data.get("provider_id")),
            namespace=normalize_optional_string(data.get("namespace")),
            object_ref=normalize_optional_string(data.get("object_ref")),
            object_id=normalize_optional_string(data.get("object_id")),
            collection=normalize_optional_string(data.get("collection")),
            cursor=normalize_optional_string(data.get("cursor")),
            limit=int(limit) if limit is not None else None,
            query=normalize_optional_string(data.get("query")),
            search_mode=normalize_optional_string(data.get("search_mode")),
            filters=ensure_json_object(data.get("filters"), field_name="filters"),
            sort=ensure_json_list(data.get("sort"), field_name="sort"),
            include=ensure_json_list(data.get("include"), field_name="include"),
            action=normalize_optional_string(data.get("action")),
            object=ensure_json_object(data.get("object"), field_name="object"),
            base_revision=normalize_optional_string(data.get("base_revision")),
            idempotency_key=normalize_optional_string(data.get("idempotency_key")),
            response_mode=normalize_optional_string(data.get("response_mode")),
            context=ensure_json_object(data.get("context"), field_name="context"),
            payload=ensure_json_object(data.get("payload"), field_name="payload"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "operation": self.operation,
            "provider": self.provider,
            "namespace": self.namespace,
            "object_ref": self.object_ref,
            "object_id": self.object_id,
            "collection": self.collection,
            "cursor": self.cursor,
            "limit": self.limit,
            "query": self.query,
            "search_mode": self.search_mode,
            "filters": dict(self.filters or {}),
            "sort": list(self.sort or []),
            "include": list(self.include or []),
            "action": self.action,
            "object": dict(self.object or {}),
            "base_revision": self.base_revision,
            "idempotency_key": self.idempotency_key,
            "response_mode": self.response_mode,
            "context": dict(self.context or {}),
            "payload": dict(self.payload or {}),
        }


@dataclass(frozen=True)
class NamedServiceResponse:
    ok: bool
    ret: dict[str, Any] = field(default_factory=dict)
    error: NamedServiceError | None = None
    status: int = 200

    @classmethod
    def coerce(cls, value: Any) -> "NamedServiceResponse":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls.from_dict(value)
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return cls.from_dict(to_dict())
        raise TypeError(f"Cannot coerce {type(value).__name__} to NamedServiceResponse")

    @classmethod
    def ok_response(
        cls,
        *,
        provider: Mapping[str, Any] | None = None,
        namespace: str | None = None,
        object_ref: str | None = None,
        object: Mapping[str, Any] | None = None,
        items: Sequence[Any] | None = None,
        attrs: Mapping[str, Any] | None = None,
        extra: Mapping[str, Any] | None = None,
        ui_event: Mapping[str, Any] | None = None,
        next_cursor: str | None = None,
        revision: str | None = None,
        capabilities: Mapping[str, Any] | None = None,
        relations: Sequence[Any] | None = None,
        warnings: Sequence[Any] | None = None,
        ret: Mapping[str, Any] | None = None,
    ) -> "NamedServiceResponse":
        payload = ensure_json_object(ret, field_name="ret")
        if object is not None:
            payload["object"] = ensure_json_object(object, field_name="ret.object")
        if items is not None:
            payload["items"] = ensure_json_list(items, field_name="ret.items")
        attrs_payload = ensure_json_object(payload.get("attrs"), field_name="ret.attrs")
        attrs_payload.update(ensure_json_object(attrs, field_name="attrs"))
        if provider:
            attrs_payload["provider"] = dict(provider)
        if namespace is not None:
            attrs_payload["namespace"] = namespace
        if object_ref is not None:
            attrs_payload["object_ref"] = object_ref
        if next_cursor is not None:
            attrs_payload["next_cursor"] = next_cursor
        if revision is not None:
            attrs_payload["revision"] = revision
        if capabilities is not None:
            attrs_payload["capabilities"] = ensure_json_object(capabilities, field_name="capabilities")
        if relations is not None:
            attrs_payload["relations"] = ensure_json_list(relations, field_name="relations")
        if warnings is not None:
            attrs_payload["warnings"] = ensure_json_list(warnings, field_name="warnings")
        if attrs_payload:
            payload["attrs"] = attrs_payload
        if extra is not None:
            payload["extra"] = ensure_json_object(extra, field_name="ret.extra")
        if ui_event is not None:
            payload["ui_event"] = ensure_json_object(ui_event, field_name="ret.ui_event")
        return cls(ok=True, ret=payload, error=None, status=200)

    @classmethod
    def error_response(
        cls,
        *,
        code: str,
        message: str,
        status: int = 400,
        details: Mapping[str, Any] | None = None,
        provider: Mapping[str, Any] | None = None,
        namespace: str | None = None,
        object_ref: str | None = None,
    ) -> "NamedServiceResponse":
        error_details = dict(details or {})
        error_details.setdefault("status", status)
        return cls(
            ok=False,
            status=status,
            ret={
                "attrs": {
                    key: value
                    for key, value in {
                        "provider": dict(provider or {}),
                        "namespace": namespace,
                        "object_ref": object_ref,
                    }.items()
                    if value not in (None, "", {})
                }
            },
            error=NamedServiceError(code=code, message=message, details=error_details),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "NamedServiceResponse":
        data = dict(value or {})
        ret = ensure_json_object(data.get("ret"), field_name="ret")
        error_payload = data.get("error")
        error = None
        if isinstance(error_payload, Mapping):
            error = NamedServiceError(
                code=str(error_payload.get("code") or "error"),
                message=str(error_payload.get("message") or "Named service request failed"),
                details=ensure_json_object(error_payload.get("details"), field_name="error.details"),
            )
        return cls(
            ok=bool(data.get("ok")),
            status=int(data.get("status") or (200 if data.get("ok") else 400)),
            ret=ret,
            error=error,
        )

    @property
    def attrs(self) -> dict[str, Any]:
        return ensure_json_object((self.ret or {}).get("attrs"), field_name="ret.attrs")

    @property
    def extra(self) -> dict[str, Any]:
        return ensure_json_object((self.ret or {}).get("extra"), field_name="ret.extra")

    @property
    def object(self) -> dict[str, Any]:
        return ensure_json_object((self.ret or {}).get("object"), field_name="ret.object")

    @property
    def items(self) -> list[Any]:
        return ensure_json_list((self.ret or {}).get("items"), field_name="ret.items")

    @property
    def provider(self) -> dict[str, Any]:
        return ensure_json_object(self.attrs.get("provider"), field_name="ret.attrs.provider")

    @property
    def namespace(self) -> str | None:
        return normalize_optional_string(self.attrs.get("namespace"))

    @property
    def object_ref(self) -> str | None:
        return normalize_optional_string(self.attrs.get("object_ref"))

    @property
    def next_cursor(self) -> str | None:
        return normalize_optional_string(self.attrs.get("next_cursor"))

    @property
    def revision(self) -> str | None:
        return normalize_optional_string(self.attrs.get("revision"))

    @property
    def capabilities(self) -> dict[str, Any]:
        return ensure_json_object(self.attrs.get("capabilities"), field_name="ret.attrs.capabilities")

    @property
    def relations(self) -> list[Any]:
        return ensure_json_list(self.attrs.get("relations"), field_name="ret.attrs.relations")

    @property
    def warnings(self) -> list[Any]:
        return ensure_json_list(self.attrs.get("warnings"), field_name="ret.attrs.warnings")

    @property
    def ui_event(self) -> dict[str, Any] | None:
        if "ui_event" not in (self.ret or {}):
            return None
        return ensure_json_object((self.ret or {}).get("ui_event"), field_name="ret.ui_event")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "ret": dict(self.ret or {}),
            "error": self.error.to_dict() if self.error else None,
        }


@dataclass(frozen=True)
class NamedServiceStreamResult:
    """Structured named-service response plus streamed object bytes.

    The response carries the same JSON contract returned by normal provider
    calls. Bytes stay on the async stream, so large files do not need base64 or
    a fully materialized in-memory payload.
    """

    response: NamedServiceResponse
    chunks: AsyncIterable[bytes]
    filename: str | None = None
    media_type: str | None = "application/octet-stream"
    headers: dict[str, str] = field(default_factory=dict)
    status_code: int = 200
