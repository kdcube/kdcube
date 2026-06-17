# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Awaitable, Callable, Mapping, Sequence

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceProvider,
    NamedServiceProviderSpec,
    NamedServiceRequest,
    NamedServiceResponse,
    NamedServiceSearchScope,
    NamedServiceStreamResult,
    TRANSPORT_API,
    TRANSPORT_LOCAL,
    build_default_operations,
    named_service_provider,
)

from .events.resolver import (
    MEMORY_OBJECT_NAMESPACE,
    MEMORY_RESOLVER_NAME,
    memory_id_from_ref,
    memory_ref,
    memory_record_to_object_payload,
    memory_ref_capabilities,
    resolve_memory_ref_action,
)
from .models import (
    MemoryEvent,
    MemoryRecord,
    MemoryScope,
    MemorySearchRequest,
    MemorySearchResult,
    MemorySignal,
    is_user_visible,
    normalize_scope_filter,
    normalize_terms,
)
from .scoring import DEFAULT_MEMORY_SCORING
from .store import UserMemoryStore


PROVIDER_ID = MEMORY_RESOLVER_NAME
NAMESPACE = MEMORY_OBJECT_NAMESPACE
RECORD_SCOPE_NAMESPACE = f"{NAMESPACE}:record"
OBJECT_KIND = "memory.record"
MEMORY_MIME = "application/vnd.kdcube.memory.record+json;version=1"
NAMED_SERVICE_OBJECT_SCHEMA = "kdcube.named_service.object.v1"
LOGGER = logging.getLogger("kdcube.sdk.memory.named_service")
KNOWN_MEMORY_KINDS: tuple[str, ...] = (
    "fact",
    "preference",
    "decision",
    "constraint",
    "communication_style",
    "anchor",
    "spec",
    "milestone",
    "state",
)

MemoryStoreFactory = Callable[[NamedServiceContext], UserMemoryStore]
MemoryScopeFactory = Callable[[NamedServiceContext], MemoryScope]
EmbeddingFactory = Callable[[str], Sequence[float] | Awaitable[Sequence[float] | None] | None]


MEMORY_SEARCH_FILTERS: dict[str, Any] = {
    "scope_filter": {
        "type": "string",
        "enum": ["current_bundle", "all_user_memories", "global_only", "current_bundle_or_global"],
        "description": "Which user-memory bundle scope to search.",
    },
    "mode": {
        "type": "string",
        "enum": ["hybrid", "recent", "recent_created", "important", "confirmed", "hotset"],
        "description": "Memory record search mode. Event-history search is not exposed as a named-service object scope.",
    },
    "labels": {"type": "string|array", "description": "Comma-separated or array memory labels."},
    "keywords": {"type": "string|array", "description": "Comma-separated or array memory keywords."},
    "kind": {
        "type": "string",
        "description": "Open-vocabulary memory kind filter. Known values include fact, preference, decision, constraint, communication_style, anchor, spec, milestone, and state.",
        "x-kdcube-known-values": list(KNOWN_MEMORY_KINDS),
    },
    "status": {"type": "string", "description": "Memory status filter; defaults to active."},
    "visible_to_user": {"type": "boolean", "description": "Restrict to user-visible memory records when true."},
    "min_relevance_score": {"type": "number", "description": "Optional minimum lexical/semantic relevance score."},
    "half_life_days": {"type": "number", "exclusiveMinimum": 0.0, "description": "Days until freshness halves; legacy top-level alias for factor_weights.half_life_days."},
    "factor_weights": {
        "type": "object",
        "description": "Per-call memory scoring tuning; unset keys use defaults. Memory uses one additive weighted sum, not RRF.",
        "properties": {
            "semantic_weight": {"type": "number", "minimum": 0.0, "default": DEFAULT_MEMORY_SCORING.semantic_weight, "description": "Embedding/cosine factor weight; 0 turns semantic off (skips the query embed, ranks on text+labels+salience)."},
            "text_weight": {"type": "number", "minimum": 0.0, "default": DEFAULT_MEMORY_SCORING.text_weight, "description": "Token-overlap/text-rank factor weight."},
            "label_weight": {"type": "number", "minimum": 0.0, "default": DEFAULT_MEMORY_SCORING.label_weight, "description": "Label/keyword match factor weight."},
            "salience_weight": {"type": "number", "minimum": 0.0, "default": DEFAULT_MEMORY_SCORING.salience_weight, "description": "Salience factor weight."},
            "importance_weight": {"type": "number", "minimum": 0.0, "default": DEFAULT_MEMORY_SCORING.importance_weight, "description": "Importance factor weight."},
            "confidence_weight": {"type": "number", "minimum": 0.0, "default": DEFAULT_MEMORY_SCORING.confidence_weight, "description": "Confidence factor weight."},
            "freshness_weight": {"type": "number", "minimum": 0.0, "default": DEFAULT_MEMORY_SCORING.freshness_weight, "description": "Recency factor weight."},
            "confirmation_weight": {"type": "number", "minimum": 0.0, "default": DEFAULT_MEMORY_SCORING.confirmation_weight, "description": "Confirmation-rate factor weight."},
            "half_life_days": {"type": "number", "exclusiveMinimum": 0.0, "default": DEFAULT_MEMORY_SCORING.half_life_days, "description": "Days until freshness halves."},
            "min_relevance_score": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": DEFAULT_MEMORY_SCORING.min_relevance_score, "description": "Relevance floor (0-1); raise to drop weak hits."},
        },
    },
}

