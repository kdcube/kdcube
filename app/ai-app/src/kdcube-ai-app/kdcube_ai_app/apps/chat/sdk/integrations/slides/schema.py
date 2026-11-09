"""Discovery metadata and connected-account requirements for presentations."""

from kdcube_ai_app.apps.chat.sdk.integrations.slides.action_schemas import composition_payload_schema, s3_payload_schema
from kdcube_ai_app.apps.chat.sdk.integrations.slides.comments import COMMENT_ACTIONS, READ_COMMENTS, WRITE_COMMENTS

SLIDES_NAMESPACE = "slides"
SLIDES_READ_CLAIM = "slides:read"
SLIDES_WRITE_CLAIM = "slides:write"
SLIDES_DELETE_CLAIM = "slides:delete"
SLIDES_COMMENT_CLAIM = "slides:comment"
SLIDES_TRANSPORTS = ("local", "api")
SLIDES_ACTIONS = ("export", "thumbnail", "copy", "fill_template", "replace_text", "replace_shapes_with_image", "add_slide", "set_text", "style_text", "set_notes", "move_slide", "delete_slide", "skip_slide", "compose", "add_shape", "add_image", "set_alt_text", "duplicate_slide_like", "import", "trash", "restore", "add_table", "set_cells", "add_row", "add_video", "list_comments", "get_comment", "create_comment", "reply_comment", "update_comment", "resolve_comment", "delete_comment")
SLIDES_INTRO = "Find a deck with object.search, then object.get its snapshot and revision_id. Address slides by position, title, tag, kind or a returned ref; address elements by role, text, kind/index or ref. Add slides using a listed theme layout, edit text or notes, style a quoted span and reorder slides. Build tables using row/column selectors, place YouTube or Drive videos and comment on slides, text or cells. Pass revision_id when editing, use preview and inspect fit warnings. Export PDF/PPTX or request a thumbnail for visual checks. Prefer font-supported text such as n or n_i to Unicode subscript glyphs such as ₙ; inspect the rendered result."
_DESCRIPTIONS = {
    "provider.about": ("About presentations", "Learn how to use connected presentations."),
    "provider.capabilities": ("Presentation capabilities", "List supported presentation operations."),
    "object.schema": ("Presentation schema", "Read the presentation vocabulary and selectors."),
    "object.search": ("Find presentations", "Find Google Slides presentations by title."),
    "object.list": ("Recent presentations", "List recent Google Slides presentations."),
    "object.get": ("Read a presentation", "Read a presentation snapshot or download an exported file."),
    "object.upsert": ("Create or rename a presentation", "Create an empty deck or change a deck's title."),
    "object.action": ("Act on a presentation", "Run a selected presentation action."),
    "export": ("Export a presentation", "Download a presentation as PDF or PowerPoint."),
    "thumbnail": ("Preview a slide", "Render one slide as a PNG image."),
    "copy": ("Copy a presentation", "Create a copy of a presentation with a new title."),
    "fill_template": ("Fill a presentation template", "Copy a template and replace its text and image tags."),
    "replace_text": ("Replace presentation text", "Replace phrases or tags in a deck or selected slides."),
    "replace_shapes_with_image": ("Replace a tagged shape with an image", "Put an image in shapes containing a chosen tag."),
    "add_slide": ("Add a slide", "Add a slide using one of the presentation's layouts."),
    "set_text": ("Write slide text", "Replace or append text and bullet items in one slide element."),
    "style_text": ("Style slide text", "Format a quoted phrase in one slide element."),
    "set_notes": ("Write speaker notes", "Replace or append a slide's speaker notes."),
    "move_slide": ("Move slides", "Reorder selected slides while preserving their relative order."),
    "delete_slide": ("Delete slides", "Remove selected slides while keeping at least one slide."),
    "skip_slide": ("Skip or show slides", "Choose whether selected slides appear in the presentation."),
    "compose": ("Compose a slide", "Arrange text, shapes, images, tables and videos in a weighted grid."),
    "add_shape": ("Add a slide shape", "Place text or a theme-colored shape in a region or beside an element."),
    "add_image": ("Add a slide image", "Place a validated image while preserving its aspect ratio."),
    "set_alt_text": ("Describe slide content", "Write accessible title and description text for an element."),
    "duplicate_slide_like": ("Extend a deck from a reference slide", "Duplicate a selected slide and replace role texts or images."),
    "import": ("Import a presentation", "Convert a PPTX or ODP source into a new native presentation."),
    "trash": ("Trash a presentation", "Move a service-created presentation into Drive trash."),
    "restore": ("Restore a presentation", "Restore a service-created presentation from Drive trash."),
    "add_table": ("Add a slide table", "Create and fill a table in a region, grid cell or beside an element."),
    "set_cells": ("Write table cells", "Replace cell text selected by row and column names or numbers."),
    "add_row": ("Add a table row", "Insert and fill a row before or after a selected table row."),
    "add_video": ("Add a slide video", "Place a YouTube or Drive video on a slide."),
    "list_comments": ("List presentation comments", "Read comments with their current slide and element anchors."),
    "get_comment": ("Read a presentation comment", "Read one selected comment and its replies."),
    "create_comment": ("Add a presentation comment", "Comment on a slide, element, text, table cells or the presentation file."),
    "reply_comment": ("Reply to a presentation comment", "Add a reply to a selected comment thread."),
    "update_comment": ("Edit a presentation comment", "Replace the text of a comment you authored."),
    "resolve_comment": ("Resolve a presentation comment", "Mark a comment thread as resolved."),
    "delete_comment": ("Delete a presentation comment", "Remove a comment thread you authored."),
}
SLIDES_PRESENTATION = {name: {"label": label, "description": desc} for name,(label,desc) in _DESCRIPTIONS.items()}
_INSTRUCTIONS = {
    "export": "Pass format pdf or pptx. The export ref supports streaming object.get and a deployment-signed download URL when configured.",
    "thumbnail": "Pass one slide selector and size SMALL, MEDIUM or LARGE. The Google capability URL stays server-side; receive a size-bound slides.thumbnail artifact with a signed download URL when configured, or stream its PNG bytes.",
    "copy": "Pass source (a presentation ref or link) and a new title. Preview before copying if needed; read and write claims are required. A parent_id additionally requires drive:write.",
    "fill_template": "Pass source, title, values as a tag-to-text map and optional images as a tag-to-image map (image_uri or file_ref). Tags are discovered before copying; missing tags and unreadable templates are refused. Pass preview to validate without creating a copy.",
    "replace_text": "Pass replacements as literal text-to-text pairs, optionally slides as selectors or slide plus element selector. Pass revision_id from object.get and preview to inspect changes without writing.",
    "replace_shapes_with_image": "Pass tag or text, one image_uri (public HTTPS PNG/JPEG/GIF) or file_ref, optional slide/slides selectors, and fit CENTER_INSIDE or CENTER_CROP. Pass revision_id and preview to validate before writing.",
    "add_slide": "Address a presentation. Read object.get with include: [layouts], then pass exactly one listed layout word or exact layout_name; optional title, subtitle, body and body_2 are text or items lists. Use before/after as a slide selector; default is the end. All content placeholders receive known refs; unfilled ones are deleted unless keep_empty_placeholders is true. Optional notes are written after creation. Pass revision_id and preview; inspect fit and shorten or split overflowing text.",
    "set_text": "Pass one slide and element selector, exactly one text or items list, and mode replace (default) or append. Items become bullets. Append inserts before the implicit terminal newline. Pass revision_id and preview; inspect predicted fit.",
    "style_text": "Pass slide and element selectors, quoted_text that occurs exactly once, and style with bold/italic booleans, font_size_pt, color #RRGGBB or link. UTF-16 ranges are resolved internally. Pass revision_id and preview and inspect fit after size changes.",
    "set_notes": "Pass one slide selector, notes as text and mode replace (default) or append. The current speaker-notes placeholder is resolved on the fresh read. Pass revision_id and preview.",
    "move_slide": "Pass slide or slides selectors and exactly one before or after selector. The fresh current order decides relative order, even if your selectors are reversed or duplicated. Pass revision_id and preview to inspect before_order and after_order.",
    "delete_slide": "Pass slide or slides selectors. Deleting every remaining slide is refused; keep at least one slide. Pass revision_id and preview to inspect the order after removal.",
    "skip_slide": "Pass slide or slides selectors and skipped true (default) or false. Pass revision_id and preview to inspect the visibility change.",
    "compose": "Pass slide, region (default body), positive cols/rows weights and cells addressed by zero-based at [row,column], with optional row_span/col_span or a nested grid. Each element is a selector, text/shape map, image source, {table: {rows, columns, values}} or {video: {source: youtube|drive, id}}. Existing charts can move. Colors use theme.accent1 or another theme color. Pass preview and revision_id; inspect fit, overlaps and off_slide.",
    "add_shape": "Pass slide and shape text_box, rectangle, round_rectangle or ellipse, with optional text/items and theme fill/outline/color. Place by region or exactly one below/above/left_of/right_of/over selector. Relative placement accepts align start/center/end, gap small/medium/large and width as an anchor fraction; overlay at selects one of nine positions. Pass preview and revision_id.",
    "add_image": "Pass slide, exactly one public HTTPS image_uri or deployment file_ref, and a region, single relative anchor or element to replace. PNG/JPEG/GIF, under 50 MB and at most 25 megapixels. The adapter validates bytes and computes an aspect-preserving box. Pass preview and revision_id; inspect geometry warnings.",
    "set_alt_text": "Pass slide and element selectors plus title or description. Use human-readable accessibility content. Pass preview and revision_id.",
    "duplicate_slide_like": "Pass like as one reference-slide selector, optional before/after selector and replacements by title/subtitle/body/body_2, notes or selected images. Unnamed content remains and is listed in kept_from_reference. Pass preview and revision_id; inspect fit before extending the deck.",
    "import": "Call named_services_call in namespace slides with operation object.action and action import, without object_ref, to create a new presentation. Pass one Drive source_file (ID/link) or deployment file_ref, optional title and parent_id. Deployment PPTX/ODP uploads are bounded to 5 MB and validated before Google. Preview converts nothing. An account-bound source ref must match the selected account.",
    "trash": "Address a presentation carrying this service's private creation marker and grant slides:delete. Preview before moving it to Drive trash. Older unmarked decks are refused; the Drive metadata write is not an atomic Slides revision compare-and-swap.",
    "restore": "Address a marked service-created presentation and grant slides:delete. Preview before restoring it from Drive trash. Older unmarked decks are refused; geometry may be unavailable while the deck is trashed.",
    "add_table": "Pass slide, rows and columns or rectangular values (plain-text rows). Use a named region or one relative anchor, as for add_shape; compose accepts the same data under table in a grid element. At most 500 cells and 100000 characters per write. Preview and revision_id are supported; Google may enlarge small tables to its minimum size.",
    "set_cells": "Pass slide, table as an element selector, and cells as row/column/text maps. Row and column numbers are 1-based. With header=1, use column names and row: {where: {column, equals|contains}}. Reuse elements[].table.selector from object.get. Merged cells and ambiguous rows/columns are refused before writing. Pass preview and revision_id.",
    "add_row": "Pass slide and table selectors, optional before or after row selector (default append), and values as one list or a column-name map with header=1. Row predicates use where column/equals/contains. Merged insertion rows are refused. Pass preview and revision_id.",
    "add_video": "Pass slide and video as a YouTube/Drive HTTPS URL or {source: youtube|drive, id}. Place with a named region or one relative anchor; compose accepts video in a grid cell. The connected account must be able to access a Drive video. Pass preview and revision_id.",
    "list_comments": "Page Drive comments with limit 1..100 and cursor/next_cursor. Set include_resolved to see closed threads. Slides anchor targets are joined from a fresh comments-included read. Optional slide selects anchored comments on that slide; an unavailable anchor refuses that filter rather than dropping unknown targets.",
    "get_comment": "Pass comment_ref returned by list_comments, or comment_selector with text_contains, quoted_text, author: me or unresolved: true. Lexical selection reads at most 1000 comments and refuses an incomplete scan or multiple matches; use a returned comment_ref for larger decks.",
    "create_comment": "Pass nonempty content (at most 2048 UTF-8 bytes). With no target, create a Drive file comment. Otherwise name slide, optionally element or quoted_text; or table plus row/column selectors, optional quoted_text, or table_range with from/to row/column endpoints. Table cells/ranges must contain text; merged cells are refused. Google can normalize a table range to one cell text anchor, so the result separates requested and saved targets. Native saves return a created thread ref and observed anchors. Pass preview and revision_id. Save status must be ALL_SAVED; otherwise read back before retrying.",
    "reply_comment": "Pass comment_ref or a unique comment_selector, and content (at most 2048 UTF-8 bytes). Replies use the thread's Slides or Drive path. Pass preview and revision_id; read back on an uncertain save.",
    "update_comment": "Pass comment_ref or a unique comment_selector and replacement content (at most 2048 UTF-8 bytes). Only the head post's author may edit it; others must reply. Pass preview and revision_id.",
    "resolve_comment": "Pass comment_ref or a unique comment_selector. Anchored resolution uses a native reply with post.commentAction RESOLVE and optional content (default Resolved.); file resolution uses Drive action resolve. Preview is supported. Native writes carry the Slides revision; Drive has a prior check rather than an atomic Slides guard.",
    "delete_comment": "Pass comment_ref or a unique comment_selector. Only the head post's author may delete the thread; others must reply. Pass preview and revision_id and read back before retrying an uncertain outcome.",
}
SLIDES_SCHEMA = {
    "namespace": "slides", "snapshot_schema": "kdcube.slides.snapshot.v1",
    "refs": {"presentation": "slides:google:<account_id>:presentation:<presentation_id>", "slide": "slides:google:<account_id>:slide:<slide_id>:<presentation_id>", "export": "slides:google:<account_id>:export:<pdf|pptx>:<presentation_id>", "thumbnail": "slides:google:<account_id>:thumbnail:<SMALL|MEDIUM|LARGE>:<slide_id>:<presentation_id>"},
    "object_kinds": {"slides.presentation": {"description": "A native presentation with a theme and slides."}, "slides.slide": {"description": "One slide in a native presentation."}, "slides.export": {"description": "PDF or PowerPoint bytes."}, "slides.thumbnail": {"description": "A rendered PNG slide."}},
    "operations": {name: {**meta, "instruction": SLIDES_INTRO} for name,meta in SLIDES_PRESENTATION.items() if "." in name},
    "actions": {name: {"label": SLIDES_PRESENTATION[name]["label"], "description": _INSTRUCTIONS[name]} for name in SLIDES_ACTIONS},
    "selectors": {"slide": ["position", "title", "title_contains", "tag", "kind", "ref", "last"], "element": ["role", "text_contains", "alt_text", "kind", "index", "ref"]},
    "diagnostics": "Mutation results include diagnostics_summary for the whole deck and detailed fit, overlaps and off_slide only for touched slides. Whole-deck creations (create, import, copy, fill_template) return the summary only. Read object.get for full per-element diagnostics. Preview summaries are predicted; import previews report diagnostics unavailable before conversion.",
    "pagination": "object.get filters.start_slide (1-based) and slide_limit (1..50, default 10); next_slide names the next range. Include layouts or relations explicitly with filters.include. Trivial grids and empty free regions are omitted. Table details use filters.table_start_row and table_row_limit (1..50), with next_row in each table. Search/list and list_comments use cursor/next_cursor.",
    "connected_account_claims": ["slides:read", "slides:write", "slides:delete", "slides:comment"],
    "limitations": ["Animations, transitions and master/theme editing are outside this service.", "Fit flags are geometric estimates. Text written through the API does not automatically shrink.", "Charts are selected as existing elements; new charts and typed raw requests are outside this service.", "Selector edits and comment anchors support tables with at most 2000 physical cells; reads explicitly mark larger table details unavailable.", "Trash/restore refuses decks created before the service creation marker existed."],
}

