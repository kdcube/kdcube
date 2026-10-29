"""Measured heuristic text fit with placeholder style inheritance."""
from __future__ import annotations

import math

# Calibrated on the captured Arial 18, Roboto 18 and Lato 13 body boxes;
# see tests/fixtures/slides/calibration.md for measurements and limits.
FIT_LATIN_WIDTH = 0.46
FIT_CYRILLIC_WIDTH = 0.54
FIT_LINE_PITCH = 1.22
FIT_INSET_PT = 7.2

def shape_text(shape):
    return "".join(str(e.get("textRun", {}).get("content", "")) for e in shape.get("text", {}).get("textElements", []))


def _font(shape, all_shapes, seen=None):
    seen = set(seen or ())
    sizes = [float(run["textRun"]["style"]["fontSize"]["magnitude"]) for run in shape.get("text", {}).get("textElements", []) if run.get("textRun", {}).get("style", {}).get("fontSize", {}).get("magnitude")]
    if sizes:
        return max(sizes)
    parent_id = shape.get("placeholder", {}).get("parentObjectId")
    if parent_id and parent_id not in seen and parent_id in all_shapes:
        return _font(all_shapes[parent_id], all_shapes, seen | {parent_id})
    return 18.0


def fit(shape, box, all_shapes):
    text = shape_text(shape)
    font = _font(shape, all_shapes)
    # Title placeholders can be only one line high. The body-box calibration's
    # vertical inset falsely removes that line; retain horizontal padding and
    # measure titles against their full transformed height.
    title = shape.get("placeholder", {}).get("type") in {"TITLE", "CENTERED_TITLE"}
    width = max(1, box["width"] / 12700 - 2 * FIT_INSET_PT)
    height = max(1, box["height"] / 12700 - (0 if title else 2 * FIT_INSET_PT))
    def styles(current, seen=None):
        seen=set(seen or ())
        own=current.get("text", {}).get("textElements", [])
        parent=current.get("placeholder", {}).get("parentObjectId")
        inherited=styles(all_shapes[parent], seen|{parent}) if parent in all_shapes and parent not in seen else []
        return own+inherited
    effective=styles(shape)
    bold=any(e.get("textRun", {}).get("style", {}).get("bold") for e in effective)
    spacing=max([float(e["paragraphMarker"]["style"]["lineSpacing"])/100 for e in effective if e.get("paragraphMarker", {}).get("style", {}).get("lineSpacing")] or [1.0])
    # Bullet membership belongs to the actual paragraph; an absent bullet
    # means no list, even when the placeholder's parent has a bulleted style.
    if any(e.get("paragraphMarker", {}).get("bullet") for e in shape.get("text", {}).get("textElements", [])):
        width=max(1,width-font)
    needed = 0
    for paragraph in text.splitlines():
        units = sum((FIT_CYRILLIC_WIDTH if "\u0400" <= c <= "\u052f" else FIT_LATIN_WIDTH)+(.08 if bold else 0) for c in paragraph)
        needed += max(1, math.ceil(units * font / width))
    available = height / (font * max(.1,spacing) * FIT_LINE_PITCH)
    ratio = needed / available
    return {"status": "ok" if ratio <= .9 else "tight" if ratio <= 1 else "overflow_risk", "ratio": round(ratio, 3), "font_size_pt": font, "lines_needed": needed, "lines_available": round(available, 2)}


