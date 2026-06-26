# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Conversation search as a named-service provider.

A SEARCH provider over the conversation memory realm. It mirrors the structural
shape of the memory provider (`context/memory/named_service.py`): a
`@named_service_provider(...)`-decorated class, a `NamedServiceProviderSpec`
factory, search scopes, and `intro=CONVERSATION_NAMESPACE_INTRO`. Unlike memory
it is read-only — conversations are recovered, never written through this
namespace — so it advertises only read operations.

Backed by `api.run_conversation_search`. Identity is EXPLICIT: the registering
bundle supplies a `context_factory` (builds a `ConversationSearchContext` from
the named-service `NamedServiceContext` — i.e. from request auth) and a
`search_backend_factory` (builds a backend bound to the caller's tenant/project
schema). This is what lets a future public/site API call the same search by
SETTING the context, rather than relying on ambient runtime state.

Registration: a bundle that service-provides `conv` constructs
`make_conversation_search_named_service_provider(...)` (or instantiates the
class) the same way it constructs the memory provider; the decorator metadata
(`__kdcube_named_service_provider__`) lets the registry pick it up like the
others. This module is defined-but-not-yet-connected: it is intentionally NOT
wired into any bundle.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceProvider,
    NamedServiceProviderSpec,
    NamedServiceRequest,
    NamedServiceResponse,
    NamedServiceSearchScope,
    TRANSPORT_API,
    TRANSPORT_LOCAL,
    build_default_operations,
    named_service_provider,
)

from .api import (
    ALLOWED_SCOPES,
    DEFAULT_TARGETS,
    SCOPE_CONVERSATION,
    SCOPE_USER,
    ConversationSearchBackend,
    ConversationSearchContext,
    ConversationSearchParams,
    run_conversation_search,
)
from .instructions import (
    CONVERSATION_NAMED_SERVICE_NAMESPACE,
    CONVERSATION_NAMESPACE_INTRO,
)


PROVIDER_ID = "sdk.conversation"
NAMESPACE = CONVERSATION_NAMED_SERVICE_NAMESPACE  # "conv"
OBJECT_KIND = "conversation.turn"
TURN_MIME = "application/vnd.kdcube.conversation.turn+json;version=1"
NAMED_SERVICE_OBJECT_SCHEMA = "kdcube.named_service.object.v1"
LOGGER = logging.getLogger("kdcube.sdk.conversation.named_service")

# A backend exposing search/search_turn_catalog/get_turn_log, bound to the
# caller's tenant/project schema. Built per request from the named-service ctx.
ConversationBackendFactory = Callable[[NamedServiceContext], ConversationSearchBackend]
# Maps the named-service request context (request auth) onto the explicit
# conversation search context. This is the seam where a public/site API sets the
# context explicitly.
ConversationContextFactory = Callable[[NamedServiceContext], ConversationSearchContext]


CONVERSATION_SEARCH_FILTERS: dict[str, Any] = {
    "targets": {
        "type": "string|array",
        "description": (
            "Which conversation content to search. Any of: assistant (replies and "
            "working summaries), user (prompts and follow-ups), attachment (the user's "
            "uploaded attachment summaries), summary (working summaries only), notes "
            "(the assistant's internal notes). Defaults to assistant+user+attachment+summary."
        ),
        "examples": list(DEFAULT_TARGETS),
    },
    "scope": {
        "type": "string",
        "enum": [SCOPE_CONVERSATION, SCOPE_USER],
        "description": (
            "conversation = search only the current conversation (default). "
            "user = also search the same user's other conversations — required for "
            "cross-conversation recall."
        ),
        "default": SCOPE_CONVERSATION,
    },
    "from": {"type": "string", "description": "ISO timestamp. Start of the temporal window."},
    "to": {"type": "string", "description": "ISO timestamp. End of the temporal window, exclusive."},
    "ordinal": {"type": "integer", "description": "1-based turn ordinal in the selected scope/window (catalog lookup, no query)."},
    "order": {"type": "string", "enum": ["asc", "desc"], "description": "Order for catalog results. Default asc.", "default": "asc"},
    "days": {"type": "integer", "description": "Lookback window in days. Default 365 for topic search, 3650 for catalog/temporal."},
    "include_recovery_sessions": {
        "type": "boolean",
        "description": (
            "Default false. Working summaries from turns that only searched/read prior "
            "memory (no new artifact produced) are excluded by default. Set true only to "
            "introspect prior search activity."
        ),
        "default": False,
    },
}