MEMORY_SEARCH_SCOPES: tuple[NamedServiceSearchScope, ...] = (
    NamedServiceSearchScope(
        namespace=NAMESPACE,
        label="all memory objects",
        object_kind=OBJECT_KIND,
        description="Search durable user memory records. Currently returns memory.record objects.",
        filters_schema=MEMORY_SEARCH_FILTERS,
    ),
    NamedServiceSearchScope(
        namespace=RECORD_SCOPE_NAMESPACE,
        label="memory records",
        object_kind=OBJECT_KIND,
        description="Search durable user memory records. Empty query can return recent records depending on mode.",
        filters_schema=MEMORY_SEARCH_FILTERS,
    ),
)

MEMORY_RECORD_SCHEMA: dict[str, Any] = {
    "object_kind": OBJECT_KIND,
    "namespace": NAMESPACE,
    "ref_pattern": "mem:record:<memory_id>",
    "title": "User memory record",
    "description": "Durable user memory note with evidence-backed status, scores, labels, keywords, visibility, and revision.",
    "fields": {
        "memory": {"type": "string", "required_on_create": True, "description": "Concise saved memory text."},
        "context": {"type": "string", "description": "Why this memory exists or when it applies."},
        "kind": {
            "type": "string",
            "default": "fact",
            "description": "Open vocabulary memory kind. Known values include: fact, preference, decision, constraint, communication_style, anchor, spec, milestone, state. Providers/clients may use other normalized lowercase values when needed.",
            "examples": ["fact", "preference", "decision", "constraint", "communication_style"],
            "x-kdcube-known-values": list(KNOWN_MEMORY_KINDS),
        },
        "status": {"type": "string", "default": "active"},
        "visibility": {"type": "string", "default": "user"},
        "labels": {"type": "array", "items": "string"},
        "keywords": {"type": "array", "items": "string"},
        "confidence": {"type": "number", "description": "Confidence for the new evidence event."},
        "importance": {"type": "number", "description": "Importance for ranking and tiering."},
        "pinned": {"type": "boolean"},
        "canonical_key": {"type": "string", "description": "Optional stable dedupe key."},
    },
    "tools": {
        "search": {"tool": "named_services.search_objects", "required": {"namespace": "mem", "query": "<text>"}},
        "get": {"tool": "named_services.get_object", "required": {"namespace": "mem", "object_ref": "mem:record:<memory_id>"}},
        "create": {
            "tool": "named_services.upsert_object",
            "required": {"namespace": "mem", "object_json": {"memory": "<memory text>"}},
            "optional_object_json": ["context", "kind", "visibility", "labels", "keywords", "confidence", "importance", "pinned", "canonical_key"],
        },
        "update": {
            "tool": "named_services.upsert_object",
            "required": {"namespace": "mem", "object_ref": "mem:record:<memory_id>", "object_json": {"memory": "<refinement or replacement text>"}},
            "optional_object_json": ["context", "kind", "status", "visibility", "labels", "keywords", "confidence", "importance", "pinned"],
        },
        "confirm": {
            "tool": "named_services.object_action",
            "required": {"namespace": "mem", "object_ref": "mem:record:<memory_id>", "action": "confirm"},
            "optional_payload": ["note", "importance"],
        },
        "retire": {
            "tool": "named_services.object_action",
            "required": {"namespace": "mem", "object_ref": "mem:record:<memory_id>", "action": "retire"},
            "optional_payload": ["reason"],
        },
        "delete": {"tool": "named_services.delete_object", "required": {"namespace": "mem", "object_ref": "mem:record:<memory_id>"}},
    },
    "search": {
        "namespace": "mem",
        "query": "Hybrid lexical/semantic memory search over memory text, context, labels, and keywords.",
        "filters": MEMORY_SEARCH_FILTERS,
        "returns": "memory.record objects with mem:record:<memory_id> refs.",
    },
}

OBJECT_SCHEMAS = {
    OBJECT_KIND: MEMORY_RECORD_SCHEMA,
}

SERVICE_ABOUT: dict[str, Any] = {
    "title": "KDCube User Memory",
    "description": "SDK user-memory namespace for searching, reading, recording, confirming, retiring, and deleting durable user memory records.",
    "base_objects": [
        {"object_kind": OBJECT_KIND, "canonical_ref": "mem:record:<memory_id>", "description": "User memory record."},
    ],
    "related_data": [
        {
            "name": "events",
            "description": "Optional embedded history/provenance records returned only when reading a memory with include=['events']; events are not exposed as openable/searchable named-service objects.",
        },
    ],
    "search_scopes": [scope.to_dict() for scope in MEMORY_SEARCH_SCOPES],
}


