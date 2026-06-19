# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Named-service facet for canvas boards, cards, and hosted canvas objects.

The canvas subsystem owns `cnv:` refs and the pin-board search index. This module
exposes the canvas search/upsert contract through the same named-service provider
shape used by `mem:` and `task:`. A bundle that mounts canvas can register this
provider later by passing handlers wired to its concrete canvas services.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers import (
    NamedServiceContext,
    NamedServiceProvider,
    NamedServiceProviderSpec,
    NamedServiceRequest,
    NamedServiceResponse,
    NamedServiceSearchScope,
    named_service_provider,
)
from kdcube_ai_app.apps.chat.sdk.solutions.named_services_providers.types import (
    NamedServiceOperationSpec,
    OBJECT_SCHEMA,
    OBJECT_SEARCH,
    OBJECT_UPSERT,
    PROVIDER_ABOUT,
    TRANSPORT_API,
    TRANSPORT_LOCAL,
)

from .pin_search import CANVAS_PIN_SEARCH_FILTERS


CANVAS_NAMESPACE = "cnv"
CANVAS_BOARD_OBJECT_KIND = "canvas.board"
CANVAS_CARD_OBJECT_KIND = "canvas.card"
CANVAS_OBJECT_OBJECT_KIND = "canvas.object"
CANVAS_OBJECT_KINDS = (CANVAS_BOARD_OBJECT_KIND, CANVAS_CARD_OBJECT_KIND, CANVAS_OBJECT_OBJECT_KIND)
CANVAS_PIN_OBJECT_KIND = CANVAS_CARD_OBJECT_KIND
CANVAS_PIN_PROVIDER_ID = "sdk.canvas.pins"

CANVAS_CARD_KINDS = (
    "user.text",
    "user.attachment",
    "agent.text",
    "file",
    "memory",
    "source",
    "search.result",
    "note",
    "object.ref",
    "conversation",
)

CANVAS_BOARD_SCHEMA: dict[str, Any] = {
    "object_kind": CANVAS_BOARD_OBJECT_KIND,
    "namespace": CANVAS_NAMESPACE,
    "ref_pattern": "cnv:<board-name> or cnv:<board-name>@<revision>",
    "title": "Canvas board",
    "description": "A canvas board document containing card snapshots, layout, and revision metadata.",
    "fields": {
        "canvas_name": {"type": "string", "description": "Human board name, for example main."},
        "canvas_id": {"type": "string", "description": "Storage-level board id."},
        "revision": {"type": "integer", "description": "Board revision."},
        "canvas_ref": {"type": "string", "description": "Versioned board ref such as cnv:main@7."},
        "latest_ref": {"type": "string", "description": "Latest board ref such as cnv:main."},
        "cards": {"type": "array", "description": "Board cards."},
    },
    "upsert": {
        "tool": "named_services.upsert_object",
        "object_json": {
            "object_kind": CANVAS_BOARD_OBJECT_KIND,
            "canvas_name": "main",
            "canvas": {"cards": []},
        },
        "description": "Replace/write a board document. Use base_revision when the caller must protect against concurrent edits.",
    },
}

CANVAS_CARD_SCHEMA: dict[str, Any] = {
    "object_kind": CANVAS_CARD_OBJECT_KIND,
    "namespace": CANVAS_NAMESPACE,
    "ref_pattern": "card lives inside a board; inline content is hosted as cnv:canvas/users/.../objects/...",
    "title": "Canvas card",
    "description": "A canvas card snapshot. The card may host canvas-owned content or pin an existing namespace ref.",
    "fields": {
        "card_id": {"type": "string", "description": "Canvas-local card id."},
        "kind": {
            "type": "string",
            "enum": list(CANVAS_CARD_KINDS),
            "description": "Canvas card kind. user.text/user.attachment/agent.text host content as cnv: objects; file/memory/source/search.result/object.ref/conversation usually pin an existing ref.",
        },
        "title": {"type": "string", "description": "Card title/label."},
        "summary": {"type": "string", "description": "Card summary or description when present."},
        "mime": {"type": "string", "description": "MIME type visible on the card when present."},
        "logical_path": {"type": "string", "description": "Hosted or pinned object ref, such as cnv:, fi:, mem:, task:, or so:."},
        "namespace": {"type": "string", "description": "Root namespace of the pinned object ref."},
        "board": {"type": "string", "description": "Canvas board id containing the card."},
        "score": {"type": "number", "description": "Provider-local relevance score; use returned order as rank."},
    },
    "upsert": {
        "tool": "named_services.upsert_object",
        "create_card": {
            "object_kind": CANVAS_CARD_OBJECT_KIND,
            "canvas_name": "main",
            "card": {
                "kind": "note",
                "title": "Short title",
                "mime": "text/markdown",
                "content": {"text": "Markdown text to host as cnv: content"},
            },
        },
        "update_card": {
            "object_kind": CANVAS_CARD_OBJECT_KIND,
            "canvas_name": "main",
            "card_id": "<existing-card-id>",
            "set": {"title": "Updated title"},
        },
    },
    "search": {
        "namespace": CANVAS_NAMESPACE,
        "query": "Hybrid semantic/lexical/recency search over canvas card snapshots. It does not search full source object bytes.",
        "filters": CANVAS_PIN_SEARCH_FILTERS,
        "returns": "canvas.card objects with the hosted or pinned ref in object_ref/logical_path.",
    },
    "tools": {
        "search": {"tool": "named_services.search_objects", "required": {"namespace": CANVAS_NAMESPACE, "query": "<text>"}},
        "pull_ref": {"tool": "react.pull", "required": {"paths": ["<object_ref from search result>"]}},
        "upsert": {"tool": "named_services.upsert_object", "required": {"namespace": CANVAS_NAMESPACE, "object_json": "<canvas card or board JSON>"}},
    },
}

