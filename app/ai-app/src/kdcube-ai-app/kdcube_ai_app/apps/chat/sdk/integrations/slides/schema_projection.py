"""Progressive presentation capability discovery, using the shared projector."""
from kdcube_ai_app.apps.chat.sdk.integrations.slides.schema import SLIDES_ACTIONS, SLIDES_PRESENTATION

SLIDE_ACTIONS = [action for action in SLIDES_ACTIONS if action not in {"copy", "fill_template", "export", "add_slide", "import", "trash", "restore"}]


def _kinds(operation):
    if operation == "object.get":
        return ("slides.presentation", "slides.slide", "slides.export", "slides.thumbnail")
    return ("slides.presentation", "slides.slide") if operation in SLIDE_ACTIONS else ("slides.presentation",)


def _node(identity, label, description, keywords, operations):
    return {
        "id": identity, "label": label, "description": description,
        "keywords": keywords, "object_kind": "slides.presentation",
        "operations": [
            {"object_kind": kind,
             "schema_operation": operation if operation.startswith("object.") else f"object.action:{operation}",
             "label": SLIDES_PRESENTATION[operation]["label"],
             "description": SLIDES_PRESENTATION[operation]["description"]}
            for operation in operations for kind in _kinds(operation)
        ],
    }


SLIDES_SCHEMA_PROJECTION = {
    "catalog_sections": ["namespace"],
    "global_sections": ["limitations"],
    "catalog": {
        "id": "slides", "label": "Presentations",
        "description": "Explore presentation reading, construction, editing and discussion capabilities.",
        "children": [
            _node("reading", "Read and find presentations", "Inspect presentations and rendered artifacts.",
                  ["reading", "presentations", "searching", "finding", "thumbnails", "exports"],
                  ["object.get", "object.search", "object.list", "export", "thumbnail"]),
            _node("building", "Build presentations", "Create presentations from themes, templates and existing content.",
                  ["building", "creating", "templates", "copies", "copy", "importing", "duplicating"],
                  ["object.upsert", "add_slide", "duplicate_slide_like", "fill_template", "copy", "import"]),
            _node("text", "Edit and format text", "Write text, speaker notes and accessibility descriptions.",
                  ["texts", "notes", "formatting", "bold", "italic", "hyperlinks", "links", "colors", "colours"],
                  ["set_text", "style_text", "replace_text", "set_notes", "set_alt_text"]),
            _node("layout", "Arrange slide content", "Position visual content and weighted layout grids.",
                  ["layouts", "grids", "shapes", "images", "pictures", "videos", "positioning"],
                  ["compose", "add_shape", "add_image", "add_video", "add_table", "replace_shapes_with_image"]),
            _node("tables", "Edit table data", "Write cells and extend data tables.",
                  ["tables", "rows", "cells", "columns", "headers"],
                  ["set_cells", "add_row"]),
            _node("slides", "Manage slide order", "Control slide order, membership and visibility.",
                  ["slides", "moving", "ordering", "deleting", "skipping", "visibility"],
                  ["move_slide", "delete_slide", "skip_slide"]),
            _node("comments", "Discuss presentations", "Read and manage anchored discussion threads.",
                  ["comments", "commenting", "replies", "reply", "threads", "resolving", "anchors"],
                  ["list_comments", "get_comment", "create_comment", "reply_comment", "update_comment", "resolve_comment", "delete_comment"]),
            _node("lifecycle", "Manage presentation lifecycle", "Move service-created presentations into and out of trash.",
                  ["lifecycle", "trash", "restoring", "cleanup"], ["trash", "restore"]),
        ],
    },
    "kinds": {
        "slides.presentation": {
            "refs": ["presentation"], "selectors": ["slide", "element", "object_schema", "presentation"],
            "related_kinds": ["slides.slide", "slides.export", "slides.thumbnail"],
            "operations": {"object.list": {}, "object.search": {"sections": ["search"]},
                           "object.get": {"sections": ["get", "pagination"]}, "object.upsert": {}},
            "actions": list(SLIDES_ACTIONS),
        },
        "slides.slide": {
            "refs": ["slide"], "selectors": ["slide", "element", "object_schema"],
            "related_kinds": ["slides.presentation", "slides.thumbnail"],
            "operations": {"object.get": {"sections": ["get", "pagination"]}},
            "actions": SLIDE_ACTIONS,
        },
        "slides.export": {"refs": ["export"], "related_kinds": ["slides.presentation"], "operations": {"object.get": {"sections": ["materialization"]}}},
        "slides.thumbnail": {"refs": ["thumbnail"], "related_kinds": ["slides.slide"], "operations": {"object.get": {"sections": ["materialization"]}}},
    },
}