SLIDES_SCHEMA["selectors"].update({
    "object_schema": {
        "description": "Schema selectors are mutually exclusive. The view is inferred; only schema_view=full is set explicitly.",
        "schema_path": "One catalog path such as /reading or /tables.",
        "query": "Search capability descriptions; mutually exclusive with schema_path, object_kind, object_ref and schema_operation.",
        "object_kind": "One object kind; mutually exclusive with object_ref and catalog/search selectors.",
        "object_ref": "Infer one object kind from its opaque ref; mutually exclusive with object_kind and catalog/search selectors.",
        "schema_operation": "One exact operation contract, paired with its object_kind or object_ref.",
        "schema_view": "Set only full explicitly, without other schema selectors, to request the complete schema.",
    },
    "presentation": {"description": "Use a canonical account-bound presentation ref returned by this provider."},
})
SLIDES_SCHEMA.update({
    "search": {"filters": ["account_id"], "description": "Search presentation titles or list recent presentations."},
    "get": {"filters": ["account_id", "start_slide", "slide_limit", "slide_selector", "table_start_row", "table_row_limit", "include"], "default_slide_limit": 10, "include": ["relations", "layouts"]},
    "materialization": "Read artifact refs with streaming object.get to receive bytes, or use their deployment-signed download URL.",
})