CANVAS_OBJECT_SCHEMA: dict[str, Any] = {
    "object_kind": CANVAS_OBJECT_OBJECT_KIND,
    "namespace": CANVAS_NAMESPACE,
    "ref_pattern": "cnv:canvas/users/<user>/canvases/<board>/objects/<kind>/<card-id>/v000001.<ext>",
    "title": "Canvas-hosted object",
    "description": "Versioned bytes/text hosted by a canvas card. This ref is produced by canvas card upsert/upload; mutate the owning card instead of editing this object directly.",
    "fields": {
        "logical_path": {"type": "string", "description": "Concrete hosted object ref."},
        "mime": {"type": "string", "description": "Stored MIME type."},
        "card_id": {"type": "string", "description": "Owning card id when known."},
        "kind": {"type": "string", "description": "Storage object kind segment, for example user-text or user-attachments."},
    },
}

CANVAS_SCHEMAS: dict[str, dict[str, Any]] = {
    CANVAS_BOARD_OBJECT_KIND: CANVAS_BOARD_SCHEMA,
    CANVAS_CARD_OBJECT_KIND: CANVAS_CARD_SCHEMA,
    CANVAS_OBJECT_OBJECT_KIND: CANVAS_OBJECT_SCHEMA,
}
CANVAS_PIN_SCHEMA = CANVAS_CARD_SCHEMA

CANVAS_PIN_SEARCH_SCOPES: tuple[NamedServiceSearchScope, ...] = (
    NamedServiceSearchScope(
        namespace=CANVAS_NAMESPACE,
        label="canvas pins",
        object_kind=CANVAS_CARD_OBJECT_KIND,
        description="Search canvas card snapshots and return hosted or pinned object refs.",
        filters_schema=CANVAS_PIN_SEARCH_FILTERS,
    ),
)

_CANVAS_REQUEST_FIELDS = {
    "object_kind",
    "canvas_name",
    "canvas_id",
    "board",
    "base_revision",
    "object_ref",
}

CANVAS_PIN_SERVICE_ABOUT: dict[str, Any] = {
    "namespace": CANVAS_NAMESPACE,
    "label": "Canvas",
    "description": "Search and update canvas boards/cards. Canvas-owned board and hosted-content refs use the cnv: namespace with subnamespace path segments.",
    "object_kinds": list(CANVAS_OBJECT_KINDS),
    "refs": [
        "cnv:<board-name>",
        "cnv:<board-name>@<revision>",
        "cnv:canvas/users/<user>/canvases/<board>/objects/<kind>/<card-id>/v000001.<ext>",
    ],
    "search_scopes": [scope.to_dict() for scope in CANVAS_PIN_SEARCH_SCOPES],
    "schema_hint": "Call named_services.object_schema with namespace='cnv' for canvas board/card/object fields, search filters, and upsert payloads.",
}

