"""Bounded Slides snapshots: theme layout words, composed geometry and text fit."""
from __future__ import annotations

import math
from fractions import Fraction

from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError
from kdcube_ai_app.apps.chat.sdk.integrations.google.slides_fit import fit, shape_text
from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import GRID_ALIGNMENT_TOLERANCE, emu as _emu, matrix as _matrix, compose as _compose, geometry, walk as _walk
from kdcube_ai_app.apps.chat.sdk.integrations.slides.regions import regions
from kdcube_ai_app.apps.chat.sdk.integrations.slides.diagnostics import layout_read as _layout_read
from kdcube_ai_app.apps.chat.sdk.integrations.slides.classify import classify

SCHEMA = "kdcube.slides.snapshot.v1"
MEDIA_TYPE = "application/vnd.kdcube.slides.snapshot+json"
MAX_ELEMENT_TEXT = 12_000
MAX_CALL_TEXT = 100_000
MAX_SLIDES = 50
LAYOUT_WORDS = frozenset("BLANK CAPTION_ONLY TITLE TITLE_AND_BODY TITLE_AND_TWO_COLUMNS TITLE_ONLY SECTION_HEADER SECTION_TITLE_AND_DESCRIPTION ONE_COLUMN_TEXT MAIN_POINT BIG_NUMBER".split())
PPTX_LAYOUTS = {"OBJECT": "title_and_body", "TWO_OBJECTS": "title_and_two_columns"}


def layout_word(name):
    name = str(name or "").upper()
    return name.lower() if name in LAYOUT_WORDS else PPTX_LAYOUTS.get(name)


def _fit_box(element, parent):
    """Measure the text frame along its transformed local axes."""
    a, b, c, d, _, _ = _compose(parent, _matrix(element.get("transform")))
    size = element.get("size", {})
    return {
        "width": _emu(size.get("width")) * math.hypot(a, b),
        "height": _emu(size.get("height")) * math.hypot(c, d),
    }


def snapshot(presentation, *, start=1, limit=20):
    if type(start) is not int or start < 1 or type(limit) is not int or not 1 <= limit <= MAX_SLIDES:
        raise SlidesValidationError("slides_invalid_range", f"Use a 1-based start_slide and a slide_limit from 1 to {MAX_SLIDES}.")
    page = presentation.get("pageSize", {})
    width, height = _emu(page.get("width")), _emu(page.get("height"))
    ratio = Fraction(width / height).limit_denominator(20) if height else Fraction(16, 9)
    all_shapes = {e.get("objectId"): e["shape"] for p in [*presentation.get("slides", []), *presentation.get("layouts", []), *presentation.get("masters", [])] for e,_ in _walk(p.get("pageElements", [])) if "shape" in e}
    layouts = []
    layout_by_id = {}
    for layout in presentation.get("layouts", []):
        props = layout.get("layoutProperties", {})
        row = {"ref": layout.get("objectId"), "layout": layout_word(props.get("name")), "layout_name": props.get("displayName", props.get("name", "")), "placeholders": [e["shape"]["placeholder"]["type"].lower() for e,_ in _walk(layout.get("pageElements", [])) if e.get("shape", {}).get("placeholder", {}).get("type")]}
        layouts.append(row)
        layout_by_id[layout.get("objectId")] = row
    slides = []
    budget = MAX_CALL_TEXT
    all_slides = presentation.get("slides", [])
    for position, slide in enumerate(all_slides[start-1:start-1+limit], start):
        elements = []
        group_parents = {child.get("objectId"): group.get("objectId") for group,_ in _walk(slide.get("pageElements", [])) if "elementGroup" in group for child,_ in _walk(group["elementGroup"].get("children", []))}
        for element,parent in _walk(slide.get("pageElements", [])):
            shape = element.get("shape", {})
            kind = next((name for key,name in (("shape","shape"),("image","image"),("table","table"),("sheetsChart","chart"),("video","video"),("elementGroup","group")) if key in element), "other")
            box = geometry(element, parent)
            raw_text = shape_text(shape) if shape else ""
            if kind == "table":
                raw_text = "\n".join(shape_text(cell) for row in element["table"].get("tableRows", []) for cell in row.get("tableCells", []))
            text = raw_text[:min(MAX_ELEMENT_TEXT, max(0,budget))]
            budget -= len(text)
            row = {"ref": element.get("objectId", ""), "kind": kind, "text": text, "text_truncated": len(text) < len(raw_text), "alt_text": element.get("description") or element.get("title", ""), "rotation_deg": box.pop("rotation_deg"), "geometry": box}
            if element.get("objectId") in group_parents:
                row["group_ref"] = group_parents[element["objectId"]]
            placeholder = str(shape.get("placeholder", {}).get("type", "")).lower()
            if placeholder:
                row["role"] = "title" if placeholder == "centered_title" else placeholder
            if shape:
                row["fit"] = fit(shape, _fit_box(element, parent), all_shapes)
            elements.append(row)
        bodies = sorted((e for e in elements if e.get("role") == "body"), key=lambda e:e["geometry"]["x"])
        for n, body in enumerate(bodies, 1):
            body["role"] = "body" if n == 1 else f"body_{n}"
            if len(bodies) == 2:
                body["region"] = "left_half" if n == 1 else "right_half"
        title = next((e["text"].strip() for e in elements if e.get("role") == "title"), "")
        props = slide.get("slideProperties", {})
        layout = layout_by_id.get(props.get("layoutObjectId"), {})
        notes_page = props.get("notesPage", {})
        notes_id = notes_page.get("notesProperties", {}).get("speakerNotesObjectId")
        notes = next((shape_text(e.get("shape", {})) for e in notes_page.get("pageElements", []) if e.get("objectId") == notes_id), "")
        raw_notes = notes
        notes = notes[:max(0,min(MAX_ELEMENT_TEXT,budget))]
        budget -= len(notes)
        available_regions, unsupported_regions = regions(presentation, slide)
        slides.append({"position": position, "ref": slide.get("objectId", ""), "kind": classify(elements, layout.get("layout"), title), "layout_name": layout.get("layout_name", ""), "title": title, "skipped": bool(props.get("isSkipped")), "elements": elements, "notes": notes, "notes_truncated": len(notes)<len(raw_notes), "regions": list(available_regions), "unsupported_regions": [{"region": name, **details} for name, details in unsupported_regions.items()], **_layout_read(elements,width,height)})
    next_slide = start + len(slides)
    truncated = budget <= 0 or any(e["text_truncated"] for slide in slides for e in slide["elements"]) or any(slide["notes_truncated"] for slide in slides)
    return {"schema": SCHEMA, "presentation": {"title": presentation.get("title", ""), "revision_id": presentation.get("revisionId", ""), "slides": len(all_slides), "page": {"aspect": f"{ratio.numerator}:{ratio.denominator}"}}, "layouts": layouts, "slides": slides, "range": {"start_slide": start, "returned": len(slides), "next_slide": next_slide if next_slide <= len(all_slides) else None}, "text_truncated": truncated}
