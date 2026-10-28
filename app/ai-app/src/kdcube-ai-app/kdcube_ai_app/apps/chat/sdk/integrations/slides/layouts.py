"""Resolve theme layouts and compile creation with known placeholder IDs."""
from __future__ import annotations

import uuid

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_structure import LAYOUT_WORDS, layout_word, geometry
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError, resolve_slide

DECORATIVE_PLACEHOLDERS = {"DATE_AND_TIME", "FOOTER", "HEADER", "SLIDE_NUMBER"}


def walk(elements):
    for element in elements:
        yield element
        yield from walk(element.get("elementGroup", {}).get("children", []))


def insertion_index(view, payload, *, required=False):
    if payload.get("before") is not None and payload.get("after") is not None:
        raise SlidesValidationError("slides_invalid_position", "Choose before or after, using one slide selector.")
    for key in ("before", "after"):
        if payload.get(key) is not None:
            row = resolve_slide(view["slides"], payload[key])
            return row["position"] - (1 if key == "before" else 0)
    if required:
        raise SlidesValidationError("slides_invalid_position", "Choose a before or after slide selector for the move.")
    return len(view["slides"])


def resolve_layout(presentation, payload, index):
    word, name = payload.get("layout"), payload.get("layout_name")
    if (word is None) == (name is None):
        raise SlidesValidationError("slides_invalid_layout", "Pass exactly one layout word or layout_name from the snapshot.")
    layouts = presentation.get("layouts", [])
    slides = presentation.get("slides", [])
    master = None
    if slides:
        props = slides[max(0, index - 1)].get("slideProperties", {})
        master = props.get("masterObjectId")
        if not master:
            master = next((l.get("layoutProperties", {}).get("masterObjectId") for l in layouts if l.get("objectId") == props.get("layoutObjectId")), None)
    elif presentation.get("masters"):
        master = presentation["masters"][0].get("objectId")
    if master:
        layouts = [l for l in layouts if l.get("layoutProperties", {}).get("masterObjectId") == master]
    if word is not None:
        word = str(word).lower()
        if word.upper() not in LAYOUT_WORDS:
            matches = []
        else:
            direct = [l for l in layouts if l.get("layoutProperties", {}).get("name", "").upper() == word.upper()]
            matches = direct or [l for l in layouts if layout_word(l.get("layoutProperties", {}).get("name")) == word]
    else:
        matches = [l for l in layouts if l.get("layoutProperties", {}).get("displayName") == name]
    if len(matches) != 1:
        candidates = matches or layouts
        raise SlidesValidationError("slides_layout_ambiguous" if matches else "slides_layout_missing", "Choose one layout_name from the layouts available at this slide position.", details={"candidates": [{"layout": layout_word(l.get("layoutProperties", {}).get("name")), "layout_name": l.get("layoutProperties", {}).get("displayName", ""), "placeholders": [e["shape"]["placeholder"]["type"].lower() for e in walk(l.get("pageElements", [])) if e.get("shape", {}).get("placeholder", {}).get("type")]} for l in candidates[:5]]})
    return matches[0]


def content_placeholders(layout):
    rows = [e for e in walk(layout.get("pageElements", [])) if e.get("shape", {}).get("placeholder", {}).get("type") not in DECORATIVE_PLACEHOLDERS and e.get("shape", {}).get("placeholder", {}).get("type")]
    if len(rows) > 100:
        raise SlidesValidationError("slides_layout_too_large", "Choose a layout with at most 100 content placeholders.")
    # Match the snapshot's left-to-right body vocabulary.
    bodies = sorted((e for e in rows if e["shape"]["placeholder"]["type"] == "BODY"), key=lambda e: geometry(e)["x"])
    body_roles = {e["objectId"]: "body" if i == 0 else f"body_{i + 1}" for i, e in enumerate(bodies)}
    result = []
    for row in rows:
        kind = row["shape"]["placeholder"]["type"]
        role = body_roles.get(row["objectId"], "title" if kind == "CENTERED_TITLE" else kind.lower())
        result.append((role, row))
    return result


def new_id():
    return "slide_" + uuid.uuid4().hex


def create_slide_plan(presentation, view, payload, text_requests):
    if payload.get("slide") is not None or payload.get("slides") is not None:
        raise SlidesValidationError("slides_invalid_position", "Place a new slide with before or after; address the whole presentation.")
    if "keep_empty_placeholders" in payload and type(payload["keep_empty_placeholders"]) is not bool:
        raise SlidesValidationError("slides_invalid_layout", "Pass keep_empty_placeholders as true or false.")
    index = insertion_index(view, payload)
    layout = resolve_layout(presentation, payload, index)
    placeholders = content_placeholders(layout)
    ids = {role: [] for role, _ in placeholders}
    slide_id, mappings, requests, changes, empty = new_id(), [], [], [], []
    for role, source in placeholders:
        object_id = new_id()
        ids[role].append(object_id)
        mappings.append({"layoutPlaceholderObjectId": source["objectId"], "objectId": object_id})
        value = payload.get(role)
        edits, change = text_requests(object_id, "\n", value, mode="replace", new=True) if value is not None else ([], None)
        if change is None or not change["after"].strip():
            if payload.get("keep_empty_placeholders") is True:
                empty.append({"role": role, "ref": object_id})
            else:
                requests.append({"deleteObject": {"objectId": object_id}})
            continue
        if len(ids[role]) > 1 or sum(r == role for r, _ in placeholders) > 1:
            raise SlidesValidationError("slides_element_ambiguous", "This layout has several placeholders with the same role. Choose another layout or fill an element after creating the slide.")
        requests.extend(edits)
        changes.append({"ref": object_id, "role": role, **change})
    supplied = {key for key in payload if key in ("title", "subtitle", "body") or key.startswith("body_")}
    missing = supplied - set(ids)
    if missing:
        raise SlidesValidationError("slides_element_not_found", "The selected layout has no placeholder for the supplied content. Choose another layout.", details={"roles": sorted(missing)[:10]})
    create = {"createSlide": {"objectId": slide_id, "insertionIndex": index, "slideLayoutReference": {"layoutId": layout["objectId"]}, "placeholderIdMappings": mappings}}
    plan = {"slide": slide_id, "position": index + 1, "layout": layout_word(layout.get("layoutProperties", {}).get("name")), "layout_name": layout.get("layoutProperties", {}).get("displayName", ""), "elements": changes, "empty_placeholders": empty}
    if "notes" in payload:
        if not isinstance(payload["notes"], str) or len(payload["notes"]) > 100_000:
            raise SlidesValidationError("slides_invalid_text", "Pass speaker notes as text of at most 100000 characters.")
        _, notes_change = text_requests("preview_notes", "\n", payload["notes"], new=True)
        plan["notes"] = {**notes_change, "before": ""}
    return [create, *requests], plan
