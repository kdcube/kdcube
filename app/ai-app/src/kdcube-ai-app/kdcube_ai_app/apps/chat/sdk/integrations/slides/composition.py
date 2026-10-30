"""Compile shapes, existing elements and images into computed layout boxes."""
from __future__ import annotations

from collections.abc import Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.slides.elements import compile_new_element, image_source
from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import compose, matrix
from kdcube_ai_app.apps.chat.sdk.integrations.slides.grid import grid_cells, invalid
from kdcube_ai_app.apps.chat.sdk.integrations.slides.placement import contained, placement
from kdcube_ai_app.apps.chat.sdk.integrations.slides.regions import resolve_region
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError, resolve_element, resolve_slide
from kdcube_ai_app.apps.chat.sdk.integrations.slides.frames import frame_content, resize_content

COMPOSITION_ACTIONS = frozenset({"compose", "add_shape", "add_image", "set_alt_text"})


def compile_element(raw_slide, slide, spec, box, *, image_sizes=None):
    if not isinstance(spec, Mapping):
        invalid("Pass an element map.")
    selector = {key: spec[key] for key in ("ref", "role", "text_contains", "alt_text", "kind", "index") if key in spec}
    if selector:
        if any(key in spec for key in ("shape", "image", "text", "items")):
            invalid("Use an existing-element selector or new content, with one form per element.")
        element = resolve_element(slide, selector)
        if element.get("group_ref"):
            raise SlidesValidationError("slides_group_child_placement", "Move the containing group; new elements are placed outside groups.")
        original = next(e for e in raw_slide.get("pageElements", []) if e["objectId"] == element["ref"])
        current = element["geometry"]
        if min(current["width"], current["height"]) <= 0:
            invalid("Choose an existing element with a positive visible width and height.")
        framed = frame_content(original)
        if element["kind"] == "image" and not framed:
            box = contained(box, current["width"]/current["height"])
        sx, sy = box["width"]/current["width"], box["height"]/current["height"]
        transformed = compose((sx, 0, 0, sy, box["x"]-sx*current["x"], box["y"]-sy*current["y"]), matrix(original.get("transform")))
        a, b, c, d, x, y = transformed
        request = {"updatePageElementTransform": {"objectId": element["ref"], "applyMode": "ABSOLUTE", "transform": {"scaleX": a, "shearY": b, "shearX": c, "scaleY": d, "translateX": x, "translateY": y, "unit": "EMU"}}}
        extra = resize_content(original, box, transformed) if framed else []
        return [request, *extra], {"ref": element["ref"], "existing": True}, box
    return compile_new_element(raw_slide, spec, box, image_sizes=image_sizes)


def compile_composition(presentation, view, action, payload, *, image_sizes=None):
    slide = resolve_slide(view["slides"], payload.get("slide"))
    raw = next(s for s in presentation["slides"] if s["objectId"] == slide["ref"])
    if action == "set_alt_text":
        element = resolve_element(slide, payload.get("element"))
        if element["kind"] == "group":
            raise SlidesValidationError("slides_group_alt_text_unsupported", "Select a shape or image inside this ordinary group to edit its alt text.")
        updates = {key: payload[key] for key in ("title", "description") if key in payload}
        if not updates or any(not isinstance(value, str) or len(value) > 12000 for value in updates.values()):
            invalid("Pass title or description text of at most 12000 characters.")
        return [{"updatePageElementAltText": {"objectId": element.get("_content_ref", element["ref"]), **updates}}], {"slide": slide["ref"], "ref": element["ref"], **updates}
    replaced = None
    if action == "compose":
        box = resolve_region(presentation, raw, payload.get("region", "body"))
        cells = grid_cells(payload, box)
    else:
        spec = dict(payload)
        if action == "add_image":
            spec["image"] = image_source(payload)
        source = spec.get("image")
        dimensions = (image_sizes or {}).get(source.get("image_uri")) if isinstance(source, Mapping) else None
        if action == "add_image" and payload.get("element") is not None:
            if any(key in payload for key in ("region", "below", "above", "left_of", "right_of", "over")):
                invalid("Replace one element or choose a region/relative anchor.")
            replaced = resolve_element(slide, payload["element"])
            if replaced.get("group_ref"):
                raise SlidesValidationError("slides_group_child_placement", "Replace the containing group as a whole.")
            box = replaced["geometry"]
        else:
            box = placement(presentation, raw, slide, spec, image_ratio=dimensions["width"]/dimensions["height"] if dimensions else None)
        cells = [(spec, box)]
    requests, changes, used = [], [], set()
    for spec, box in cells:
        edits, change, _ = compile_element(raw, slide, spec, box, image_sizes=image_sizes)
        if change["ref"] in used:
            invalid("Place each existing element only once in a composition.")
        used.add(change["ref"])
        requests.extend(edits)
        if "over" in spec:
            requests.append({"updatePageElementsZOrder": {"pageElementObjectIds": [change["ref"]], "operation": "BRING_TO_FRONT"}})
        changes.append(change)
    if replaced:
        requests.append({"deleteObject": {"objectId": replaced["ref"]}})
    return requests, {"slide": slide["ref"], "region": payload.get("region", "body") if action == "compose" else None, "elements": changes, "replaced_element": replaced["ref"] if replaced else None}
