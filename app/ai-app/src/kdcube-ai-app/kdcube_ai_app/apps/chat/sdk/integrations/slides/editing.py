"""Selector-resolved text, notes and slide-order requests, without transport."""
from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlparse

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import shape_text
from kdcube_ai_app.apps.chat.sdk.integrations.slides.layouts import create_slide_plan, insertion_index, walk
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError, resolve_slide, resolve_element
from kdcube_ai_app.apps.chat.sdk.integrations.slides.templates import selected_slides

EDIT_ACTIONS = frozenset({"add_slide", "set_text", "style_text", "set_notes", "move_slide", "delete_slide", "skip_slide"})
MAX_TEXT = 100_000


def utf16_len(text):
    return len(text.encode("utf-16-le")) // 2


def content(value):
    items = None
    if isinstance(value, list):
        items = value
    elif isinstance(value, Mapping):
        if set(value) - {"text", "items"} or ("text" in value) == ("items" in value):
            raise SlidesValidationError("slides_invalid_text", "Pass text or items, with one content form per element.")
        items = value.get("items")
        value = value.get("text") if items is None else items
    if items is not None:
        if not isinstance(items, list) or not items or len(items) > 1000 or any(not isinstance(v, str) or not v or "\n" in v or "\t" in v for v in items):
            raise SlidesValidationError("slides_invalid_text", "Pass 1 to 1000 nonempty bullet items, each on one line without tabs.")
        value = "\n".join(items)
    if not isinstance(value, str) or len(value) > MAX_TEXT or any((ord(c) < 32 and c not in "\n\t") or 0xE000 <= ord(c) <= 0xF8FF for c in value):
        raise SlidesValidationError("slides_invalid_text", "Pass text of at most 100000 characters without control or private-use characters.")
    try:
        value.encode("utf-16-le")
    except UnicodeEncodeError as exc:
        raise SlidesValidationError("slides_invalid_text", "Pass valid Unicode text without unpaired surrogates.") from exc
    return value, items is not None


def text_requests(object_id, current, value, *, mode="replace", new=False):
    text, bullets = content(value)
    if mode not in ("replace", "append"):
        raise SlidesValidationError("slides_invalid_text_mode", "Use mode replace or append.")
    terminal = "\n" if current.endswith("\n") or new else ""
    existing = current[:-1] if terminal and current.endswith("\n") else current
    # Slides keeps one implicit terminal newline. Insert before it.
    inserted = text[:-1] if terminal and text.endswith("\n") else text
    if mode == "append":
        if bullets and existing and not existing.endswith("\n"):
            inserted = "\n" + inserted
        index = utf16_len(existing)
        after = existing + inserted + terminal
        requests = []
    else:
        index = 0
        after = inserted + terminal
        requests = [] if new else [{"deleteText": {"objectId": object_id, "textRange": {"type": "ALL"}}}]
    if len(after) > MAX_TEXT:
        raise SlidesValidationError("slides_text_too_large", "Keep the complete element text within 100000 characters.")
    if inserted:
        requests.append({"insertText": {"objectId": object_id, "text": inserted, "insertionIndex": index}})
        if bullets:
            requests.append({"createParagraphBullets": {"objectId": object_id, "textRange": {"type": "FIXED_RANGE", "startIndex": index + (1 if mode == "append" and inserted.startswith("\n") else 0), "endIndex": index + utf16_len(inserted)}, "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE"}})
        elif mode == "replace":
            requests.append({"deleteParagraphBullets": {"objectId": object_id, "textRange": {"type": "ALL"}}})
    return requests, {"before": current[:12000], "after": after[:12000], "before_truncated": len(current) > 12000, "after_truncated": len(after) > 12000}


def style(value):
    if not isinstance(value, Mapping) or not value or set(value) - {"bold", "italic", "font_size_pt", "color", "link"}:
        raise SlidesValidationError("slides_invalid_style", "Pass a style map with bold, italic, font_size_pt, color or link.")
    result = {}
    for key in ("bold", "italic"):
        if key in value:
            if type(value[key]) is not bool:
                raise SlidesValidationError("slides_invalid_style", "Bold and italic must be true or false.")
            result[key] = value[key]
    if "font_size_pt" in value:
        size = value["font_size_pt"]
        if type(size) not in (float, int) or not 1 <= size <= 400:
            raise SlidesValidationError("slides_invalid_style", "Use font_size_pt between 1 and 400.")
        result["fontSize"] = {"magnitude": size, "unit": "PT"}
    if "color" in value:
        color = value["color"]
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise SlidesValidationError("slides_invalid_style", "Pass color as #RRGGBB.")
        result["foregroundColor"] = {"opaqueColor": {"rgbColor": {key: int(color[i:i + 2], 16) / 255 for key, i in (("red", 1), ("green", 3), ("blue", 5))}}}
    if "link" in value:
        link = value["link"]
        if not isinstance(link, str) or len(link) > 2048 or urlparse(link).scheme not in ("https", "http", "mailto"):
            raise SlidesValidationError("slides_invalid_style", "Use an HTTP, HTTPS or mailto link of at most 2048 characters.")
        result["link"] = {"url": link}
    return result