def memory_named_service_spec(*, bundle_id: str | None = None) -> NamedServiceProviderSpec:
    return NamedServiceProviderSpec(
        provider_id=PROVIDER_ID,
        bundle_id=bundle_id,
        namespace=NAMESPACE,
        refs=("mem:*", "me:*"),
        object_kinds=(OBJECT_KIND,),
        search_scopes=MEMORY_SEARCH_SCOPES,
        operations=build_default_operations((TRANSPORT_LOCAL, TRANSPORT_API)),
        label="User memories",
        description="SDK memory namespace provider for durable user-memory records.",
        metadata={
            "canonical_ref": "mem:record:<memory_id>",
            "viewer_surface": "sdk.memory.viewer",
        },
    )


def _clamp_limit(value: Any, *, default: int = 20, max_value: int = 100) -> int:
    try:
        parsed = int(value if value is not None else default)
    except Exception:
        parsed = default
    return max(1, min(parsed, max_value))


def _cursor_offset(value: Any) -> int:
    try:
        return max(0, int(str(value or "0").strip() or 0))
    except Exception:
        return 0


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _factor_weights(value: Any) -> dict[str, float] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return None
    if not isinstance(value, Mapping):
        return None
    weights: dict[str, float] = {}
    for key, raw in value.items():
        try:
            weights[str(key)] = float(raw)
        except Exception:
            continue
    return weights or None