CONVERSATION_SEARCH_SCOPES: tuple[NamedServiceSearchScope, ...] = (
    NamedServiceSearchScope(
        namespace=NAMESPACE,
        label="conversation turns",
        object_kind=OBJECT_KIND,
        description=(
            "Search the conversation memory realm — what the user said, what the assistant "
            "said, and the user's uploaded attachment summaries. Returns turn-level recovery "
            "handles (paths to read or pull). Empty query is valid for ordinal/temporal/timeline "
            "catalog lookups."
        ),
        filters_schema=CONVERSATION_SEARCH_FILTERS,
    ),
)

SERVICE_ABOUT: dict[str, Any] = {
    "title": "KDCube Conversation Search",
    "description": (
        "Read-only search over the user's conversation memory realm: what was actually said "
        "(user prompts/follow-ups, assistant replies/working summaries) and the user's uploaded "
        "attachment summaries — not bot-produced files. Default scope is the current conversation; "
        "the user scope widens to the same user's other conversations."
    ),
    "search_scopes": [scope.to_dict() for scope in CONVERSATION_SEARCH_SCOPES],
}


def conversation_search_named_service_spec(*, bundle_id: str | None = None) -> NamedServiceProviderSpec:
    return NamedServiceProviderSpec(
        provider_id=PROVIDER_ID,
        bundle_id=bundle_id,
        namespace=NAMESPACE,
        refs=("conv:*",),
        object_kinds=(OBJECT_KIND,),
        search_scopes=CONVERSATION_SEARCH_SCOPES,
        # Search realm: read operations only (no upsert/delete/host_file).
        operations=build_default_operations((TRANSPORT_LOCAL, TRANSPORT_API), include_mutations=False),
        label="Conversations",
        description="SDK conversation namespace provider for searching what was said across the user's conversations.",
        intro=CONVERSATION_NAMESPACE_INTRO,
        metadata={"viewer_surface": "sdk.conversation.viewer"},
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _clamp_limit(value: Any, *, default: int = 5, max_value: int = 50) -> int:
    try:
        parsed = int(value if value is not None else default)
    except Exception:
        parsed = default
    return max(1, min(parsed, max_value))


def _normalize_targets(value: Any) -> list[str]:
    if value in (None, ""):
        return list(DEFAULT_TARGETS)
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(part).strip() for part in value]
    else:
        return list(DEFAULT_TARGETS)
    targets = [item for item in items if item]
    return targets or list(DEFAULT_TARGETS)


def _conversation_turn_to_named_service_object(hit: dict[str, Any], *, namespace: str) -> dict[str, Any]:
    """Shape one rich API hit into a named-service search object.

    The hit carries turn identity + snippets. The object's primary text is a
    compact preview drawn from the first snippet; full snippet content is kept in
    the body so callers can read it without an extra fetch.
    """
    turn_id = _text(hit.get("turn_id"))
    conversation_id = _text(hit.get("conversation_id"))
    snippets = [sn for sn in (hit.get("snippets") or []) if isinstance(sn, dict)]
    first_text = ""
    for sn in snippets:
        text = _text(sn.get("text"))
        if text:
            first_text = text
            break
    ref = f"conv:turn:{turn_id}" if turn_id else ""
    obj = {
        "schema": NAMED_SERVICE_OBJECT_SCHEMA,
        "ref": ref,
        "namespace": namespace,
        "object_kind": OBJECT_KIND,
        "label": (first_text[:120] or turn_id),
        "title": (first_text[:120] or turn_id),
        "summary": first_text[:500],
        "mime": TURN_MIME,
        "identity": {
            "object_ref": ref,
            "object_id": turn_id,
            "object_kind": OBJECT_KIND,
            "namespace": namespace,
        },
        "body": {
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "turn_index_path": hit.get("turn_index_path"),
            "snippets": [
                {key: sn.get(key) for key in ("role", "path", "text", "ts") if sn.get(key) not in (None, "")}
                for sn in snippets
            ],
            "ordinal": hit.get("ordinal"),
            "total_turns": hit.get("total_turns"),
        },
    }
    score = hit.get("score")
    if score is not None:
        obj["score"] = float(score)
        obj["rank_score"] = float(score)
    return {key: value for key, value in obj.items() if value not in (None, "", [])}