def compile_edit(presentation, view, action, payload):
    if action == "add_slide":
        return create_slide_plan(presentation, view, payload, text_requests)
    if action in ("move_slide", "delete_slide", "skip_slide"):
        selected = selected_slides(view, payload)
        if payload.get("slide") is None and payload.get("slides") is None:
            raise SlidesValidationError("slides_slide_not_found", "Name the slide or slides to change.")
        ids = [s["ref"] for s in sorted(selected, key=lambda s: s["position"])]
        if action == "delete_slide":
            if len(ids) >= len(view["slides"]):
                raise SlidesValidationError("slides_last_slide", "Keep at least one slide in the presentation.")
            requests = [{"deleteObject": {"objectId": ref}} for ref in ids]
            after = [s["ref"] for s in view["slides"] if s["ref"] not in ids]
        elif action == "skip_slide":
            skipped = payload.get("skipped", True)
            if type(skipped) is not bool:
                raise SlidesValidationError("slides_invalid_skip", "Pass skipped as true or false.")
            requests = [{"updateSlideProperties": {"objectId": ref, "slideProperties": {"isSkipped": skipped}, "fields": "isSkipped"}} for ref in ids]
            return requests, {"slides": ids, "skipped": skipped}
        else:
            index = insertion_index(view, payload, required=True)
            requests = [{"updateSlidesPosition": {"slideObjectIds": ids, "insertionIndex": index}}]
            remaining = [s["ref"] for s in view["slides"] if s["ref"] not in ids]
            position = index - sum(s["position"] <= index for s in selected)
            after = remaining[:position] + ids + remaining[position:]
        return requests, {"slides": ids, "before_order": [s["ref"] for s in view["slides"]], "after_order": after}
    slide = resolve_slide(view["slides"], payload.get("slide"))
    if action == "set_notes":
        raw = next(s for s in presentation.get("slides", []) if s["objectId"] == slide["ref"])
        page = raw.get("slideProperties", {}).get("notesPage", {})
        notes_id = page.get("notesProperties", {}).get("speakerNotesObjectId")
        shape = next((e.get("shape", {}) for e in walk(page.get("pageElements", [])) if e.get("objectId") == notes_id), None)
        if not notes_id or shape is None:
            raise SlidesValidationError("slides_notes_unavailable", "Read the slide again to obtain its speaker-notes placeholder.")
        value = payload.get("notes", payload.get("text"))
        if not isinstance(value, str):
            raise SlidesValidationError("slides_invalid_text", "Pass speaker notes as a text string.")
        requests, change = text_requests(notes_id, shape_text(shape), value, mode=payload.get("mode", "replace"))
        return requests, {"slide": slide["ref"], "role": "notes", "ref": notes_id, **change}
    element = resolve_element(slide, payload.get("element"))
    if element.get("kind") not in ("shape", "callout") or element.get("text_truncated"):
        raise SlidesValidationError("slides_element_not_editable", "Read one untruncated text shape before editing.")
    current = element.get("text", "")
    target_id = element.get("_content_ref", element["ref"])
    if action == "set_text":
        if ("text" in payload) == ("items" in payload):
            raise SlidesValidationError("slides_invalid_text", "Pass exactly one text string or items list.")
        value = {k: payload[k] for k in ("text", "items") if k in payload}
        requests, change = text_requests(target_id, current, value, mode=payload.get("mode", "replace"))
        return requests, {"slide": slide["ref"], "ref": element["ref"], **change}
    quoted = payload.get("quoted_text")
    if not isinstance(quoted, str) or not quoted or len(quoted) > MAX_TEXT:
        raise SlidesValidationError("slides_invalid_text_span", "Pass quoted_text from the selected element.")
    matches = list(re.finditer(re.escape(quoted), current))
    if len(matches) != 1:
        raise SlidesValidationError("slides_text_ambiguous" if matches else "slides_text_not_found", "Quote text that occurs exactly once in the selected element.")
    patch = style(payload.get("style"))
    match = matches[0]
    bounds = {"type": "FIXED_RANGE", "startIndex": utf16_len(current[:match.start()]), "endIndex": utf16_len(current[:match.end()])}
    request = {"updateTextStyle": {"objectId": target_id, "textRange": bounds, "style": patch, "fields": ",".join(patch)}}
    return [request], {"slide": slide["ref"], "ref": element["ref"], "quoted_text": quoted[:12000], "style": dict(payload["style"]), "before": current[:12000], "after": current[:12000]}
