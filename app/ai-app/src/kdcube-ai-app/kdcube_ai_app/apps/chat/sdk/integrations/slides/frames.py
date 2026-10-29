"""Recognizable ordinary groups for image cells and inset filled callouts."""
from __future__ import annotations

from kdcube_ai_app.apps.chat.sdk.integrations.slides.geometry import EMU_PER_PT, TEXT_INSET_PT, compose, emu, geometry, inverse, matrix
from kdcube_ai_app.apps.chat.sdk.integrations.slides.selectors import SlidesValidationError

IMAGE_GROUP = "kdcube:image-frame"
CALLOUT_GROUP = "kdcube:callout"
FRAME_TITLE = "kdcube:frame"


def frame_content(element):
    children = element.get("elementGroup", {}).get("children", [])
    if len(children) != 2 or element.get("title") not in (IMAGE_GROUP, CALLOUT_GROUP):
        return None
    frames = [child for child in children if child.get("title") == FRAME_TITLE and "shape" in child]
    if len(frames) != 1:
        return None
    content = next(child for child in children if child is not frames[0])
    if element["title"] == IMAGE_GROUP and "image" in content:
        return "image", content
    if element["title"] == CALLOUT_GROUP and "shape" in content:
        return "callout", content
    return None


def title_request(object_id, title):
    return {"updatePageElementAltText": {"objectId": object_id, "title": title}}


def resize_content(group, target, new_group_transform):
    frame = frame_content(group)
    if not frame:
        return []
    kind, content = frame
    original = compose(matrix(group.get("transform")), matrix(content.get("transform")))
    if abs(original[1]) > 1e-8 or abs(original[2]) > 1e-8 or original[0] <= 0 or original[3] <= 0:
        raise SlidesValidationError("slides_composition_unsupported_geometry", "Unrotate the framed image or callout before resizing its cell.")
    if kind == "image":
        old_box = geometry(content, matrix(group.get("transform")))
        ratio = old_box["width"]/old_box["height"]
        width = min(target["width"], target["height"]*ratio)
        height = width/ratio
        box = {"x": target["x"]+(target["width"]-width)/2, "y": target["y"]+(target["height"]-height)/2, "width": width, "height": height}
    else:
        inset = TEXT_INSET_PT*EMU_PER_PT
        if min(target["width"], target["height"]) <= 2*inset:
            raise SlidesValidationError("slides_invalid_composition", "Enlarge the callout cell to fit its text inset.")
        box = {"x": target["x"]+inset, "y": target["y"]+inset, "width": target["width"]-2*inset, "height": target["height"]-2*inset}
    size = content.get("size", {})
    if min(emu(size.get("width")), emu(size.get("height"))) <= 0:
        raise SlidesValidationError("slides_composition_unsupported_geometry", "The framed content has no positive intrinsic size.")
    desired = (box["width"]/emu(size.get("width")), 0, 0, box["height"]/emu(size.get("height")), box["x"], box["y"])
    a, b, c, d, x, y = compose(inverse(new_group_transform), desired)
    return [{"updatePageElementTransform": {"objectId": content["objectId"], "applyMode": "ABSOLUTE", "transform": {"scaleX": a, "shearY": b, "shearX": c, "scaleY": d, "translateX": x, "translateY": y, "unit": "EMU"}}}]
