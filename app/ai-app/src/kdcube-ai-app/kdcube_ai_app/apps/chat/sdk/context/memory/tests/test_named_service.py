from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from kdcube_ai_app.apps.chat.sdk.context.memory.models import (
    MemoryRecord,
    MemoryScope,
    resolve_collection_update,
)
from kdcube_ai_app.apps.chat.sdk.context.memory.named_service import (
    KNOWN_MEMORY_KINDS,
    MEMORY_RECORD_SCHEMA,
    MEMORY_SEARCH_SCOPES,
    _collection_update,
    make_memory_named_service_provider,
    memory_named_service_spec,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceRequest,
    NamedServiceStreamResult,
)


def test_memory_named_service_exposes_only_memory_record_scope() -> None:
    spec = memory_named_service_spec(bundle_id="bundle@1")

    assert spec.object_kinds == ("memory.record",)
    assert [scope.namespace for scope in spec.search_scopes] == ["mem", "mem:record"]
    assert [scope.object_kind for scope in spec.search_scopes] == ["memory.record", "memory.record"]


def test_memory_kind_schema_is_open_vocabulary() -> None:
    kind_schema = MEMORY_RECORD_SCHEMA["fields"]["kind"]

    assert kind_schema["type"] == "string"
    assert "enum" not in kind_schema
    assert kind_schema["default"] == "fact"
    assert kind_schema["x-kdcube-known-values"] == list(KNOWN_MEMORY_KINDS)
    assert "open vocabulary" in kind_schema["description"].lower()


def test_memory_search_scopes_do_not_expose_events_as_objects() -> None:
    namespaces = {scope.namespace for scope in MEMORY_SEARCH_SCOPES}
    object_kinds = {scope.object_kind for scope in MEMORY_SEARCH_SCOPES}

    assert namespaces == {"mem", "mem:record"}
    assert object_kinds == {"memory.record"}


def test_memory_search_scopes_advertise_memory_native_factor_weights() -> None:
    filters = MEMORY_SEARCH_SCOPES[0].filters_schema or {}
    factor_weights = filters["factor_weights"]
    properties = factor_weights["properties"]

    assert factor_weights["type"] == "object"
    assert "semantic_weight" in properties
    assert "freshness_weight" in properties
    assert "half_life_days" not in properties
    assert "min_relevance_score" not in properties
    assert "relevance_score" in filters["thresholds"]["properties"]
    assert "half_life_days" in filters["scoring"]["properties"]
    assert "rrf_k" not in properties


def _provider():
    return make_memory_named_service_provider(
        store_factory=lambda ctx: object(),  # not used by about/schema tests
        scope_factory=lambda ctx: MemoryScope(tenant="tenant-a", project="project-a", user_id="user-a"),
        bundle_id="bundle@1",
        allow_write=True,
    )


def _record(memory_id: str = "mem_1") -> MemoryRecord:
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    return MemoryRecord(
        id=memory_id,
        scope=MemoryScope(tenant="tenant-a", project="project-a", user_id="user-a", bundle_id="other@1"),
        memory="Prefer balanced legal/commercial wording",
        context="Applies to relationship documents.",
        kind="preference",
        status="active",
        visibility="user",
        labels=("legal",),
        keywords=("commercial",),
        tier=1,
        pinned=False,
        confidence_score=0.9,
        importance_score=0.8,
        freshness_score=1.0,
        salience_score=0.85,
        confirmation_rate=1.0,
        evidence_count=2,
        update_count=1,
        confirmation_count=1,
        contradiction_count=0,
        created_at=now,
        updated_at=now,
        last_event_at=now,
        revision=4,
    )


class _Store:
    def __init__(self, record: MemoryRecord | None = None) -> None:
        self.record = record
        self.search_requests = []
        self.get_requests = []

    async def search(self, request):
        self.search_requests.append(request)
        return []

    async def get_memory(self, **kwargs):
        self.get_requests.append(kwargs)
        return self.record