@named_service_provider(
    provider_id=PROVIDER_ID,
    namespace=NAMESPACE,
    refs=("conv:*",),
    object_kinds=(OBJECT_KIND,),
    search_scopes=CONVERSATION_SEARCH_SCOPES,
    operations=build_default_operations((TRANSPORT_LOCAL, TRANSPORT_API), include_mutations=False),
    label="Conversations",
    description="SDK conversation namespace provider for searching what was said across the user's conversations.",
    intro=CONVERSATION_NAMESPACE_INTRO,
    metadata={"viewer_surface": "sdk.conversation.viewer"},
)
class ConversationSearchNamedServiceProvider(NamedServiceProvider):
    """Read-only named-service provider over the conversation memory realm.

    Identity is explicit. The registering bundle supplies:
      * `context_factory(ns_ctx) -> ConversationSearchContext` — turns the
        named-service request context (request auth) into the explicit search
        context (user_id, conversation_id, bundle_id, tenant/project provenance).
      * `search_backend_factory(ns_ctx) -> ConversationSearchBackend` — a search
        backend bound to the caller's tenant/project-derived schema.
    """

    def __init__(
        self,
        *,
        context_factory: ConversationContextFactory,
        search_backend_factory: ConversationBackendFactory,
        bundle_id: str | None = None,
    ) -> None:
        super().__init__(conversation_search_named_service_spec(bundle_id=bundle_id))
        self._context_factory = context_factory
        self._search_backend_factory = search_backend_factory

    async def provider_about(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx, request
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=NAMESPACE,
            extra={
                **SERVICE_ABOUT,
                "schema_hint": (
                    "Call named_services.search_objects(namespace='conv', query='<text>', "
                    "filters={'targets': [...], 'scope': 'conversation'|'user'}) to recover turns. "
                    "Empty query with ordinal/from/to does a deterministic catalog lookup."
                ),
            },
        )

    async def provider_capabilities(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            capabilities={
                "search": True,
                "get": False,
                "upsert": False,
                "delete": False,
                "actions": ["preview", "describe", "capabilities"],
            },
        )

    async def object_search(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        namespace = _text(request.namespace).lower().rstrip(":") or NAMESPACE
        if namespace != NAMESPACE:
            return NamedServiceResponse.error_response(
                code="conversation_search_scope_not_found",
                message=f"Unsupported conversation search scope: {namespace}. Search with namespace='conv'.",
                status=404,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
            )
        filters = dict(request.filters or {})
        scope = _text(filters.get("scope") or SCOPE_CONVERSATION).lower()
        if scope not in ALLOWED_SCOPES:
            scope = SCOPE_CONVERSATION
        params = ConversationSearchParams(
            query=_text(request.query),
            targets=_normalize_targets(filters.get("targets")),
            scope=scope,
            from_ts=_text(filters.get("from") or filters.get("from_ts")),
            to_ts=_text(filters.get("to") or filters.get("to_ts")),
            ordinal=_int_or_none(filters.get("ordinal")),
            order=_text(filters.get("order") or "asc").lower() or "asc",
            top_k=_clamp_limit(request.limit, default=5, max_value=50),
            days=_int_or_none(filters.get("days")),
            mode=_text(filters.get("mode") or request.search_mode),
            include_recovery_sessions=bool(filters.get("include_recovery_sessions")),
        )
        context = self._context_factory(ctx)
        backend = self._search_backend_factory(ctx)
        LOGGER.info(
            "[conversation.named_service.search] namespace=%s query=%r scope=%s targets=%s user_id=%s conversation_id=%s",
            namespace,
            params.query,
            params.scope,
            params.targets,
            context.user_id,
            context.conversation_id,
        )
        result = await run_conversation_search(
            context=context,
            params=params,
            search_backend=backend,
        )
        if result.missing_query:
            return NamedServiceResponse.error_response(
                code="conversation_query_required",
                message="A query is required for topic search; pass ordinal/from/to for a catalog lookup.",
                status=400,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
            )
        items = [
            _conversation_turn_to_named_service_object(hit, namespace=NAMESPACE)
            for hit in result.hits
            if isinstance(hit, dict) and _text(hit.get("turn_id"))
        ]
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            items=items,
            warnings=[{"code": "conversation_search_warning", "message": w} for w in result.warnings] or None,
            extra={
                "search_scope": namespace,
                "query": params.query,
                "mode": result.effective_mode,
                "scope": params.scope,
                "tokens": result.tokens,
            },
        )

    async def object_list(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        return await self.object_search(ctx, request)


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def make_conversation_search_named_service_provider(
    *,
    context_factory: ConversationContextFactory,
    search_backend_factory: ConversationBackendFactory,
    bundle_id: str | None = None,
) -> ConversationSearchNamedServiceProvider:
    return ConversationSearchNamedServiceProvider(
        context_factory=context_factory,
        search_backend_factory=search_backend_factory,
        bundle_id=bundle_id,
    )


__all__ = [
    "CONVERSATION_SEARCH_FILTERS",
    "CONVERSATION_SEARCH_SCOPES",
    "ConversationSearchNamedServiceProvider",
    "NAMESPACE",
    "OBJECT_KIND",
    "PROVIDER_ID",
    "conversation_search_named_service_spec",
    "make_conversation_search_named_service_provider",
]
