"""Compile shapes, existing elements and images into computed layout boxes."""
from __future__ import annotations

from collections.abc import Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.slides.editing import content, text_requests
from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import EMU_PER_PT, TEXT_INSET_PT, compose, element_properties, matrix
from kdcube_ai_app.apps.chat.sdk.integrations.slides.grid import grid_cells, invalid
from kdcube_ai_app.apps.chat.sdk.integrations.slides.layouts import new_id, walk
from kdcube_ai_app.apps.chat.sdk.integrations.slides.placement import contained, font_size, placement
from kdcube_ai_app.apps.chat.sdk.integrations.slides.regions import resolve_region
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError, resolve_element, resolve_slide

COMPOSITION_ACTIONS = frozenset({"compose", "add_shape", "add_image", "set_alt_text"})
SHAPES = {"text_box": "TEXT_BOX", "rectangle": "RECTANGLE", "round_rectangle": "ROUND_RECTANGLE", "ellipse": "ELLIPSE"}
THEME_COLORS = {"dark1", "light1", "dark2", "light2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hyperlink", "followed_hyperlink"}


def theme_color(value):
    if not isinstance(value, str) or not value.startswith("theme.") or value[6:] not in THEME_COLORS:
        invalid("Choose a theme color such as theme.accent1 or theme.dark1.")
    return {"themeColor": value[6:].upper()}