CANVAS_PIN_OPERATIONS = {
    PROVIDER_ABOUT: NamedServiceOperationSpec(PROVIDER_ABOUT, (TRANSPORT_LOCAL, TRANSPORT_API)),
    OBJECT_SEARCH: NamedServiceOperationSpec(OBJECT_SEARCH, (TRANSPORT_LOCAL, TRANSPORT_API)),
    OBJECT_SCHEMA: NamedServiceOperationSpec(OBJECT_SCHEMA, (TRANSPORT_LOCAL, TRANSPORT_API)),
    OBJECT_UPSERT: NamedServiceOperationSpec(OBJECT_UPSERT, (TRANSPORT_LOCAL, TRANSPORT_API)),
}

CanvasSearchHandler = Callable[[NamedServiceContext, NamedServiceRequest], Awaitable[Mapping[str, Any]]]
CanvasUpsertHandler = Callable[[NamedServiceContext, NamedServiceRequest], Awaitable[Mapping[str, Any] | NamedServiceResponse]]
CanvasStoreFactory = Callable[[NamedServiceContext], Any]


def _provider_spec() -> NamedServiceProviderSpec:
    return NamedServiceProviderSpec(
        provider_id=CANVAS_PIN_PROVIDER_ID,
        namespace=CANVAS_NAMESPACE,
        refs=("cnv:*",),
        object_kinds=CANVAS_OBJECT_KINDS,
        search_scopes=CANVAS_PIN_SEARCH_SCOPES,
        operations=CANVAS_PIN_OPERATIONS,
        label="Canvas",
        description="Named-service provider for canvas boards, cards, hosted objects, and card search.",
    )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_result_item(item: Any, *, namespace: str) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    record = dict(item)
    object_ref = _text(record.get("object_ref") or record.get("logical_path") or record.get("ref"))
    if not object_ref:
        return None
    title = _text(record.get("title") or record.get("label") or object_ref)
    return {
        "object_ref": object_ref,
        "namespace": namespace,
        "object_kind": CANVAS_CARD_OBJECT_KIND,
        "label": title,
        "summary": _text(record.get("summary") or record.get("description")),
        "body": {
            "card_id": _text(record.get("card_id")),
            "kind": _text(record.get("kind")),
            "title": title,
            "mime": _text(record.get("mime")),
            "logical_path": object_ref,
            "namespace": _text(record.get("namespace")),
            "board": _text(record.get("board")),
        },
        "score": record.get("score"),
    }


def _payload_object_kind(request: NamedServiceRequest) -> str:
    value = _text(request.payload.get("object_kind") or request.object.get("object_kind"))
    if value == "canvas.pin":
        return CANVAS_CARD_OBJECT_KIND
    if value:
        return value
    ref = _text(request.object_ref)
    if ref.startswith("cnv:canvas/") and "/objects/" in ref:
        return CANVAS_OBJECT_OBJECT_KIND
    if ref.startswith("cnv:"):
        return CANVAS_BOARD_OBJECT_KIND
    if request.object.get("card") or request.object.get("card_id"):
        return CANVAS_CARD_OBJECT_KIND
    if request.object.get("canvas") or request.object.get("cards"):
        return CANVAS_BOARD_OBJECT_KIND
    return CANVAS_CARD_OBJECT_KIND


def _board_name_from_ref(ref: str) -> str:
    if not ref.startswith("cnv:"):
        return ""
    body = ref.split(":", 1)[1]
    if body.startswith("canvas/"):
        return ""
    return body.split("@", 1)[0].strip()


def _extract_canvas_target(request: NamedServiceRequest) -> tuple[str, str]:
    obj = request.object or {}
    payload = request.payload or {}
    canvas_name = _text(
        obj.get("canvas_name")
        or obj.get("board")
        or payload.get("canvas_name")
        or request.context.get("canvas_name")
        or _board_name_from_ref(_text(request.object_ref))
        or "main"
    )
    canvas_id = _text(
        obj.get("canvas_id")
        or payload.get("canvas_id")
        or request.context.get("canvas_id")
    )
    return canvas_name, canvas_id


def _actor_from_context(ctx: NamedServiceContext) -> str:
    actor = ctx.actor if isinstance(ctx.actor, Mapping) else {}
    return _text(actor.get("name") or actor.get("user_id") or ctx.user_id or ctx.principal_id or "agent")


def _changed_card_from_result(result: Mapping[str, Any], fallback_card_id: str = "") -> dict[str, Any]:
    changed_cards = result.get("changed_cards")
    if isinstance(changed_cards, list):
        for item in changed_cards:
            if isinstance(item, Mapping):
                return dict(item)
    canvas = result.get("canvas")
    cards = canvas.get("cards") if isinstance(canvas, Mapping) else []
    if isinstance(cards, list):
        for card in cards:
            if isinstance(card, Mapping) and (not fallback_card_id or _text(card.get("id")) == fallback_card_id):
                return dict(card)
    return {}


