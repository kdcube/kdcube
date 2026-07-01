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

from kdcube_ai_app.apps.chat.sdk.solutions.conversation.api import (
    ALLOWED_SCOPES,
    DEFAULT_TARGETS,
    SCOPE_CONVERSATION,
    SCOPE_USER,
    ConversationSearchBackend,
    ConversationSearchContext,
    ConversationSearchParams,
    run_conversation_search,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.instructions import (
    CONVERSATION_NAMESPACE_INTRO,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.read import (
    SCOPE_SELF as READ_SCOPE_SELF,
    SCOPE_USER as READ_SCOPE_USER,
    ConversationGetRequest,
    ConversationListRequest,
    ConversationReadScope,
    ConversationReadService,
    ConversationScopeError,
)
from kdcube_ai_app.apps.chat.sdk.solutions.conversation.presentation import (
    CONVERSATION_OBJECT_KIND,
    NAMESPACE,
    TURN_OBJECT_KIND as OBJECT_KIND,
    conversation_id_from_ref,
    conversation_ref,
    conversation_schema_payload,
    conversation_summary_to_object,
    conversation_to_object,
    turn_hit_to_object,
)


PROVIDER_ID = "sdk.conversation"
LOGGER = logging.getLogger("kdcube.sdk.conversation.named_service")

_CONVERSATION_TRANSPORTS = (TRANSPORT_LOCAL, TRANSPORT_API)


def _conversation_operations() -> dict:
    # Read realm: default read ops (list/search/get/schema/action/...). Reading a
    # conversation is object.get; there is no separate export operation.
    return build_default_operations(_CONVERSATION_TRANSPORTS, include_mutations=False)


_CONVERSATION_OBJECT_KINDS = (OBJECT_KIND, CONVERSATION_OBJECT_KIND)

# Advisory grant hints for the managed boundary (Connection Hub). NOT enforced
# here: the provider makes no platform-role decisions — boundary policy owns
# consent/enforcement. Selected-user access is expected to require `:any_user`.
_CONVERSATION_GRANT_HINTS = {
    "object.list": ["conversations:read"],
    "object.search": ["conversations:read"],
    "object.get": ["conversations:read"],
    "selected_user": ["conversations:read:any_user"],
}
_CONVERSATION_METADATA = {
    "viewer_surface": "sdk.conversation.viewer",
    "canonical_ref": "conv:conversation:<conversation_id>",
    "grant_hints": _CONVERSATION_GRANT_HINTS,
}
_CONVERSATION_DESCRIPTION = (
    "SDK conversation namespace provider: search what was said, and list/get/export "
    "the user's conversations (selected-user access is admin-scoped)."
)


# A backend exposing search/search_turn_catalog/get_turn_log, bound to the
# caller's tenant/project schema. Built per request from the named-service ctx.
ConversationBackendFactory = Callable[[NamedServiceContext], ConversationSearchBackend]
# Maps the named-service request context (request auth) onto the explicit
# conversation search context. This is the seam where a public/site API sets the
# context explicitly.
ConversationContextFactory = Callable[[NamedServiceContext], ConversationSearchContext]
# Builds an SDK-owned user-scoped read/export service bound to the caller's
# tenant/project. This is the seam for list/get/export (the provider never
# touches control-plane internals). Optional: when absent, list/get/export
# report the read service is not configured and search still works.
ConversationReadServiceFactory = Callable[[NamedServiceContext], ConversationReadService]


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
        "enum": [SCOPE_USER, SCOPE_CONVERSATION],
        "description": (
            "user = search across the user's conversations (default) — the right choice for "
            "external recall. conversation = restrict to a single conversation; only meaningful "
            "when the caller supplies a conversation_id it owns."
        ),
        "default": SCOPE_USER,
    },
    "from": {"type": "string", "description": "ISO timestamp. Start of the temporal window (date-window recall)."},
    "to": {"type": "string", "description": "ISO timestamp. End of the temporal window, exclusive."},
    "days": {"type": "integer", "description": "Lookback window in days. Default 365 for topic search, 3650 for temporal."},
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
        "attachment summaries — not bot-produced files. Default scope searches across the user's "
        "conversations; narrow to a single conversation by passing scope=conversation with a "
        "conversation_id."
    ),
    "search_scopes": [scope.to_dict() for scope in CONVERSATION_SEARCH_SCOPES],
}