def image_source(spec):
    source = spec.get("image", spec)
    if not isinstance(source, Mapping) or bool(source.get("image_uri")) == bool(source.get("file_ref")):
        raise SlidesValidationError("slides_image_source_ambiguous", "Pass exactly one image_uri or file_ref.")
    return source


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
        if element["kind"] == "image":
            box = contained(box, current["width"]/current["height"])
        sx, sy = box["width"]/current["width"], box["height"]/current["height"]
        transformed = compose((sx, 0, 0, sy, box["x"]-sx*current["x"], box["y"]-sy*current["y"]), matrix(original.get("transform")))
        a, b, c, d, x, y = transformed
        request = {"updatePageElementTransform": {"objectId": element["ref"], "applyMode": "ABSOLUTE", "transform": {"scaleX": a, "shearY": b, "shearX": c, "scaleY": d, "translateX": x, "translateY": y, "unit": "EMU"}}}
        return [request], {"ref": element["ref"], "existing": True}, box
    if "image" in spec or "image_uri" in spec or "file_ref" in spec:
        image = image_source(spec)
        url = image.get("image_uri")
        if not url:
            raise SlidesValidationError("slides_image_hosting_unavailable", "Stage file_ref through the deployment's image delivery route.")
        dimensions = (image_sizes or {}).get(url)
        if not dimensions:
            raise SlidesValidationError("slides_image_dimensions_unavailable", "Validate the image format and dimensions before placing it.")
        box = contained(box, dimensions["width"]/dimensions["height"])
        object_id = new_id()
        requests = [{"createImage": {"objectId": object_id, "url": url, "elementProperties": element_properties(raw_slide["objectId"], box)}}]
        if spec.get("border") is not None:
            if type(spec["border"]) is not bool:
                invalid("Pass border as true or false.")
            requests.append({"updateImageProperties": {"objectId": object_id, "imageProperties": {"outline": {"propertyState": "RENDERED" if spec["border"] else "NOT_RENDERED", "outlineFill": {"solidFill": {"color": theme_color("theme.dark1")}}, "weight": {"magnitude": 1, "unit": "PT"}}}, "fields": "outline"}})
        return requests, {"ref": object_id, "kind": "image"}, box
    if any(key in spec for key in ("table", "video", "chart")):
        raise SlidesValidationError("slides_element_kind_not_yet_supported", "Position an existing table, video or chart by selector. New table and video creation follows in S3.")
    kind = spec.get("shape", "text_box")
    if not isinstance(kind, str) or kind not in SHAPES:
        invalid("Choose text_box, rectangle, round_rectangle or ellipse.")
    object_id = new_id()
    requests = [{"createShape": {"objectId": object_id, "shapeType": SHAPES[kind], "elementProperties": element_properties(raw_slide["objectId"], box)}}]
    props, fields = {}, []
    if "fill" in spec:
        props["shapeBackgroundFill"] = {"solidFill": {"color": theme_color(spec["fill"])}}
        fields.append("shapeBackgroundFill")
    if "outline" in spec:
        props["outline"] = {"outlineFill": {"solidFill": {"color": theme_color(spec["outline"])}}}
        fields.append("outline")
    if fields:
        requests.append({"updateShapeProperties": {"objectId": object_id, "shapeProperties": props, "fields": ",".join(fields)}})
    value = spec.get("items", spec.get("text", ""))
    plain, _ = content(value)
    text_id = object_id
    if kind != "text_box" and plain:
        inset = TEXT_INSET_PT*EMU_PER_PT
        if min(box["width"], box["height"]) <= 2*inset:
            invalid("The shape is too small for its text inset; enlarge its grid cell.")
        text_id = new_id()
        inner = {"x": box["x"]+inset, "y": box["y"]+inset, "width": box["width"]-2*inset, "height": box["height"]-2*inset}
        requests.append({"createShape": {"objectId": text_id, "shapeType": "TEXT_BOX", "elementProperties": element_properties(raw_slide["objectId"], inner)}})
    edits, change = text_requests(text_id, "\n", value, new=True)
    requests.extend(edits)
    font = font_size(spec)
    requests.append({"updateTextStyle": {"objectId": text_id, "textRange": {"type": "ALL"}, "style": {"fontSize": {"magnitude": font, "unit": "PT"}, "foregroundColor": {"opaqueColor": theme_color(spec.get("color", "theme.dark1"))}}, "fields": "fontSize,foregroundColor"}})
    align = spec.get("align", "start")
    if align not in ("start", "center", "end"):
        invalid("Text align is start, center or end.")
    requests.append({"updateParagraphStyle": {"objectId": text_id, "textRange": {"type": "ALL"}, "style": {"alignment": align.upper()}, "fields": "alignment"}})
    if text_id != object_id:
        group_id = new_id()
        requests.append({"groupObjects": {"groupObjectId": group_id, "childrenObjectIds": [object_id, text_id]}})
        object_id = group_id
    if "footer" in spec and type(spec["footer"]) is not bool:
        invalid("Pass footer as true or false.")
    return requests, {"ref": object_id, "text_ref": text_id, "kind": "shape", **change, "text_inset_pt": TEXT_INSET_PT if text_id != object_id else None, "warnings": ["text_below_14pt"] if font < 14 and not spec.get("footer") else []}, box


def compile_composition(presentation, view, action, payload, *, image_sizes=None):
    slide = resolve_slide(view["slides"], payload.get("slide"))
    raw = next(s for s in presentation["slides"] if s["objectId"] == slide["ref"])
    if action == "set_alt_text":
        element = resolve_element(slide, payload.get("element"))
        updates = {key: payload[key] for key in ("title", "description") if key in payload}
        if not updates or any(not isinstance(value, str) or len(value) > 12000 for value in updates.values()):
            invalid("Pass title or description text of at most 12000 characters.")
        return [{"updatePageElementAltText": {"objectId": element["ref"], **updates}}], {"slide": slide["ref"], "ref": element["ref"], **updates}
    if action == "compose":
        box = resolve_region(presentation, raw, payload.get("region", "body"))
        cells = grid_cells(payload, box)
    else:
        spec = dict(payload)
        if action == "add_image":
            spec["image"] = image_source(payload)
        source = spec.get("image")
        dimensions = (image_sizes or {}).get(source.get("image_uri")) if isinstance(source, Mapping) else None
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
    return requests, {"slide": slide["ref"], "region": payload.get("region", "body") if action == "compose" else None, "elements": changes}
