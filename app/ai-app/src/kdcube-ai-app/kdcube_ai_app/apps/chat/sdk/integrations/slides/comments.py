"""Comment targets and read projections in slide/element/cell vocabulary."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_fit import shape_text
from kdcube_ai_app.apps.chat.sdk.integrations.slides.editing import content, utf16_len
from kdcube_ai_app.apps.chat.sdk.integrations.slides.layouts import walk
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError, resolve_element, resolve_slide
from kdcube_ai_app.apps.chat.sdk.integrations.slides.tables import cell_location, resolve_table

READ_COMMENTS = frozenset({"list_comments", "get_comment"})
WRITE_COMMENTS = frozenset({"create_comment", "reply_comment", "update_comment", "resolve_comment", "delete_comment"})
COMMENT_ACTIONS = READ_COMMENTS | WRITE_COMMENTS
TARGET_KEYS = frozenset({"slide", "element", "quoted_text", "table", "row", "column", "table_range"})


def comment_content(value):
    content(value)
    if not value.strip() or len(value.encode("utf-8")) > 2048:
        raise SlidesValidationError("slides_invalid_comment", "Pass nonempty comment content of at most 2048 UTF-8 bytes.")
    return value


def quote_range(text, quote):
    if not isinstance(quote, str) or not quote:
        raise SlidesValidationError("slides_comment_target_required", "Pass nonempty quoted_text from the intended shape or cell.")
    matches = list(re.finditer(re.escape(quote), text))
    if len(matches) != 1:
        raise SlidesValidationError("slides_text_ambiguous" if matches else "slides_text_not_found", "Choose text occurring exactly once in the selected target.")
    match = matches[0]
    return {"type": "FIXED_RANGE", "startIndex": utf16_len(text[:match.start()]), "endIndex": utf16_len(text[:match.end()])}


def compile_anchor(presentation, view, payload):
    named = TARGET_KEYS & payload.keys()
    if not named:
        return None, {"scope": "file"}
    if "table" in named:
        if "element" in named or ("table_range" in named and ({"row", "column", "quoted_text"} & named)):
            raise SlidesValidationError("slides_comment_target_required", "Choose one table cell or one table_range, without other targets.")
        slide, element, table = resolve_table(presentation, view, payload)
        identity = element["ref"]
        common = {"slide": slide["ref"], "table": identity}
        header = payload.get("header", 0)
        if "table_range" in named:
            span = payload["table_range"]
            if not isinstance(span, Mapping) or set(span) != {"from", "to"} or any(not isinstance(span[k], Mapping) or set(span[k]) != {"row", "column"} for k in span):
                raise SlidesValidationError("slides_comment_target_required", "Pass table_range with from/to endpoints, each containing row and column selectors.")
            r1, c1 = cell_location(table, **span["from"], header=header)
            r2, c2 = cell_location(table, **span["to"], header=header)
            if r2 < r1 or c2 < c1:
                raise SlidesValidationError("slides_comment_target_required", "The table range must end at or after its start row and column.")
            selected = [cell for row in table["cells"][r1:r2+1] for cell in row[c1:c2+1]]
            if any(cell["merged"] for cell in selected):
                raise SlidesValidationError("slides_table_cell_merged", "Choose a range of unmerged cells.")
            if not any(cell["text"].strip() for cell in selected):
                raise SlidesValidationError("slides_comment_target_empty", "Add text to at least one cell before anchoring a comment to this range.")
            return {"tableAnchor": {"objectId": identity, "tableRange": {"location": {"rowIndex": r1, "columnIndex": c1}, "rowSpan": r2-r1+1, "columnSpan": c2-c1+1}}}, {**common, "scope": "table_range", "from": {"row": r1+1, "column": c1+1}, "to": {"row": r2+1, "column": c2+1}}
        if not {"row", "column"} <= named:
            raise SlidesValidationError("slides_comment_target_required", "Name both row and column, or table_range, for a table comment.")
        r, c = cell_location(table, payload["row"], payload["column"], header=header)
        text = table["cells"][r][c]["text"]
        if not text.strip():
            raise SlidesValidationError("slides_comment_target_empty", "Add text to the cell before anchoring a comment to it.")
        bounds = quote_range(text, payload["quoted_text"]) if "quoted_text" in named else {"type": "ALL"}
        return {"tableCellTextAnchor": {"objectId": identity, "cellLocation": {"rowIndex": r, "columnIndex": c}, "textRange": bounds}}, {**common, "scope": "cell", "row": r+1, "column": c+1}
    if {"row", "column", "table_range"} & named:
        raise SlidesValidationError("slides_comment_target_required", "A cell or range comment needs a table selector.")
    if "slide" not in named and "quoted_text" not in named:
        raise SlidesValidationError("slides_comment_target_required", "Name a slide for an element comment.")
    slides = [resolve_slide(view["slides"], payload["slide"])] if "slide" in named else view["slides"]
    if "quoted_text" in named:
        quote = payload["quoted_text"]
        if not isinstance(quote, str) or not quote:
            raise SlidesValidationError("slides_comment_target_required", "Pass nonempty quoted_text.")
        matches = []
        for slide in slides:
            elements = [resolve_element(slide, payload["element"])] if "element" in named else [e for e in slide["elements"] if e["kind"] in ("shape", "callout")]
            raw_slide = next(s for s in presentation["slides"] if s["objectId"] == slide["ref"])
            raw = {e["objectId"]: e for e in walk(raw_slide.get("pageElements", []))}
            for element in elements:
                identity = element.get("_content_ref", element["ref"])
                if "shape" not in raw[identity]:
                    continue
                text = shape_text(raw[identity]["shape"])
                matches.extend((slide, identity, text) for _ in re.finditer(re.escape(quote), text))
        if len(matches) != 1:
            raise SlidesValidationError("slides_text_ambiguous" if matches else "slides_text_not_found", "Narrow quoted_text to one occurrence with a slide and element selector.")
        slide, identity, text = matches[0]
        return {"shapeTextAnchor": {"objectId": identity, "textRange": quote_range(text, quote)}}, {"scope": "text", "slide": slide["ref"], "element": identity, "quoted_text": quote}
    slide = slides[0]
    element = resolve_element(slide, payload["element"]) if "element" in named else None
    return {"objectId": element["ref"] if element else slide["ref"]}, {"scope": "element" if element else "page", "slide": slide["ref"], **({"element": element["ref"]} if element else {})}


def anchor_views(presentation):
    """Join returned anchor IDs, preserving multiple object anchors per thread."""
    result = {}
    for position, slide in enumerate(presentation.get("slides", []), 1):
        identities = {e["objectId"] for e in walk(slide.get("pageElements", []))}
        for anchor in slide.get("commentAnchors", []):
            objects = []
            for item in anchor.get("objectAnchors", []):
                identity = item.get("objectId")
                if identity != slide["objectId"] and identity not in identities:
                    continue
                target = {"slide": {"ref": slide["objectId"], "position": position}}
                if identity != slide["objectId"]:
                    target["element"] = {"ref": identity}
                # Read ranges are informational; callers select text/cells.
                for key in ("shapeTextAnchors", "tableCellTextAnchors", "tableAnchors"):
                    if key in item:
                        target[key] = item[key]
                objects.append(target)
            result[anchor.get("anchorId")] = {"scope": "anchored", "anchor_state": "attached" if objects else "unavailable", "targets": objects}
    for thread in presentation.get("comments", []):
        identity = thread.get("anchorId")
        if identity in result:
            continue
        # The design's probe observed JSON anchor IDs too. Retain their safe
        # target identity when no modern commentAnchors record is available.
        try:
            legacy = json.loads(identity) if isinstance(identity, str) else None
        except ValueError:
            legacy = None
        result[identity] = {"scope": "anchored", "anchor_state": "unavailable"}
        if isinstance(legacy, Mapping):
            result[identity]["anchor_kind"] = str(legacy.get("type", "unknown"))[:80]
    return result
