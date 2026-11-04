"""Advanced native Slides reads and bounded batch request validation."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json

from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import EMU_PER_PT
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError

MAX_NATIVE_REQUESTS = 50
MAX_NATIVE_BYTES = 1_000_000
MAX_NATIVE_TEXT = 100_000
# Comment writes retain their author, claim and save/read-back policy in the
# dedicated actions. Slide deletion retains its own last-slide guard.
ALLOWED_REQUEST_KINDS = frozenset({
    "createSlide", "duplicateObject", "updateSlidesPosition", "updateSlideProperties",
    "createShape", "createImage", "createVideo", "createTable", "deleteObject",
    "insertText", "deleteText", "replaceAllText", "updateTextStyle", "updateParagraphStyle",
    "createParagraphBullets", "deleteParagraphBullets", "updateShapeProperties",
    "updateImageProperties", "updateVideoProperties", "updatePageElementTransform",
    "updatePageElementAltText", "updatePageElementsZOrder", "groupObjects", "ungroupObjects",
    "insertTableRows", "insertTableColumns", "deleteTableRow", "deleteTableColumn",
    "mergeTableCells", "unmergeTableCells", "updateTableCellProperties",
    "updateTableRowProperties", "updateTableColumnProperties", "updateTableBorderProperties",
})


def validate_batch(payload):
    revision = payload.get("requiredRevisionId")
    if not isinstance(revision, str) or not revision.strip() or len(revision) > 1024:
        raise SlidesValidationError("slides_revision_required", "Read get_structure and pass its revision_id as requiredRevisionId. A raw batch requires an exact revision.")
    requests = payload.get("requests")
    if not isinstance(requests, list) or not 1 <= len(requests) <= MAX_NATIVE_REQUESTS:
        raise SlidesValidationError("slides_batch_invalid", "Pass 1–50 native requests, each with one allowlisted request kind.")
    try:
        encoded = json.dumps(requests, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError) as exc:
        raise SlidesValidationError("slides_batch_invalid", "Pass a finite JSON request list.") from exc
    if len(encoded) > MAX_NATIVE_BYTES:
        raise SlidesValidationError("slides_batch_too_large", "Keep the raw batch below 1 MB and split larger edits using the returned revision.")
    text_count = 0
    for request in requests:
        if not isinstance(request, Mapping) or len(request) != 1:
            raise SlidesValidationError("slides_batch_invalid", "Each request must contain exactly one native request kind.")
        kind, body = next(iter(request.items()))
        if kind not in ALLOWED_REQUEST_KINDS:
            raise SlidesValidationError("slides_batch_request_forbidden", "Use an allowlisted content request. Use the dedicated comment or delete_slide tool for those actions.", details={"request_kind": kind, "allowed_request_kinds": sorted(ALLOWED_REQUEST_KINDS)})
        if not isinstance(body, Mapping):
            raise SlidesValidationError("slides_batch_invalid", "Every native request body must be an object.")
        if kind == "createTable":
            rows, columns = body.get("rows"), body.get("columns")
            if any(type(n) is not int or not 1 <= n <= 50 for n in (rows, columns)) or rows * columns > 500:
                raise SlidesValidationError("slides_batch_invalid", "Create at most 500 table cells, with each dimension from 1 to 50.")
        if kind in ("insertTableRows", "insertTableColumns"):
            number = body.get("number", 1)
            if type(number) is not int or not 1 <= number <= 50:
                raise SlidesValidationError("slides_batch_invalid", "Insert between 1 and 50 table rows or columns per request.")
        pending = [(body, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > 30:
                raise SlidesValidationError("slides_batch_invalid", "Keep native request JSON within 30 nested levels.")
            if isinstance(value, Mapping):
                text_count += sum(len(child) for key, child in value.items() if key in ("text", "replaceText") and isinstance(child, str))
                pending.extend((child, depth + 1) for child in value.values() if isinstance(child, (Mapping, list)))
            elif isinstance(value, list):
                pending.extend((child, depth + 1) for child in value if isinstance(child, (Mapping, list)))
    if text_count > MAX_NATIVE_TEXT:
        raise SlidesValidationError("slides_batch_too_large", "Keep inserted/replacement text below 100000 characters per raw batch.")
    return deepcopy(requests), revision


def validate_delete_targets(presentation, requests):
    elements = set()
    def walk(rows):
        for row in rows:
            if row.get("objectId"):
                elements.add(row["objectId"])
            walk((row.get("elementGroup") or {}).get("children", []))
    for slide in presentation.get("slides", []):
        walk(slide.get("pageElements", []))
    for request in requests:
        if "deleteObject" in request and request["deleteObject"].get("objectId") not in elements:
            raise SlidesValidationError("slides_batch_delete_target_forbidden", "Raw deleteObject may delete only an existing page element from get_structure. Use delete_slide for slides; it preserves the last-slide guard.")


def structure(presentation):
    """Keep the fixed provider read mask and normalize physical geometry to EMU."""
    result = deepcopy({key: presentation[key] for key in ("presentationId", "title", "revisionId", "pageSize", "slides", "layouts", "masters") if key in presentation})
    def normalize(value):
        if isinstance(value, dict):
            for key in ("contentUrl", "sourceUrl", "thumbnailUrl"):
                value.pop(key, None)  # Provider capability URLs stay server-side.
            for key, child in value.items():
                if key in ("size", "pageSize") and isinstance(child, dict):
                    for dimension in ("width", "height"):
                        if isinstance(child.get(dimension), dict):
                            d = child[dimension]
                            d["magnitude"] = d.get("magnitude", 0) * (EMU_PER_PT if d.get("unit") == "PT" else 1)
                            d["unit"] = "EMU"
                elif key == "transform" and isinstance(child, dict):
                    factor = EMU_PER_PT if child.get("unit") == "PT" else 1
                    for axis in ("translateX", "translateY"):
                        child[axis] = child.get(axis, 0) * factor
                    child["unit"] = "EMU"
                normalize(child)
        elif isinstance(value, list):
            for child in value:
                normalize(child)
    normalize(result)
    return result