def _object_from_board_result(result: Mapping[str, Any]) -> dict[str, Any]:
    canvas = result.get("canvas") if isinstance(result.get("canvas"), Mapping) else {}
    return {
        "object_kind": CANVAS_BOARD_OBJECT_KIND,
        "canvas_id": _text(canvas.get("canvas_id") or result.get("canvas_id")),
        "canvas_name": _text(canvas.get("canvas_name") or result.get("canvas_name")),
        "revision": int(canvas.get("revision") or result.get("revision") or 0),
        "canvas_ref": _text(result.get("canvas_ref")),
        "latest_ref": _text(result.get("latest_ref")),
        "canvas_uri": _text(result.get("canvas_uri")),
        "cards_count": len(canvas.get("cards") or []) if isinstance(canvas.get("cards"), list) else 0,
    }


def _object_from_card_result(result: Mapping[str, Any], *, fallback_card_id: str = "") -> dict[str, Any]:
    card = _changed_card_from_result(result, fallback_card_id=fallback_card_id)
    pinned_ref = _text(card.get("logical_path") or card.get("storage_ref") or card.get("artifact_ref") or card.get("ref"))
    return {
        "object_kind": CANVAS_CARD_OBJECT_KIND,
        "canvas_ref": _text(result.get("canvas_ref")),
        "latest_ref": _text(result.get("latest_ref")),
        "canvas_uri": _text(result.get("canvas_uri")),
        "card_id": _text(card.get("id") or fallback_card_id),
        "pinned_object_ref": pinned_ref,
        "card": card,
    }


def _response_from_mapping(
    result: Mapping[str, Any],
    *,
    provider: Mapping[str, Any],
    namespace: str,
    default_object_kind: str,
    fallback_card_id: str = "",
) -> NamedServiceResponse:
    if not bool(result.get("ok", True)):
        return NamedServiceResponse.error_response(
            code=_text(result.get("error")) or "canvas_upsert_failed",
            message=_text(result.get("message")) or _text(result.get("error")) or "Canvas operation failed",
            status=int(result.get("status") or 400),
            provider=provider,
            namespace=namespace,
            details={key: value for key, value in dict(result).items() if key not in {"ok", "message"}},
        )
    if default_object_kind == CANVAS_BOARD_OBJECT_KIND:
        obj = _object_from_board_result(result)
        object_ref = _text(result.get("latest_ref") or result.get("canvas_ref") or result.get("canvas_uri"))
    else:
        obj = _object_from_card_result(result, fallback_card_id=fallback_card_id)
        object_ref = _text(obj.get("pinned_object_ref") or result.get("latest_ref") or result.get("canvas_ref") or result.get("canvas_uri"))
    return NamedServiceResponse.ok_response(
        provider=provider,
        namespace=namespace,
        object_ref=object_ref or None,
        object=obj,
        revision=_text(obj.get("revision") or (result.get("canvas") or {}).get("revision") if isinstance(result.get("canvas"), Mapping) else ""),
        attrs={
            "canvas_ref": _text(result.get("canvas_ref")),
            "latest_ref": _text(result.get("latest_ref")),
            "canvas_uri": _text(result.get("canvas_uri")),
            "noop": bool(result.get("noop")),
        },
        ui_event=result.get("ui_event") if isinstance(result.get("ui_event"), Mapping) else None,
        extra={"raw_result": dict(result)},
    )


def _raw_patch_from_object(obj: Mapping[str, Any]) -> dict[str, Any] | None:
    patch = obj.get("patch")
    if isinstance(patch, Mapping):
        return dict(patch)
    operations = obj.get("operations")
    if isinstance(operations, list):
        return {
            "schema": "kdcube.canvas.patch.v1",
            "operations": [dict(op) for op in operations if isinstance(op, Mapping)],
        }
    if obj.get("op"):
        return {
            key: value
            for key, value in dict(obj).items()
            if key not in _CANVAS_REQUEST_FIELDS
        }
    return None


