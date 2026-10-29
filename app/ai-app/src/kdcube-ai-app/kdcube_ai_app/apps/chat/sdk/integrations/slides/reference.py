"""Duplicate a resolved reference slide, preserving unnamed content explicitly."""
from __future__ import annotations

import copy
from collections.abc import Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.slides.composition import image_source
from kdcube_ai_app.apps.chat.sdk.integrations.slides.editing import text_requests
from kdcube_ai_app.apps.chat.sdk.integrations.slides.layouts import insertion_index, new_id, walk
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError, resolve_element, resolve_slide


def duplicate_slide_requests(presentation, view, payload):
    if "like" in payload and "slide" in payload:
        raise SlidesValidationError("slides_invalid_selector", "Choose one reference with like or the addressed slide ref.")
    source = resolve_slide(view["slides"], payload.get("like", payload.get("slide")))
    raw = next(s for s in presentation["slides"] if s["objectId"] == source["ref"])
    mappings = {source["ref"]: new_id(), **{e["objectId"]: new_id() for e in walk(raw.get("pageElements", []))}}
    if len(mappings) > 1000:
        raise SlidesValidationError("slides_reference_too_large", "Choose a reference slide with at most 999 elements.")
    copied = copy.deepcopy(source)
    copied["ref"] = mappings[source["ref"]]
    for element in copied["elements"]:
        element["ref"] = mappings[element["ref"]]
        if element.get("_content_ref") in mappings:
            element["_content_ref"] = mappings[element["_content_ref"]]
        if element.get("group_ref") in mappings:
            element["group_ref"] = mappings[element["group_ref"]]
    rows = copy.deepcopy(view["slides"])
    rows.insert(source["position"], copied)
    for position, row in enumerate(rows, 1):
        row["position"] = position
    position_payload = {key: {"ref": resolve_slide(view["slides"], payload[key])["ref"]} for key in ("before", "after") if payload.get(key) is not None}
    index = insertion_index({"slides": rows}, position_payload) if position_payload else source["position"]
    requests = [
        {"duplicateObject": {"objectId": source["ref"], "objectIds": mappings}},
        {"updateSlidesPosition": {"slideObjectIds": [copied["ref"]], "insertionIndex": index}},
    ]
    replacements = payload.get("replacements", {})
    if not isinstance(replacements, Mapping):
        raise SlidesValidationError("slides_invalid_reference", "Pass replacements as a role-to-text map with optional images.")
    used, changes = set(), []
    for role, value in replacements.items():
        if role in ("images", "notes"):
            continue
        if role not in ("title", "subtitle", "body") and not role.startswith("body_"):
            raise SlidesValidationError("slides_invalid_reference", "Replace title, subtitle, body, body_2, notes or selected images.")
        element = resolve_element(copied, {"role": role})
        edits, change = text_requests(element.get("_content_ref", element["ref"]), element.get("text", ""), value)
        requests.extend(edits)
        used.add(element["ref"])
        changes.append({"role": role, "ref": element["ref"], **change})
    images = replacements.get("images", [])
    if not isinstance(images, list) or len(images) > 100:
        raise SlidesValidationError("slides_invalid_reference", "Pass up to 100 image replacements with element selectors.")
    for image in images:
        if not isinstance(image, Mapping):
            raise SlidesValidationError("slides_invalid_reference", "Each image replacement needs an element selector and image source.")
        element = resolve_element(copied, image.get("element"))
        source_image = image_source(image)
        if element["kind"] != "image" or not source_image.get("image_uri"):
            raise SlidesValidationError("slides_image_source_ambiguous", "Choose an image element and stage exactly one image source.")
        method = image.get("fit", "CENTER_INSIDE")
        if method not in ("CENTER_INSIDE", "CENTER_CROP"):
            raise SlidesValidationError("slides_invalid_image_fit", "Use CENTER_INSIDE or CENTER_CROP.")
        requests.append({"replaceImage": {"imageObjectId": element.get("_content_ref", element["ref"]), "url": source_image["image_uri"], "imageReplaceMethod": method}})
        used.add(element["ref"])
    kept = [{"ref": e["ref"], "kind": e["kind"], "role": e.get("role")} for e in copied["elements"] if e["ref"] not in used and (e.get("text", "").strip() or e["kind"] in ("image", "table", "chart", "video"))]
    if source.get("notes", "").strip() and "notes" not in replacements:
        kept.append({"ref": copied["ref"], "kind": "notes", "role": "notes"})
    plan = {"slide": copied["ref"], "reference_slide": source["ref"], "elements": changes, "kept_from_reference": kept[:100], "kept_from_reference_truncated": len(kept)>100}
    if "notes" in replacements:
        notes = replacements["notes"]
        if not isinstance(notes, str):
            raise SlidesValidationError("slides_invalid_text", "Pass notes as text.")
        text_requests("preview_notes", "\n", notes, new=True)
        plan["notes"] = notes
    return requests, plan
