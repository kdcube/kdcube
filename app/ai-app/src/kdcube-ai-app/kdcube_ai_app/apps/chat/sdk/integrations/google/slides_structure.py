"""Bounded Slides snapshots: theme layout words, composed geometry and text fit."""
from __future__ import annotations

import math
from fractions import Fraction

from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError

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


def _emu(dimension):
    return float((dimension or {}).get("magnitude", 0)) * (12700 if (dimension or {}).get("unit") == "PT" else 1)


def _matrix(transform):
    t = transform or {}
    factor = 12700 if t.get("unit") == "PT" else 1
    return (t.get("scaleX", 1), t.get("shearY", 0), t.get("shearX", 0), t.get("scaleY", 1), t.get("translateX", 0) * factor, t.get("translateY", 0) * factor)


def _compose(p, c):
    a,b,cx,d,e,f = p
    g,h,i,j,k,l = c
    return (a*g+cx*h, b*g+d*h, a*i+cx*j, b*i+d*j, a*k+cx*l+e, b*k+d*l+f)


def geometry(element, parent=(1,0,0,1,0,0)):
    if "elementGroup" in element:
        composed = _compose(parent, _matrix(element.get("transform")))
        children = [geometry(child, composed) for child in element["elementGroup"].get("children", [])]
        if children:
            x, y = min(c["x"] for c in children), min(c["y"] for c in children)
            return {"x": x, "y": y, "width": max(c["x"]+c["width"] for c in children)-x, "height": max(c["y"]+c["height"] for c in children)-y, "rotation_deg": round(math.degrees(math.atan2(composed[1], composed[0])), 3)}
    a,b,c,d,x,y = _compose(parent, _matrix(element.get("transform")))
    width = _emu(element.get("size", {}).get("width"))
    height = _emu(element.get("size", {}).get("height"))
    corners = [(x, y), (x+a*width,y+b*width), (x+c*height,y+d*height), (x+a*width+c*height,y+b*width+d*height)]
    xs,ys = zip(*corners)
    return {"x": min(xs), "y": min(ys), "width": max(xs)-min(xs), "height": max(ys)-min(ys), "rotation_deg": round(math.degrees(math.atan2(b,a)), 3)}


def shape_text(shape):
    return "".join(str(e.get("textRun", {}).get("content", "")) for e in shape.get("text", {}).get("textElements", []))


def _fit_box(element, parent):
    """Measure the text frame along its transformed local axes."""
    a, b, c, d, _, _ = _compose(parent, _matrix(element.get("transform")))
    size = element.get("size", {})
    return {
        "width": _emu(size.get("width")) * math.hypot(a, b),
        "height": _emu(size.get("height")) * math.hypot(c, d),
    }


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
    width, height = max(1, box["width"] / 12700 - 20), max(1, box["height"] / 12700 - 20)
    def styles(current, seen=None):
        seen=set(seen or ())
        own=current.get("text", {}).get("textElements", [])
        parent=current.get("placeholder", {}).get("parentObjectId")
        inherited=styles(all_shapes[parent], seen|{parent}) if parent in all_shapes and parent not in seen else []
        return own+inherited
    effective=styles(shape)
    bold=any(e.get("textRun", {}).get("style", {}).get("bold") for e in effective)
    spacing=max([float(e["paragraphMarker"]["style"]["lineSpacing"])/100 for e in effective if e.get("paragraphMarker", {}).get("style", {}).get("lineSpacing")] or [1.0])
    if any(e.get("paragraphMarker", {}).get("bullet") for e in effective):
        width=max(1,width-font)
    needed = 0
    for paragraph in text.splitlines():
        units = sum((0.60 if "\u0400" <= c <= "\u052f" else 0.50)+(.08 if bold else 0) for c in paragraph)
        needed += max(1, math.ceil(units * font / width))
    available = height / (font * max(.1,spacing) * 1.35)
    ratio = needed / available
    return {"status": "ok" if ratio <= .9 else "tight" if ratio <= 1 else "overflow_risk", "ratio": round(ratio, 3), "font_size_pt": font, "lines_needed": needed, "lines_available": round(available, 2)}


def _walk(elements, parent=(1,0,0,1,0,0)):
    for element in elements:
        yield element, parent
        if "elementGroup" in element:
            yield from _walk(element["elementGroup"].get("children", []), _compose(parent, _matrix(element.get("transform"))))