def _provider_with_store(store: _Store, *, model_service=None, embedding_factory=None, search_embedding_factory=None):
    return make_memory_named_service_provider(
        store_factory=lambda ctx: store,
        scope_factory=lambda ctx: MemoryScope(tenant="tenant-a", project="project-a", user_id="user-a", bundle_id="bundle@1"),
        bundle_id="bundle@1",
        allow_write=True,
        model_service=model_service,
        embedding_factory=embedding_factory,
        search_embedding_factory=search_embedding_factory,
    )


@pytest.mark.asyncio
async def test_memory_provider_about_only_advertises_record_objects() -> None:
    response = await _provider().provider_about(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(operation="provider.about", namespace="mem"),
    )

    assert response.ok is True
    assert response.extra["base_objects"] == [
        {"object_kind": "memory.record", "canonical_ref": "mem:record:<memory_id>", "description": "User memory record."}
    ]
    assert response.extra["search_scopes"][0]["namespace"] == "mem"
    assert "mem:event" not in str(response.to_dict())


@pytest.mark.asyncio
async def test_memory_event_schema_is_not_public_named_service_object() -> None:
    response = await _provider().object_schema(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(
            operation="object.schema",
            namespace="mem",
            payload={"object_kind": "memory.event"},
        ),
    )

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "memory_schema_not_found"


@pytest.mark.asyncio
async def test_memory_search_defaults_to_all_user_memories() -> None:
    store = _Store()
    response = await _provider_with_store(store).object_search(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(operation="object.search", namespace="mem", query="legal commercial", limit=5),
    )

    assert response.ok is True
    assert len(store.search_requests) == 1
    request = store.search_requests[0]
    assert request.scope_filter == "all_user_memories"
    assert request.visible_to_user is True
    assert request.include_private is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("origin", "expected_scope_filter", "expected_originator"),
    [
        ("any", "all_user_memories", ""),
        ("any_agent", "all_user_memories", "agent"),
        ("this_agent", "current_bundle", ""),
        ("created_by_user", "all_user_memories", "user"),
        ("global", "global_only", ""),
    ],
)
async def test_memory_search_maps_public_origin_filter_to_internal_scope_filter(
    origin: str,
    expected_scope_filter: str,
    expected_originator: str,
) -> None:
    store = _Store()
    response = await _provider_with_store(store).object_search(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(
            operation="object.search",
            namespace="mem",
            query="legal commercial",
            limit=5,
            filters={"origin": origin},
        ),
    )

    assert response.ok is True
    assert store.search_requests[0].scope_filter == expected_scope_filter
    assert store.search_requests[0].originator == expected_originator


@pytest.mark.asyncio
async def test_memory_search_passes_factor_weight_filters_to_request() -> None:
    store = _Store()
    response = await _provider_with_store(store).object_search(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(
            operation="object.search",
            namespace="mem",
            query="legal commercial",
            limit=5,
            filters={
                "factor_weights": {"semantic_weight": "1.0", "freshness_weight": 0},
                "scoring": {"half_life_days": "10"},
                "thresholds": {"relevance_score": "0.2"},
            },
        ),
    )

    assert response.ok is True
    request = store.search_requests[0]
    assert request.factor_weights == {"semantic_weight": 1.0, "freshness_weight": 0.0}
    assert request.half_life_days == 10.0
    assert request.min_relevance_score == 0.2


@pytest.mark.asyncio
async def test_semantic_weight_zero_skips_query_embed() -> None:
    # semantic_weight <= 0 turns the semantic factor off: the query embed must be
    # skipped (no embedder cost) and the request carries no embedding.
    store = _Store()
    embed_calls: list[str] = []

    def spy_embed(text: str):
        embed_calls.append(text)
        return [0.1, 0.2, 0.3]

    response = await _provider_with_store(store, embedding_factory=spy_embed).object_search(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(
            operation="object.search",
            namespace="mem",
            query="legal commercial",
            limit=5,
            filters={"factor_weights": {"semantic_weight": 0}},
        ),
    )

    assert response.ok is True
    assert embed_calls == [], "semantic off must not call the embedder"
    assert store.search_requests[0].query_embedding is None


