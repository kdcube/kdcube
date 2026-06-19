# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Elena Viter
"""Pin-board search (hybrid semantic + lexical + recency) over infra.index.sqlite."""
from .pin_index import PinSearchIndex, card_to_document, card_text
from .pin_search import CANVAS_PIN_SEARCH_FILTERS, clear_pins, index_pins, search_pins, pin_index_db_path
from .service import CanvasPinSearch
from .named_service import (
    CANVAS_BOARD_OBJECT_KIND,
    CANVAS_BOARD_SCHEMA,
    CANVAS_CARD_OBJECT_KIND,
    CANVAS_CARD_SCHEMA,
    CANVAS_NAMESPACE,
    CANVAS_OBJECT_KINDS,
    CANVAS_OBJECT_OBJECT_KIND,
    CANVAS_OBJECT_SCHEMA,
    CANVAS_PIN_OBJECT_KIND,
    CANVAS_PIN_PROVIDER_ID,
    CANVAS_PIN_SCHEMA,
    CANVAS_PIN_SEARCH_SCOPES,
    CANVAS_PIN_SERVICE_ABOUT,
    CANVAS_SCHEMAS,
    CanvasPinSearchNamedServiceProvider,
)

__all__ = [
    "CanvasPinSearch",
    "CanvasPinSearchNamedServiceProvider",
    "CANVAS_PIN_SEARCH_FILTERS",
    "CANVAS_NAMESPACE",
    "CANVAS_BOARD_OBJECT_KIND",
    "CANVAS_BOARD_SCHEMA",
    "CANVAS_CARD_OBJECT_KIND",
    "CANVAS_CARD_SCHEMA",
    "CANVAS_OBJECT_KINDS",
    "CANVAS_OBJECT_OBJECT_KIND",
    "CANVAS_OBJECT_SCHEMA",
    "CANVAS_PIN_OBJECT_KIND",
    "CANVAS_PIN_PROVIDER_ID",
    "CANVAS_PIN_SCHEMA",
    "CANVAS_PIN_SEARCH_SCOPES",
    "CANVAS_PIN_SERVICE_ABOUT",
    "CANVAS_SCHEMAS",
    "PinSearchIndex",
    "card_to_document",
    "card_text",
    "clear_pins",
    "index_pins",
    "search_pins",
    "pin_index_db_path",
]
