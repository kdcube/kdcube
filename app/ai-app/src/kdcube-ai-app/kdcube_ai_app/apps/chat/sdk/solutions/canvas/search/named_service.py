# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter

"""Named-service facet for canvas boards, cards, and hosted canvas objects.

The canvas subsystem owns `cnv:` refs and the pin-board search index. This module
exposes the canvas search/upsert contract through the same named-service provider
shape used by `mem:` and `task:`. A bundle that mounts canvas can register this
provider later by passing handlers wired to its concrete canvas services.
"""

from __future__ import annotations

from dataclasses import replace
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
    OBJECT_LIST,
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
CANVAS_OPERATION_BATCH_OBJECT_KIND = "canvas.operation_batch"
CANVAS_CARD_COMMENT_OBJECT_KIND = "canvas.card.comment"
CANVAS_CARD_REPLACEMENT_OBJECT_KIND = "canvas.card.replacement"
CANVAS_CARD_DELETION_SUGGESTION_OBJECT_KIND = "canvas.card.deletion_suggestion"
CANVAS_CARD_DELETE_OBJECT_KIND = "canvas.card.delete"
CANVAS_CARD_LAYOUT_OBJECT_KIND = "canvas.card.layout"
CANVAS_OBJECT_KINDS = (
    CANVAS_BOARD_OBJECT_KIND,
    CANVAS_CARD_OBJECT_KIND,
    CANVAS_OBJECT_OBJECT_KIND,
    CANVAS_OPERATION_BATCH_OBJECT_KIND,
    CANVAS_CARD_COMMENT_OBJECT_KIND,
    CANVAS_CARD_REPLACEMENT_OBJECT_KIND,
    CANVAS_CARD_DELETION_SUGGESTION_OBJECT_KIND,
    CANVAS_CARD_DELETE_OBJECT_KIND,
    CANVAS_CARD_LAYOUT_OBJECT_KIND,
)
CANVAS_PIN_OBJECT_KIND = CANVAS_CARD_OBJECT_KIND
CANVAS_PIN_PROVIDER_ID = "sdk.canvas.pins"

CANVAS_PATCH_OPS = (
    "new_card",
    "update_card",
    "move_card",
    "resize_card",
    "replace_card",
    "suggest_deletion",
    "delete_card",
    "comment_card",
)

