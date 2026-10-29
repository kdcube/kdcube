"""Composed page geometry, shared by snapshots and coordinate-free placement."""
from __future__ import annotations

import math

EMU_PER_PT = 12700
PAGE_MARGIN_PT = 36
TEXT_INSET_PT = 12
GRID_GAPS_PT = {"small": 21.6, "medium": 28.8, "large": 36}
REGION_GUTTER_FRACTION = .02
GRID_ALIGNMENT_TOLERANCE = .015  # Calibrate against captured slide boxes.


def emu(dimension):
    return float((dimension or {}).get("magnitude", 0)) * (EMU_PER_PT if (dimension or {}).get("unit") == "PT" else 1)


def matrix(transform):
    t = transform or {}
    factor = EMU_PER_PT if t.get("unit") == "PT" else 1
    return (t.get("scaleX", 1), t.get("shearY", 0), t.get("shearX", 0), t.get("scaleY", 1), t.get("translateX", 0) * factor, t.get("translateY", 0) * factor)


def compose(p, c):
    a, b, cx, d, e, f = p
    g, h, i, j, k, l = c
    return (a*g+cx*h, b*g+d*h, a*i+cx*j, b*i+d*j, a*k+cx*l+e, b*k+d*l+f)


def inverse(transform):
    a, b, c, d, e, f = transform
    determinant = a*d-b*c
    if abs(determinant) < 1e-12:
        raise ValueError("singular transform")
    return (d/determinant, -b/determinant, -c/determinant, a/determinant, (c*f-d*e)/determinant, (b*e-a*f)/determinant)


def geometry(element, parent=(1, 0, 0, 1, 0, 0)):
    combined = compose(parent, matrix(element.get("transform")))
    if "elementGroup" in element:
        children = [geometry(child, combined) for child in element["elementGroup"].get("children", [])]
        if children:
            return {**union(children), "rotation_deg": round(math.degrees(math.atan2(combined[1], combined[0])), 3)}
    a, b, c, d, x, y = combined
    width, height = emu(element.get("size", {}).get("width")), emu(element.get("size", {}).get("height"))
    corners = [(x, y), (x+a*width, y+b*width), (x+c*height, y+d*height), (x+a*width+c*height, y+b*width+d*height)]
    xs, ys = zip(*corners)
    return {"x": min(xs), "y": min(ys), "width": max(xs)-min(xs), "height": max(ys)-min(ys), "rotation_deg": round(math.degrees(math.atan2(b, a)), 3)}


def union(boxes):
    x, y = min(b["x"] for b in boxes), min(b["y"] for b in boxes)
    return {"x": x, "y": y, "width": max(b["x"]+b["width"] for b in boxes)-x, "height": max(b["y"]+b["height"] for b in boxes)-y}


def walk(elements, parent=(1, 0, 0, 1, 0, 0)):
    for element in elements:
        yield element, parent
        if "elementGroup" in element:
            yield from walk(element["elementGroup"].get("children", []), compose(parent, matrix(element.get("transform"))))


def element_properties(slide_id, box):
    return {"pageObjectId": slide_id, "size": {"width": {"magnitude": box["width"], "unit": "EMU"}, "height": {"magnitude": box["height"], "unit": "EMU"}}, "transform": {"scaleX": 1, "scaleY": 1, "translateX": box["x"], "translateY": box["y"], "unit": "EMU"}}
