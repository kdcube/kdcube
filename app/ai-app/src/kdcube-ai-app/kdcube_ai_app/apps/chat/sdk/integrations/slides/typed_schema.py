"""Typed MCP payload discovery, reusing the named-action schemas."""
from copy import deepcopy

from kdcube_ai_app.apps.chat.sdk.integrations.slides.action_schemas import object_schema
from kdcube_ai_app.apps.chat.sdk.integrations.slides.schema import SLIDES_ACTIONS, SLIDES_SCHEMA

TEXT = {"type": "string", "maxLength": 100000}
SELECTOR = {"oneOf": [{"type": "string", "minLength": 1}, {"type": "object", "minProperties": 1}]}
COMMON = {"preview": {"type": "boolean"}, "revision_id": {"type": "string"}}
SLIDE = {"slide": SELECTOR}
ELEMENT = {**SLIDE, "element": SELECTOR}
ITEMS = {"type": "array", "maxItems": 1000, "items": {"type": "string"}}


def payload_schema(operation):
    published = SLIDES_SCHEMA["actions"].get(operation, {}).get("payload_schema")
    if published:
        return deepcopy(published)
    properties = dict(COMMON)
    required = []
    if operation == "search":
        properties = {"query": {"type": "string", "maxLength": 300}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}, "cursor": {"type": "string"}}
    elif operation == "get":
        properties = {"slide_selector": SELECTOR, "start_slide": {"type": "integer", "minimum": 1}, "slide_limit": {"type": "integer", "minimum": 1, "maximum": 50}, "table_start_row": {"type": "integer", "minimum": 1}, "table_row_limit": {"type": "integer", "minimum": 1, "maximum": 50}}
    elif operation in ("create", "rename"):
        properties["title"] = {"type": "string", "minLength": 1, "maxLength": 1000}
        required = ["title"]
    elif operation in ("copy", "fill_template"):
        properties.update({"source": {"type": "string"}, "title": {"type": "string", "minLength": 1}, "parent_id": {"type": "string"}})
        required = ["title"]
        if operation == "fill_template":
            properties.update({"values": {"type": "object", "additionalProperties": TEXT}, "images": {"type": "object", "additionalProperties": {"oneOf": [TEXT, {"type": "object"}]}}})
    elif operation == "export":
        properties = {"format": {"enum": ["pdf", "pptx"]}}
    elif operation == "thumbnail":
        properties = {**SLIDE, "size": {"enum": ["SMALL", "MEDIUM", "LARGE"]}}
        required = ["slide"]
    elif operation == "replace_text":
        properties.update({**ELEMENT, "slides": {"type": "array", "items": SELECTOR}, "replacements": {"type": "object", "minProperties": 1, "additionalProperties": TEXT}})
        required = ["replacements"]
    elif operation == "replace_shapes_with_image":
        properties.update({**SLIDE, "slides": {"type": "array", "items": SELECTOR}, "tag": TEXT, "text": TEXT, "image_uri": {"type": "string"}, "file_ref": {"type": "string"}, "fit": {"enum": ["CENTER_INSIDE", "CENTER_CROP"]}})
    elif operation == "add_slide":
        properties.update({"layout": TEXT, "layout_name": TEXT, **{key: {"oneOf": [TEXT, ITEMS]} for key in ("title", "subtitle", "body", "body_2")}, "notes": TEXT, "before": SELECTOR, "after": SELECTOR, "keep_empty_placeholders": {"type": "boolean"}})
    elif operation in ("set_text", "style_text", "set_alt_text"):
        properties.update(ELEMENT)
        if operation == "set_text":
            properties.update({"text": TEXT, "items": ITEMS, "mode": {"enum": ["replace", "append"]}})
        elif operation == "style_text":
            properties.update({"quoted_text": TEXT, "style": object_schema({"bold": {"type": "boolean"}, "italic": {"type": "boolean"}, "font_size_pt": {"type": "number"}, "color": TEXT, "link": TEXT}, minProperties=1)})
            required = ["element", "quoted_text", "style"]
        else:
            properties.update({"title": TEXT, "description": TEXT})
    elif operation == "set_notes":
        properties.update({**SLIDE, "notes": TEXT, "mode": {"enum": ["replace", "append"]}})
        required = ["notes"]
    elif operation in ("move_slide", "delete_slide", "skip_slide"):
        properties.update({**SLIDE, "slides": {"type": "array", "minItems": 1, "items": SELECTOR}})
        if operation == "move_slide":
            properties.update({"before": SELECTOR, "after": SELECTOR})
        if operation == "skip_slide":
            properties["skipped"] = {"type": "boolean"}
    elif operation == "duplicate_slide_like":
        properties.update({"like": SELECTOR, "before": SELECTOR, "after": SELECTOR, "replacements": {"type": "object"}, "notes": TEXT})
        required = ["like"]
    elif operation == "import":
        properties.update({"source_file": TEXT, "file_ref": TEXT, "title": TEXT, "parent_id": TEXT})
    elif operation not in ("trash", "restore"):
        raise ValueError(f"Unknown typed Slides operation: {operation}")
    return object_schema(properties, required=required)


TYPED_OPERATIONS = ("search", "get", "create", "rename", *SLIDES_ACTIONS)


def inline_schema(schema):
    """Embed standalone action schemas in a tool payload without root ref drift.

    Grid depth remains enforced by the shared compiler; discovery expands its
    recursive definition through the same three nested levels.
    """
    definitions = schema.get("$defs", {})
    def expand(value, grid_depth=0):
        if isinstance(value, list):
            return [expand(child, grid_depth) for child in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            key = value["$ref"].rsplit("/", 1)[-1]
            depth = grid_depth + (key == "grid")
            if depth > 3:
                return {"type": "object", "description": "The compiler enforces at most three nested grids."}
            return expand(definitions[key], depth)
        return {key: expand(child, grid_depth) for key, child in value.items() if key not in ("$defs", "$schema")}
    return expand(deepcopy(schema))
