"""Structured discovery payloads for region and grid composition actions."""
from __future__ import annotations

from copy import deepcopy


def object_schema(properties, *, required=(), **extra):
    return {"type": "object", "properties": properties, "additionalProperties": False, **({"required": list(required)} if required else {}), **extra}


_TEXT = {"type": "string", "maxLength": 100000}
_ITEMS = {"type": "array", "minItems": 1, "maxItems": 1000, "items": {"type": "string", "minLength": 1, "pattern": r"^[^\n\t]+$"}}
_COLOR = {"type": "string", "enum": ["theme." + name for name in ("dark1", "light1", "dark2", "light2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hyperlink", "followed_hyperlink")]}
_ELEMENT_SELECTOR = object_schema({name: {"type": "string"} for name in ("ref", "role", "text_contains", "alt_text", "kind")} | {"index": {"type": "integer", "minimum": 1}}, minProperties=1)
_SELECTOR = {"oneOf": [{"type": "string", "minLength": 1}, _ELEMENT_SELECTOR]}
_SLIDE = {"oneOf": [{"type": "string", "minLength": 1}, object_schema({name: {"type": "string"} for name in ("ref", "title", "title_contains", "tag", "kind")} | {"position": {"type": "integer", "minimum": 1}, "last": {"const": True}}, minProperties=1)]}
_SHAPE_PROPERTIES = {
    "shape": {"enum": ["text_box", "rectangle", "round_rectangle", "ellipse"], "default": "text_box"},
    "text": _TEXT, "items": _ITEMS, "fill": _COLOR, "outline": _COLOR,
    "color": {**_COLOR, "description": "Optional text color; omission keeps the Slides editor default."},
    "font_size_pt": {"type": "number", "minimum": 1, "maximum": 400, "default": 18},
    "align": {"enum": ["start", "center", "end"], "default": "start"}, "footer": {"type": "boolean"},
}
_SHAPE = object_schema(_SHAPE_PROPERTIES, anyOf=[{"required": [name]} for name in ("shape", "text", "items")], **{"not": {"required": ["text", "items"]}})
_IMAGE_SOURCE = object_schema({"image_uri": {"type": "string", "format": "uri", "pattern": "^https://", "description": "Public HTTPS PNG, JPEG or GIF, under 50 MB and at most 25 megapixels."}, "file_ref": {"type": "string", "minLength": 1, "description": "An authorized deployment file ref; staging is bounded to 25 MB."}}, oneOf=[{"required": ["image_uri"]}, {"required": ["file_ref"]}])
_IMAGE_PROPERTIES = {**_IMAGE_SOURCE["properties"], "image": _IMAGE_SOURCE, "border": {"type": "boolean", "default": False}}
_IMAGE_CHOICE = [{"required": [name], "not": {"anyOf": [{"required": [other]} for other in ("image_uri", "file_ref", "image") if other != name]}} for name in ("image_uri", "file_ref", "image")]
_IMAGE = object_schema(_IMAGE_PROPERTIES, oneOf=_IMAGE_CHOICE)
_TABLE_SOURCE = object_schema({"rows": {"type": "integer", "minimum": 1, "maximum": 50}, "columns": {"type": "integer", "minimum": 1, "maximum": 50}, "values": {"type": "array", "minItems": 1, "maxItems": 50, "items": {"type": "array", "minItems": 1, "maxItems": 50, "items": _TEXT}}}, anyOf=[{"required": ["rows", "columns"]}, {"required": ["values"]}])
_VIDEO_SOURCE = {"oneOf": [{"type": "string", "pattern": "^https://"}, object_schema({"source": {"enum": ["youtube", "drive", "YOUTUBE", "DRIVE"]}, "id": {"type": "string", "minLength": 1}, "url": {"type": "string", "pattern": "^https://"}}, oneOf=[{"required": ["source", "id"], "not": {"required": ["url"]}}, {"required": ["url"], "not": {"required": ["id"]}}])]}
_TABLE = object_schema({"table": _TABLE_SOURCE}, required=("table",))
_VIDEO = object_schema({"video": _VIDEO_SOURCE}, required=("video",))
_WEIGHTS = {"type": "array", "minItems": 1, "maxItems": 12, "items": {"type": "number", "exclusiveMinimum": 0}, "default": [1]}
_GRID = object_schema({
    "cols": _WEIGHTS, "rows": _WEIGHTS, "gap": {"enum": ["small", "medium", "large"], "default": "medium"},
    "cells": {"type": "array", "minItems": 1, "maxItems": 100, "items": object_schema({
        "at": {"type": "array", "items": {"type": "integer", "minimum": 0}, "minItems": 2, "maxItems": 2, "description": "Zero-based [row, column]."},
        "row_span": {"type": "integer", "minimum": 1, "default": 1}, "col_span": {"type": "integer", "minimum": 1, "default": 1},
        "element": {"oneOf": [{"$ref": "#/$defs/element_selector"}, {"$ref": "#/$defs/shape"}, {"$ref": "#/$defs/image"}, {"$ref": "#/$defs/table"}, {"$ref": "#/$defs/video"}]},
        "grid": {"$ref": "#/$defs/grid"},
    }, required=("at",), oneOf=[{"required": ["element"], "not": {"required": ["grid"]}}, {"required": ["grid"], "not": {"required": ["element"]}}])},
}, required=("cells",), description="Weighted non-overlapping tracks and spans; at most three nested levels and 100 total placed elements. Spans must remain in bounds.")
_COMMON = {"slide": {**_SLIDE, "description": "One slide selector; omit when object_ref already selects a slide."}, "preview": {"type": "boolean", "default": False}, "revision_id": {"type": "string", "description": "Revision returned by a fresh object.get."}}
_PLACEMENT = {"region": {"type": "string", "default": "body", "description": "A named region listed by the slide snapshot."}, **{name: _SELECTOR for name in ("below", "above", "left_of", "right_of", "over")}, "width": {"type": "number", "exclusiveMinimum": 0, "maximum": 4, "description": "Fraction of the relative anchor width."}, "gap": {"enum": ["small", "medium", "large"]}, "align": {"enum": ["start", "center", "end"]}, "at": {"enum": ["center", "top_left", "top_center", "top_right", "center_left", "center_center", "center_right", "bottom_left", "bottom_center", "bottom_right"]}}


