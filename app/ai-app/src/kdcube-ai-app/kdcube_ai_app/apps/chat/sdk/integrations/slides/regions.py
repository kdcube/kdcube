"""Named regions derived from a slide's own, group-composed placeholders."""
from __future__ import annotations

import math

from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import EMU_PER_PT, PAGE_MARGIN_PT, REGION_GUTTER_FRACTION, compose, emu, geometry, matrix, union, walk
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError


def regions(presentation, slide):
    size = presentation.get("pageSize", {})
    width, height = emu(size.get("width")), emu(size.get("height"))
    margin = PAGE_MARGIN_PT * EMU_PER_PT
    boxes, unsupported = {}, {}
    placeholders = {"title": [], "body": []}
    for element, parent in walk(slide.get("pageElements", [])):
        kind = element.get("shape", {}).get("placeholder", {}).get("type")
        role = "title" if kind in ("TITLE", "CENTERED_TITLE") else "body" if kind == "BODY" else None
        if not role:
            continue
        transform = compose(parent, matrix(element.get("transform")))
        box = geometry(element, parent)
        if abs(transform[1]) > 1e-8 or abs(transform[2]) > 1e-8 or transform[0] <= 0 or transform[3] <= 0:
            unsupported[role] = {"ref": element.get("objectId"), "rotation_deg": round(math.degrees(math.atan2(transform[1], transform[0])), 3)}
        placeholders[role].append(box)
    for role, values in placeholders.items():
        if values and role not in unsupported:
            boxes[role] = union(values)
    boxes["full"] = {"x": margin, "y": margin, "width": width-2*margin, "height": height-2*margin}
    if not placeholders["body"]:
        if "title" in unsupported:
            unsupported["body"] = unsupported["title"]
        else:
            top = max(margin, boxes.get("title", {}).get("y", 0)+boxes.get("title", {}).get("height", 0)+margin)
            boxes["body"] = {"x": margin, "y": top, "width": width-2*margin, "height": height-margin-top}
    if "body" in boxes:
        body = boxes["body"]
        gutter = width * REGION_GUTTER_FRACTION
        for count, names in ((2, ("left_half", "right_half")), (3, ("left_third", "middle_third", "right_third"))):
            part_width = (body["width"]-(count-1)*gutter)/count
            for index, name in enumerate(names):
                boxes[name] = {**body, "x": body["x"]+index*(part_width+gutter), "width": part_width}
        if len(placeholders["body"]) == 2:
            boxes["left_half"], boxes["right_half"] = sorted(placeholders["body"], key=lambda b: b["x"])
    else:
        for name in ("left_half", "right_half", "left_third", "middle_third", "right_third"):
            if "body" in unsupported:
                unsupported[name] = unsupported["body"]
    return {name: box for name, box in boxes.items() if box["width"] > 0 and box["height"] > 0}, unsupported


def resolve_region(presentation, slide, name="body"):
    boxes, unsupported = regions(presentation, slide)
    if name in unsupported:
        raise SlidesValidationError("slides_region_unsupported_geometry", "Choose another region or layout; this placeholder is rotated or sheared.", details={"region": name, **unsupported[name]})
    if name not in boxes:
        raise SlidesValidationError("slides_region_missing", "Choose an available region from this slide's snapshot.", details={"regions": list(boxes)})
    return boxes[name]
