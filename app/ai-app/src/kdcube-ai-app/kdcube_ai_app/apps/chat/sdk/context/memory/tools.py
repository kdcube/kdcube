"""Reusable cross-conversation memory tools for bundles.

Bundles normally connect this module through
``surfaces.as_consumer.agents.<agent>.tools`` with alias ``memory``. The
exported tool ids are therefore:

```text
memory.search_memory
memory.recent_memories
memory.read_memory
memory.record_memory
memory.confirm_memory
memory.retire_memory
```

The module-level functions are the portable callable surface used by isolated
tool runtimes. They bind to the current bundle request context at call time,
then delegate to `UserMemoryTools`, which owns scope resolution, store access,
embedding lookup, and write gating. The `UserMemoryTools` class remains
available for bundles or jobs that want to instantiate the same tools with an
explicit scope provider.

Agent tool calls do not accept an `originator` parameter. Tool writes are
recorded with the originator supplied by the runtime scope, defaulting to
`agent`. User-facing widgets and service APIs set user-originated events
directly on `UserMemoryStore`.

Memory tool results are ordinary structured tool results. They do not produce
files, hosted artifacts, or source-pool rows. In the ReAct event-source policy
pipeline they use the generic structured-result block-production policies so
the visible timeline shape stays aligned with the legacy external-tool path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import pathlib
import re
import sys
from dataclasses import dataclass
from typing import Annotated, Any, Awaitable, Callable, Dict, Optional, Sequence

from kdcube_ai_app.apps.chat.sdk.events import artifact_namespace_rehoster, event_source_declaration, event_source_reader
from kdcube_ai_app.apps.chat.sdk.solutions.react.events import structured_result_source_policies

from .events.resolver import memory_id_from_ref, memory_ref
from .events.policies import (  # noqa: F401 - discovered by event-source subsystem
    MEMORY_CONTEXT_BLOCK_POLICY_ID,
    MEMORY_CONTEXT_COMPACTION_POLICY_ID,
    MEMORY_CONTEXT_RENDER_POLICY_ID,
    MEMORY_READ_BLOCK_POLICY_ID,
    memory_context_block_policy,
    memory_context_render_policy,
    memory_read_block_policy,
)
from .models import MemoryScope, MemorySearchRequest, MemorySignal, normalize_scope_filter, normalize_terms
from .store import UserMemoryStore

try:
    from semantic_kernel.functions import kernel_function
except Exception:  # pragma: no cover - semantic-kernel compatibility fallback
    try:
        from semantic_kernel.utils.function_decorator import kernel_function
    except Exception:  # pragma: no cover
        def kernel_function(*_args: Any, **_kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
            def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
                return fn

            return _decorator


ScopeProvider = Callable[[], Dict[str, Any]]
StoreFactory = Callable[[Dict[str, Any]], UserMemoryStore]
Embedder = Callable[[Sequence[str]], Awaitable[Sequence[Sequence[float]]]]

REGISTRY: Dict[str, Any] = {}
_SERVICE: Any = None
SERVICE: Any = None
LOGGER = logging.getLogger("kdcube.memory.tools")

_MEMORY_TOOL_EVENT_SOURCE_DESCRIPTIONS: Dict[str, str] = {
    "search_memory": (
        "Search durable cross-conversation user memory and return structured memory or event rows."
    ),
    "recent_memories": "Return recent durable user memories for the current configured scope.",
    "read_memory": "Read one durable user memory by mem:record URI or bare memory id.",
    "record_memory": "Create or refine durable user memory when the bundle config permits writes.",
    "confirm_memory": "Confirm an existing durable memory by id when the bundle config permits writes.",
    "retire_memory": "Retire an existing durable memory by id when the bundle config permits writes.",
}


def list_event_sources() -> list[Any]:
    """Declare ReAct event sources for the module-level memory tools.

    The declarations are alias-relative because the runtime tool id is produced
    by the configured tool alias: `alias + "." + callable_name`. Current reference
    bundles use alias `memory`, yielding ids such as `memory.search_memory`.

    Memory tools return JSON dictionaries with `ok`, `memory`, `memories`,
    `events`, `count`, `error`, and `message` fields. They do not own special
    artifact production, so they bind to the shared structured-result policies:

    - `react.block_production.tool_default` for the ordinary tool result;
    - `react.block_production.generic_result_item` for the existing result
      block shape;
    - `react.block_production.declared_file_items` for parity with the generic
      structured-result path when a memory tool deliberately declares files.

    Timeline, compaction, and announce phases currently use the default
    identity behavior. Add a memory-specific ReAct policy only when a memory
    result needs a different projection than ordinary structured tool output.
    """

    declarations: list[Any] = [
        event_source_declaration(
            event_source_id="{alias}.context",
            policies=[
                {
                    "react_phase": "block_production",
                    "event_policy_id": MEMORY_CONTEXT_BLOCK_POLICY_ID,
                },
                {
                    "react_phase": "timeline_projection",
                    "event_policy_id": MEMORY_CONTEXT_RENDER_POLICY_ID,
                },
                {
                    "react_phase": "compaction_projection",
                    "event_policy_id": MEMORY_CONTEXT_COMPACTION_POLICY_ID,
                },
            ],
            description="Attached durable user-memory context refs such as mem:record:<id>.",
            kind="event.context",
        )
    ]
    for name, description in _MEMORY_TOOL_EVENT_SOURCE_DESCRIPTIONS.items():
        policies = structured_result_source_policies()
        if name == "read_memory":
            policies = [
                {
                    "react_phase": "block_production",
                    "event_policy_id": "react.block_production.tool_default",
                },
                {
                    "react_phase": "block_production",
                    "event_policy_id": MEMORY_READ_BLOCK_POLICY_ID,
                },
                {
                    "react_phase": "timeline_projection",
                    "event_policy_id": "react.timeline_projection.identity",
                },
                {
                    "react_phase": "compaction_projection",
                    "event_policy_id": "react.compaction_projection.identity",
                },
            ]
        declarations.append(
            event_source_declaration(
                event_source_id=f"{{alias}}.{name}",
                policies=policies,
                description=description,
                kind="react.tool",
            )
        )
    return declarations


def _ok(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"ok": True, **payload}


def _error(code: str, message: str) -> Dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _bool_filter(value: str) -> Optional[bool]:
    normalized = str(value or "").strip().lower()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0"}:
        return False
    return None


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"", "0", "false", "off", "disabled", "no"}:
        return False
    if normalized in {"1", "true", "on", "enabled", "yes"}:
        return True
    return default


def _cfg_path(data: Dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _scope_from_dict(raw: Dict[str, Any]) -> MemoryScope:
    return MemoryScope(
        tenant=str(raw.get("tenant") or raw.get("TENANT") or "default"),
        project=str(raw.get("project") or raw.get("PROJECT") or "default"),
        user_id=str(raw.get("user_id") or raw.get("owner_user_id") or "anonymous"),
        bundle_id=str(raw.get("bundle_id") or raw.get("app_bundle_id") or ""),
    ).normalized()


def _source_from_scope(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "conversation_id": str(raw.get("conversation_id") or ""),
        "turn_id": str(raw.get("turn_id") or ""),
        "bundle_id": str(raw.get("bundle_id") or raw.get("app_bundle_id") or ""),
    }


def _source_for_action(raw: Dict[str, Any], *, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    scope = _scope_from_dict(raw)
    source = _source_from_scope(raw)
    digest = hashlib.sha256(
        "\n".join([
            scope.tenant,
            scope.project,
            scope.user_id,
            scope.bundle_id,
            source.get("conversation_id", ""),
            source.get("turn_id", ""),
            action,
            json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str),
        ]).encode("utf-8")
    ).hexdigest()
    source["idempotency_key"] = f"memory_tool:{action}:{digest}"
    return source


def _record_payload(result: Any) -> Dict[str, Any]:
    nested_memory = getattr(result, "memory", None)
    memory = nested_memory if hasattr(nested_memory, "id") and hasattr(nested_memory, "scope") else result
    payload = {
        "id": memory.id,
        "object_ref": memory_ref(str(memory.id)),
        "scope": {
            "tenant": memory.scope.tenant,
            "project": memory.scope.project,
            "user_id": memory.scope.user_id,
            "bundle_id": memory.scope.bundle_id,
        },
        "bundle_id": memory.scope.bundle_id,
        "memory": memory.memory,
        "context": memory.context,
        "kind": memory.kind,
        "status": memory.status,
        "visibility": memory.visibility,
        "labels": list(memory.labels),
        "keywords": list(memory.keywords),
        "tier": memory.tier,
        "pinned": bool(getattr(memory, "pinned", False)),
        "confidence_score": memory.confidence_score,
        "importance_score": memory.importance_score,
        "freshness_score": memory.freshness_score,
        "salience_score": memory.salience_score,
        "confirmation_rate": memory.confirmation_rate,
        "evidence_count": memory.evidence_count,
        "update_count": memory.update_count,
        "confirmation_count": memory.confirmation_count,
        "contradiction_count": memory.contradiction_count,
        "created_at": memory.created_at.isoformat(),
        "updated_at": memory.updated_at.isoformat(),
        "last_event_at": memory.last_event_at.isoformat(),
        "revision": memory.revision,
    }
    if hasattr(result, "score"):
        payload["score"] = result.score
        payload["score_breakdown"] = dict(result.score_breakdown)
    return payload


def _event_payload(event: Any) -> Dict[str, Any]:
    return {
        "id": event.id,
        "memory_id": event.memory_id,
        "bundle_id": event.scope.bundle_id,
        "event_type": event.event_type,
        "signal_text": event.signal_text,
        "context": event.context,
        "originator": event.originator,
        "confidence": event.confidence,
        "importance": event.importance,
        "labels": list(event.labels),
        "keywords": list(event.keywords),
        "created_at": event.created_at.isoformat(),
    }


@dataclass
class UserMemoryToolConfig:
    allow_write: bool = True
    ensure_schema_on_first_use: bool = False
    default_limit: int = 8
    default_scope_filter: str = "current_bundle"
    embedding_enabled: bool = True
    embedding_timeout_seconds: float = 3.0
    embedding_model: str = ""


class UserMemoryTools:
    """Reusable bundle tools for SDK cross-conversation memory.

    The bundle supplies a scope provider and optionally a store factory. This
    keeps auth/runtime ownership outside the tool while avoiding copy/paste
    memory tools in every bundle.

    The scope provider must return enough runtime context to resolve:

    - tenant/project/user/bundle scope;
    - processor `pg_pool`, unless a custom store factory is provided;
    - optional conversation/turn provenance for memory events.

    `allow_write` controls the state-changing tools. Read tools still respect
    the user-level "memory enabled" preference stored by the memory subsystem.
    """

    def __init__(
        self,
        *,
        scope_provider: ScopeProvider,
        store_factory: StoreFactory | None = None,
        embedder: Embedder | None = None,
        config: UserMemoryToolConfig | None = None,
    ):
        self._scope_provider = scope_provider
        self._store_factory = store_factory or self._default_store_factory
        self._embedder = embedder
        self._config = config or UserMemoryToolConfig()
        self._schema_ready = False

    def _default_store_factory(self, raw_scope: Dict[str, Any]) -> UserMemoryStore:
        pg_pool = raw_scope.get("pg_pool")
        app_state = raw_scope.get("app_state")
        if pg_pool is None and app_state is not None:
            pg_pool = getattr(app_state, "pg_pool", None)
        if pg_pool is None:
            raise RuntimeError("memory tools require pg_pool in scope or a custom store_factory")
        scope = _scope_from_dict(raw_scope)
        return UserMemoryStore(pg_pool=pg_pool, tenant=scope.tenant, project=scope.project)

    async def _store_and_scope(self) -> tuple[UserMemoryStore, MemoryScope, Dict[str, Any]]:
        raw = self._scope_provider()
        store = self._store_factory(raw)
        if self._config.ensure_schema_on_first_use and not self._schema_ready:
            await store.ensure_schema()
            self._schema_ready = True
        return store, _scope_from_dict(raw), raw

    async def _memory_usage_enabled(self, store: UserMemoryStore, scope: MemoryScope) -> bool:
        try:
            prefs = await store.get_user_preferences(scope=scope)
            return bool(prefs.get("memory_enabled", True))
        except Exception:
            # Older deployments may not have the preference table until the
            # schema migration runs. Do not break existing tools because of
            # the preference lookup itself.
            return True

    async def _disabled_by_user(self, store: UserMemoryStore, scope: MemoryScope) -> Optional[Dict[str, Any]]:
        if await self._memory_usage_enabled(store, scope):
            return None
        return _error(
            "memory_usage_disabled_by_user",
            "The user disabled durable memory use. Do not read or write user memory.",
        )

    async def _embed_one(self, text: str) -> Optional[Sequence[float]]:
        if not self._config.embedding_enabled or self._embedder is None:
            return None
        value = str(text or "").strip()
        if not value:
            return None
        try:
            result = await asyncio.wait_for(
                self._embedder([value]),
                timeout=max(0.1, float(self._config.embedding_timeout_seconds or 3.0)),
            )
            if not result:
                return None
            return result[0]
        except Exception:
            return None

    @kernel_function(
        name="search_memory",
        description=(
            "Search durable cross-conversation user memory. Supports hybrid, recent, recent_created, "
            "recent_events, important, confirmed, and hotset modes."
        ),
    )
    async def search_memory(
        self,
        query: Annotated[str, "Search text. Use for semantic/text lookup. Leave empty for recency/important/confirmed modes."] = "",
        mode: Annotated[str, "Search mode: hybrid|recent|recent_created|recent_events|important|confirmed|hotset."] = "hybrid",
        labels: Annotated[str, "Optional comma/space separated label filters, e.g. communication-style, project-scope."] = "",
        keywords: Annotated[str, "Optional comma/space separated keyword filters or aliases."] = "",
        kind: Annotated[str, "Optional memory kind filter, e.g. fact, preference, decision, communication_style."] = "",
        status: Annotated[str, "Memory status filter. Usually active; use any only for explicit inspection flows."] = "active",
        visible_to_user: Annotated[str, "Optional boolean string filter: true|false|yes|no|1|0. Empty means no visibility filter."] = "",
        scope_filter: Annotated[str, "Scope filter: current_bundle|all_user_memories|global_only|current_bundle_or_global. Empty uses bundle config default."] = "",
        limit: Annotated[int, "Maximum rows to return. Keep small for chat context; default 8."] = 8,
    ) -> Annotated[Dict[str, Any], "Envelope. Success returns {ok:true, memories:[...], count:n}; mode=recent_events returns {ok:true, events:[...], count:n}. Failure returns {ok:false,error,message}."]:
        try:
            store, scope, _raw = await self._store_and_scope()
            disabled = await self._disabled_by_user(store, scope)
            if disabled:
                return disabled
            mode_value = mode if mode else "hybrid"
            query_embedding = None
            if mode_value == "hybrid":
                query_embedding = await self._embed_one(query)
            rows = await store.search(
                MemorySearchRequest(
                    scope=scope,
                    query=query,
                    mode=mode_value,
                    labels=normalize_terms(labels),
                    keywords=normalize_terms(keywords),
                    kind=kind,
                    status=status or "active",
                    visible_to_user=_bool_filter(visible_to_user),
                    scope_filter=normalize_scope_filter(scope_filter or self._config.default_scope_filter),
                    limit=limit or self._config.default_limit,
                    query_embedding=query_embedding,
                )
            )
            if mode == "recent_events":
                return _ok({"events": [_event_payload(row) for row in rows], "count": len(rows)})
            return _ok({"memories": [_record_payload(row) for row in rows], "count": len(rows)})
        except Exception as exc:
            return _error("search_memory_failed", str(exc))

    @kernel_function(name="recent_memories", description="Return the last N durable user memories.")
    async def recent_memories(
        self,
        limit: Annotated[int, "Maximum recent memory rows to return. Keep small for chat context; default 10."] = 10,
        created: Annotated[str, "Optional boolean string. true sorts by creation time; false/default sorts by recent update."] = "",
        visible_to_user: Annotated[str, "Optional boolean string filter: true|false|yes|no|1|0. Empty means no visibility filter."] = "",
        scope_filter: Annotated[str, "Scope filter: current_bundle|all_user_memories|global_only|current_bundle_or_global. Empty uses bundle config default."] = "",
    ) -> Annotated[Dict[str, Any], "Envelope. Success returns {ok:true, memories:[...], count:n}. Failure returns {ok:false,error,message}."]:
        try:
            store, scope, _raw = await self._store_and_scope()
            disabled = await self._disabled_by_user(store, scope)
            if disabled:
                return disabled
            rows = await store.search(
                MemorySearchRequest(
                    scope=scope,
                    mode="recent_created" if bool(_bool_filter(created)) else "recent",
                    status="any",
                    visible_to_user=_bool_filter(visible_to_user),
                    scope_filter=normalize_scope_filter(scope_filter or self._config.default_scope_filter),
                    limit=limit,
                )
            )
            return _ok({"memories": [_record_payload(row) for row in rows], "count": len(rows)})
        except Exception as exc:
            return _error("recent_memories_failed", str(exc))

    @kernel_function(name="read_memory", description="Read one durable user memory by mem:record URI or bare memory id.")
    async def read_memory(
        self,
        object_ref: Annotated[str, "Memory object ref such as mem:record:<id>, or a bare durable memory id."],
        visible_to_user: Annotated[str, "Optional boolean string filter: true|false|yes|no|1|0. Empty means user-visible only."] = "true",
        scope_filter: Annotated[str, "Scope filter: current_bundle|all_user_memories|global_only|current_bundle_or_global. Empty uses bundle config default."] = "",
        include_events: Annotated[str, "Optional boolean string. true includes recent memory evidence/update events."] = "false",
        event_limit: Annotated[int, "Maximum event rows when include_events=true. Keep small; default 5."] = 5,
    ) -> Annotated[Dict[str, Any], "Envelope. Success returns {ok:true,object_ref:'mem:record:<id>',memory:{...}}. Failure returns {ok:false,error,message}."]:
        try:
            store, scope, _raw = await self._store_and_scope()
            LOGGER.info(
                "[memory.read_memory] request object_ref=%s user_id=%s bundle_id=%s scope_filter=%s visible_to_user=%s include_events=%s event_limit=%s",
                object_ref,
                scope.user_id,
                scope.bundle_id,
                scope_filter or "",
                visible_to_user,
                include_events,
                event_limit,
            )
            disabled = await self._disabled_by_user(store, scope)
            if disabled:
                LOGGER.info(
                    "[memory.read_memory] disabled_by_user object_ref=%s user_id=%s bundle_id=%s",
                    object_ref,
                    scope.user_id,
                    scope.bundle_id,
                )
                return disabled
            memory_id = memory_id_from_ref(object_ref) or str(object_ref or "").strip()
            if not memory_id:
                LOGGER.info("[memory.read_memory] missing_object_ref object_ref=%s user_id=%s", object_ref, scope.user_id)
                return _error("object_ref_required", "Provide a mem:record:<id> object_ref or a memory id.")
            # A mem:record:<id> URI is already a fully-qualified user-memory object
            # reference. Resolve explicit refs across the user's visible memory
            # scope unless the caller deliberately narrows the scope.
            explicit_ref = str(object_ref or "").strip().startswith(("mem:", "me:"))
            default_scope_filter = "all_user_memories" if explicit_ref else self._config.default_scope_filter
            resolved_scope_filter = normalize_scope_filter(scope_filter or default_scope_filter)
            LOGGER.info(
                "[memory.read_memory] resolved object_ref=%s memory_id=%s user_id=%s bundle_id=%s scope_filter=%s",
                object_ref,
                memory_id,
                scope.user_id,
                scope.bundle_id,
                resolved_scope_filter,
            )
            record = await store.get_memory(
                scope=scope,
                memory_id=memory_id,
                visible_to_user=_bool_filter(visible_to_user) if str(visible_to_user or "").strip() else True,
                scope_filter=resolved_scope_filter,
            )
            if record is None:
                LOGGER.info(
                    "[memory.read_memory] not_found object_ref=%s memory_id=%s user_id=%s bundle_id=%s scope_filter=%s",
                    object_ref,
                    memory_id,
                    scope.user_id,
                    scope.bundle_id,
                    resolved_scope_filter,
                )
                return _error("memory_not_found", f"Memory {memory_id!r} was not found")
            payload = _record_payload(record)
            result = _ok({
                "object_ref": memory_ref(str(payload["id"])),
                "memory": payload,
                "count": 1,
            })
            if _bool_filter(include_events):
                events = await store.list_memory_events(
                    scope=scope,
                    memory_id=memory_id,
                    limit=max(1, min(int(event_limit or 5), 25)),
                    visible_to_user=_bool_filter(visible_to_user) if str(visible_to_user or "").strip() else True,
                    scope_filter=resolved_scope_filter,
                )
                result["events"] = [_event_payload(event) for event in events]
                result["events_count"] = len(events)
            LOGGER.info(
                "[memory.read_memory] success object_ref=%s memory_id=%s user_id=%s bundle_id=%s events_count=%s",
                result.get("object_ref"),
                memory_id,
                scope.user_id,
                scope.bundle_id,
                result.get("events_count", 0),
            )
            return result
        except Exception as exc:
            LOGGER.exception("[memory.read_memory] failed object_ref=%s", object_ref)
            return _error("read_memory_failed", str(exc))

    @kernel_function(
        name="record_memory",
        description=(
            "Create or update durable user memory from a durable signal. Use only after searching for "
            "existing memory; prefer match_memory_id when updating a known row. "
            "The memory text must be compact trigger first + rule. Context is only why/provenance/examples."
        ),
    )
    async def record_memory(
        self,
        memory: Annotated[str, "Compact durable memory text. Put the trigger/condition first, then the rule/fact/preference."],
        context: Annotated[str, "Why/provenance/examples/disambiguation only. Do not repeat the memory text unless needed."] = "",
        kind: Annotated[str, "Memory kind, e.g. fact, preference, decision, constraint, communication_style."] = "fact",
        event_type: Annotated[str, "Evidence event type, e.g. agent_observation, user_edit, confirmation, refinement."] = "agent_observation",
        labels: Annotated[str, "Optional comma/space separated stable facets for grouping/filtering."] = "",
        keywords: Annotated[str, "Optional comma/space separated retrieval hooks, aliases, names, and likely future terms."] = "",
        visibility: Annotated[str, "Visibility value. user/owner/public are user-visible; private/internal are not user-visible."] = "user",
        confidence: Annotated[float, "Confidence score from 0.0 to 1.0."] = 0.5,
        importance: Annotated[float, "Importance score from 0.0 to 1.0."] = 0.5,
        match_memory_id: Annotated[str, "Existing memory id to refine/update. Search first; leave empty only when creating a new memory."] = "",
    ) -> Annotated[Dict[str, Any], "Envelope. Success returns {ok:true, memory:{...}}. Failure returns {ok:false,error,message}."]:
        if not self._config.allow_write:
            return _error("memory_write_disabled", "This memory tool instance is read-only")
        try:
            store, scope, raw = await self._store_and_scope()
            disabled = await self._disabled_by_user(store, scope)
            if disabled:
                return disabled
            embedding = await self._embed_one("\n".join(part for part in [memory, context] if str(part).strip()))
            labels_list = normalize_terms(labels)
            keywords_list = normalize_terms(keywords)
            payload = {
                "memory": memory,
                "context": context,
                "kind": kind,
                "event_type": event_type,
                "labels": labels_list,
                "keywords": keywords_list,
                "visibility": visibility,
                "match_memory_id": match_memory_id,
            }
            record = await store.record_signal(
                scope=scope,
                match_memory_id=match_memory_id,
                require_match=bool(match_memory_id),
                signal=MemorySignal(
                    memory=memory,
                    context=context,
                    kind=kind,
                    event_type=event_type,
                    originator=str(raw.get("originator") or "agent"),
                    labels=labels_list,
                    keywords=keywords_list,
                    visibility=visibility,
                    confidence=confidence,
                    importance=importance,
                    embedding=embedding,
                    embedding_model=self._config.embedding_model,
                    source=_source_for_action(raw, action="record", payload=payload),
                ),
            )
            return _ok({"memory": _record_payload(record)})
        except Exception as exc:
            return _error("record_memory_failed", str(exc))

    @kernel_function(name="confirm_memory", description="Confirm an existing durable memory by id.")
    async def confirm_memory(
        self,
        memory_id: Annotated[str, "Existing durable memory id to confirm. Search first when the id is not already known."],
        note: Annotated[str, "Short confirmation note/evidence text."] = "confirmed",
        importance: Annotated[float, "Importance score for this confirmation event, from 0.0 to 1.0."] = 0.7,
    ) -> Annotated[Dict[str, Any], "Envelope. Success returns {ok:true, memory:{...}} or {ok:false,error:'memory_not_found',message}."]:
        if not self._config.allow_write:
            return _error("memory_write_disabled", "This memory tool instance is read-only")
        try:
            store, scope, raw = await self._store_and_scope()
            disabled = await self._disabled_by_user(store, scope)
            if disabled:
                return disabled
            record = await store.confirm_memory(
                scope=scope,
                memory_id=memory_id,
                note=note,
                originator=str(raw.get("originator") or "agent"),
                importance=importance,
                source=_source_for_action(raw, action="confirm", payload={"memory_id": memory_id, "note": note}),
            )
            if record is None:
                return _error("memory_not_found", f"Memory {memory_id!r} was not found")
            return _ok({"memory": _record_payload(record)})
        except Exception as exc:
            return _error("confirm_memory_failed", str(exc))

    @kernel_function(name="retire_memory", description="Retire an existing durable memory by id.")
    async def retire_memory(
        self,
        memory_id: Annotated[str, "Existing durable memory id to retire. Search first when the id is not already known."],
        reason: Annotated[str, "Short reason for retiring the memory."] = "retired",
    ) -> Annotated[Dict[str, Any], "Envelope. Success returns {ok:true, memory:{...}} or {ok:false,error:'memory_not_found',message}."]:
        if not self._config.allow_write:
            return _error("memory_write_disabled", "This memory tool instance is read-only")
        try:
            store, scope, raw = await self._store_and_scope()
            disabled = await self._disabled_by_user(store, scope)
            if disabled:
                return disabled
            record = await store.retire_memory(
                scope=scope,
                memory_id=memory_id,
                reason=reason,
                originator=str(raw.get("originator") or "agent"),
                source=_source_for_action(raw, action="retire", payload={"memory_id": memory_id, "reason": reason}),
            )
            if record is None:
                return _error("memory_not_found", f"Memory {memory_id!r} was not found")
            return _ok({"memory": _record_payload(record)})
        except Exception as exc:
            return _error("retire_memory_failed", str(exc))


def make_user_memory_tools(
    *,
    scope_provider: ScopeProvider,
    store_factory: StoreFactory | None = None,
    embedder: Embedder | None = None,
    allow_write: bool = True,
    ensure_schema_on_first_use: bool = False,
    default_scope_filter: str = "current_bundle",
    embedding_enabled: bool = True,
    embedding_timeout_seconds: float = 3.0,
    embedding_model: str = "",
) -> UserMemoryTools:
    return UserMemoryTools(
        scope_provider=scope_provider,
        store_factory=store_factory,
        embedder=embedder,
        config=UserMemoryToolConfig(
            allow_write=allow_write,
            ensure_schema_on_first_use=ensure_schema_on_first_use,
            default_scope_filter=default_scope_filter,
            embedding_enabled=embedding_enabled,
            embedding_timeout_seconds=embedding_timeout_seconds,
            embedding_model=embedding_model,
        ),
    )


def bind_registry(registry: Dict[str, Any] | None) -> None:
    global REGISTRY
    REGISTRY = dict(registry or {})


def bind_service(service: Any) -> None:
    global _SERVICE, SERVICE
    _SERVICE = service
    SERVICE = service


def _bundle_props() -> Dict[str, Any]:
    props = REGISTRY.get("bundle_props") or {}
    return props if isinstance(props, dict) else {}


def _tools_config() -> Dict[str, Any]:
    cfg = _cfg_path(_bundle_props(), "memory.tools", {})
    return cfg if isinstance(cfg, dict) else {}


def _memory_tools_enabled() -> bool:
    props = _bundle_props()
    return _truthy(_cfg_path(props, "memory.enabled"), False) and _truthy(_cfg_path(props, "memory.tools.enabled"), False)


def _runtime_scope() -> Dict[str, Any]:
    try:
        from kdcube_ai_app.apps.chat.sdk.runtime.comm_ctx import (
            get_current_bundle_id,
            get_current_comm,
            get_current_request_context,
        )
    except Exception:  # pragma: no cover - import guard for isolated test contexts
        get_current_bundle_id = lambda: None  # type: ignore
        get_current_comm = lambda: None  # type: ignore
        get_current_request_context = lambda: None  # type: ignore

    request_context = get_current_request_context()
    comm = get_current_comm()
    actor = getattr(request_context, "actor", None)
    user = getattr(request_context, "user", None)
    routing = getattr(request_context, "routing", None)
    bundle_spec = getattr(REGISTRY.get("config"), "ai_bundle_spec", None)
    bundle_id = (
        get_current_bundle_id()
        or getattr(routing, "bundle_id", None)
        or getattr(bundle_spec, "id", None)
        or ""
    )
    conversation = getattr(comm, "conversation", None)
    if not isinstance(conversation, dict):
        conversation = {}
    return {
        "pg_pool": REGISTRY.get("pg_pool"),
        "tenant": getattr(actor, "tenant_id", None) or getattr(comm, "tenant", None) or "default",
        "project": getattr(actor, "project_id", None) or getattr(comm, "project", None) or "default",
        "user_id": getattr(user, "user_id", None) or getattr(comm, "user_id", None) or "anonymous",
        "bundle_id": bundle_id,
        "conversation_id": getattr(routing, "conversation_id", None) or conversation.get("conversation_id") or "",
        "turn_id": getattr(routing, "turn_id", None) or conversation.get("turn_id") or "",
        "originator": "agent",
    }


async def _embed_texts(texts: Sequence[str]) -> Sequence[Sequence[float]]:
    service = _SERVICE or SERVICE
    if service is None or not hasattr(service, "embed_texts"):
        return []
    return await service.embed_texts(list(texts))


def _configured_tools() -> UserMemoryTools | None:
    if not _memory_tools_enabled():
        return None
    cfg = _tools_config()
    return make_user_memory_tools(
        scope_provider=_runtime_scope,
        embedder=_embed_texts,
        allow_write=_truthy(cfg.get("allow_write"), False),
        ensure_schema_on_first_use=_truthy(cfg.get("ensure_schema_on_first_use"), False),
        default_scope_filter=str(cfg.get("default_scope_filter") or "current_bundle"),
        embedding_enabled=_truthy(cfg.get("embedding_enabled"), True),
        embedding_timeout_seconds=float(cfg.get("embedding_timeout_seconds") or 3.0),
        embedding_model=str(cfg.get("embedding_model") or ""),
    )


def _disabled_error() -> Dict[str, Any]:
    return _error(
        "memory_tools_disabled",
        "Durable user memory tools are disabled for this bundle. Enable memory.enabled and memory.tools.enabled in the bundle config.",
    )


async def search_memory(
    query: Annotated[str, "Search text. Use for semantic/text lookup. Leave empty for recency/important/confirmed modes."] = "",
    mode: Annotated[str, "Search mode: hybrid|recent|recent_created|recent_events|important|confirmed|hotset."] = "hybrid",
    labels: Annotated[str, "Optional comma/space separated label filters, e.g. communication-style, project-scope."] = "",
    keywords: Annotated[str, "Optional comma/space separated keyword filters or aliases."] = "",
    kind: Annotated[str, "Optional memory kind filter, e.g. fact, preference, decision, communication_style."] = "",
    status: Annotated[str, "Memory status filter. Usually active; use any only for explicit inspection flows."] = "active",
    visible_to_user: Annotated[str, "Optional boolean string filter: true|false|yes|no|1|0. Empty means no visibility filter."] = "",
    scope_filter: Annotated[str, "Scope filter: current_bundle|all_user_memories|global_only|current_bundle_or_global. Empty uses bundle config default."] = "",
    limit: Annotated[int, "Maximum rows to return. Keep small for chat context; default 8."] = 8,
) -> Annotated[Dict[str, Any], "Envelope. Success returns {ok:true, memories:[...], count:n}; mode=recent_events returns {ok:true, events:[...], count:n}. Failure returns {ok:false,error,message}."]:
    """Search durable cross-conversation user memory for the current runtime user scope."""
    tools = _configured_tools()
    if tools is None:
        return _disabled_error()
    return await tools.search_memory(
        query=query,
        mode=mode,
        labels=labels,
        keywords=keywords,
        kind=kind,
        status=status,
        visible_to_user=visible_to_user,
        scope_filter=scope_filter,
        limit=limit,
    )


async def recent_memories(
    limit: Annotated[int, "Maximum recent memory rows to return. Keep small for chat context; default 10."] = 10,
    created: Annotated[str, "Optional boolean string. true sorts by creation time; false/default sorts by recent update."] = "",
    visible_to_user: Annotated[str, "Optional boolean string filter: true|false|yes|no|1|0. Empty means no visibility filter."] = "",
    scope_filter: Annotated[str, "Scope filter: current_bundle|all_user_memories|global_only|current_bundle_or_global. Empty uses bundle config default."] = "",
) -> Annotated[Dict[str, Any], "Envelope. Success returns {ok:true, memories:[...], count:n}. Failure returns {ok:false,error,message}."]:
    """Return recent durable user memories for the current runtime user scope."""
    tools = _configured_tools()
    if tools is None:
        return _disabled_error()
    return await tools.recent_memories(
        limit=limit,
        created=created,
        visible_to_user=visible_to_user,
        scope_filter=scope_filter,
    )


@event_source_reader(
    namespace="mem",
    event_source_id="{alias}.read_memory",
    description="Resolve a mem:record:<id> ref into the memory.read_memory event-source payload.",
)
@event_source_reader(
    namespace="me",
    event_source_id="{alias}.read_memory",
    description="Resolve a legacy me:<id> ref into the memory.read_memory event-source payload.",
)
async def read_memory_event_ref(
    *,
    ref: str,
    namespace: str = "mem",
    key: str = "",
    ctx_browser: Any = None,
    **_context: Any,
) -> Dict[str, Any]:
    object_ref = ref or (f"{namespace}:{key}" if key else "")
    return await read_memory(object_ref=object_ref, scope_filter="all_user_memories", include_events="true")


async def read_memory(
    object_ref: Annotated[str, "Memory object ref such as mem:record:<id>, or a bare durable memory id."],
    visible_to_user: Annotated[str, "Optional boolean string filter: true|false|yes|no|1|0. Empty means user-visible only."] = "true",
    scope_filter: Annotated[str, "Scope filter: current_bundle|all_user_memories|global_only|current_bundle_or_global. Empty uses bundle config default."] = "",
    include_events: Annotated[str, "Optional boolean string. true includes recent memory evidence/update events."] = "false",
    event_limit: Annotated[int, "Maximum event rows when include_events=true. Keep small; default 5."] = 5,
) -> Annotated[Dict[str, Any], "Envelope. Success returns {ok:true,object_ref:'mem:record:<id>',memory:{...}}. Failure returns {ok:false,error,message}."]:
    """Read one durable user memory for the current runtime user scope."""
    tools = _configured_tools()
    if tools is None:
        return _disabled_error()
    return await tools.read_memory(
        object_ref=object_ref,
        visible_to_user=visible_to_user,
        scope_filter=scope_filter,
        include_events=include_events,
        event_limit=event_limit,
    )


def _safe_rehost_segment(value: str, *, default: str = "memory") -> str:
    raw = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._-")
    return raw[:96] or default


@artifact_namespace_rehoster(
    namespace="mem",
    description="Materialize a mem:record:<id> ref as a JSON snapshot in the current ReAct artifact workspace.",
)
@artifact_namespace_rehoster(
    namespace="me",
    description="Materialize a legacy me:<id> memory ref as a JSON snapshot in the current ReAct artifact workspace.",
)
async def rehost_memory_ref(
    *,
    ref: str,
    namespace: str = "mem",
    key: str = "",
    ctx_browser: Any = None,
    outdir: pathlib.Path | None = None,
    **_context: Any,
) -> Dict[str, Any]:
    object_ref = str(ref or (f"{namespace}:{key}" if key else "")).strip()
    runtime = getattr(ctx_browser, "runtime_ctx", None)
    turn_id = str(getattr(runtime, "turn_id", "") or "").strip()
    if not object_ref or not turn_id or outdir is None:
        return {"missing": [{"object_ref": object_ref, "reason": "missing_ref_or_runtime"}]}

    result = await read_memory(object_ref=object_ref, scope_filter="all_user_memories", include_events="true")
    if not isinstance(result, dict) or not result.get("ok"):
        return {"missing": [{"object_ref": object_ref, "reason": str((result or {}).get("error") or "memory_not_found")}]}

    from kdcube_ai_app.apps.chat.sdk.runtime.workspace import resolve_artifact_path
    from kdcube_ai_app.apps.chat.sdk.solutions.react.artifacts import (
        ARTIFACT_NAMESPACE_SNAPSHOTS,
        build_physical_artifact_path,
        physical_path_to_logical_path,
    )

    memory_id = memory_id_from_ref(object_ref) or hashlib.sha1(object_ref.encode("utf-8")).hexdigest()[:16]
    relpath = f"mem/{_safe_rehost_segment(memory_id)}.json"
    physical_path = build_physical_artifact_path(
        turn_id=turn_id,
        namespace=ARTIFACT_NAMESPACE_SNAPSHOTS,
        relpath=relpath,
    )
    logical_path = physical_path_to_logical_path(physical_path)
    payload = json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8")
    target = resolve_artifact_path(pathlib.Path(outdir), physical_path, prefer_existing=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return {
        "materialized": [{
            "object_ref": object_ref,
            "logical_path": logical_path,
            "physical_path": physical_path,
            "scope": ARTIFACT_NAMESPACE_SNAPSHOTS,
            "mime": "application/json",
            "size_bytes": len(payload),
            "file_count": 1,
        }],
    }


async def record_memory(
    memory: Annotated[str, "Compact durable memory text. Put the trigger/condition first, then the rule/fact/preference."],
    context: Annotated[str, "Why/provenance/examples/disambiguation only. Do not repeat the memory text unless needed."] = "",
    kind: Annotated[str, "Memory kind, e.g. fact, preference, decision, constraint, communication_style."] = "fact",
    event_type: Annotated[str, "Evidence event type, e.g. agent_observation, user_edit, confirmation, refinement."] = "agent_observation",
    labels: Annotated[str, "Optional comma/space separated stable facets for grouping/filtering."] = "",
    keywords: Annotated[str, "Optional comma/space separated retrieval hooks, aliases, names, and likely future terms."] = "",
    visibility: Annotated[str, "Visibility value. user/owner/public are user-visible; private/internal are not user-visible."] = "user",
    confidence: Annotated[float, "Confidence score from 0.0 to 1.0."] = 0.5,
    importance: Annotated[float, "Importance score from 0.0 to 1.0."] = 0.5,
    match_memory_id: Annotated[str, "Existing memory id to refine/update. Search first; leave empty only when creating a new memory."] = "",
) -> Annotated[Dict[str, Any], "Envelope. Success returns {ok:true, memory:{...}}. Failure returns {ok:false,error,message}."]:
    """Create or update durable user memory when the bundle policy permits writes."""
    tools = _configured_tools()
    if tools is None:
        return _disabled_error()
    return await tools.record_memory(
        memory=memory,
        context=context,
        kind=kind,
        event_type=event_type,
        labels=labels,
        keywords=keywords,
        visibility=visibility,
        confidence=confidence,
        importance=importance,
        match_memory_id=match_memory_id,
    )


async def confirm_memory(
    memory_id: Annotated[str, "Existing durable memory id to confirm. Search first when the id is not already known."],
    note: Annotated[str, "Short confirmation note/evidence text."] = "confirmed",
    importance: Annotated[float, "Importance score for this confirmation event, from 0.0 to 1.0."] = 0.7,
) -> Annotated[Dict[str, Any], "Envelope. Success returns {ok:true, memory:{...}} or {ok:false,error:'memory_not_found',message}."]:
    """Confirm an existing durable memory by id when writes are enabled."""
    tools = _configured_tools()
    if tools is None:
        return _disabled_error()
    return await tools.confirm_memory(memory_id=memory_id, note=note, importance=importance)


async def retire_memory(
    memory_id: Annotated[str, "Existing durable memory id to retire. Search first when the id is not already known."],
    reason: Annotated[str, "Short reason for retiring the memory."] = "retired",
) -> Annotated[Dict[str, Any], "Envelope. Success returns {ok:true, memory:{...}} or {ok:false,error:'memory_not_found',message}."]:
    """Retire an existing durable memory by id when writes are enabled."""
    tools = _configured_tools()
    if tools is None:
        return _disabled_error()
    return await tools.retire_memory(memory_id=memory_id, reason=reason)


def list_tools() -> Dict[str, Dict[str, Any]]:
    return {
        "search_memory": {
            "callable": search_memory,
            "description": (
                "Search durable cross-conversation user memory. Use this for stable user facts, "
                "preferences, constraints, or durable project state, not for current-turn recovery."
            ),
        },
        "recent_memories": {
            "callable": recent_memories,
            "description": "Return recent durable user memories for the current user and configured scope.",
        },
        "read_memory": {
            "callable": read_memory,
            "description": "Read one durable user memory by mem:record URI or bare memory id.",
        },
        "record_memory": {
            "callable": record_memory,
            "description": (
                "Create or update durable user memory when policy allows writes. Search first. "
                "Memory text should be compact trigger first plus rule; context is why/provenance/examples."
            ),
        },
        "confirm_memory": {
            "callable": confirm_memory,
            "description": "Confirm an existing durable user memory by id when policy allows writes.",
        },
        "retire_memory": {
            "callable": retire_memory,
            "description": "Retire an existing durable user memory by id when policy allows writes.",
        },
    }


# Isolated tool runtimes import every registered alias as:
#   from <dynamic_module> import tools as <alias>
# This module exposes callable tools through list_tools(), so the module itself
# is the callable owner for portable runtime imports.
tools = sys.modules[__name__]