def composition_payload_schema(action):
    if action == "compose":
        schema = object_schema({**_COMMON, "region": _PLACEMENT["region"], **_GRID["properties"]}, required=("cells",))
        schema["$defs"] = {"grid": _GRID, "shape": _SHAPE, "image": _IMAGE, "table": _TABLE, "video": _VIDEO, "element_selector": _ELEMENT_SELECTOR}
    else:
        properties = _SHAPE_PROPERTIES if action == "add_shape" else {**_IMAGE_PROPERTIES, "element": _SELECTOR}
        schema = object_schema({**_COMMON, **_PLACEMENT, **properties})
        if action == "add_image":
            schema["oneOf"] = _IMAGE_CHOICE
        else:
            schema["not"] = {"required": ["text", "items"]}
        schema["description"] = "Choose a region or exactly one relative anchor. Image replacement uses element instead. Preview reports fit, overlaps and off-slide warnings."
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return deepcopy(schema)


_COLUMN = {"oneOf": [{"type": "integer", "minimum": 1}, {"type": "string", "minLength": 1}]}
_ROW = {"oneOf": [{"type": "integer", "minimum": 1}, object_schema({"where": object_schema({"column": _COLUMN, "equals": {"type": "string"}, "contains": {"type": "string", "minLength": 1}}, required=("column",), oneOf=[{"required": ["equals"], "not": {"required": ["contains"]}}, {"required": ["contains"], "not": {"required": ["equals"]}}])}, required=("where",))]}
_CELL = object_schema({"row": _ROW, "column": _COLUMN}, required=("row", "column"))
_TABLE_SELECTION = {"table": _SELECTOR, "header": {"type": "integer", "minimum": 0, "description": "Number of physical header rows. Set 1 to address columns by first-row text."}}


def s3_payload_schema(action):
    properties = dict(_COMMON)
    required = []
    if action == "add_table":
        properties.update({**_PLACEMENT, **_TABLE_SOURCE["properties"]})
    elif action == "add_video":
        properties.update({**_PLACEMENT, "video": _VIDEO_SOURCE})
        required = ["video"]
    elif action in ("set_cells", "add_row"):
        properties.update(_TABLE_SELECTION)
        required = ["table"]
        if action == "set_cells":
            properties["cells"] = {"type": "array", "minItems": 1, "maxItems": 500, "items": object_schema({**_CELL["properties"], "text": _TEXT}, required=("row", "column", "text"))}
            required.append("cells")
        else:
            properties.update({"before": _ROW, "after": _ROW, "values": {"oneOf": [{"type": "array", "items": _TEXT}, {"type": "object", "additionalProperties": _TEXT}]}})
    else:
        properties.update({"comment_ref": {"type": "string", "minLength": 1, "maxLength": 256}, "comment_selector": {"oneOf": [{"type": "string", "minLength": 1}, object_schema({**{k: {"type": "string"} for k in ("text_contains", "quoted_text", "quoted_text_contains", "author", "author_contains")}, "unresolved": {"type": "boolean"}, "resolved": {"type": "boolean"}, "position": {"type": "integer", "minimum": 1}}, minProperties=1)]}})
        if action in ("create_comment", "reply_comment", "update_comment"):
            properties["content"] = {"type": "string", "minLength": 1, "maxLength": 2048, "description": "At most 2048 UTF-8 bytes."}
            required = ["content"]
        if action == "resolve_comment":
            properties["content"] = {"type": "string", "minLength": 1, "maxLength": 2048, "description": "Optional native resolution reply text; defaults to Resolved. At most 2048 UTF-8 bytes."}
        if action == "create_comment":
            properties.update({**_TABLE_SELECTION, **_CELL["properties"], "element": _SELECTOR, "quoted_text": {"type": "string", "minLength": 1}, "table_range": object_schema({"from": _CELL, "to": _CELL}, required=("from", "to"))})
            properties.pop("comment_ref")
            properties.pop("comment_selector")
        if action == "list_comments":
            properties = {"slide": _COMMON["slide"], "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "cursor": {"type": "string"}, "include_resolved": {"type": "boolean", "default": False}}
    schema = object_schema(properties, required=required)
    if action == "add_table":
        schema["anyOf"] = _TABLE_SOURCE["anyOf"]
    if action == "add_row":
        schema["not"] = {"required": ["before", "after"]}
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return deepcopy(schema)