def _bool_or_none(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _memory_namespace(namespace: str | None) -> str:
    return _text(namespace).lower().rstrip(":")


def _memory_event_to_history_payload(event: MemoryEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "memory_id": event.memory_id,
        "event_type": event.event_type,
        "signal_text": event.signal_text,
        "context": event.context,
        "originator": event.originator,
        "confidence": event.confidence,
        "importance": event.importance,
        "labels": list(event.labels or ()),
        "keywords": list(event.keywords or ()),
        "created_at": event.created_at.isoformat(),
        "source": dict(event.source or {}),
        "metadata": dict(event.metadata or {}),
    }


def _memory_record_to_named_service_object(
    record: MemoryRecord,
    *,
    score: float | None = None,
    score_breakdown: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    base = memory_record_to_object_payload(record)
    ref = memory_ref(record.id)
    obj = {
        **base,
        "schema": NAMED_SERVICE_OBJECT_SCHEMA,
        "ref": ref,
        "namespace": NAMESPACE,
        "object_kind": OBJECT_KIND,
        "label": record.memory[:120],
        "title": record.memory[:120],
        # The card's primary text is the memory itself; `context` ("why this exists")
        # is secondary and must not replace the fact on the pin.
        "summary": record.memory[:500] or record.context[:500],
        "mime": MEMORY_MIME,
        "capabilities": memory_ref_capabilities(),
        "default_open_effect_action": "open",
        "identity": {
            "object_ref": ref,
            "object_id": record.id,
            "object_kind": OBJECT_KIND,
            "namespace": NAMESPACE,
        },
        "meta": {
            "mime": MEMORY_MIME,
            "revision": str(record.revision),
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
        },
        "body": {
            "memory": record.memory,
            "context": record.context,
            "kind": record.kind,
            "status": record.status,
            "visibility": record.visibility,
            "visible_to_user": is_user_visible(record.visibility),
            "labels": list(record.labels or ()),
            "keywords": list(record.keywords or ()),
            "tier": record.tier,
            "pinned": bool(record.pinned),
            "confidence_score": record.confidence_score,
            "importance_score": record.importance_score,
            "freshness_score": record.freshness_score,
            "salience_score": record.salience_score,
            "confirmation_rate": record.confirmation_rate,
            "evidence_count": record.evidence_count,
            "update_count": record.update_count,
            "confirmation_count": record.confirmation_count,
            "contradiction_count": record.contradiction_count,
            "source": dict(record.source or {}),
            "metadata": dict(record.metadata or {}),
        },
    }
    if score is not None:
        obj["score"] = float(score)
        obj["rank_score"] = float(score)
    if score_breakdown:
        obj["score_breakdown"] = dict(score_breakdown)
    return obj


async def _single_json_chunk(payload: Mapping[str, Any]):
    yield json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def _memory_object_to_read_payload(obj: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": True,
        "object_ref": obj.get("object_ref") or obj.get("ref"),
        "memory": dict(obj),
        "count": 1,
    }
    events = obj.get("events")
    if isinstance(events, list):
        payload["events"] = list(events)
        payload["events_count"] = len(events)
    return {key: value for key, value in payload.items() if value not in (None, "", [])}


def _memory_object_to_stream_descriptor(obj: Mapping[str, Any]) -> dict[str, Any]:
    keep = (
        "id",
        "object_ref",
        "ref",
        "namespace",
        "object_kind",
        "label",
        "title",
        "summary",
        "mime",
        "capabilities",
        "default_open_effect_action",
        "identity",
        "meta",
    )
    return {key: obj[key] for key in keep if key in obj and obj[key] not in (None, "", [], {})}


def _json_object_from_text(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _memory_object_from_block_target(target: Mapping[str, Any]) -> dict[str, Any]:
    for source in (
        target.get("object"),
        target.get("memory"),
        target.get("ret"),
        target.get("raw"),
    ):
        if not isinstance(source, Mapping):
            continue
        memory = source.get("memory")
        if isinstance(memory, Mapping):
            return dict(memory)
        obj = source.get("object")
        if isinstance(obj, Mapping):
            return dict(obj)
    payload = _json_object_from_text(target.get("text"))
    if isinstance(payload.get("memory"), Mapping):
        return dict(payload["memory"])
    if isinstance(payload.get("object"), Mapping):
        return dict(payload["object"])
    source_block = target.get("source_block") if isinstance(target.get("source_block"), Mapping) else {}
    payload = _json_object_from_text(source_block.get("text"))
    if isinstance(payload.get("memory"), Mapping):
        return dict(payload["memory"])
    if isinstance(payload.get("object"), Mapping):
        return dict(payload["object"])
    return {}


def _memory_ref_from_object_or_request(request: NamedServiceRequest, target: Mapping[str, Any], obj: Mapping[str, Any]) -> str:
    for value in (
        request.object_ref,
        obj.get("object_ref"),
        obj.get("ref"),
        target.get("object_ref"),
        target.get("ref"),
    ):
        text = _text(value)
        if text:
            memory_id = memory_id_from_ref(text)
            return memory_ref(memory_id) if memory_id else text
    memory_id = _text(obj.get("id") or obj.get("object_id") or request.object_id)
    return memory_ref(memory_id) if memory_id else ""


def _memory_object_read_text(obj: Mapping[str, Any], *, object_ref: str) -> str:
    body = obj.get("body") if isinstance(obj.get("body"), Mapping) else {}
    memory_text = _text(obj.get("memory") or body.get("memory") or obj.get("summary") or obj.get("title"))
    context_text = _text(obj.get("context") or body.get("context"))
    kind = _text(obj.get("kind") or body.get("kind"))
    status = _text(obj.get("status") or body.get("status"))
    visibility = _text(obj.get("visibility") or body.get("visibility"))
    labels = obj.get("labels") or body.get("labels") or ()
    keywords = obj.get("keywords") or body.get("keywords") or ()
    lines = ["[MEMORY RECORD]"]
    if object_ref:
        lines.append(f"object_ref: {object_ref}")
    if kind:
        lines.append(f"kind: {kind}")
    if status:
        lines.append(f"status: {status}")
    if visibility:
        lines.append(f"visibility: {visibility}")
    if labels:
        lines.append("labels: " + ", ".join(str(item) for item in labels if str(item).strip()))
    if keywords:
        lines.append("keywords: " + ", ".join(str(item) for item in keywords if str(item).strip()))
    if memory_text:
        lines.extend(["", "memory:", memory_text])
    if context_text:
        lines.extend(["", "context:", context_text])
    return "\n".join(lines).strip()


@named_service_provider(
    provider_id=PROVIDER_ID,
    namespace=NAMESPACE,
    refs=("mem:*", "me:*"),
    object_kinds=(OBJECT_KIND,),
    search_scopes=MEMORY_SEARCH_SCOPES,
    operations=build_default_operations((TRANSPORT_LOCAL, TRANSPORT_API)),
    label="User memories",
    description="SDK memory namespace provider for durable user-memory records.",
    metadata={"canonical_ref": "mem:record:<memory_id>", "viewer_surface": "sdk.memory.viewer"},
)
class MemoryNamedServiceProvider(NamedServiceProvider):
    def __init__(
        self,
        *,
        store_factory: MemoryStoreFactory,
        scope_factory: MemoryScopeFactory,
        bundle_id: str | None = None,
        allow_write: bool = False,
        default_scope_filter: str = "current_bundle",
        embedding_factory: EmbeddingFactory | None = None,
        embedding_enabled: bool = True,
        ensure_schema: bool = False,
    ) -> None:
        super().__init__(memory_named_service_spec(bundle_id=bundle_id))
        self._store_factory = store_factory
        self._scope_factory = scope_factory
        self._allow_write = bool(allow_write)
        self._default_scope_filter = normalize_scope_filter(default_scope_filter)
        self._embedding_factory = embedding_factory
        self._embedding_enabled = bool(embedding_enabled)
        self._ensure_schema = bool(ensure_schema)

    def _store(self, ctx: NamedServiceContext) -> UserMemoryStore:
        return self._store_factory(ctx)

    def _scope(self, ctx: NamedServiceContext) -> MemoryScope:
        return self._scope_factory(ctx).normalized()

    async def _ensure_store_schema(self, store: UserMemoryStore) -> None:
        if not self._ensure_schema:
            return
        ensure = getattr(store, "ensure_schema", None)
        if callable(ensure):
            await ensure()

    async def _embedding(self, query: str) -> Sequence[float] | None:
        if not self._embedding_enabled or self._embedding_factory is None or not _text(query):
            return None
        try:
            value = self._embedding_factory(query)
            if inspect.isawaitable(value):
                value = await value
            return value if value is not None else None
        except Exception:
            return None

    def _scope_filter(self, request: NamedServiceRequest, *, default: str | None = None) -> str:
        return normalize_scope_filter(
            request.filters.get("scope_filter")
            or request.payload.get("scope_filter")
            or default
            or self._default_scope_filter
        )

    def _source(self, ctx: NamedServiceContext, request: NamedServiceRequest, *, action: str) -> dict[str, Any]:
        return {
            "source": "named_service",
            "provider": PROVIDER_ID,
            "operation": request.operation,
            "action": action,
            "request_id": ctx.request_id or "",
            "conversation_id": ctx.conversation_id or "",
            "turn_id": ctx.turn_id or "",
        }

    def _writable_or_error(self, request: NamedServiceRequest) -> NamedServiceResponse | None:
        if self._allow_write:
            return None
        return NamedServiceResponse.error_response(
            code="memory_write_disabled",
            message="Memory named-service writes are disabled by bundle policy.",
            status=403,
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            object_ref=request.object_ref,
        )

    async def provider_about(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx, request
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=NAMESPACE,
            extra={
                **SERVICE_ABOUT,
                "canonical_ref": "mem:record:<memory_id>",
                "viewer_surface": "sdk.memory.viewer",
                "schema_hint": "Call object.schema with object_kind='memory.record' for concrete payload fields. Event history is related data returned only when reading a memory with include=['events'].",
            },
        )

    async def provider_capabilities(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            capabilities={
                "search": True,
                "get": True,
                "upsert": self._allow_write,
                "delete": self._allow_write,
                "actions": ["preview", "open", "describe", "capabilities", "confirm", "retire"],
                "default_open_effect_action": "open",
            },
        )

    async def object_schema(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        object_kind = (
            _text(request.payload.get("object_kind"))
            or _text(request.object.get("object_kind"))
            or OBJECT_KIND
        )
        schema = OBJECT_SCHEMAS.get(object_kind)
        if schema is None:
            return NamedServiceResponse.error_response(
                code="memory_schema_not_found",
                message=f"Unknown memory object kind: {object_kind}",
                status=404,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
                object_ref=request.object_ref,
            )
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            object_ref=request.object_ref,
            extra={"schema": schema, "search_scopes": SERVICE_ABOUT["search_scopes"]},
        )

    async def object_search(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        store = self._store(ctx)
        await self._ensure_store_schema(store)
        namespace = _memory_namespace(request.namespace) or NAMESPACE
        if namespace not in {NAMESPACE, RECORD_SCOPE_NAMESPACE}:
            return NamedServiceResponse.error_response(
                code="memory_search_scope_not_found",
                message=f"Unsupported memory search scope: {namespace}. Search durable memory records with namespace='mem' or namespace='mem:record'.",
                status=404,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
            )
        filters = dict(request.filters or {})
        mode = _text(filters.get("mode") or request.search_mode or "hybrid")
        if mode == "recent_events":
            return NamedServiceResponse.error_response(
                code="memory_event_search_not_exposed",
                message="Memory event history is related data, not a named-service search object. Read a mem:record:<id> record with include=['events'] when provenance is needed.",
                status=400,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
            )
        limit = _clamp_limit(request.limit, default=10, max_value=50)
        offset = _cursor_offset(request.cursor)
        query = _text(request.query)
        visible_to_user = _bool_or_none(filters.get("visible_to_user"))
        if visible_to_user is None:
            visible_to_user = True
        scope_filter = self._scope_filter(request, default="all_user_memories")
        factor_weights = _factor_weights(filters.get("factor_weights"))
        # Semantic factor off (semantic_weight <= 0) → skip the query embed entirely,
        # so the search costs no embedder call and ranks on text + labels + salience.
        # Aligns with the hybrid index's "semantic off" mode: turning the factor off
        # is a valid, graceful choice (no embeddings / no budget). Memory uses a
        # weighted sum, so weight 0 (not a negative floor) is the off switch here.
        effective_semantic_weight = float(
            (factor_weights or {}).get("semantic_weight", DEFAULT_MEMORY_SCORING.semantic_weight)
        )
        query_embedding = None if effective_semantic_weight <= 0 else await self._embedding(query)
        search_request = MemorySearchRequest(
            scope=self._scope(ctx),
            query=query,
            mode=mode,  # type: ignore[arg-type]
            labels=normalize_terms(filters.get("labels")),
            keywords=normalize_terms(filters.get("keywords")),
            kind=_text(filters.get("kind")),
            status=_text(filters.get("status") or "active"),
            visible_to_user=visible_to_user,
            include_private=not bool(visible_to_user),
            scope_filter=scope_filter,
            limit=limit + 1,
            offset=offset,
            query_embedding=query_embedding,
            min_relevance_score=_float(filters.get("min_relevance_score"), 0.0),
            half_life_days=_float(filters.get("half_life_days"), DEFAULT_MEMORY_SCORING.half_life_days),
            factor_weights=factor_weights,
        )
        LOGGER.info(
            "[memory.named_service.search] start namespace=%s query=%r mode=%s scope_filter=%s visible_to_user=%s include_private=%s limit=%s offset=%s embedding=%s filters=%s user_id=%s bundle_id=%s",
            namespace,
            query,
            mode,
            scope_filter,
            visible_to_user,
            search_request.include_private,
            limit,
            offset,
            bool(query_embedding),
            filters,
            search_request.scope.user_id,
            search_request.scope.bundle_id,
        )
        results = await store.search(search_request)
        has_more = len(results) > limit
        visible_results = results[:limit]
        items: list[dict[str, Any]] = []
        for item in visible_results:
            if isinstance(item, MemorySearchResult):
                items.append(
                    _memory_record_to_named_service_object(
                        item.memory,
                        score=item.score,
                        score_breakdown=item.score_breakdown,
                    )
                )
            elif isinstance(item, MemoryEvent):
                continue
        next_cursor = str(offset + limit) if has_more else None
        LOGGER.info(
            "[memory.named_service.search] complete namespace=%s query=%r mode=%s scope_filter=%s raw_count=%s returned=%s has_more=%s",
            namespace,
            query,
            mode,
            scope_filter,
            len(results),
            len(items),
            has_more,
        )
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            items=items,
            next_cursor=next_cursor,
            extra={
                "search_scope": namespace,
                "query": query,
                "filters": filters,
            },
        )

    async def object_list(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        filters = dict(request.filters or {})
        filters.setdefault("mode", "recent")
        return await self.object_search(
            ctx,
            NamedServiceRequest.from_dict({
                **request.to_dict(),
                "operation": request.operation,
                "query": request.query or "",
                "filters": filters,
            }),
        )

    async def object_get(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse | NamedServiceStreamResult:
        store = self._store(ctx)
        await self._ensure_store_schema(store)
        memory_id = memory_id_from_ref(request.object_ref or "") or _text(request.object_id)
        if memory_id.startswith("event:"):
            return NamedServiceResponse.error_response(
                code="memory_event_object_not_exposed",
                message="Memory events are internal history records. Read the parent mem:record:<memory_id> with include=['events'] when provenance is needed.",
                status=400,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
                object_ref=request.object_ref,
            )
        if not memory_id:
            return NamedServiceResponse.error_response(
                code="memory_id_required",
                message="object_ref or object_id is required.",
                status=400,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
            )
        record = await store.get_memory(
            scope=self._scope(ctx),
            memory_id=memory_id,
            visible_to_user=None,
            scope_filter=self._scope_filter(request, default="all_user_memories"),
        )
        if record is None:
            return NamedServiceResponse.error_response(
                code="memory_not_found",
                message=f"Memory was not found: {memory_id}",
                status=404,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
                object_ref=request.object_ref,
            )
        obj = _memory_record_to_named_service_object(record)
        if "events" in {str(item).strip() for item in (request.include or [])}:
            events = await store.list_memory_events(
                scope=self._scope(ctx),
                memory_id=memory_id,
                limit=25,
                visible_to_user=None,
                scope_filter=self._scope_filter(request, default="all_user_memories"),
            )
            obj["events"] = [_memory_event_to_history_payload(event) for event in events]
        if _text(request.response_mode).lower() == "stream":
            object_ref = memory_ref(record.id)
            return NamedServiceStreamResult(
                response=NamedServiceResponse.ok_response(
                    provider=self.provider_identity(),
                    namespace=request.namespace or NAMESPACE,
                    object_ref=object_ref,
                    object=_memory_object_to_stream_descriptor(obj),
                    revision=str(record.revision),
                ),
                chunks=_single_json_chunk(_memory_object_to_read_payload(obj)),
                filename=f"{record.id}.json",
                media_type=MEMORY_MIME,
            )
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            object_ref=memory_ref(record.id),
            object=obj,
            revision=str(record.revision),
        )

    async def event_resolve(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        object_ref = request.object_ref or ""
        memory_id = memory_id_from_ref(object_ref)
        if not memory_id:
            return NamedServiceResponse.error_response(
                code="memory_id_required",
                message="event.resolve requires a mem:record:<memory_id> object_ref.",
                status=400,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
                object_ref=request.object_ref,
            )
        canonical_ref = memory_ref(memory_id)
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            object_ref=canonical_ref,
            extra={
                "event_source_id": f"named_services.{NAMESPACE}",
                "object_ref": canonical_ref,
                "target_surface": "sdk.memory.viewer",
            },
        )

    async def block_produce(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        target = request.payload.get("target") if isinstance(request.payload.get("target"), Mapping) else {}
        target = dict(target or {})
        obj = _memory_object_from_block_target(target)
        object_ref = _memory_ref_from_object_or_request(request, target, obj)
        if not obj:
            get_response = await self.object_get(
                ctx,
                NamedServiceRequest.from_dict({
                    **request.to_dict(),
                    "operation": "object.get",
                    "namespace": request.namespace or NAMESPACE,
                    "object_ref": object_ref or request.object_ref,
                    "response_mode": "",
                }),
            )
            if not isinstance(get_response, NamedServiceResponse) or not get_response.ok:
                return NamedServiceResponse.ok_response(
                    provider=self.provider_identity(),
                    namespace=request.namespace or NAMESPACE,
                    object_ref=object_ref or request.object_ref,
                    extra={"blocks": []},
                    warnings=[{
                        "code": get_response.error.code if isinstance(get_response, NamedServiceResponse) and get_response.error else "memory_block_produce_get_failed",
                        "message": get_response.error.message if isinstance(get_response, NamedServiceResponse) and get_response.error else "Memory object could not be loaded for block production.",
                    }],
                )
            obj = dict(get_response.object or {})
            object_ref = _memory_ref_from_object_or_request(request, target, obj)
        if not object_ref:
            return NamedServiceResponse.ok_response(
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
                extra={"blocks": []},
                warnings=[{"code": "memory_ref_missing", "message": "Memory block production could not determine object_ref."}],
            )

        text = _memory_object_read_text(obj, object_ref=object_ref)
        block = {
            "turn": target.get("turn_id") or ctx.turn_id or "",
            "type": "react.tool.result",
            "call_id": target.get("tool_call_id") or "",
            "tool_id": "named_services.mem",
            "event_source_id": f"named_services.{NAMESPACE}",
            "mime": "text/markdown",
            "path": object_ref,
            "text": text,
            "meta": {
                "tool_call_id": target.get("tool_call_id") or "",
                "tool_id": target.get("tool_id") or "react.read",
                "turn_id": target.get("turn_id") or ctx.turn_id or "",
                "object_ref": object_ref,
                "source_namespace": NAMESPACE,
                "materialized_path": target.get("logical_path") or target.get("path") or "",
                "object_kind": OBJECT_KIND,
                "mime": MEMORY_MIME,
                "render_policy": "memory.named_service.block_produce",
            },
        }
        LOGGER.info(
            "[memory.named_service.block_produce] produced object_ref=%s materialized_path=%s text_symbols=%s",
            object_ref,
            target.get("logical_path") or target.get("path") or "",
            len(text),
        )
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            object_ref=object_ref,
            extra={"blocks": [block]},
        )

    async def object_resolve(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        return await self.object_action(
            ctx,
            NamedServiceRequest.from_dict({
                **request.to_dict(),
                "operation": request.operation,
                "action": request.action or "preview",
            }),
        )

    async def object_action(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        action = _text(request.action or request.payload.get("action") or "preview").lower()
        if action in {"confirm", "retire"}:
            write_error = self._writable_or_error(request)
            if write_error is not None:
                return write_error
            store = self._store(ctx)
            await self._ensure_store_schema(store)
            memory_id = memory_id_from_ref(request.object_ref or "") or _text(request.object_id)
            if not memory_id:
                return NamedServiceResponse.error_response(
                    code="memory_id_required",
                    message="object_ref or object_id is required.",
                    status=400,
                    provider=self.provider_identity(),
                    namespace=request.namespace or NAMESPACE,
                    object_ref=request.object_ref,
                )
            if action == "confirm":
                record = await store.confirm_memory(
                    scope=self._scope(ctx),
                    memory_id=memory_id,
                    note=_text(request.payload.get("note") or request.payload.get("memory") or "confirmed"),
                    importance=_float(request.payload.get("importance"), 0.7),
                    originator="agent",
                    source=self._source(ctx, request, action=action),
                )
            else:
                record = await store.retire_memory(
                    scope=self._scope(ctx),
                    memory_id=memory_id,
                    reason=_text(request.payload.get("reason") or request.payload.get("memory") or "retired"),
                    originator="agent",
                    source=self._source(ctx, request, action=action),
                )
            if record is None:
                return NamedServiceResponse.error_response(
                    code="memory_not_found",
                    message=f"Memory was not found: {memory_id}",
                    status=404,
                    provider=self.provider_identity(),
                    namespace=request.namespace or NAMESPACE,
                    object_ref=request.object_ref,
                )
            return NamedServiceResponse.ok_response(
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
                object_ref=memory_ref(record.id),
                object=_memory_record_to_named_service_object(record),
                revision=str(record.revision),
                extra={"action": action},
            )

        store = self._store(ctx)
        payload = {**dict(request.payload or {}), "object_ref": request.object_ref, "action": action}
        raw = await resolve_memory_ref_action(
            payload,
            store=store,
            scope=self._scope(ctx),
            scope_filter=self._scope_filter(request, default="all_user_memories"),
        )
        if not raw.get("ok", True):
            return NamedServiceResponse.error_response(
                code=str(raw.get("error") or "memory_action_failed"),
                message=str(raw.get("message") or raw.get("error") or "Memory action failed"),
                status=int(raw.get("status") or 400),
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
                object_ref=request.object_ref,
            )
        memory_payload = raw.get("memory") if isinstance(raw.get("memory"), Mapping) else None
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            object_ref=str(raw.get("object_ref") or request.object_ref or ""),
            object=memory_payload,
            ui_event=raw.get("ui_event") if isinstance(raw.get("ui_event"), Mapping) else None,
            capabilities=raw.get("capabilities") if isinstance(raw.get("capabilities"), Mapping) else None,
            extra={key: value for key, value in raw.items() if key not in {"ok", "memory", "ui_event", "capabilities"}},
        )

    async def object_upsert(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        write_error = self._writable_or_error(request)
        if write_error is not None:
            return write_error
        store = self._store(ctx)
        await self._ensure_store_schema(store)
        body = dict(request.object or {})
        memory_text = _text(body.get("memory") or body.get("text") or body.get("title"))
        if not memory_text:
            return NamedServiceResponse.error_response(
                code="memory_required",
                message="object_json.memory is required.",
                status=400,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
                object_ref=request.object_ref,
            )
        memory_id = memory_id_from_ref(request.object_ref or "") or _text(request.object_id or body.get("id"))
        signal = MemorySignal(
            memory=memory_text,
            context=_text(body.get("context") or body.get("summary") or body.get("description")),
            kind=_text(body.get("kind") or "fact"),
            event_type=_text(body.get("event_type") or ("agent_refinement" if memory_id else "agent_observation")),
            originator=_text(body.get("originator") or "agent"),
            status=_text(body.get("status") or "active"),
            visibility=_text(body.get("visibility") or "user"),
            labels=normalize_terms(body.get("labels")),
            keywords=normalize_terms(body.get("keywords")),
            confidence=_float(body.get("confidence"), 0.6),
            importance=_float(body.get("importance"), 0.5),
            pinned=_bool_or_none(body.get("pinned")),
            canonical_key=_text(body.get("canonical_key")),
            embedding=await self._embedding(memory_text),
            source=self._source(ctx, request, action="upsert"),
            metadata=dict(body.get("metadata") or {}) if isinstance(body.get("metadata"), Mapping) else {},
        )
        try:
            record = await store.record_signal(
                scope=self._scope(ctx),
                signal=signal,
                match_memory_id=memory_id or None,
                require_match=bool(memory_id),
            )
        except ValueError as exc:
            return NamedServiceResponse.error_response(
                code=str(exc) or "memory_upsert_failed",
                message=str(exc) or "Memory upsert failed.",
                status=400,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
                object_ref=request.object_ref,
            )
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            object_ref=memory_ref(record.id),
            object=_memory_record_to_named_service_object(record),
            revision=str(record.revision),
        )

    async def object_delete(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        write_error = self._writable_or_error(request)
        if write_error is not None:
            return write_error
        store = self._store(ctx)
        await self._ensure_store_schema(store)
        memory_id = memory_id_from_ref(request.object_ref or "") or _text(request.object_id)
        if not memory_id:
            return NamedServiceResponse.error_response(
                code="memory_id_required",
                message="object_ref or object_id is required.",
                status=400,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
                object_ref=request.object_ref,
            )
        mode = _text(request.payload.get("mode") or request.payload.get("delete_mode")).lower()
        if mode in {"retire", "archive"}:
            record = await store.retire_memory(
                scope=self._scope(ctx),
                memory_id=memory_id,
                reason=_text(request.payload.get("reason") or "retired"),
                originator="agent",
                source=self._source(ctx, request, action="retire"),
            )
            if record is None:
                deleted = False
            else:
                deleted = True
                return NamedServiceResponse.ok_response(
                    provider=self.provider_identity(),
                    namespace=request.namespace or NAMESPACE,
                    object_ref=memory_ref(record.id),
                    object=_memory_record_to_named_service_object(record),
                    revision=str(record.revision),
                    extra={"retired": True, "deleted": False},
                )
        else:
            deleted = await store.delete_memory(
                scope=self._scope(ctx),
                memory_id=memory_id,
                visible_to_user=None,
                scope_filter=self._scope_filter(request, default="all_user_memories"),
                ensure_schema=False,
            )
        if not deleted:
            return NamedServiceResponse.error_response(
                code="memory_not_found",
                message=f"Memory was not found: {memory_id}",
                status=404,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
                object_ref=request.object_ref,
            )
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            object_ref=memory_ref(memory_id),
            extra={"deleted": True, "retired": False},
        )


def make_memory_named_service_provider(
    *,
    store_factory: MemoryStoreFactory,
    scope_factory: MemoryScopeFactory,
    bundle_id: str | None = None,
    allow_write: bool = False,
    default_scope_filter: str = "current_bundle",
    embedding_factory: EmbeddingFactory | None = None,
    embedding_enabled: bool = True,
    ensure_schema: bool = False,
) -> MemoryNamedServiceProvider:
    return MemoryNamedServiceProvider(
        store_factory=store_factory,
        scope_factory=scope_factory,
        bundle_id=bundle_id,
        allow_write=allow_write,
        default_scope_filter=default_scope_filter,
        embedding_factory=embedding_factory,
        embedding_enabled=embedding_enabled,
        ensure_schema=ensure_schema,
    )


__all__ = [
    "KNOWN_MEMORY_KINDS",
    "MEMORY_RECORD_SCHEMA",
    "MEMORY_SEARCH_SCOPES",
    "MemoryNamedServiceProvider",
    "NAMESPACE",
    "OBJECT_KIND",
    "PROVIDER_ID",
    "make_memory_named_service_provider",
    "memory_named_service_spec",
]
