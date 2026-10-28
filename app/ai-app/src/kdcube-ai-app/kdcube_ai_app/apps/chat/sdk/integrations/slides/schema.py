"""Discovery metadata and the S1 connected-account requirements."""

SLIDES_NAMESPACE = "slides"
SLIDES_READ_CLAIM = "slides:read"
SLIDES_WRITE_CLAIM = "slides:write"
SLIDES_TRANSPORTS = ("local", "api")
SLIDES_ACTIONS = ("export", "thumbnail", "copy", "fill_template", "replace_text", "replace_shapes_with_image")
SLIDES_INTRO = "Find a deck with object.search, then object.get its snapshot and revision_id. Address slides by position, title, tag, kind or a returned ref; address elements by role, text, kind/index or ref. Pass revision_id when editing and inspect fit warnings. Export PDF/PPTX or request a thumbnail for visual checks."
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
}
SLIDES_PRESENTATION = {name: {"label": label, "description": desc} for name,(label,desc) in _DESCRIPTIONS.items()}
_INSTRUCTIONS = {
    "export": "Pass format pdf or pptx. The export ref supports streaming object.get and a deployment-signed download URL when configured.",
    "thumbnail": "Pass one slide selector and size SMALL, MEDIUM or LARGE. The Google capability URL stays server-side; receive a slides.thumbnail artifact or stream its PNG bytes.",
    "copy": "Pass source (a presentation ref or link) and a new title. Preview before copying if needed; read and write claims are required. A parent_id additionally requires drive:write.",
    "fill_template": "Pass source, title, values as a tag-to-text map and optional images as a tag-to-image map (image_uri or file_ref). Tags are discovered before copying; missing tags and unreadable templates are refused. Pass preview to validate without creating a copy.",
    "replace_text": "Pass replacements as literal text-to-text pairs, optionally slides as selectors or slide plus element selector. Pass revision_id from object.get and preview to inspect changes without writing.",
    "replace_shapes_with_image": "Pass tag or text, one image_uri (public HTTPS PNG/JPEG/GIF) or file_ref, optional slide/slides selectors, and fit CENTER_INSIDE or CENTER_CROP. Pass revision_id and preview to validate before writing.",
}
SLIDES_SCHEMA = {
    "namespace": "slides", "snapshot_schema": "kdcube.slides.snapshot.v1",
    "refs": ["slides:google:<account_id>:presentation:<presentation_id>", "slides:google:<account_id>:slide:<slide_id>:<presentation_id>", "slides:google:<account_id>:export:<pdf|pptx>:<presentation_id>", "slides:google:<account_id>:thumbnail:<slide_id>:<presentation_id>"],
    "object_kinds": {"slides.presentation": {"description": "A native presentation with a theme and slides."}, "slides.export": {"description": "PDF or PowerPoint bytes."}, "slides.thumbnail": {"description": "A rendered PNG slide."}},
    "operations": {name: {**meta, "instruction": SLIDES_INTRO} for name,meta in SLIDES_PRESENTATION.items() if "." in name},
    "actions": {name: {"label": SLIDES_PRESENTATION[name]["label"], "description": _INSTRUCTIONS[name]} for name in SLIDES_ACTIONS},
    "selectors": {"slide": ["position", "title", "title_contains", "tag", "kind", "ref", "last"], "element": ["role", "text_contains", "alt_text", "kind", "index", "ref"]},
    "pagination": "object.get filters.start_slide (1-based) and slide_limit (1..50); next_slide names the next range. Search/list uses cursor/next_cursor.",
    "connected_account_claims": ["slides:read", "slides:write"],
    "limitations": ["Animations, transitions and master/theme editing are outside this service.", "Fit flags are geometric estimates. Text written through the API does not automatically shrink.", "Building, slide composition, comments, import and typed raw requests follow in later stages."],
}


def action_claims(action, payload=None):
    payload = payload or {}
    claims = [SLIDES_READ_CLAIM] if action in ("get", "search", "export", "thumbnail") else [SLIDES_READ_CLAIM, SLIDES_WRITE_CLAIM]
    if payload.get("parent_id"):
        claims.append("drive:write")
    return claims


CLAIMS_BY_OPERATION = {"object.get": [SLIDES_READ_CLAIM], "object.list": [SLIDES_READ_CLAIM], "object.search": [SLIDES_READ_CLAIM], "object.upsert": [SLIDES_READ_CLAIM, SLIDES_WRITE_CLAIM], "object.action": [SLIDES_READ_CLAIM, SLIDES_WRITE_CLAIM], **{f"object.action.{name}": action_claims(name) for name in SLIDES_ACTIONS}}
SLIDES_CONNECTED_ACCOUNT_REQUIREMENTS = [{"provider_id": "google", "provider_label": "Google", "claims": [SLIDES_READ_CLAIM, SLIDES_WRITE_CLAIM], "claim_labels": {SLIDES_READ_CLAIM: "read presentations", SLIDES_WRITE_CLAIM: "edit presentations"}, "claims_by_operation": CLAIMS_BY_OPERATION}]
