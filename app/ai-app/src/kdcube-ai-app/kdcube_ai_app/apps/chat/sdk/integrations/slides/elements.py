"""New shape and image construction; owns physical padding and frame policy."""
from __future__ import annotations

from collections.abc import Mapping

from kdcube_ai_app.apps.chat.sdk.integrations.slides.editing import content, text_requests
from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import EMU_PER_PT, TEXT_INSET_PT, element_properties
from kdcube_ai_app.apps.chat.sdk.integrations.slides.grid import invalid
from kdcube_ai_app.apps.chat.sdk.integrations.slides.layouts import new_id
from kdcube_ai_app.apps.chat.sdk.integrations.slides.placement import contained, font_size
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError
from kdcube_ai_app.apps.chat.sdk.integrations.slides.frames import IMAGE_GROUP, CALLOUT_GROUP, FRAME_TITLE, title_request

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


def compile_new_element(raw_slide, spec, box, *, image_sizes=None):
    if "image" in spec or "image_uri" in spec or "file_ref" in spec:
        image = image_source(spec)
        url = image.get("image_uri")
        if not url:
            raise SlidesValidationError("slides_image_hosting_unavailable", "Stage file_ref through the deployment's image delivery route.")
        dimensions = (image_sizes or {}).get(url)
        if not dimensions:
            raise SlidesValidationError("slides_image_dimensions_unavailable", "Validate the image format and dimensions before placing it.")
        cell = box
        box = contained(cell, dimensions["width"]/dimensions["height"])
        object_id = new_id()
        requests = [{"createImage": {"objectId": object_id, "url": url, "elementProperties": element_properties(raw_slide["objectId"], box)}}]
        if "border" in spec and type(spec["border"]) is not bool:
            invalid("Pass border as true or false.")
        outline = {"propertyState": "RENDERED" if spec.get("border") else "NOT_RENDERED", "outlineFill": {"solidFill": {"color": theme_color("theme.dark1")}}, "weight": {"magnitude": 1, "unit": "PT"}}
        if any(abs(box[key]-cell[key]) > 1e-6 for key in ("width", "height")):
            frame_id, group_id = new_id(), new_id()
            requests.extend([
                {"createShape": {"objectId": frame_id, "shapeType": "RECTANGLE", "elementProperties": element_properties(raw_slide["objectId"], cell)}},
                {"updateShapeProperties": {"objectId": frame_id, "shapeProperties": {"shapeBackgroundFill": {"propertyState": "NOT_RENDERED"}, "outline": outline}, "fields": "shapeBackgroundFill,outline"}},
                title_request(frame_id, FRAME_TITLE),
                {"groupObjects": {"groupObjectId": group_id, "childrenObjectIds": [frame_id, object_id]}},
                title_request(group_id, IMAGE_GROUP),
            ])
            return requests, {"ref": group_id, "kind": "image"}, cell
        if spec.get("border"):
            requests.append({"updateImageProperties": {"objectId": object_id, "imageProperties": {"outline": outline}, "fields": "outline"}})
        return requests, {"ref": object_id, "kind": "image"}, cell
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
    if kind != "text_box" and plain and "fill" in spec:
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
        requests.append(title_request(object_id, FRAME_TITLE))
        requests.append({"groupObjects": {"groupObjectId": group_id, "childrenObjectIds": [object_id, text_id]}})
        requests.append(title_request(group_id, CALLOUT_GROUP))
        object_id = group_id
    if "footer" in spec and type(spec["footer"]) is not bool:
        invalid("Pass footer as true or false.")
    return requests, {"ref": object_id, "kind": "callout" if text_id != object_id else "shape", **change, "text_inset_pt": TEXT_INSET_PT if text_id != object_id else None, "warnings": ["text_below_14pt"] if font < 14 and not spec.get("footer") else []}, box