CANVAS_BOARD_SCHEMA: dict[str, Any] = {
    "object_kind": CANVAS_BOARD_OBJECT_KIND,
    "namespace": CANVAS_NAMESPACE,
    "ref_pattern": "cnv:<board-name> or cnv:<board-name>@<revision>",
    "title": "Canvas board",
    "description": "A canvas board document containing card snapshots, layout, and revision metadata.",
    "fields": {
        "canvas_name": {"type": "string", "description": "Human board name, for example main."},
        "canvas_id": {"type": "string", "description": "Storage-level board id returned as metadata. Do not turn it into object_ref; reuse the listed board object_ref instead."},
        "revision": {"type": "integer", "description": "Board revision."},
        "canvas_ref": {"type": "string", "description": "Versioned board ref such as cnv:main@7."},
        "latest_ref": {"type": "string", "description": "Latest board ref such as cnv:main."},
        "cards": {"type": "array", "update_strategy": "replace", "description": "Board cards. A board upsert REPLACES the entire cards array — provide the full intended set of cards, not a delta. To change individual cards without overwriting the board, use the canvas card-level operations."},
    },
    "upsert": {
        "tool": "named_services.upsert_object",
        "object_json": {
            "object_kind": CANVAS_BOARD_OBJECT_KIND,
            "canvas_name": "main",
            "canvas": {"cards": []},
        },
        "description": "Replace/write a board document. Use the object_ref returned by list/read/pull plus base_revision when the caller must protect against concurrent edits.",
    },
    "tools": {
        "list_boards": {"tool": "named_services.list_objects", "required": {"namespace": CANVAS_NAMESPACE}},
        "get_latest": {"tool": "react.pull", "required": {"paths": ["cnv:<board-name>"]}},
        "get_revision": {"tool": "react.pull", "required": {"paths": ["cnv:<board-name>@<revision>"]}},
        "replace_board": {
            "tool": "named_services.upsert_object",
            "required": {
                "namespace": CANVAS_NAMESPACE,
                "object_ref": "<board object_ref from named_services.list_objects or visible canvas context>",
                "base_revision": "<visible revision>",
                "object_json": {
                    "object_kind": CANVAS_BOARD_OBJECT_KIND,
                    "canvas_name": "<board-name>",
                    "canvas": {"cards": []},
                },
            },
        },
        "apply_atomic_operations": {
            "tool": "named_services.upsert_object",
            "required": {
                "namespace": CANVAS_NAMESPACE,
                "object_ref": "<board object_ref from named_services.list_objects or visible canvas context>",
                "base_revision": "<visible revision>",
                "object_json": {
                    "object_kind": CANVAS_OPERATION_BATCH_OBJECT_KIND,
                    "canvas_name": "<board-name>",
                    "operations": [{"op": "<canvas patch op>"}],
                },
            },
        },
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
            "description": "Source-provided card type label. Canvas preserves this value; resolver ownership is determined from logical_path/object_ref namespace and object_kind.",
        },
        "title": {"type": "string", "description": "Card title/label."},
        "summary": {"type": "string", "description": "Card summary or description when present."},
        "mime": {"type": "string", "description": "MIME type visible on the card when present."},
        "logical_path": {"type": "string", "description": "Hosted or pinned object ref, such as cnv:, fi:, mem:, task:, or so:."},
        "namespace": {"type": "string", "description": "Root namespace of the pinned object ref."},
        "board": {"type": "string", "description": "Canvas board id containing the card."},
        "score": {"type": "number", "description": "Provider-local relevance score; use returned order as rank."},
        "base_revision": {"type": "integer|string", "description": "Expected current board revision for optimistic concurrency."},
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
    "mutations": {
        "create": {
            "object_kind": CANVAS_CARD_OBJECT_KIND,
            "description": "Create a new card or pin an existing namespace ref onto a board.",
            "maps_to_patch_op": "new_card",
            "required": ["canvas_name", "card.kind", "card.title or card.logical_path"],
        },
        "update": {
            "object_kind": CANVAS_CARD_OBJECT_KIND,
            "description": "Update card-owned metadata/content. Proxy object bytes remain owned by the proxied namespace.",
            "maps_to_patch_op": "update_card",
            "required": ["canvas_name", "card_id", "set or content"],
        },
        "comment": {
            "object_kind": CANVAS_CARD_COMMENT_OBJECT_KIND,
            "description": "Append a markdown/plain-text comment to an existing card.",
            "maps_to_patch_op": "comment_card",
        },
        "replace": {
            "object_kind": CANVAS_CARD_REPLACEMENT_OBJECT_KIND,
            "description": "Suggest or apply a replacement for a card. Default mode is suggested/floating.",
            "maps_to_patch_op": "replace_card",
        },
        "suggest_deletion": {
            "object_kind": CANVAS_CARD_DELETION_SUGGESTION_OBJECT_KIND,
            "description": "Record a deletion suggestion without removing the card.",
            "maps_to_patch_op": "suggest_deletion",
        },
        "delete": {
            "object_kind": CANVAS_CARD_DELETE_OBJECT_KIND,
            "description": "Remove a card from the board.",
            "maps_to_patch_op": "delete_card",
        },
        "layout": {
            "object_kind": CANVAS_CARD_LAYOUT_OBJECT_KIND,
            "description": "UI layout operation. Agents should not use this unless explicitly asked to arrange layout.",
            "maps_to_patch_ops": ["move_card", "resize_card"],
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
        "comment": {
            "tool": "named_services.upsert_object",
            "required": {
                "namespace": CANVAS_NAMESPACE,
                "object_ref": "<board object_ref from named_services.list_objects or visible canvas context>",
                "base_revision": "<visible revision>",
                "object_json": {
                    "object_kind": CANVAS_CARD_COMMENT_OBJECT_KIND,
                    "canvas_name": "<board-name>",
                    "card_id": "<card-id>",
                    "text": "<comment text>",
                },
            },
        },
        "suggest_replacement": {
            "tool": "named_services.upsert_object",
            "required": {
                "namespace": CANVAS_NAMESPACE,
                "object_ref": "<board object_ref from named_services.list_objects or visible canvas context>",
                "base_revision": "<visible revision>",
                "object_json": {
                    "object_kind": CANVAS_CARD_REPLACEMENT_OBJECT_KIND,
                    "canvas_name": "<board-name>",
                    "card_id": "<card-id>",
                    "mode": "suggested",
                    "card": {"kind": "agent.text", "title": "<replacement title>", "content": {"text": "<markdown>"}},
                },
            },
        },
        "suggest_deletion": {
            "tool": "named_services.upsert_object",
            "required": {
                "namespace": CANVAS_NAMESPACE,
                "object_ref": "<board object_ref from named_services.list_objects or visible canvas context>",
                "base_revision": "<visible revision>",
                "object_json": {
                    "object_kind": CANVAS_CARD_DELETION_SUGGESTION_OBJECT_KIND,
                    "canvas_name": "<board-name>",
                    "card_id": "<card-id>",
                    "reason": "<why>",
                },
            },
        },
    },
}

CANVAS_OPERATION_BATCH_SCHEMA: dict[str, Any] = {
    "object_kind": CANVAS_OPERATION_BATCH_OBJECT_KIND,
    "namespace": CANVAS_NAMESPACE,
    "ref_pattern": "cnv:<board-name>",
    "title": "Canvas atomic operation batch",
    "description": "Atomic canvas board mutation batch. Use when one user intent requires multiple canvas operations in one revision.",
    "fields": {
        "canvas_name": {"type": "string", "required": True, "description": "Board name, for example main."},
        "base_revision": {"type": "integer|string", "required": True, "description": "Expected current board revision. On conflict, pull/read cnv:<board-name> again before issuing another upsert."},
        "operations": {
            "type": "array",
            "required": True,
            "items": "canvas operation object",
            "allowed_ops": list(CANVAS_PATCH_OPS),
            "update_strategy": "replace",
            "description": "Ordered operations applied in one store.patch call. This array is the complete op batch for THIS upsert — operations are applied in order against the current revision, not merged with or appended to any earlier batch. Prefer typed schemas for single card comments/replacements/deletions.",
        },
        "reason": {"type": "string", "description": "Short reason recorded in canvas history."},
    },
    "tools": {
        "apply_batch": {
            "tool": "named_services.upsert_object",
            "required": {
                "namespace": CANVAS_NAMESPACE,
                "object_ref": "<board object_ref from named_services.list_objects or visible canvas context>",
                "base_revision": "<visible revision>",
                "object_json": {
                    "object_kind": CANVAS_OPERATION_BATCH_OBJECT_KIND,
                    "canvas_name": "<board-name>",
                    "operations": [{"op": "new_card", "card": {"kind": "agent.text", "title": "<title>", "content": {"text": "<markdown>"}}}],
                },
            },
        },
    },
    "conflict_behavior": "If the provider returns canvas_revision_conflict, the mutation was not applied. Pull/read cnv:<board-name> again and compose a new upsert against the returned revision.",
}

CANVAS_CARD_COMMENT_SCHEMA: dict[str, Any] = {
    "object_kind": CANVAS_CARD_COMMENT_OBJECT_KIND,
    "namespace": CANVAS_NAMESPACE,
    "ref_pattern": "cnv:<board-name>#<card-id>/comments",
    "title": "Canvas card comment",
    "description": "Append a comment to a card without changing the proxied object.",
    "fields": {
        "canvas_name": {"type": "string", "required": True},
        "card_id": {"type": "string", "required": True},
        "text": {"type": "string", "required": True, "format": "markdown"},
        "comment_id": {"type": "string", "description": "Optional idempotent comment id."},
        "base_revision": {"type": "integer|string", "required": True},
    },
    "tools": {
        "append_comment": {
            "tool": "named_services.upsert_object",
            "required": {
                "namespace": CANVAS_NAMESPACE,
                "object_ref": "<board object_ref from named_services.list_objects or visible canvas context>",
                "base_revision": "<visible revision>",
                "object_json": {
                    "object_kind": CANVAS_CARD_COMMENT_OBJECT_KIND,
                    "canvas_name": "<board-name>",
                    "card_id": "<card-id>",
                    "text": "<comment>",
                },
            },
        },
    },
}

CANVAS_CARD_REPLACEMENT_SCHEMA: dict[str, Any] = {
    "object_kind": CANVAS_CARD_REPLACEMENT_OBJECT_KIND,
    "namespace": CANVAS_NAMESPACE,
    "ref_pattern": "cnv:<board-name>#<card-id>/replacement",
    "title": "Canvas card replacement",
    "description": "Suggest a floating replacement card or explicitly overwrite a card in place.",
    "fields": {
        "canvas_name": {"type": "string", "required": True},
        "card_id": {"type": "string", "required": True},
        "mode": {"type": "string", "enum": ["suggested", "in_place"], "default": "suggested"},
        "card": {"type": "object", "required": True, "update_strategy": "patch", "description": "Replacement card body. In_place mode shallow-merges these keys onto the existing card (top-level keys you provide overwrite; keys you omit are preserved; nested values are replaced wholesale, not deep-merged). Suggested mode (default) instead creates a new floating card from this body linked to source_card_ids."},
        "base_revision": {"type": "integer|string", "required": True},
    },
    "tools": {
        "suggest_replacement": {
            "tool": "named_services.upsert_object",
            "required": {
                "namespace": CANVAS_NAMESPACE,
                "object_ref": "<board object_ref from named_services.list_objects or visible canvas context>",
                "base_revision": "<visible revision>",
                "object_json": {
                    "object_kind": CANVAS_CARD_REPLACEMENT_OBJECT_KIND,
                    "canvas_name": "<board-name>",
                    "card_id": "<card-id>",
                    "mode": "suggested",
                    "card": {"kind": "agent.text", "title": "<title>", "content": {"text": "<markdown>"}},
                },
            },
        },
    },
}

CANVAS_CARD_DELETION_SUGGESTION_SCHEMA: dict[str, Any] = {
    "object_kind": CANVAS_CARD_DELETION_SUGGESTION_OBJECT_KIND,
    "namespace": CANVAS_NAMESPACE,
    "ref_pattern": "cnv:<board-name>#<card-id>/deletion-suggestion",
    "title": "Canvas card deletion suggestion",
    "description": "Record a deletion suggestion for user review; does not delete the card.",
    "fields": {
        "canvas_name": {"type": "string", "required": True},
        "card_id": {"type": "string", "required": True},
        "reason": {"type": "string", "description": "Why the card may be removed."},
        "base_revision": {"type": "integer|string", "required": True},
    },
    "tools": {
        "suggest_deletion": {
            "tool": "named_services.upsert_object",
            "required": {
                "namespace": CANVAS_NAMESPACE,
                "object_ref": "<board object_ref from named_services.list_objects or visible canvas context>",
                "base_revision": "<visible revision>",
                "object_json": {
                    "object_kind": CANVAS_CARD_DELETION_SUGGESTION_OBJECT_KIND,
                    "canvas_name": "<board-name>",
                    "card_id": "<card-id>",
                    "reason": "<why>",
                },
            },
        },
    },
}

CANVAS_CARD_DELETE_SCHEMA: dict[str, Any] = {
    "object_kind": CANVAS_CARD_DELETE_OBJECT_KIND,
    "namespace": CANVAS_NAMESPACE,
    "ref_pattern": "cnv:<board-name>#<card-id>/delete",
    "title": "Canvas card delete",
    "description": "Remove a card from the board. Prefer deletion suggestions unless the user explicitly asks to delete.",
    "fields": {
        "canvas_name": {"type": "string", "required": True},
        "card_id": {"type": "string", "required": True},
        "base_revision": {"type": "integer|string", "required": True},
    },
    "tools": {
        "delete_card": {
            "tool": "named_services.upsert_object",
            "required": {
                "namespace": CANVAS_NAMESPACE,
                "object_ref": "<board object_ref from named_services.list_objects or visible canvas context>",
                "base_revision": "<visible revision>",
                "object_json": {
                    "object_kind": CANVAS_CARD_DELETE_OBJECT_KIND,
                    "canvas_name": "<board-name>",
                    "card_id": "<card-id>",
                },
            },
        },
    },
}

CANVAS_CARD_LAYOUT_SCHEMA: dict[str, Any] = {
    "object_kind": CANVAS_CARD_LAYOUT_OBJECT_KIND,
    "namespace": CANVAS_NAMESPACE,
    "ref_pattern": "cnv:<board-name>#<card-id>/layout",
    "title": "Canvas card layout",
    "description": "Move or resize a card. This is primarily a UI operation; agents should not arrange cards unless the user explicitly asks.",
    "fields": {
        "canvas_name": {"type": "string", "required": True},
        "card_id": {"type": "string", "required": True},
        "op": {"type": "string", "enum": ["move_card", "resize_card"], "required": True},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "w": {"type": "number"},
        "h": {"type": "number"},
        "base_revision": {"type": "integer|string", "required": True},
    },
    "tools": {
        "move_or_resize": {
            "tool": "named_services.upsert_object",
            "required": {
                "namespace": CANVAS_NAMESPACE,
                "object_ref": "<board object_ref from named_services.list_objects or visible canvas context>",
                "base_revision": "<visible revision>",
                "object_json": {
                    "object_kind": CANVAS_CARD_LAYOUT_OBJECT_KIND,
                    "canvas_name": "<board-name>",
                    "card_id": "<card-id>",
                    "op": "move_card",
                    "x": 0,
                    "y": 0,
                },
            },
        },
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
    CANVAS_OPERATION_BATCH_OBJECT_KIND: CANVAS_OPERATION_BATCH_SCHEMA,
    CANVAS_CARD_COMMENT_OBJECT_KIND: CANVAS_CARD_COMMENT_SCHEMA,
    CANVAS_CARD_REPLACEMENT_OBJECT_KIND: CANVAS_CARD_REPLACEMENT_SCHEMA,
    CANVAS_CARD_DELETION_SUGGESTION_OBJECT_KIND: CANVAS_CARD_DELETION_SUGGESTION_SCHEMA,
    CANVAS_CARD_DELETE_OBJECT_KIND: CANVAS_CARD_DELETE_SCHEMA,
    CANVAS_CARD_LAYOUT_OBJECT_KIND: CANVAS_CARD_LAYOUT_SCHEMA,
}
CANVAS_PIN_SCHEMA = CANVAS_CARD_SCHEMA
CANVAS_MUTATION_OBJECT_KINDS = {
    CANVAS_BOARD_OBJECT_KIND,
    CANVAS_CARD_OBJECT_KIND,
    CANVAS_OPERATION_BATCH_OBJECT_KIND,
    CANVAS_CARD_COMMENT_OBJECT_KIND,
    CANVAS_CARD_REPLACEMENT_OBJECT_KIND,
    CANVAS_CARD_DELETION_SUGGESTION_OBJECT_KIND,
    CANVAS_CARD_DELETE_OBJECT_KIND,
    CANVAS_CARD_LAYOUT_OBJECT_KIND,
    "canvas.pin",
}

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
    "list_hint": "Call named_services.list_objects(namespace='cnv') to discover the user's boards. Reuse the returned board object_ref exactly; do not derive it from search results or canvas_id.",
    "search_hint": "Call named_services.search_objects(namespace='cnv', query=...) only to semantically/lexically match card snapshots by text/content. It is not board discovery.",
    "schema_hint": "Call named_services.object_schema with namespace='cnv' for canvas board/card/object fields, search filters, and upsert payloads.",
    "mutation_hint": "Use named_services.upsert_object with object_kind canvas.card, canvas.card.comment, canvas.card.replacement, canvas.card.deletion_suggestion, canvas.card.delete, canvas.card.layout, or canvas.operation_batch. Always pass the board object_ref returned by list/read/pull and base_revision when mutating a visible board.",
}

CANVAS_PIN_OPERATIONS = {
    PROVIDER_ABOUT: NamedServiceOperationSpec(PROVIDER_ABOUT, (TRANSPORT_LOCAL, TRANSPORT_API)),
    OBJECT_LIST: NamedServiceOperationSpec(OBJECT_LIST, (TRANSPORT_LOCAL, TRANSPORT_API)),
    OBJECT_SEARCH: NamedServiceOperationSpec(OBJECT_SEARCH, (TRANSPORT_LOCAL, TRANSPORT_API)),
    OBJECT_SCHEMA: NamedServiceOperationSpec(OBJECT_SCHEMA, (TRANSPORT_LOCAL, TRANSPORT_API)),
    OBJECT_UPSERT: NamedServiceOperationSpec(OBJECT_UPSERT, (TRANSPORT_LOCAL, TRANSPORT_API)),
}

CanvasListHandler = Callable[[NamedServiceContext, NamedServiceRequest], Awaitable[Mapping[str, Any]]]
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


def _board_ref(canvas_name: str) -> str:
    name = _text(canvas_name) or "main"
    return f"cnv:{name}"


def _normalize_board_item(item: Any, *, active_canvas: str = "") -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    record = dict(item)
    canvas_name = _text(record.get("canvas_name") or record.get("name"))
    canvas_id = _text(record.get("canvas_id"))
    if not canvas_name and canvas_id.startswith("cnv:"):
        body = canvas_id.split(":", 1)[1].split("@", 1)[0]
        canvas_name = _text(body.split(":", 1)[-1])
    if not canvas_name:
        return None
    try:
        latest_revision = int(record.get("latest_revision") or 0)
    except Exception:
        latest_revision = 0
    object_ref = _board_ref(canvas_name)
    versioned_ref = _text(record.get("canvas_ref")) or (f"{object_ref}@{latest_revision}" if latest_revision else "")
    return {
        "object_ref": object_ref,
        "namespace": CANVAS_NAMESPACE,
        "object_kind": CANVAS_BOARD_OBJECT_KIND,
        "label": canvas_name,
        "summary": f"Canvas board {canvas_name}" + (f" at revision {latest_revision}" if latest_revision else ""),
        "body": {
            "canvas_name": canvas_name,
            "canvas_id": canvas_id,
            "latest_revision": latest_revision,
            "canvas_ref": versioned_ref,
            "latest_ref": object_ref,
            "active": bool(active_canvas and active_canvas == canvas_name),
            "archived": bool(record.get("archived")),
            "updated_at": record.get("updated_at"),
        },
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
    name, _canvas_id = _target_from_board_ref(ref)
    return name


def _target_from_board_ref(ref: str, *, user_id: str = "") -> tuple[str, str]:
    if not ref.startswith("cnv:"):
        return "", ""
    body = ref.split(":", 1)[1].split("@", 1)[0].strip()
    if body.startswith("canvas/"):
        return "", ""
    if ":" in body:
        owner, board = body.split(":", 1)
        owner = _text(owner)
        board = _text(board)
        if owner and board and (not user_id or owner == user_id):
            return board, f"cnv:{owner}:{board}"
    return _text(body), ""


def _looks_like_sanitized_canvas_id(value: str, *, user_id: str) -> bool:
    text = _text(value).split("@", 1)[0]
    return bool(user_id and text.startswith(f"{user_id}_"))


def _target_error_response(
    *,
    provider: Mapping[str, Any],
    namespace: str,
    request: NamedServiceRequest,
    code: str,
    message: str,
    details: Mapping[str, Any],
) -> NamedServiceResponse:
    return NamedServiceResponse.error_response(
        code=code,
        message=message,
        status=400,
        provider=provider,
        namespace=namespace,
        object_ref=request.object_ref,
        details=details,
    )


def _request_with_canvas_target(
    ctx: NamedServiceContext,
    request: NamedServiceRequest,
    *,
    provider: Mapping[str, Any],
    namespace: str,
) -> tuple[NamedServiceRequest, NamedServiceResponse | None]:
    user_id = _text(ctx.user_id or request.object.get("user_id") or request.context.get("user_id"))
    ref = _text(request.object_ref)
    obj = dict(request.object or {})
    raw_canvas_name = _text(
        obj.get("canvas_name")
        or obj.get("board")
        or request.payload.get("canvas_name")
        or request.context.get("canvas_name")
    )
    raw_canvas_id = _text(
        obj.get("canvas_id")
        or request.payload.get("canvas_id")
        or request.context.get("canvas_id")
    )

    target_name = ""
    target_id = ""
    if ref.startswith("cnv:"):
        body = ref.split(":", 1)[1].split("@", 1)[0].strip()
        if _looks_like_sanitized_canvas_id(body, user_id=user_id):
            return request, _target_error_response(
                provider=provider,
                namespace=namespace,
                request=request,
                code="canvas_object_ref_not_canonical",
                message=(
                    "Canvas object_ref looks like a storage id with ':' replaced by '_'. "
                    "Use the board object_ref returned by named_services.list_objects or a visible canvas context."
                ),
                details={
                    "object_ref": ref,
                    "user_id": user_id,
                    "hint": "Use cnv:<board-name> for a board ref; keep canvas_id as metadata only.",
                },
            )
        target_name, target_id = _target_from_board_ref(ref, user_id=user_id)
        if ":" in body and not target_id and not body.startswith("canvas/"):
            return request, _target_error_response(
                provider=provider,
                namespace=namespace,
                request=request,
                code="canvas_object_ref_user_mismatch",
                message="Canvas object_ref belongs to a different user or is not a valid board ref for this context.",
                details={"object_ref": ref, "user_id": user_id},
            )

    if raw_canvas_name and _looks_like_sanitized_canvas_id(raw_canvas_name, user_id=user_id):
        return request, _target_error_response(
            provider=provider,
            namespace=namespace,
            request=request,
            code="canvas_name_not_canonical",
            message=(
                "Canvas canvas_name looks like a storage id with ':' replaced by '_'. "
                "Use the board name from list/read/pull, not a synthesized id."
            ),
            details={"canvas_name": raw_canvas_name, "user_id": user_id},
        )
    if target_name and raw_canvas_name and raw_canvas_name != target_name:
        return request, _target_error_response(
            provider=provider,
            namespace=namespace,
            request=request,
            code="canvas_target_conflict",
            message="Canvas object_ref and object_json identify different boards.",
            details={"object_ref": ref, "object_ref_canvas_name": target_name, "object_canvas_name": raw_canvas_name},
        )
    if target_id and raw_canvas_id and raw_canvas_id != target_id:
        return request, _target_error_response(
            provider=provider,
            namespace=namespace,
            request=request,
            code="canvas_target_conflict",
            message="Canvas object_ref and object_json identify different canvas ids.",
            details={"object_ref": ref, "object_ref_canvas_id": target_id, "object_canvas_id": raw_canvas_id},
        )

    changed = False
    if target_name and not raw_canvas_name:
        obj["canvas_name"] = target_name
        changed = True
    if target_id and not raw_canvas_id:
        obj["canvas_id"] = target_id
        changed = True
    if changed:
        return replace(request, object=obj), None
    return request, None


def _extract_canvas_target(request: NamedServiceRequest) -> tuple[str, str]:
    obj = request.object or {}
    payload = request.payload or {}
    canvas_name = _text(
        obj.get("canvas_name")
        or obj.get("board")
        or payload.get("canvas_name")
        or request.context.get("canvas_name")
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
        op = {
            key: value
            for key, value in dict(obj).items()
            if key not in _CANVAS_REQUEST_FIELDS
        }
        return {
            "schema": "kdcube.canvas.patch.v1",
            "operations": [op],
        }
    return None


def _patch_with_op(op: Mapping[str, Any], obj: Mapping[str, Any]) -> dict[str, Any]:
    patch: dict[str, Any] = {
        "schema": "kdcube.canvas.patch.v1",
        "operations": [dict(op)],
    }
    if obj.get("reason"):
        patch["reason"] = obj.get("reason")
    return patch


def _patch_from_typed_object(obj: Mapping[str, Any], object_kind: str) -> dict[str, Any] | None:
    card_id = _text(obj.get("card_id") or obj.get("target_card_id") or obj.get("object_id"))
    if object_kind == CANVAS_OPERATION_BATCH_OBJECT_KIND:
        raw_patch = _raw_patch_from_object(obj)
        if raw_patch is not None:
            return raw_patch
        return None
    if object_kind == CANVAS_CARD_COMMENT_OBJECT_KIND:
        op: dict[str, Any] = {
            "op": "comment_card",
            "card_id": card_id,
            "text": _text(obj.get("text") or obj.get("comment")),
        }
        if obj.get("comment_id"):
            op["comment_id"] = obj.get("comment_id")
        return _patch_with_op(op, obj)
    if object_kind == CANVAS_CARD_REPLACEMENT_OBJECT_KIND:
        replacement = obj.get("card") if isinstance(obj.get("card"), Mapping) else obj.get("replacement")
        op = {
            "op": "replace_card",
            "card_id": card_id,
            "mode": _text(obj.get("mode")) or "suggested",
            "card": dict(replacement) if isinstance(replacement, Mapping) else {},
        }
        return _patch_with_op(op, obj)
    if object_kind == CANVAS_CARD_DELETION_SUGGESTION_OBJECT_KIND:
        return _patch_with_op({
            "op": "suggest_deletion",
            "card_id": card_id,
            "reason": _text(obj.get("reason")),
        }, obj)
    if object_kind == CANVAS_CARD_DELETE_OBJECT_KIND:
        return _patch_with_op({
            "op": "delete_card",
            "card_id": card_id,
        }, obj)
    if object_kind == CANVAS_CARD_LAYOUT_OBJECT_KIND:
        op_name = _text(obj.get("op") or obj.get("operation"))
        if op_name not in {"move_card", "resize_card"}:
            op_name = "resize_card" if ("w" in obj or "h" in obj) and "x" not in obj and "y" not in obj else "move_card"
        op: dict[str, Any] = {
            "op": op_name,
            "card_id": card_id,
        }
        for key in ("x", "y", "w", "h"):
            if key in obj:
                op[key] = obj.get(key)
        return _patch_with_op(op, obj)
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
        list_handler: CanvasListHandler | None = None,
        search_handler: CanvasSearchHandler | None = None,
        upsert_handler: CanvasUpsertHandler | None = None,
        store_factory: CanvasStoreFactory | None = None,
        spec: NamedServiceProviderSpec | None = None,
    ) -> None:
        super().__init__(spec or _provider_spec())
        self._list_handler = list_handler
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

    async def object_list(self, ctx: NamedServiceContext, request: NamedServiceRequest) -> NamedServiceResponse:
        namespace = request.namespace or CANVAS_NAMESPACE
        if self._list_handler is not None:
            result = await self._list_handler(ctx, request)
        elif self._store_factory is not None:
            store = self._store_factory(ctx)
            include_archived = bool((request.filters or {}).get("include_archived"))
            result = store.list_canvases(include_archived=include_archived)
        else:
            return NamedServiceResponse.error_response(
                code="canvas_board_list_not_configured",
                message="Canvas provider is registered without a board list handler or store factory.",
                status=503,
                provider=self.provider_identity(),
                namespace=namespace,
            )
        raw_items = result.get("canvases") or result.get("items") if isinstance(result, Mapping) else []
        active_canvas = _text(result.get("active_canvas")) if isinstance(result, Mapping) else ""
        items = [
            item
            for item in (
                _normalize_board_item(raw, active_canvas=active_canvas)
                for raw in (raw_items if isinstance(raw_items, list) else [])
            )
            if item
        ]
        limit = max(1, int(request.limit or len(items) or 20))
        return NamedServiceResponse.ok_response(
            provider=self.provider_identity(),
            namespace=namespace,
            items=items[:limit],
            attrs={
                "count": min(len(items), limit),
                "total_count": len(items),
                "source": "canvas.board_list",
                "active_canvas": active_canvas,
            },
            extra={"raw_result": dict(result)},
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
                details={"object_kind": object_kind, "supported_object_kinds": sorted(CANVAS_MUTATION_OBJECT_KINDS)},
            )
        if object_kind not in CANVAS_MUTATION_OBJECT_KINDS:
            return NamedServiceResponse.error_response(
                code="canvas_upsert_kind_not_supported",
                message=f"Canvas upsert does not support object_kind {object_kind!r}",
                status=400,
                provider=self.provider_identity(),
                namespace=namespace,
                object_ref=request.object_ref,
                details={"supported_object_kinds": sorted(CANVAS_MUTATION_OBJECT_KINDS)},
            )
        request, target_error = _request_with_canvas_target(
            ctx,
            request,
            provider=self.provider_identity(),
            namespace=namespace,
        )
        if target_error is not None:
            return target_error
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

        raw_patch = _patch_from_typed_object(request.object, object_kind) or _raw_patch_from_object(request.object)
        if raw_patch is not None:
            if base_revision is not None and "base_revision" not in raw_patch:
                raw_patch["base_revision"] = base_revision
            result = store.patch(canvas_name=canvas_name, canvas_id=canvas_id, patch=raw_patch, actor=actor)
            return _response_from_mapping(
                result,
                provider=self.provider_identity(),
                namespace=namespace,
                default_object_kind=CANVAS_BOARD_OBJECT_KIND if object_kind in {CANVAS_BOARD_OBJECT_KIND, CANVAS_OPERATION_BATCH_OBJECT_KIND} else CANVAS_CARD_OBJECT_KIND,
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
    "CANVAS_OPERATION_BATCH_OBJECT_KIND",
    "CANVAS_CARD_COMMENT_OBJECT_KIND",
    "CANVAS_CARD_REPLACEMENT_OBJECT_KIND",
    "CANVAS_CARD_DELETION_SUGGESTION_OBJECT_KIND",
    "CANVAS_CARD_DELETE_OBJECT_KIND",
    "CANVAS_CARD_LAYOUT_OBJECT_KIND",
    "CANVAS_OBJECT_KINDS",
    "CANVAS_PATCH_OPS",
    "CANVAS_PIN_OBJECT_KIND",
    "CANVAS_PIN_PROVIDER_ID",
    "CANVAS_BOARD_SCHEMA",
    "CANVAS_CARD_SCHEMA",
    "CANVAS_OBJECT_SCHEMA",
    "CANVAS_OPERATION_BATCH_SCHEMA",
    "CANVAS_CARD_COMMENT_SCHEMA",
    "CANVAS_CARD_REPLACEMENT_SCHEMA",
    "CANVAS_CARD_DELETION_SUGGESTION_SCHEMA",
    "CANVAS_CARD_DELETE_SCHEMA",
    "CANVAS_CARD_LAYOUT_SCHEMA",
    "CANVAS_SCHEMAS",
    "CANVAS_MUTATION_OBJECT_KINDS",
    "CANVAS_PIN_SCHEMA",
    "CANVAS_PIN_SEARCH_SCOPES",
    "CANVAS_PIN_SERVICE_ABOUT",
    "CanvasPinSearchNamedServiceProvider",
]
