"""One-anchor relative placement and aspect-preserving image boxes."""
from __future__ import annotations

import math

from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_fit import FIT_CYRILLIC_WIDTH, FIT_LATIN_WIDTH, FIT_LINE_PITCH
from kdcube_ai_app.apps.chat.sdk.integrations.slides.editing import content
from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import EMU_PER_PT, TEXT_INSET_PT
from kdcube_ai_app.apps.chat.sdk.integrations.slides.grid import gap_size, invalid
from kdcube_ai_app.apps.chat.sdk.integrations.slides.regions import resolve_region
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import resolve_element

ANCHORS = ("below", "above", "left_of", "right_of", "over")


def font_size(spec):
    size = spec.get("font_size_pt", 18)
    if type(size) not in (int, float) or not math.isfinite(size) or not 1 <= size <= 400:
        invalid("Use font_size_pt between 1 and 400.")
    return size


def contained(box, ratio):
    width = min(box["width"], box["height"]*ratio)
    height = width/ratio
    return {"x": box["x"]+(box["width"]-width)/2, "y": box["y"]+(box["height"]-height)/2, "width": width, "height": height}


def estimated_height(spec, width, image_ratio=None):
    if image_ratio:
        return width/image_ratio
    value, _ = content(spec.get("items", spec.get("text", "")))
    font = font_size(spec)
    usable = max(1, width/EMU_PER_PT-2*TEXT_INSET_PT)
    lines = sum(max(1, math.ceil(sum(FIT_CYRILLIC_WIDTH if "\u0400" <= c <= "\u052f" else FIT_LATIN_WIDTH for c in paragraph)*font/usable)) for paragraph in value.splitlines()) or 1
    return (lines*font*FIT_LINE_PITCH+2*TEXT_INSET_PT)*EMU_PER_PT


def placement(presentation, raw_slide, slide, spec, *, image_ratio=None):
    anchors = [key for key in ANCHORS if spec.get(key) is not None]
    if len(anchors) > 1 or (anchors and "region" in spec):
        invalid("Choose one region or exactly one relative anchor.")
    if not anchors:
        box = resolve_region(presentation, raw_slide, spec.get("region", "body"))
        return box
    key = anchors[0]
    anchor = resolve_element(slide, spec[key])["geometry"]
    if min(anchor["width"], anchor["height"]) <= 0:
        invalid("Choose an anchor with a positive visible width and height.")
    fraction = spec.get("width", 1)
    if type(fraction) not in (int, float) or not math.isfinite(fraction) or not 0 < fraction <= 4:
        invalid("Use width as a positive fraction of the anchor width, at most 4.")
    width = anchor["width"]*fraction
    height = estimated_height(spec, width, image_ratio)
    align = spec.get("align", "start")
    if align not in ("start", "center", "end"):
        invalid("Relative align is start, center or end.")
    factor = {"start": 0, "center": .5, "end": 1}[align]
    gap = gap_size(spec.get("gap", "medium"))
    x, y = anchor["x"], anchor["y"]
    if key in ("below", "above"):
        x += factor*(anchor["width"]-width)
        y += anchor["height"]+gap if key == "below" else -height-gap
    elif key in ("left_of", "right_of"):
        y += factor*(anchor["height"]-height)
        x += anchor["width"]+gap if key == "right_of" else -width-gap
    else:
        parts = str(spec.get("at", "center")).split("_")
        if parts == ["center"]:
            vertical, horizontal = "center", "center"
        elif len(parts) == 2:
            vertical, horizontal = parts
        else:
            invalid("Overlay at is center or top/center/bottom with left/center/right.")
        if vertical not in ("top", "center", "bottom") or horizontal not in ("left", "center", "right"):
            invalid("Choose one of the nine overlay anchor points.")
        x += {"left": 0, "center": .5, "right": 1}[horizontal]*(anchor["width"]-width)
        y += {"top": 0, "center": .5, "bottom": 1}[vertical]*(anchor["height"]-height)
    return {"x": x, "y": y, "width": width, "height": height}