def _layout_read(elements, width, height):
    """A deterministic read projection; tolerance is an uncalibrated heuristic."""
    rows = [e for e in elements if not e.get("group_ref") and e["geometry"]["width"] > 0 and e["geometry"]["height"] > 0]
    off_slide = [e["ref"] for e in rows if e["geometry"]["x"] < 0 or e["geometry"]["y"] < 0 or e["geometry"]["x"]+e["geometry"]["width"] > width or e["geometry"]["y"]+e["geometry"]["height"] > height]
    relations, overlaps = [], []
    diagnostic_rows = rows[:50]
    for i,left in enumerate(diagnostic_rows):
        a = left["geometry"]
        for right in diagnostic_rows[i+1:]:
            b = right["geometry"]
            if a["x"] < b["x"]+b["width"] and b["x"] < a["x"]+a["width"] and a["y"] < b["y"]+b["height"] and b["y"] < a["y"]+a["height"]:
                overlaps.append([left["ref"],right["ref"]])
            elif a["x"]+a["width"] <= b["x"]:
                relations.append({"element": left["ref"], "left_of": right["ref"]})
            elif b["x"]+b["width"] <= a["x"]:
                relations.append({"element": right["ref"], "left_of": left["ref"]})
            elif a["y"]+a["height"] <= b["y"]:
                relations.append({"element": left["ref"], "above": right["ref"]})
            elif b["y"]+b["height"] <= a["y"]:
                relations.append({"element": right["ref"], "above": left["ref"]})
    result = {"relations": relations[:100], "overlaps": overlaps[:100], "off_slide": off_slide[:100], "free_regions": [], "geometry_diagnostics_truncated": len(rows)>50 or len(relations)>100 or len(overlaps)>100 or len(off_slide)>100}
    if width <= 0 or height <= 0 or not rows or len(rows) > 30 or overlaps or any(e["rotation_deg"] for e in rows):
        return result
    def edges(axis, size):
        values = sorted(v for e in rows for v in (e["geometry"][axis],e["geometry"][axis]+e["geometry"][size]))
        groups = []
        tolerance = (width if axis == "x" else height)*.015
        for value in values:
            if groups and value-groups[-1][0] <= tolerance:groups[-1].append(value)
            else:groups.append([value])
        return [sum(g)/len(g) for g in groups], any(len(g)>1 for g in groups)
    xs, x_shared = edges("x","width")
    ys, y_shared = edges("y","height")
    if (len(rows)>1 and not (x_shared or y_shared)) or not 2 <= len(xs) <= 9 or not 2 <= len(ys) <= 9:
        return result
    occupied, cells = set(), []
    for e in rows:
        box=e["geometry"]
        x1=min(range(len(xs)),key=lambda i:abs(xs[i]-box["x"]))
        x2=min(range(len(xs)),key=lambda i:abs(xs[i]-box["x"]-box["width"]))
        y1=min(range(len(ys)),key=lambda i:abs(ys[i]-box["y"]))
        y2=min(range(len(ys)),key=lambda i:abs(ys[i]-box["y"]-box["height"]))
        if x2 <= x1 or y2 <= y1:
            return result
        cells.append({"at":[y1,x1],"row_span":y2-y1,"col_span":x2-x1,"element":e["ref"]})
        occupied.update((y,x) for y in range(y1,y2) for x in range(x1,x2))
    result["grid"]={"region":"full","cols":[round((xs[i+1]-xs[i])/width,4) for i in range(len(xs)-1)],"rows":[round((ys[i+1]-ys[i])/height,4) for i in range(len(ys)-1)],"cells":cells}
    result["free_regions"]=[{"at":[y,x]} for y in range(len(ys)-1) for x in range(len(xs)-1) if (y,x) not in occupied]
    return result


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
        slides.append({"position": position, "ref": slide.get("objectId", ""), "kind": layout.get("layout") or ("table" if any(e["kind"] == "table" for e in elements) else "other"), "layout_name": layout.get("layout_name", ""), "title": title, "skipped": bool(props.get("isSkipped")), "elements": elements, "notes": notes, "notes_truncated": len(notes)<len(raw_notes), **_layout_read(elements,width,height)})
    next_slide = start + len(slides)
    truncated = budget <= 0 or any(e["text_truncated"] for slide in slides for e in slide["elements"]) or any(slide["notes_truncated"] for slide in slides)
    return {"schema": SCHEMA, "presentation": {"title": presentation.get("title", ""), "revision_id": presentation.get("revisionId", ""), "slides": len(all_slides), "page": {"aspect": f"{ratio.numerator}:{ratio.denominator}"}}, "layouts": layouts, "slides": slides, "range": {"start_slide": start, "returned": len(slides), "next_slide": next_slide if next_slide <= len(all_slides) else None}, "text_truncated": truncated}