SLIDES_SCHEMA["actions"]["import"]["object_ref"] = "none"


for _action in ("compose", "add_shape", "add_image"):
    _payload = composition_payload_schema(_action)
    SLIDES_SCHEMA["actions"][_action].update(payload=list(_payload["properties"]), payload_schema=_payload)

for _action in ("add_table", "set_cells", "add_row", "add_video", *sorted(COMMENT_ACTIONS)):
    _payload = s3_payload_schema(_action)
    SLIDES_SCHEMA["actions"][_action].update(payload=list(_payload["properties"]), payload_schema=_payload)


def action_claims(action, payload=None):
    payload = payload or {}
    claims = [SLIDES_DELETE_CLAIM] if action in ("trash", "restore") else [SLIDES_READ_CLAIM] if action in ("get", "search", "export", "thumbnail", *READ_COMMENTS) else [SLIDES_READ_CLAIM, SLIDES_COMMENT_CLAIM] if action in WRITE_COMMENTS else [SLIDES_READ_CLAIM, SLIDES_WRITE_CLAIM]
    if payload.get("parent_id"):
        claims.append("drive:write")
    return claims


CLAIMS_BY_OPERATION = {"object.get": [SLIDES_READ_CLAIM], "object.list": [SLIDES_READ_CLAIM], "object.search": [SLIDES_READ_CLAIM], "object.upsert": [SLIDES_READ_CLAIM, SLIDES_WRITE_CLAIM], "object.action": [SLIDES_READ_CLAIM, SLIDES_WRITE_CLAIM], **{f"object.action.{name}": action_claims(name) for name in SLIDES_ACTIONS}}
SLIDES_CONNECTED_ACCOUNT_REQUIREMENTS = [{"provider_id": "google", "provider_label": "Google", "claims": [SLIDES_READ_CLAIM, SLIDES_WRITE_CLAIM, SLIDES_DELETE_CLAIM, SLIDES_COMMENT_CLAIM], "claim_labels": {SLIDES_READ_CLAIM: "read presentations", SLIDES_WRITE_CLAIM: "edit presentations", SLIDES_DELETE_CLAIM: "trash or restore service-created presentations", SLIDES_COMMENT_CLAIM: "comment on presentations"}, "claims_by_operation": CLAIMS_BY_OPERATION}]