def conversation_search_named_service_spec(*, bundle_id: str | None = None) -> NamedServiceProviderSpec:
    return NamedServiceProviderSpec(
        provider_id=PROVIDER_ID,
        bundle_id=bundle_id,
        namespace=NAMESPACE,
        refs=("conv:*",),
        object_kinds=_CONVERSATION_OBJECT_KINDS,
        search_scopes=CONVERSATION_SEARCH_SCOPES,
        # Read realm: search + list/get/export (no upsert/delete/host_file).
        operations=_conversation_operations(),
        label="Conversations",
        description=_CONVERSATION_DESCRIPTION,
        intro=CONVERSATION_NAMESPACE_INTRO,
        metadata=dict(_CONVERSATION_METADATA),
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


@named_service_provider(
    provider_id=PROVIDER_ID,
    namespace=NAMESPACE,
    refs=("conv:*",),
    object_kinds=_CONVERSATION_OBJECT_KINDS,
    search_scopes=CONVERSATION_SEARCH_SCOPES,
    operations=_conversation_operations(),
    label="Conversations",
    description=_CONVERSATION_DESCRIPTION,
    intro=CONVERSATION_NAMESPACE_INTRO,
    metadata=dict(_CONVERSATION_METADATA),
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
        context_factory: ConversationContextFactory | None = None,
        search_backend_factory: ConversationBackendFactory | None = None,
        read_service_factory: ConversationReadServiceFactory | None = None,
        bundle_id: str | None = None,
    ) -> None:
        super().__init__(conversation_search_named_service_spec(bundle_id=bundle_id))
        self._context_factory = context_factory
        self._search_backend_factory = search_backend_factory
        self._read_service_factory = read_service_factory

    @property
    def _search_enabled(self) -> bool:
        return self._context_factory is not None and self._search_backend_factory is not None

    # -- read/export scope + guards -----------------------------------------

    def _read_scope(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> ConversationReadScope:
        """Map the request onto a read scope. Default is the caller's own
        conversations; an explicit `scope.mode="user"` + `user_id` selects a user
        (an admin path the managed boundary is expected to have granted)."""
        raw = request.filters.get("scope") or request.payload.get("scope") or {}
        if not isinstance(raw, dict):
            raw = {}
        mode = _text(raw.get("mode")).lower()
        selected = _text(raw.get("user_id"))
        use_selected = mode == READ_SCOPE_USER and bool(selected)
        return ConversationReadScope(
            mode=READ_SCOPE_USER if use_selected else READ_SCOPE_SELF,
            current_user_id=_text(ctx.user_id),
            user_id=selected,
        )

    def _read_not_configured(self, request: NamedServiceRequest) -> NamedServiceResponse:
        return NamedServiceResponse.error_response(
            code="conversation_read_not_configured",
            message="This conversation provider was registered without a read/export service.",
            status=501,
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
        )

    def _scope_error(self, request: NamedServiceRequest, exc: ConversationScopeError) -> NamedServiceResponse:
        return NamedServiceResponse.error_response(
            code="conversation_scope_invalid",
            message=str(exc),
            status=400,
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
        )

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
        read_enabled = self._read_service_factory is not None
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            capabilities={
                "search": self._search_enabled,
                "list": read_enabled,
                "get": read_enabled,
                "upsert": False,
                "delete": False,
                "actions": ["preview", "describe", "capabilities"],
                "scopes": [READ_SCOPE_SELF, READ_SCOPE_USER],
                "grant_hints": _CONVERSATION_GRANT_HINTS,
            },
        )

    async def provider_schema(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            extra=conversation_schema_payload(
                grant_hints=_CONVERSATION_GRANT_HINTS,
                scopes=[READ_SCOPE_SELF, READ_SCOPE_USER],
                search_filters=CONVERSATION_SEARCH_FILTERS,
            ),
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
        if not self._search_enabled:
            return NamedServiceResponse.error_response(
                code="conversation_search_not_configured",
                message="This conversation provider was registered without a search backend; list/get/export are available.",
                status=501,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
            )
        filters = dict(request.filters or {})
        scope = _text(filters.get("scope") or SCOPE_USER).lower()
        if scope not in ALLOWED_SCOPES:
            scope = SCOPE_USER
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
            turn_hit_to_object(hit, namespace=NAMESPACE)
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
        # Conversation summaries for the resolved user. Without a read service the
        # provider is search-only, so fall back to the turn search for back-compat.
        if self._read_service_factory is None:
            return await self.object_search(ctx, request)
        try:
            scope = self._read_scope(ctx, request)
        except ConversationScopeError as exc:
            return self._scope_error(request, exc)
        filters = dict(request.filters or {})
        service = self._read_service_factory(ctx)
        list_request = ConversationListRequest(
            scope=scope,
            since=_text(filters.get("since") or filters.get("from")),
            days=_int_or_none(filters.get("days")) or 3650,
            last_n=_int_or_none(filters.get("last_n") or request.limit),
        )
        summaries = await service.list_user_conversations(list_request)
        items = [conversation_summary_to_object(summary) for summary in summaries]
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            items=items,
            extra={"scope": scope.normalized_mode, "count": len(items)},
        )

    async def object_get(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        if self._read_service_factory is None:
            return self._read_not_configured(request)
        conversation_id = conversation_id_from_ref(request.object_ref) or _text(request.object_id)
        if not conversation_id:
            return NamedServiceResponse.error_response(
                code="conversation_id_required",
                message="object_ref (conv:conversation:<id>) or object_id is required.",
                status=400,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
            )
        try:
            scope = self._read_scope(ctx, request)
        except ConversationScopeError as exc:
            return self._scope_error(request, exc)
        service = self._read_service_factory(ctx)
        record = await service.get_conversation(
            ConversationGetRequest(scope=scope, conversation_id=conversation_id)
        )
        if record is None:
            return NamedServiceResponse.error_response(
                code="conversation_not_found",
                message=f"Conversation was not found: {conversation_id}",
                status=404,
                provider=self.provider_identity(),
                namespace=request.namespace or NAMESPACE,
                object_ref=conversation_ref(conversation_id),
            )
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or NAMESPACE,
            object_ref=conversation_ref(conversation_id),
            object=conversation_to_object(record),
        )


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except Exception:
        return None


def make_conversation_search_named_service_provider(
    *,
    context_factory: ConversationContextFactory | None = None,
    search_backend_factory: ConversationBackendFactory | None = None,
    read_service_factory: ConversationReadServiceFactory | None = None,
    bundle_id: str | None = None,
) -> ConversationSearchNamedServiceProvider:
    return ConversationSearchNamedServiceProvider(
        context_factory=context_factory,
        search_backend_factory=search_backend_factory,
        read_service_factory=read_service_factory,
        bundle_id=bundle_id,
    )


__all__ = [
    "CONVERSATION_OBJECT_KIND",
    "CONVERSATION_SEARCH_FILTERS",
    "CONVERSATION_SEARCH_SCOPES",
    "ConversationReadServiceFactory",
    "ConversationSearchNamedServiceProvider",
    "NAMESPACE",
    "OBJECT_KIND",
    "PROVIDER_ID",
    "conversation_ref",
    "conversation_search_named_service_spec",
    "make_conversation_search_named_service_provider",
]