@named_service_provider(
    provider_id=CANVAS_PIN_PROVIDER_ID,
    namespace=CANVAS_NAMESPACE,
    refs=("cnv:*",),
    object_kinds=CANVAS_OBJECT_KINDS,
    search_scopes=CANVAS_PIN_SEARCH_SCOPES,
    operations=CANVAS_PIN_OPERATIONS,
    label="Canvas",
    description="Named-service provider for canvas boards, cards, hosted objects, and card search.",
)
class CanvasPinSearchNamedServiceProvider(NamedServiceProvider):
    """Provider facade for canvas search and upsert.

    Handlers or a store factory are supplied by the bundle that owns the
    concrete canvas storage. Search handlers should return the existing
    `CanvasPinSearch.search` envelope. Store factories must return a
    `CanvasStore`-compatible object with `write` and `patch`.
    """

    def __init__(
        self,
        *,
        search_handler: CanvasSearchHandler | None = None,
        upsert_handler: CanvasUpsertHandler | None = None,
        store_factory: CanvasStoreFactory | None = None,
        spec: NamedServiceProviderSpec | None = None,
    ) -> None:
        super().__init__(spec or _provider_spec())
        self._search_handler = search_handler
        self._upsert_handler = upsert_handler
        self._store_factory = store_factory

    async def provider_about(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx, request
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=CANVAS_NAMESPACE,
            extra=CANVAS_PIN_SERVICE_ABOUT,
        )

    async def object_schema(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        del ctx
        object_kind = _payload_object_kind(request)
        if object_kind == "canvas.pin":
            object_kind = CANVAS_CARD_OBJECT_KIND
        if object_kind not in CANVAS_SCHEMAS:
            return NamedServiceResponse.error_response(
                code="canvas_schema_not_found",
                message=f"Canvas provider does not expose schema for {object_kind!r}",
                status=404,
                provider=self.provider_identity(),
                namespace=request.namespace or CANVAS_NAMESPACE,
                details={"object_kind": object_kind, "available_object_kinds": list(CANVAS_OBJECT_KINDS)},
            )
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or CANVAS_NAMESPACE,
            object_ref=request.object_ref,
            extra={
                "schema": CANVAS_SCHEMAS[object_kind],
                "schemas": CANVAS_SCHEMAS,
                "search_scopes": CANVAS_PIN_SERVICE_ABOUT["search_scopes"],
            },
        )

    async def object_search(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        if self._search_handler is None:
            return NamedServiceResponse.error_response(
                code="canvas_pin_search_not_configured",
                message="Canvas pin search provider is registered without a canvas search handler.",
                status=503,
                provider=self.provider_identity(),
                namespace=request.namespace or CANVAS_NAMESPACE,
            )
        result = await self._search_handler(ctx, request)
        raw_items = result.get("items") or result.get("results") if isinstance(result, Mapping) else []
        items = [
            item
            for item in (
                _normalize_result_item(raw, namespace=request.namespace or CANVAS_NAMESPACE)
                for raw in (raw_items if isinstance(raw_items, list) else [])
            )
            if item
        ]
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=request.namespace or CANVAS_NAMESPACE,
            items=items,
            attrs={
                "query": request.query,
                "count": len(items),
                "source": "canvas.card_search",
            },
            extra={"raw_result": dict(result)},
        )

    async def object_upsert(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        namespace = request.namespace or CANVAS_NAMESPACE
        object_kind = _payload_object_kind(request)
        if object_kind == CANVAS_OBJECT_OBJECT_KIND:
            return NamedServiceResponse.error_response(
                code="canvas_object_upsert_uses_card",
                message="Canvas-hosted object refs are produced by canvas.card upsert/upload. Mutate the owning card instead.",
                status=400,
                provider=self.provider_identity(),
                namespace=namespace,
                object_ref=request.object_ref,
                details={"object_kind": object_kind, "supported_object_kinds": [CANVAS_BOARD_OBJECT_KIND, CANVAS_CARD_OBJECT_KIND]},
            )
        if object_kind not in {CANVAS_BOARD_OBJECT_KIND, CANVAS_CARD_OBJECT_KIND, "canvas.pin"}:
            return NamedServiceResponse.error_response(
                code="canvas_upsert_kind_not_supported",
                message=f"Canvas upsert does not support object_kind {object_kind!r}",
                status=400,
                provider=self.provider_identity(),
                namespace=namespace,
                object_ref=request.object_ref,
                details={"supported_object_kinds": [CANVAS_BOARD_OBJECT_KIND, CANVAS_CARD_OBJECT_KIND]},
            )
        if self._upsert_handler is not None:
            response = await self._upsert_handler(ctx, request)
            if isinstance(response, NamedServiceResponse):
                return response
            return _response_from_mapping(
                response,
                provider=self.provider_identity(),
                namespace=namespace,
                default_object_kind=CANVAS_CARD_OBJECT_KIND if object_kind == "canvas.pin" else object_kind,
                fallback_card_id=_text(request.object.get("card_id") or request.object_id),
            )
        if self._store_factory is None:
            return NamedServiceResponse.error_response(
                code="canvas_upsert_not_configured",
                message="Canvas provider is registered without a canvas upsert handler or store factory.",
                status=503,
                provider=self.provider_identity(),
                namespace=namespace,
                object_ref=request.object_ref,
            )

        store = self._store_factory(ctx)
        canvas_name, explicit_canvas_id = _extract_canvas_target(request)
        canvas_id = store.canvas_id(canvas_name=canvas_name, canvas_id=explicit_canvas_id)
        base_revision = request.base_revision or request.object.get("base_revision")
        actor = _actor_from_context(ctx)

        raw_patch = _raw_patch_from_object(request.object)
        if raw_patch is not None:
            if base_revision is not None and "base_revision" not in raw_patch:
                raw_patch["base_revision"] = base_revision
            result = store.patch(canvas_name=canvas_name, canvas_id=canvas_id, patch=raw_patch, actor=actor)
            return _response_from_mapping(
                result,
                provider=self.provider_identity(),
                namespace=namespace,
                default_object_kind=CANVAS_BOARD_OBJECT_KIND if object_kind == CANVAS_BOARD_OBJECT_KIND else CANVAS_CARD_OBJECT_KIND,
                fallback_card_id=_text(request.object.get("card_id") or request.object_id),
            )

        if object_kind == CANVAS_BOARD_OBJECT_KIND:
            canvas_input = request.object.get("canvas") if isinstance(request.object.get("canvas"), Mapping) else request.object
            result = store.write(
                canvas_name=canvas_name,
                canvas_id=canvas_id,
                canvas_input=canvas_input,
                base_revision=base_revision,
            )
            return _response_from_mapping(
                result,
                provider=self.provider_identity(),
                namespace=namespace,
                default_object_kind=CANVAS_BOARD_OBJECT_KIND,
            )

        card_payload = request.object.get("card") if isinstance(request.object.get("card"), Mapping) else request.object
        card_id = _text(request.object.get("card_id") or card_payload.get("id") or request.object_id)
        if card_id:
            updates = request.object.get("set") if isinstance(request.object.get("set"), Mapping) else {
                key: value
                for key, value in dict(card_payload).items()
                if key
                not in {
                    "object_kind",
                    "canvas_name",
                    "canvas_id",
                    "board",
                    "card",
                    "card_id",
                    "content",
                    "base_revision",
                }
            }
            op: dict[str, Any] = {"op": "update_card", "card_id": card_id, "set": dict(updates or {})}
            if "content" in request.object:
                op["content"] = request.object.get("content")
            elif isinstance(card_payload, Mapping) and "content" in card_payload:
                op["content"] = card_payload.get("content")
        else:
            op = {
                "op": "new_card",
                "card": {
                    key: value
                    for key, value in dict(card_payload).items()
                    if key not in {"object_kind", "canvas_name", "canvas_id", "board", "card", "base_revision"}
                },
            }
        patch: dict[str, Any] = {"schema": "kdcube.canvas.patch.v1", "operations": [op]}
        if base_revision is not None:
            patch["base_revision"] = base_revision
        result = store.patch(canvas_name=canvas_name, canvas_id=canvas_id, patch=patch, actor=actor)
        return _response_from_mapping(
            result,
            provider=self.provider_identity(),
            namespace=namespace,
            default_object_kind=CANVAS_CARD_OBJECT_KIND,
            fallback_card_id=card_id,
        )


__all__ = [
    "CANVAS_NAMESPACE",
    "CANVAS_BOARD_OBJECT_KIND",
    "CANVAS_CARD_OBJECT_KIND",
    "CANVAS_OBJECT_OBJECT_KIND",
    "CANVAS_OBJECT_KINDS",
    "CANVAS_PIN_OBJECT_KIND",
    "CANVAS_PIN_PROVIDER_ID",
    "CANVAS_BOARD_SCHEMA",
    "CANVAS_CARD_SCHEMA",
    "CANVAS_OBJECT_SCHEMA",
    "CANVAS_SCHEMAS",
    "CANVAS_PIN_SCHEMA",
    "CANVAS_PIN_SEARCH_SCOPES",
    "CANVAS_PIN_SERVICE_ABOUT",
    "CanvasPinSearchNamedServiceProvider",
]