@pytest.mark.asyncio
async def test_positive_semantic_weight_still_embeds() -> None:
    # The control case: with semantic weight present, the embed is paid and flows
    # through to the request, so the zero-skip above is a real gate (not always-off).
    store = _Store()
    embed_calls: list[str] = []

    def spy_embed(text: str):
        embed_calls.append(text)
        return [0.1, 0.2, 0.3]

    response = await _provider_with_store(store, embedding_factory=spy_embed).object_search(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(
            operation="object.search",
            namespace="mem",
            query="legal commercial",
            limit=5,
            filters={"factor_weights": {"semantic_weight": 1.0}},
        ),
    )

    assert response.ok is True
    assert embed_calls == ["legal commercial"]
    assert store.search_requests[0].query_embedding == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_memory_search_uses_model_service_search_query() -> None:
    store = _Store()
    write_calls: list[str] = []
    search_calls: list[tuple[str, str | None]] = []

    def write_embed(text: str):
        write_calls.append(text)
        return [0.1]

    class _ModelService:
        async def embed_search_query(self, text: str, *, flow: str | None = None):
            search_calls.append((text, flow))
            return [0.9]

    response = await _provider_with_store(
        store,
        model_service=_ModelService(),
        embedding_factory=write_embed,
    ).object_search(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(
            operation="object.search",
            namespace="mem",
            query="legal commercial",
            limit=5,
        ),
    )

    assert response.ok is True
    assert write_calls == []
    assert search_calls == [("legal commercial", "memory.search")]
    assert store.search_requests[0].query_embedding == [0.9]


@pytest.mark.asyncio
async def test_memory_get_accepts_legacy_me_ref_and_returns_canonical_ref() -> None:
    store = _Store(record=_record())
    response = await _provider_with_store(store).object_get(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(operation="object.get", namespace="mem", object_ref="me:mem_1"),
    )

    assert response.ok is True
    assert response.object_ref == "mem:record:mem_1"
    assert response.object is not None
    assert response.object["ref"] == "mem:record:mem_1"
    assert response.object["object_ref"] == "mem:record:mem_1"
    assert store.get_requests[0]["memory_id"] == "mem_1"
    assert store.get_requests[0]["scope_filter"] == "all_user_memories"


@pytest.mark.asyncio
async def test_memory_get_streams_compact_read_payload_for_react_pull() -> None:
    store = _Store(record=_record())
    response = await _provider_with_store(store).object_get(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a"),
        NamedServiceRequest(
            operation="object.get",
            namespace="mem",
            object_ref="mem:record:mem_1",
            response_mode="stream",
        ),
    )

    assert isinstance(response, NamedServiceStreamResult)
    assert response.filename == "mem_1.json"
    assert response.media_type == "application/vnd.kdcube.memory.record+json;version=1"
    assert response.response.ok is True
    assert response.response.object_ref == "mem:record:mem_1"
    assert response.response.object["summary"] == "Prefer balanced legal/commercial wording"
    assert "body" not in response.response.object

    chunks = []
    async for chunk in response.chunks:
        chunks.append(chunk)
    payload = json.loads(b"".join(chunks).decode("utf-8"))
    assert payload["ok"] is True
    assert payload["object_ref"] == "mem:record:mem_1"
    assert payload["memory"]["object_ref"] == "mem:record:mem_1"
    assert payload["memory"]["memory"] == "Prefer balanced legal/commercial wording"
    assert payload["memory"]["context"] == "Applies to relationship documents."


@pytest.mark.asyncio
async def test_memory_block_produce_projects_pulled_read_payload() -> None:
    store = _Store(record=_record())
    provider = _provider_with_store(store)
    payload = {
        "ok": True,
        "object_ref": "mem:record:mem_1",
        "memory": {
            "id": "mem_1",
            "object_ref": "mem:record:mem_1",
            "memory": "Prefer balanced legal/commercial wording",
            "context": "Applies to relationship documents.",
            "kind": "preference",
            "status": "active",
            "visibility": "user",
            "labels": ["legal"],
            "keywords": ["commercial"],
        },
    }
    response = await provider.block_produce(
        NamedServiceContext(tenant="tenant-a", project="project-a", user_id="user-a", turn_id="turn_read"),
        NamedServiceRequest(
            operation="block.produce",
            namespace="mem",
            object_ref="mem:record:mem_1",
            payload={
                "target": {
                    "turn_id": "turn_read",
                    "tool_call_id": "r_mem",
                    "logical_path": "fi:turn_read.files/mem_1.json",
                    "text": json.dumps(payload, ensure_ascii=False),
                }
            },
        ),
    )

    assert response.ok is True
    blocks = response.extra["blocks"]
    assert len(blocks) == 1
    block = blocks[0]
    assert block["path"] == "mem:record:mem_1"
    assert "[MEMORY RECORD]" in block["text"]
    assert "Prefer balanced legal/commercial wording" in block["text"]
    assert block["meta"]["object_ref"] == "mem:record:mem_1"
    assert block["meta"]["materialized_path"] == "fi:turn_read.files/mem_1.json"


def test_memory_record_schema_replaces_label_and_keyword_groups() -> None:
    fields = MEMORY_RECORD_SCHEMA["fields"]

    # Labels and keywords are value-lists (bare strings), so the only way to
    # remove an item is to re-send the list without it. The provided list
    # replaces the stored set; omitting the field preserves the existing set.
    assert fields["labels"]["update_strategy"] == "replace"
    assert fields["keywords"]["update_strategy"] == "replace"


def test_memory_record_schema_documents_add_remove_delta() -> None:
    fields = MEMORY_RECORD_SCHEMA["fields"]

    # Bare list still replaces; the description also advertises the incremental
    # {add, remove} delta shape for removing one item without re-sending the set.
    for key in ("labels", "keywords"):
        description = fields[key]["description"]
        assert '"add"' in description and '"remove"' in description
        assert "replace" in description.lower()


def test_resolve_collection_update_replaces_on_bare_list() -> None:
    # Bare list replaces the stored set regardless of what was there.
    assert resolve_collection_update(["draft", "ready"], ["final"]) == ["final"]
    assert resolve_collection_update(["draft"], []) == []
    # Insert semantics: resolving against no existing set yields the list.
    assert resolve_collection_update(None, ["A", "b"]) == ["a", "b"]


def test_resolve_collection_update_delta_removes_only_named_item() -> None:
    # {remove: ["draft"]} drops only that item, preserving the rest.
    assert resolve_collection_update(
        ["draft", "ready", "legal"], {"remove": ["draft"]}
    ) == ["legal", "ready"]


def test_resolve_collection_update_delta_swaps_add_and_remove() -> None:
    # {add, remove} swaps: remove draft, add ready (removes applied first).
    assert resolve_collection_update(
        ["draft", "legal"], {"add": ["ready"], "remove": ["draft"]}
    ) == ["legal", "ready"]
    # Adding an item already present is idempotent; either side is optional.
    assert resolve_collection_update(["legal"], {"add": ["legal", "new"]}) == [
        "legal",
        "new",
    ]


def test_collection_update_coercion_shapes() -> None:
    # Omitted -> None (preserve). Bare list -> normalized list (replace).
    assert _collection_update({}, "labels") is None
    assert _collection_update({"labels": ["A", "b"]}, "labels") == ["a", "b"]
    assert _collection_update({"labels": []}, "labels") == []
    # Delta mapping is normalized and passed through for incremental apply.
    assert _collection_update(
        {"labels": {"add": ["Ready"], "remove": ["Draft"]}}, "labels"
    ) == {"add": ["ready"], "remove": ["draft"]}
